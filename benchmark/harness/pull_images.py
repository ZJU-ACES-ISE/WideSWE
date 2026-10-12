#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import os
import re
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


def image_repository(ref: str) -> str:
    ref = ref.split("@", 1)[0]
    if ":" in ref.rsplit("/", 1)[-1]:
        ref = ref.rsplit(":", 1)[0]
    return ref


def pinned_reference(digest: str, release_tag: str | None) -> str:
    if "@" in digest:
        repo, digest_value = digest.rsplit("@", 1)
        if release_tag and repo != image_repository(release_tag):
            raise SystemExit("release digest belongs to a different image repository")
    else:
        if not release_tag:
            raise SystemExit("bare release digest requires a release_tag")
        repo, digest_value = image_repository(release_tag), digest
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest_value):
        raise SystemExit("invalid release image SHA256 digest")
    return f"{repo}@{digest_value}"


def image_reference(spec: dict[str, Any]) -> str:
    release_tag = spec.get("release_image") or spec.get("release_tag")
    if spec.get("release_digest"):
        return pinned_reference(spec["release_digest"], release_tag)
    digests = spec.get("release_digests") or []
    if digests:
        if release_tag:
            image_repo = image_repository(release_tag)
            for digest in digests:
                if isinstance(digest, str) and digest.startswith(image_repo + "@"):
                    return pinned_reference(digest, release_tag)
            raise SystemExit("no release digest matches the release image repository")
        return pinned_reference(digests[0], None)
    if spec.get("release_status") == "published":
        raise SystemExit("published release image is missing a pinned digest")
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
        raise SystemExit(
            f"unpublished image is not present locally: {name} -> {target}. "
            "The release maintainer must publish this image and update its lock; "
            "a case-base image is not a replacement for an evaluator dependency image."
        )
    source = image_reference(spec)
    if image_exists(source, dry_run=dry_run):
        print(f"# found local release image: {source}")
    else:
        run(["docker", "pull", source], dry_run=dry_run)
    run(["docker", "tag", source, target], dry_run=dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull WIDESWE release images and tag local runtime names.")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--task", action="append", help="Task ID; may be repeated")
    selection.add_argument("--all", action="store_true", help="All tasks indexed by release120.tsv")
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
    parser.add_argument("--include-build-parents", action="store_true", help="Also pull the custom FROM images")
    args = parser.parse_args()
    if args.all:
        with (REPO_ROOT / "release120.tsv").open(encoding="utf-8", newline="") as handle:
            tasks = [row["case_id"] for row in csv.DictReader(handle, delimiter="\t")]
    else:
        tasks = args.task
    if args.release_lock and len(tasks) != 1:
        parser.error("--release-lock requires exactly one --task")
    entries: dict[str, tuple[str, dict[str, Any]]] = {}
    for task in tasks:
        release_lock_path = args.release_lock or TASKS_DIR / task / "environment" / "deps_image_lock.yaml"
        if not release_lock_path.exists():
            raise SystemExit(f"missing release image lock: {release_lock_path}")
        lock = load_yaml(release_lock_path)
        task_entries = dict(lock.get("images", {}))
        if args.include_build_parents:
            task_entries.update({f"build:{key}": value for key, value in lock.get("build_parent_images", {}).items()})
        if isinstance(lock.get("case_base_image"), dict):
            task_entries["case_base_image"] = lock["case_base_image"]
        if args.include_case_agent_images:
            agents = lock.get("case_agent_images", {}) or {}
            if args.agent_id and args.agent_id not in agents:
                raise SystemExit(f"no case-agent image entry for agent-id: {args.agent_id}")
            task_entries.update({f"agent:{key}": value for key, value in agents.items() if not args.agent_id or args.agent_id == key})
        for name, spec in task_entries.items():
            target = spec.get("local_tag")
            if not target:
                raise SystemExit(f"release lock entry has no local_tag: {task}:{name}")
            if target in entries and spec != entries[target][1]:
                raise SystemExit(f"conflicting release locks for local image: {target}")
            entries[target] = (f"{task}:{name}", spec)
    # Fail before large pulls if any prerequisite is unpublished or malformed.
    for target, (name, spec) in entries.items():
        if spec.get("release_status") == "not_published":
            if not image_exists(target, dry_run=args.dry_run):
                raise SystemExit(f"unpublished release prerequisite: {name} -> {target}")
        else:
            image_reference(spec)
    for name, spec in entries.values():
        pull_image_entry(name, spec, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
