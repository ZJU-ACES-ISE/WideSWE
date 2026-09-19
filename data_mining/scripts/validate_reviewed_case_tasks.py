#!/usr/bin/env python3
"""Validate construction-stage task artifacts before running test matrix."""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required: python3 -m pip install pyyaml") from exc


REPO_ROOT = Path(__file__).resolve().parents[2]


REQUIRED_FILES = [
    "task.yaml",
    "repos.yaml",
    "harness.yaml",
    "prompt.md",
    "prompts",
    "prompt_sources.yaml",
    "CASE_NOTES.zh.md",
    "gold/metadata.yaml",
    "provenance/pr_records.json",
    "environment/run_profile.sh",
    "environment/test_profiles.yaml",
]


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=REPO_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def validate_task_dir(task_dir: Path, keep_worktrees: bool) -> list[str]:
    errors: list[str] = []
    print(f"\n[validate] {task_dir.name}")

    for rel in REQUIRED_FILES:
        if not (task_dir / rel).exists():
            errors.append(f"missing required file: {task_dir / rel}")

    if errors:
        return errors

    task = load_yaml(task_dir / "task.yaml")
    repos_yaml = load_yaml(task_dir / "repos.yaml")
    harness = load_yaml(task_dir / "harness.yaml")

    repos = repos_yaml.get("repos") or {}
    if len(repos) != 10:
        errors.append(f"expected 10 pinned ecosystem repos, got {len(repos)}")
    missing_pin = [name for name, spec in repos.items() if not spec.get("snapshot_commit") or not spec.get("base_commit")]
    if missing_pin:
        errors.append(f"repos missing snapshot/base commit: {missing_pin}")

    task_repos = {repo["name"]: repo for repo in task.get("repositories", [])}
    prompt_sources = load_yaml(task_dir / "prompt_sources.yaml") if (task_dir / "prompt_sources.yaml").exists() else {}
    prompt_source_repos = {
        str(item.get("repo"))
        for item in (prompt_sources.get("repo_prompts") or [])
        if item.get("repo")
    }
    for repo_name, repo in task_repos.items():
        if repo_name not in repos:
            errors.append(f"task repo not present in repos.yaml: {repo_name}")
        repo_prompt = task_dir / "prompts" / f"{repo_name}.md"
        if not repo_prompt.exists():
            errors.append(f"task repo {repo_name} missing single-repo prompt: {repo_prompt}")
        elif not repo_prompt.read_text(encoding="utf-8").strip():
            errors.append(f"task repo {repo_name} single-repo prompt is empty: {repo_prompt}")
        if repo_name not in prompt_source_repos:
            errors.append(f"task repo {repo_name} missing prompt_sources.yaml repo_prompts entry")
        for key in ("base_commit", "gold_commit", "gold_patch"):
            if not repo.get(key):
                errors.append(f"task repo {repo_name} missing {key}")

    with tempfile.TemporaryDirectory(prefix=f"{task_dir.name}-patch-check-", dir="/tmp") as temp_dir:
        temp_root = Path(temp_dir)
        worktrees: list[tuple[Path, Path]] = []
        try:
            for repo_name, repo in task_repos.items():
                source = REPO_ROOT / repo["path"]
                worktree = temp_root / repo_name
                source_check = run(["git", "-C", str(source), "cat-file", "-e", f"{repo['base_commit']}^{{commit}}"])
                if source_check.returncode != 0:
                    errors.append(f"{repo_name}: base commit missing locally: {repo['base_commit']}")
                    continue
                add = run(["git", "-C", str(source), "worktree", "add", "--quiet", "--detach", str(worktree), repo["base_commit"]])
                if add.returncode != 0:
                    errors.append(f"{repo_name}: failed to create base worktree: {add.stdout[:500]}")
                    continue
                worktrees.append((source, worktree))

                gold_patch = task_dir / repo["gold_patch"]
                gold = run(["git", "-C", str(worktree), "apply", "--check", str(gold_patch.resolve())])
                print(f"  {repo_name} gold {'ok' if gold.returncode == 0 else 'FAIL'}")
                if gold.returncode != 0:
                    errors.append(f"{repo_name}: gold patch does not apply: {gold.stdout[:1000]}")

            for item in harness.get("hidden_test_patches", []):
                repo_name = item["repo"]
                repo = task_repos.get(repo_name)
                if repo is None:
                    errors.append(f"hidden patch refers to unknown task repo: {repo_name}")
                    continue
                worktree = temp_root / repo_name
                hidden_patch = task_dir / item["patch"]
                hidden = run(["git", "-C", str(worktree), "apply", "--check", str(hidden_patch.resolve())])
                print(f"  {repo_name} hidden {'ok' if hidden.returncode == 0 else 'FAIL'}")
                if hidden.returncode != 0:
                    errors.append(f"{repo_name}: hidden patch does not apply: {hidden.stdout[:1000]}")
        finally:
            if not keep_worktrees:
                for source, worktree in reversed(worktrees):
                    run(["git", "-C", str(source), "worktree", "remove", "--force", str(worktree)])

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT / "data_mining" / "data" / "cases" / "test_matrix" / "home-assistant",
    )
    parser.add_argument("--case", action="append", help="Validate only the given case id. May be repeated.")
    parser.add_argument("--keep-worktrees", action="store_true")
    args = parser.parse_args()

    selected = set(args.case or [])
    task_dirs = sorted(
        path
        for path in args.root.iterdir()
        if path.is_dir() and (path / "task.yaml").exists() and (not selected or path.name in selected)
    )
    if selected and len(task_dirs) != len(selected):
        found = {path.name for path in task_dirs}
        missing = sorted(selected - found)
        raise SystemExit(f"selected cases not found: {missing}")

    all_errors: list[str] = []
    for task_dir in task_dirs:
        all_errors.extend(validate_task_dir(task_dir, args.keep_worktrees))

    if all_errors:
        print("\nErrors:")
        for error in all_errors:
            print(f"- {error}")
        return 1
    print("\nAll construction-stage task artifacts passed structural and patch-apply validation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
