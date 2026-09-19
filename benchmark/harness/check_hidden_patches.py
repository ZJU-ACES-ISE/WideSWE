#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required: python3 -m pip install pyyaml") from exc


REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = Path(os.environ.get("ECOSYNC_TASKS_DIR", REPO_ROOT / "benchmark" / "tasks")).resolve()

SUPPORTED_ORIGINS = {
    "upstream_test_direct",
    "upstream_test_diff",
    "upstream_added_test_file",
    "benchmark_oracle_from_upstream_behavior",
    "contract_aligned_test",
    "contract_aligned_hidden_test",
    "contract_alignment",
    "contract_test",
    "independent_behavior_contract",
    "prompt_aligned_behavioral_test",
}


def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def path_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def patch_changed_files(patch: Path) -> list[dict[str, Any]]:
    check = subprocess.run(
        ["git", "apply", "--numstat", "-z", str(patch)],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check.returncode != 0:
        detail = check.stderr.decode("utf-8", errors="replace")
        raise SystemExit(f"invalid patch format: {patch}\n{detail}")

    summary = subprocess.run(
        ["git", "apply", "--summary", str(patch)],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    created_paths: set[str] = set()
    deleted_paths: set[str] = set()
    for line in summary.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("create mode "):
            created_paths.add(stripped.split(" ", 3)[3])
        elif stripped.startswith("delete mode "):
            deleted_paths.add(stripped.split(" ", 3)[3])

    changes: dict[str, str] = {}

    def add_text_path(path: str, change: str | None = None) -> None:
        if not path:
            return
        if change is None:
            if path in created_paths:
                change = "create"
            elif path in deleted_paths:
                change = "delete"
            else:
                change = "modify"
        changes[path] = change

    def add_path(raw_path: bytes, change: str | None = None) -> None:
        add_text_path(raw_path.decode("utf-8", errors="surrogateescape"), change)

    fields = check.stdout.split(b"\0")
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if not record:
            continue
        parts = record.split(b"\t", 2)
        if len(parts) != 3:
            raise SystemExit(f"invalid numstat record in patch {patch}: {record!r}")
        if parts[2]:
            add_path(parts[2])
            continue

        # With -z, rename/copy records contain the source and destination in
        # the next two NUL-delimited fields. Keep both real paths instead of
        # treating git's human-readable brace notation as a filename.
        if index + 1 >= len(fields):
            raise SystemExit(f"incomplete rename/copy record in patch {patch}")
        add_path(fields[index], "rename")
        add_path(fields[index + 1], "rename")
        index += 2

    # A rename whose contents also changed can be represented by numstat as
    # only the destination path. Patch headers remain authoritative for the
    # source and destination names, so include both in the safety check.
    pending_source: str | None = None
    for line in patch.read_text(encoding="utf-8", errors="surrogateescape").splitlines():
        if line.startswith(("rename from ", "copy from ")):
            pending_source = line.split(" ", 2)[2]
            if pending_source.startswith('"'):
                parsed = shlex.split(pending_source)
                pending_source = parsed[0] if parsed else pending_source
            continue
        if not line.startswith(("rename to ", "copy to ")) or pending_source is None:
            continue
        destination = line.split(" ", 2)[2]
        if destination.startswith('"'):
            parsed = shlex.split(destination)
            destination = parsed[0] if parsed else destination
        add_text_path(pending_source, "rename")
        add_text_path(destination, "rename")
        pending_source = None

    return [{"path": path, "change": change} for path, change in changes.items()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate hidden test patch metadata and injection shape.")
    parser.add_argument("--task", required=True)
    args = parser.parse_args()

    task_dir = TASKS_DIR / args.task
    harness_path = task_dir / "harness.yaml"
    harness = load_yaml(harness_path)
    failures = []
    results = []

    for item in harness.get("hidden_test_patches", []):
        label = f"{item.get('repo')}:{item.get('patch')}"
        required = ["repo", "patch", "test_profile", "origin", "source_file", "hidden_file", "semantic_changes"]
        missing = [field for field in required if not item.get(field)]
        if missing:
            failures.append(f"{label} missing metadata fields: {missing}")
            continue

        if item["origin"] not in SUPPORTED_ORIGINS:
            failures.append(f"{label} has unsupported origin: {item['origin']}")

        patch = task_dir / item["patch"]
        if not patch.exists():
            failures.append(f"{label} missing patch file: {patch}")
            continue

        changed = patch_changed_files(patch)
        created = [entry["path"] for entry in changed if entry["change"] == "create"]
        non_created = [entry for entry in changed if entry["change"] != "create"]

        if item["semantic_changes"] == "none":
            source_files = path_list(item["source_file"])
            hidden_files = path_list(item["hidden_file"])
            changed_paths = {entry["path"] for entry in changed}
            source_set = set(source_files)
            hidden_set = set(hidden_files)
            if source_set != hidden_set:
                failures.append(
                    f"{label} release hidden patches must keep upstream test paths; "
                    f"source_file and hidden_file differ: source={source_files}, hidden={hidden_files}"
                )
                injection_mode = "invalid"
            elif not changed_paths.issubset(hidden_set):
                failures.append(
                    f"{label} hidden patch changes files outside hidden_file/source_file metadata: "
                    f"changed={sorted(changed_paths)}, expected_subset={sorted(hidden_set)}"
                )
                injection_mode = "invalid"
            elif created:
                if non_created:
                    injection_mode = "direct_upstream_test_diff"
                else:
                    injection_mode = "direct_upstream_added_test_file"
            else:
                injection_mode = "direct_upstream_test_diff"

            if injection_mode == "direct_upstream_added_test_file":
                # Upstream PRs can add a brand-new test file that is then
                # applied only in the evaluator workspace. This still uses the
                # original upstream path; it is not a renamed hidden file.
                injection_mode = "direct_upstream_added_test_file"

        results.append(
            {
                "repo": item["repo"],
                "patch": item["patch"],
                "origin": item["origin"],
                "source_file": item["source_file"],
                "hidden_file": item["hidden_file"],
                "semantic_changes": item["semantic_changes"],
                "injection_mode": injection_mode if item["semantic_changes"] == "none" else "custom",
                "changed_files": changed,
            }
        )

    output = {"task_id": args.task, "ok": not failures, "hidden_patches": results, "failures": failures}
    print(json.dumps(output, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
