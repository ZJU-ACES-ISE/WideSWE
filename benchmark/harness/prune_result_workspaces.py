#!/usr/bin/env python3
"""Remove heavyweight result workspaces while keeping diffs, logs, and traces."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS_DIR = REPO_ROOT / "results"
CHOWN_IMAGES = (
    "ecosyncbench/base/android-sdk:36",
    "ecosyncbench/base/dotnet-sdk:8.0.100",
    "ecosyncbench/base/node:22-bookworm-slim",
    "ecosyncbench/base/python:3.14-slim",
    "ecosyncbench/deps/home-assistant-core-py:beaea2d99806",
    "ecosyncbench/deps/home-assistant-supervisor-py:2c6253e4b664",
    "ghcr.io/code-tmp/wideswe/agents/claude-code:2.1.139",
    "ghcr.io/code-tmp/wideswe/agents/codex:0.147.0",
)
WORKSPACE_NAMES = ("agent_workspace", "evaluator_workspace", "agent_scratch")
WORKSPACE_PREFIXES = ("agent_workspace_", "evaluator_workspace_", "agent_scratch_")


def is_workspace_dir(path: Path) -> bool:
    return path.is_dir() and (path.name in WORKSPACE_NAMES or path.name.startswith(WORKSPACE_PREFIXES))


def find_workspaces(results_dir: Path) -> list[Path]:
    if not results_dir.exists():
        return []
    return sorted(path for path in results_dir.rglob("*") if is_workspace_dir(path))


def docker_chown(path: Path) -> bool:
    uid = os.getuid()
    gid = os.getgid()
    for image in CHOWN_IMAGES:
        present = subprocess.run(
            ["docker", "image", "inspect", image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if present.returncode != 0:
            continue
        proc = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{path.resolve()}:/cleanup",
                "--entrypoint",
                "sh",
                image,
                "-c",
                f"chown -R {uid}:{gid} /cleanup",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if proc.returncode == 0:
            return True
    return False


def remove_tree(path: Path) -> dict[str, Any]:
    def make_writable_and_retry(func: Any, item: str, _exc_info: Any) -> None:
        try:
            os.chmod(item, 0o700)
        except OSError:
            pass
        func(item)

    item: dict[str, Any] = {"path": str(path), "status": "removed"}
    try:
        shutil.rmtree(path, onerror=make_writable_and_retry)
    except PermissionError:
        if not docker_chown(path):
            item["status"] = "failed"
            item["error"] = "permission denied and no chown-capable Docker image was available"
            return item
        shutil.rmtree(path, onerror=make_writable_and_retry)
        item["used_docker_chown"] = True
    return item


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    workspaces = find_workspaces(args.results_dir.resolve())
    if args.dry_run:
        print(json.dumps({"dry_run": True, "count": len(workspaces), "workspaces": [str(p) for p in workspaces]}, indent=2))
        return

    results = [remove_tree(path) for path in workspaces]
    print(json.dumps({"dry_run": False, "count": len(results), "results": results}, indent=2))
    failed = [item for item in results if item.get("status") != "removed"]
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
