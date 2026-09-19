#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required: python3 -m pip install pyyaml") from exc


REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = Path(os.environ.get("ECOSYNC_TASKS_DIR", REPO_ROOT / "benchmark" / "tasks")).resolve()


def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def run(cmd: list[str], *, dry_run: bool) -> None:
    print("$ " + " ".join(cmd))
    if not dry_run:
        subprocess.run(cmd, check=True)


def image_exists(ref: str, *, dry_run: bool) -> bool:
    if dry_run:
        return False
    proc = subprocess.run(
        ["docker", "image", "inspect", ref],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc.returncode == 0


def image_reference(spec: dict[str, Any]) -> str:
    if spec.get("release_digest"):
        release_tag = spec.get("release_image") or spec.get("release_tag")
        if release_tag and "@" not in release_tag:
            image_repo = release_tag.split(":", 1)[0]
            digest = spec["release_digest"]
            if isinstance(digest, str) and digest.startswith(image_repo + "@"):
                return digest
            return f"{image_repo}@{digest}"
        return spec["release_digest"]
    digests = spec.get("release_digests") or []
    if digests:
        release_tag = spec.get("release_image") or spec.get("release_tag")
        if release_tag:
            image_repo = release_tag.split(":", 1)[0]
            for digest in digests:
                if isinstance(digest, str) and digest.startswith(image_repo + "@"):
                    return digest
        return digests[0]
    if spec.get("release_tag"):
        return spec["release_tag"]
    raise SystemExit(f"release image spec has no digest or tag: {spec}")


def pull_image_entry(name: str, spec: dict[str, Any], *, dry_run: bool) -> None:
    if not spec.get("local_tag"):
        raise SystemExit(f"release lock entry has no local_tag: {name}")
    target = spec["local_tag"]
    if spec.get("release_status") == "not_published":
        if image_exists(target, dry_run=dry_run):
            print(f"# found local unpublished image: {target}")
            return
        raise SystemExit(f"unpublished image is not present locally: {name} -> {target}")
    try:
        source = image_reference(spec)
    except SystemExit:
        if image_exists(target, dry_run=dry_run):
            print(f"# found local unpublished image: {target}")
            return
        raise
    if image_exists(source, dry_run=dry_run):
        print(f"# found local release image: {source}")
    else:
        run(["docker", "pull", source], dry_run=dry_run)
    run(["docker", "tag", source, target], dry_run=dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull WIDESWE release images and tag local runtime names.")
    parser.add_argument("--task", required=True)
    parser.add_argument(
        "--release-lock",
        type=Path,
        help="Defaults to benchmark/tasks/<task>/environment/deps_image_lock.yaml when published.",
    )
    parser.add_argument(
        "--include-case-agent-images",
        action="store_true",
        help="Also process task-specific case-env agent images from case_agent_images.",
    )
    parser.add_argument(
        "--agent-id",
        help="When --include-case-agent-images is set, restrict to one case-agent image key.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    task_dir = TASKS_DIR / args.task
    if args.release_lock:
        release_lock_path = args.release_lock
    else:
        release_lock_path = task_dir / "environment" / "deps_image_lock.yaml"
    if not release_lock_path.exists():
        raise SystemExit(f"missing release image lock: {release_lock_path}")

    release_lock = load_yaml(release_lock_path)

    for service, release_spec in release_lock.get("images", {}).items():
        pull_image_entry(service, release_spec, dry_run=args.dry_run)

    case_base_image = release_lock.get("case_base_image")
    if isinstance(case_base_image, dict):
        pull_image_entry("case_base_image", case_base_image, dry_run=args.dry_run)

    if args.include_case_agent_images:
        case_agent_images = release_lock.get("case_agent_images", {}) or {}
        matched_case_agent = False
        for agent_id, release_spec in case_agent_images.items():
            if args.agent_id and args.agent_id != agent_id:
                continue
            matched_case_agent = True
            pull_image_entry(f"case_agent_images.{agent_id}", release_spec, dry_run=args.dry_run)
        if args.agent_id and not matched_case_agent:
            raise SystemExit(f"no case-agent image entry for agent-id: {args.agent_id}")


if __name__ == "__main__":
    main()
