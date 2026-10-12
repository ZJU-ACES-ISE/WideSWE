#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
TASKS_DIR = Path(os.environ.get("ECOSYNC_TASKS_DIR", REPO_ROOT / "benchmark" / "tasks"))


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


def push_docker(local_tag: str, release_tag: str) -> str:
    """Reuse daemon-side layer uploads without exporting large tar archives."""
    subprocess.run(["docker", "tag", local_tag, release_tag], cwd=REPO_ROOT, check=True)
    proc = subprocess.Popen(
        ["docker", "push", release_tag], cwd=REPO_ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    digest = None
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="", flush=True)
        match = re.search(r"\bdigest: (sha256:[0-9a-f]{64})\b", line)
        if match:
            digest = match.group(1)
    code = proc.wait()
    if code:
        raise subprocess.CalledProcessError(code, proc.args)
    if digest is None:
        raise SystemExit(f"push succeeded but no digest was found in output for {release_tag}")
    return release_tag.rsplit(":", 1)[0] + "@" + digest


def publish_task(
    task_id: str, force: bool, *, push: bool = False,
    include_build_parents: bool = False, published: dict[str, tuple[str, str]] | None = None,
    transport: str = "archive",
) -> dict[str, Any]:
    lock_path = TASKS_DIR / task_id / "environment" / "deps_image_lock.yaml"
    if not lock_path.exists():
        raise SystemExit(f"missing deps image lock: {lock_path}")
    lock = load_yaml(lock_path)
    if published is None:
        published = {}

    results: dict[str, Any] = {}
    selected_profiles = set(os.environ.get("ECOSYNC_PUBLISH_PROFILES", "").split())
    entries = dict(lock.get("images", {}))
    if include_build_parents:
        entries.update({f"build:{name}": spec for name, spec in lock.get("build_parent_images", {}).items()})
    for profile, spec in entries.items():
        if selected_profiles and profile not in selected_profiles:
            results[profile] = {"skipped": True, "reason": "not selected"}
            continue
        local_tag = spec["local_tag"]
        release_tag = spec["release_tag"]
        if spec.get("release_digest") and not force:
            results[profile] = {"release_digest": spec["release_digest"], "skipped": True}
            continue
        inspect = image_inspect(local_tag)
        if spec.get("local_image_id") != inspect.get("Id"):
            raise SystemExit(f"image changed since release audit: {local_tag}")
        if not push:
            results[profile] = {"local_tag": local_tag, "release_tag": release_tag, "dry_run": True}
            continue
        if release_tag in published:
            digest, image_id = published[release_tag]
            if image_id != inspect.get("Id"):
                raise SystemExit(f"conflicting local images for release tag: {release_tag}")
        else:
            uploader = push_docker if transport == "docker" else push_archive
            digest = uploader(local_tag, release_tag)
            published[release_tag] = (digest, inspect["Id"])
        spec["release_digest"] = digest
        # Uploading does not make a package public. Anonymous verification is separate.
        spec["release_status"] = "not_published"
        spec["local_image_id"] = inspect.get("Id")
        spec["size_bytes"] = inspect.get("Size")
        lock["generated_at_unix"] = int(time.time())
        write_yaml(lock_path, lock)
        results[profile] = {"release_digest": digest, "skipped": False}

    return {"task_id": task_id, "lock": str(lock_path), "images": results}


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish reusable dependency images and update deps_image_lock.yaml.")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--task", action="append")
    selection.add_argument("--all", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--include-build-parents", action="store_true")
    parser.add_argument("--push", action="store_true", help="Actually upload; default is a read-only plan")
    parser.add_argument("--transport", choices=("archive", "docker"), default="archive",
                        help="Use docker to reuse daemon layer uploads without temporary exports")
    args = parser.parse_args()

    if args.all:
        with (REPO_ROOT / "release120.tsv").open(encoding="utf-8", newline="") as handle:
            tasks = [row["case_id"] for row in csv.DictReader(handle, delimiter="\t")]
    else:
        tasks = args.task
    published: dict[str, tuple[str, str]] = {}
    output = {"tasks": [publish_task(
        task_id, args.force, push=args.push,
        include_build_parents=args.include_build_parents, published=published,
        transport=args.transport,
    ) for task_id in tasks]}
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
