#!/usr/bin/env python3
"""Fetch the exact repository commits referenced by bundled WIDESWE tasks."""

from __future__ import annotations

import argparse
import re
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


def source_destination(output_dir: Path, local_path: str) -> Path:
    relative = Path(local_path)
    if relative.parts and relative.parts[0] == "repos":
        relative = Path(*relative.parts[1:])
    destination = (output_dir / relative).resolve()
    if relative.is_absolute() or ".." in relative.parts or destination == output_dir or not destination.is_relative_to(output_dir):
        raise ValueError("Repository path must stay within the source cache")
    return destination


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
    repos_files = sorted(tasks_dir.glob("*/repos.yaml"))
    if not repos_files:
        parser.error("No task repos.yaml files found; use the flat benchmark/tasks directory")
    destinations: dict[Path, str] = {}
    for repos_file in repos_files:
        document = load_yaml(repos_file) or {}
        for spec in (document.get("repos") or {}).values():
            local_path = str(spec["local_path"])
            key = (str(spec["url"]), local_path)
            destination = source_destination(output_dir, local_path)
            if destination in destinations and destinations[destination] != key[0]:
                parser.error("Different repository URLs resolve to the same source-cache path")
            destinations[destination] = key[0]
            for field in ("snapshot_commit", "base_commit", "gold_commit"):
                if spec.get(field):
                    commit = str(spec[field])
                    if not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
                        parser.error(f"Expected a full pinned commit in {repos_file.name}: {field}")
                    repositories[key].add(commit.lower())

    for (url, local_path), commits in sorted(repositories.items()):
        destination = source_destination(output_dir, local_path)
        if not (destination / ".git").is_dir():
            destination.parent.mkdir(parents=True, exist_ok=True)
            run(["git", "init", "--quiet", str(destination)])
            run(["git", "-C", str(destination), "remote", "add", "origin", url])
        else:
            origin = subprocess.check_output(
                ["git", "-C", str(destination), "remote", "get-url", "origin"], text=True,
            ).strip()
            if origin != url:
                raise SystemExit("Existing source-cache origin does not match task metadata")
        for commit in sorted(commits):
            exists = subprocess.run(
                ["git", "-C", str(destination), "cat-file", "-e", f"{commit}^{{commit}}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if exists.returncode != 0:
                run(["git", "-C", str(destination), "fetch", "--no-tags", "--depth=1", "origin", commit])
            # Protect already-present objects too, not only newly fetched ones.
            run(["git", "-C", str(destination), "update-ref", f"refs/wideswe/{commit}", commit])

    print(f"Fetched {len(repositories)} repositories into {output_dir}")
    print(f'export ECOSYNC_SOURCE_REPOS_ROOT="{output_dir}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
