#!/usr/bin/env python3
"""Fetch the exact repository commits referenced by bundled WIDESWE tasks."""

from __future__ import annotations

import argparse
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml


def run(command: list[str]) -> None:
    print("$ " + " ".join(command), flush=True)
    subprocess.run(command, check=True)


def load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks-dir", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.home() / ".cache" / "wideswe" / "source-repos",
    )
    args = parser.parse_args()

    tasks_dir = args.tasks_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    repositories: dict[tuple[str, str], set[str]] = defaultdict(set)
    for repos_file in sorted(tasks_dir.glob("*/repos.yaml")):
        document = load_yaml(repos_file) or {}
        for spec in (document.get("repos") or {}).values():
            local_path = str(spec["local_path"])
            key = (str(spec["url"]), local_path)
            for field in ("snapshot_commit", "base_commit", "gold_commit"):
                if spec.get(field):
                    repositories[key].add(str(spec[field]))

    for (url, local_path), commits in sorted(repositories.items()):
        relative = Path(local_path)
        if relative.parts and relative.parts[0] == "repos":
            relative = Path(*relative.parts[1:])
        destination = output_dir / relative
        if not (destination / ".git").is_dir():
            destination.parent.mkdir(parents=True, exist_ok=True)
            run(["git", "init", "--quiet", str(destination)])
            run(["git", "-C", str(destination), "remote", "add", "origin", url])
        for commit in sorted(commits):
            exists = subprocess.run(
                ["git", "-C", str(destination), "cat-file", "-e", f"{commit}^{{commit}}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if exists.returncode == 0:
                continue
            run(["git", "-C", str(destination), "fetch", "--no-tags", "--depth=1", "origin", commit])
            run(["git", "-C", str(destination), "update-ref", f"refs/wideswe/{commit}", commit])

    print(f"Fetched {len(repositories)} repositories into {output_dir}")
    print(f'export ECOSYNC_SOURCE_REPOS_ROOT="{output_dir}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
