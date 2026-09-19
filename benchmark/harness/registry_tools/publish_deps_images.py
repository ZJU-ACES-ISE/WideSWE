#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
TASKS_DIR = REPO_ROOT / "benchmark" / "tasks"


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False, width=120), encoding="utf-8")


def image_inspect(image: str) -> dict[str, Any]:
    output = subprocess.check_output(["docker", "image", "inspect", image], cwd=REPO_ROOT, text=True)
    return json.loads(output)[0]


def push_archive(local_tag: str, release_tag: str) -> str:
    subprocess.run(["docker", "tag", local_tag, release_tag], cwd=REPO_ROOT, check=True)
    with tempfile.NamedTemporaryFile(prefix="ecosync-deps-publish-", suffix=".tar") as archive:
        subprocess.run(["docker", "save", "-o", archive.name, release_tag], cwd=REPO_ROOT, check=True)
        env = os.environ.copy()
        if env.get("ECOSYNC_REGISTRY_USE_PROXY") != "1":
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
                env.pop(key, None)
        proc = subprocess.Popen(
            [
                "python3",
                "benchmark/harness/registry_tools/push_docker_archive.py",
                archive.name,
                release_tag,
            ],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        lines: list[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="", flush=True)
            lines.append(line)
        code = proc.wait()
        if code != 0:
            raise subprocess.CalledProcessError(code, proc.args)
        output = "".join(lines)
    prefix = release_tag.rsplit(":", 1)[0] + "@sha256:"
    for line in reversed([line.strip() for line in output.splitlines() if line.strip()]):
        if line.startswith(prefix):
            return line
    raise SystemExit(f"push succeeded but no digest was found in output for {release_tag}")


def publish_task(task_id: str, force: bool) -> dict[str, Any]:
    lock_path = TASKS_DIR / task_id / "environment" / "deps_image_lock.yaml"
    if not lock_path.exists():
        raise SystemExit(f"missing deps image lock: {lock_path}")
    lock = load_yaml(lock_path)
    lock["status"] = "publishing"
    lock["generated_at_unix"] = int(time.time())
    lock["publish_method"] = "benchmark/harness/registry_tools/push_docker_archive.py"
    write_yaml(lock_path, lock)

    results: dict[str, Any] = {}
    selected_profiles = set(os.environ.get("ECOSYNC_PUBLISH_PROFILES", "").split())
    for profile, spec in lock.get("images", {}).items():
        if selected_profiles and profile not in selected_profiles:
            results[profile] = {"skipped": True, "reason": "not selected"}
            continue
        local_tag = spec["local_tag"]
        release_tag = spec["release_tag"]
        if spec.get("release_digest") and not force:
            results[profile] = {"release_digest": spec["release_digest"], "skipped": True}
            continue
        inspect = image_inspect(local_tag)
        digest = push_archive(local_tag, release_tag)
        spec["release_digest"] = digest
        spec["local_image_id"] = inspect.get("Id")
        spec["size_bytes"] = inspect.get("Size")
        lock["generated_at_unix"] = int(time.time())
        write_yaml(lock_path, lock)
        results[profile] = {"release_digest": digest, "skipped": False}

    lock["status"] = "published"
    lock["generated_at_unix"] = int(time.time())
    write_yaml(lock_path, lock)
    return {"task_id": task_id, "lock": str(lock_path), "images": results}


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish reusable dependency images and update deps_image_lock.yaml.")
    parser.add_argument("--task", action="append", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    output = {"tasks": [publish_task(task_id, args.force) for task_id in args.task]}
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
