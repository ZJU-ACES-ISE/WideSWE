#!/usr/bin/env python3
"""Download WIDESWE Release120 and expose its cases as a flat task directory."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

DEFAULT_REPO_ID = "wwww369/WIDESWE"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-id", default=DEFAULT_REPO_ID)
    parser.add_argument("--output-dir", type=Path, default=Path("data/release120"))
    args = parser.parse_args()

    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise SystemExit("huggingface-hub is required: python -m pip install -r requirements.txt") from exc

    output_dir = args.output_dir.expanduser().resolve()
    snapshot_download(
        repo_id=args.repo_id,
        repo_type="dataset",
        local_dir=output_dir,
        token=os.environ.get("HF_TOKEN") or None,
    )

    index = output_dir / "release120.tsv"
    tasks_dir = output_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    with index.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    for row in rows:
        case_id = row["case_id"]
        source = output_dir / "cases" / row["ecosystem"] / case_id
        target = tasks_dir / case_id
        if target.is_symlink() and target.resolve() == source.resolve():
            continue
        if target.exists() or target.is_symlink():
            raise SystemExit(f"refusing to replace existing task path: {target}")
        target.symlink_to(source, target_is_directory=True)

    print(f"Downloaded {len(rows)} tasks to {output_dir}")
    print(f'export ECOSYNC_TASKS_DIR="{tasks_dir}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
