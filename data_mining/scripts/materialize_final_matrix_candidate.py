#!/usr/bin/env python3
"""Materialize a final manually reviewed case into matrix-staging artifacts.

This script converts one row from the 646-case matrix ledger into a runnable
task skeleton. It deliberately does not invent test commands or environments:
hidden patches come only from upstream test-file diffs, while
environment/test_profiles.yaml records profiles as needing manual completion.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required: python3 -m pip install pyyaml") from exc


ROOT = Path(__file__).resolve().parents[2]
AUDIT_DIR = ROOT / "data_mining" / "data" / "cases" / "manual_audit_since20250601"
LEDGER = AUDIT_DIR / "matrix_audit" / "matrix_candidate_ledger.jsonl"
MINING_ROOT = ROOT / "data_mining" / "data" / "cases" / "pr_mining_all_repos_since2024"
DEFAULT_STAGING_ROOT = ROOT / "data_mining" / "data" / "cases" / "final_matrix_staging"
PR_BODY_CACHE = AUDIT_DIR / "pr_body_cache"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def run(cmd: list[str], *, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        rendered = " ".join(cmd)
        raise SystemExit(f"command failed: {rendered}\n{proc.stderr.strip()}")
    return proc


def slug(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


def repo_short_name(full_name: str) -> str:
    return full_name.rsplit("/", 1)[-1]


def local_repo_path(full_name: str) -> Path:
    owner, name = full_name.split("/", 1)
    return ROOT / "repos" / owner / name


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)) if path.is_absolute() and path.is_relative_to(ROOT) else str(path)


def pr_key(repo: str, number: int | str) -> str:
    return f"{repo}#{number}"


def parse_pr_id(pr_id: str) -> tuple[str, str]:
    repo, number = str(pr_id).rsplit("#", 1)
    return repo, number


def included_pr_set(row: dict[str, Any]) -> set[str]:
    return set(str(item) for item in row.get("pr_ids") or [])


def excluded_pr_set(row: dict[str, Any]) -> set[str]:
    return set(str(item) for item in row.get("excluded_pr_ids") or [])


def candidate_pr_set(candidate: dict[str, Any]) -> set[str]:
    result = set()
    for repo, item in (candidate.get("involved_prs") or {}).items():
        number = item.get("pr_number")
        if number is not None:
            result.add(pr_key(repo, number))
    return result


def prompt_source_prs(row: dict[str, Any]) -> set[str]:
    source = ((row.get("prompt_artifacts") or {}).get("prompt_source_originals")) or ""
    if not source:
        return set()
    path = ROOT / source
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r"^##\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)\s*$", text, flags=re.MULTILINE))


def prompt_source_pr_keys(row: dict[str, Any]) -> set[str]:
    return {pr_key(repo, number) for repo, number in prompt_source_prs(row)}


def select_ledger_row(candidate_id: str | None, audit_id: str | None, include_started: bool) -> dict[str, Any]:
    rows = read_jsonl(LEDGER)
    for row in rows:
        if candidate_id and row.get("candidate_id") == candidate_id:
            return row
        if audit_id and row.get("audit_id") == audit_id:
            return row
    if candidate_id or audit_id:
        raise SystemExit("requested candidate was not found in matrix ledger")
    for row in rows:
        if row.get("final_decision") == "reject" or row.get("final_category") in {
            "duplicate_queue_row_of_included_case",
            "duplicate_subset_or_superseded",
            "prompt_stage_duplicate_subset_or_not_publishable",
        }:
            continue
        status = (row.get("matrix") or {}).get("status")
        if status == "not_started" or (include_started and status):
            return row
    raise SystemExit("no eligible matrix ledger row found")


def find_mining_candidate(row: dict[str, Any]) -> dict[str, Any]:
    ecosystem = str(row["ecosystem"])
    path = MINING_ROOT / ecosystem / "candidates.jsonl"
    target = included_pr_set(row)
    candidates = read_jsonl(path)
    for candidate in candidates:
        if candidate_pr_set(candidate) == target:
            return candidate
    source_keys = prompt_source_pr_keys(row)
    for candidate in candidates:
        current = candidate_pr_set(candidate)
        missing = target - current
        if current and current < target and missing <= source_keys:
            return candidate
        extra = current - target
        if target and target < current and extra <= source_keys:
            return candidate
    cached_keys = set()
    for pr_id in target:
        repo, number = parse_pr_id(pr_id)
        if cached_pr_record(repo, number):
            cached_keys.add(pr_id)
    manually_available = source_keys | cached_keys
    replaceable_extras = source_keys | excluded_pr_set(row)
    for candidate in candidates:
        current = candidate_pr_set(candidate)
        if not (current & target):
            continue
        missing = target - current
        extra = current - target
        if missing and missing <= manually_available and extra <= replaceable_extras:
            return candidate
    raise SystemExit(f"could not find exact mining candidate for {row['candidate_id']} in {path}")


def cached_pr_record(repo: str, number: str) -> dict[str, Any] | None:
    owner, name = repo.split("/", 1)
    path = PR_BODY_CACHE / f"{owner}__{name}__{number}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "url": data.get("html_url"),
        "pr_number": int(number),
        "role": "manual_closure",
        "metadata": {
            "base_sha": data.get("base_sha"),
            "closed_at": data.get("closed_at"),
            "created_at": data.get("created_at"),
            "head_sha": data.get("head_sha"),
            "html_url": data.get("html_url"),
            "merge_commit_sha": data.get("merge_commit_sha"),
            "merged_at": data.get("merged_at"),
            "state": data.get("state"),
            "title": data.get("title"),
            "updated_at": data.get("updated_at"),
        },
    }


def candidate_item_by_pr(candidate: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for repo, item in (candidate.get("involved_prs") or {}).items():
        number = item.get("pr_number")
        if number is not None:
            result[pr_key(repo, number)] = item
    return result


def item_sort_key(item: dict[str, Any]) -> tuple[str, int]:
    metadata = item.get("metadata") or {}
    return (str(metadata.get("merged_at") or metadata.get("updated_at") or ""), int(item.get("pr_number") or 0))


def collect_involved_pr_items(row: dict[str, Any], candidate: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Return one item per structured ledger PR, including same-repo supplemental PRs.

    The mining candidate stores involved PRs as repo -> PR, so it cannot represent
    same-repo closure/test PRs added during manual audit. The matrix ledger's
    pr_ids are the authoritative boundary; cached PR metadata is used for any PR
    that is not present in the original mining candidate.
    """

    by_pr = candidate_item_by_pr(candidate)
    collected: list[tuple[str, dict[str, Any]]] = []
    missing: list[str] = []
    for pr_id in row.get("pr_ids") or []:
        repo, number = parse_pr_id(pr_id)
        item = by_pr.get(pr_id)
        if item is None:
            item = cached_pr_record(repo, number)
        if item is None:
            missing.append(pr_id)
            continue
        item = json.loads(json.dumps(item))
        item["pr_number"] = int(number)
        collected.append((repo, item))
    if missing:
        raise SystemExit("manual supplemental PR metadata is missing from cache: " + ", ".join(missing))
    collected.sort(key=lambda pair: (pair[0], item_sort_key(pair[1])))
    return collected


def supplement_candidate_from_prompt_sources(row: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(candidate))
    involved = result.setdefault("involved_prs", {})
    for repo, number in prompt_source_prs(row):
        key = pr_key(repo, number)
        if key not in included_pr_set(row) or repo in involved:
            continue
        record = cached_pr_record(repo, number)
        if not record:
            raise SystemExit(f"manual supplemental PR is missing cached metadata: {key}")
        involved[repo] = record
    return result


def is_test_path(path: str) -> bool:
    lower = path.lower()
    parts = lower.split("/")
    name = parts[-1]
    if parts and parts[0] in {"test", "tests"}:
        return True
    if any(part in {"tests", "__tests__"} for part in parts):
        return True
    if "browser-integration-tests" in parts:
        return True
    if any("test" in part for part in parts) and name in {
        "test.ts",
        "test.tsx",
        "test.js",
        "test.jsx",
        "spec.ts",
        "spec.tsx",
        "spec.js",
        "spec.jsx",
    }:
        return True
    if "/src/test/" in lower or "/src/androidtest/" in lower or "/src/unittest/" in lower:
        return True
    if any(part in {"spec", "specs"} for part in parts) and (
        re.search(r"(\.|-|_)(test|spec)\.(js|jsx|ts|tsx|py|rb|php|cs|java|kt|rs|dart)$", lower)
        or name.startswith("test_")
        or name.endswith(("_test.go", "_test.rs", "_test.dart", "_spec.rb", "_spec.py", "_spec.php"))
    ):
        return True
    if any(part.endswith((".test", ".tests")) for part in parts):
        return True
    if name in {"test.rs", "tests.rs"}:
        return True
    if name.startswith("test_") or name.endswith("_test.go") or name.endswith("_test.rs") or name.endswith("_test.dart"):
        return True
    if name.endswith(
        (
            "test.cs",
            "tests.cs",
            "test.java",
            "tests.java",
            "test.kt",
            "tests.kt",
            "spec.java",
            "spec.kt",
        )
    ):
        return True
    if re.search(r"(\.|-|_)(test|spec)\.(js|jsx|ts|tsx|py|rb|php|cs|java|kt|rs)$", lower):
        return True
    if lower.endswith((".snap", ".snapshots")):
        return True
    return False


def ensure_repo(full_name: str) -> Path:
    repo = local_repo_path(full_name)
    if (repo / ".git").exists():
        return repo
    repo.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", "--filter=blob:none", f"https://github.com/{full_name}.git", str(repo)])
    return repo


def commit_exists(repo: Path, sha: str) -> bool:
    env = os.environ.copy()
    env["GIT_NO_LAZY_FETCH"] = "1"
    proc = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    return proc.returncode == 0


def ensure_commit(repo: Path, sha: str) -> None:
    if commit_exists(repo, sha):
        return
    run(["git", "-c", "http.version=HTTP/1.1", "-C", str(repo), "fetch", "--depth=1", "origin", sha])
    if not commit_exists(repo, sha):
        raise SystemExit(f"commit still missing after fetch: {repo} {sha}")


def run_bytes(cmd: list[str], *, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        rendered = " ".join(cmd)
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise SystemExit(f"command failed: {rendered}\n{stderr}")
    return proc


def git_diff(repo: Path, base: str, gold: str, paths: list[str] | None = None) -> bytes:
    cmd = ["git", "-C", str(repo), "diff", "--binary", base, gold, "--"]
    if paths:
        cmd.extend(paths)
    return run_bytes(cmd).stdout


def append_patch_chunk(chunks: list[bytes], diff_bytes: bytes) -> None:
    """Append one complete git diff without stripping meaningful context lines."""

    if not diff_bytes.strip():
        return
    chunks.append(diff_bytes if diff_bytes.endswith(b"\n") else diff_bytes + b"\n")


def changed_paths(repo: Path, base: str, gold: str) -> list[str]:
    out = run(["git", "-C", str(repo), "diff", "--name-only", base, gold, "--"]).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def fetch_pr_changed_paths(full_name: str, pr_number: int | str) -> list[str]:
    """Return GitHub's exact PR file list.

    Some upstream branch heads contain commits or merge-side changes that are not
    part of the reviewed PR file list. The task patches must follow GitHub PR
    files, otherwise hidden tests can be polluted by unrelated branch changes.
    """

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ecosyncbench-case-materializer",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    paths: list[str] = []
    page = 1
    owner_repo = urllib.parse.quote(full_name, safe="/")
    while True:
        url = f"https://api.github.com/repos/{owner_repo}/pulls/{pr_number}/files?per_page=100&page={page}"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception:
            return []
        if not data:
            return paths
        for item in data:
            filename = item.get("filename")
            if filename:
                paths.append(filename)
        if len(data) < 100:
            return paths
        page += 1


def copy_prompt_artifacts(row: dict[str, Any], out: Path) -> None:
    artifacts = row["prompt_artifacts"]
    for source_key, target_name in [
        ("prompt", "prompt.md"),
        ("case_notes", "CASE_NOTES.zh.md"),
        ("same_demand_review", "prompt_same_demand.zh.md"),
        ("prompt_source_originals", "prompt_source_originals.md"),
    ]:
        source = ROOT / artifacts[source_key]
        if source.exists():
            shutil.copy2(source, out / target_name)
    prompts_out = out / "prompts"
    prompts_out.mkdir(parents=True, exist_ok=True)
    for prompt in artifacts.get("repo_prompts") or []:
        source = ROOT / prompt
        if source.exists():
            shutil.copy2(source, prompts_out / source.name)


def profile_name(repo_name: str) -> str:
    return f"{slug(repo_name)}-hidden"


def normalize_context_repos(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Return context-only repositories needed to run real upstream tests.

    Context repos are not part of the upstream PR bundle. They are checked out at
    the same commit for base and gold so framework/app tests can run in their
    normal repository layout without inventing or cropping tests.
    """

    result: list[dict[str, Any]] = []
    for item in row.get("context_repos") or []:
        if isinstance(item, str):
            item = {"repo": item}
        full_name = str(item.get("repo") or item.get("full_name") or "")
        commit = item.get("commit") or item.get("snapshot_commit") or item.get("base_commit")
        if not full_name or not commit:
            raise SystemExit(
                f"context_repos entries for {row.get('candidate_id')} must include repo and commit/snapshot_commit"
            )
        result.append(
            {
                "repo": full_name,
                "commit": str(commit),
                "role": str(item.get("role") or "context"),
                "included_in_task": bool(item.get("included_in_task", False)),
                "agent_editable": bool(item.get("agent_editable", True)),
                "reason": str(item.get("reason") or ""),
            }
        )
    return result


def materialize(row: dict[str, Any], candidate: dict[str, Any], out_root: Path, force: bool) -> Path:
    task_id = row["candidate_id"]
    ecosystem_slug = slug(str(row["ecosystem"]))
    out = out_root / ecosystem_slug / task_id
    if out.exists():
        if not force:
            raise SystemExit(f"output already exists; pass --force to overwrite: {out}")
        shutil.rmtree(out)
    out.mkdir(parents=True)

    copy_prompt_artifacts(row, out)

    repos_yaml = {"ecosystem": row["ecosystem"], "snapshot_date": datetime.now(timezone.utc).date().isoformat(), "repos": {}}
    task_repos = []
    source_prs = []
    hidden_patches = []
    profiles: dict[str, Any] = {}
    gold_meta = {"source": "upstream_pr_merge_commits", "patches": {}, "hidden_tests": {"policy": "upstream test-file diffs only"}}
    pr_records = []

    involved_items = collect_involved_pr_items(row, candidate)
    candidate_for_provenance = json.loads(json.dumps(candidate))
    candidate_for_provenance["manual_involved_prs"] = [
        {
            "repo": full_name,
            "pr_number": item.get("pr_number"),
            "metadata": item.get("metadata") or {},
        }
        for full_name, item in involved_items
    ]
    write_json(out / "provenance" / "mining_candidate.json", candidate_for_provenance)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for full_name, item in involved_items:
        grouped.setdefault(full_name, []).append(item)

    for full_name, items in grouped.items():
        items.sort(key=item_sort_key)
        first_metadata = items[0].get("metadata") or {}
        last_metadata = items[-1].get("metadata") or {}
        base = first_metadata.get("base_sha")
        patch_commit = last_metadata.get("head_sha") or last_metadata.get("merge_commit_sha")
        merge_commit = last_metadata.get("merge_commit_sha")
        if not base or not patch_commit:
            raise SystemExit(f"missing base or patch commit for {full_name}")
        short = repo_short_name(full_name)
        repo = ensure_repo(full_name)
        ensure_commit(repo, base)
        ensure_commit(repo, patch_commit)
        if merge_commit:
            ensure_commit(repo, merge_commit)
        local_path = rel(local_repo_path(full_name))

        gold_patch = f"gold/patches/{short}.patch"
        hidden_patch = f"hidden/patches/{short}-hidden-tests.patch"
        (out / gold_patch).parent.mkdir(parents=True, exist_ok=True)
        gold_chunks: list[bytes] = []
        hidden_chunks: list[bytes] = []
        test_paths: list[str] = []
        changed_paths_by_pr: list[dict[str, Any]] = []
        for item in items:
            metadata = item.get("metadata") or {}
            item_base = metadata.get("base_sha")
            item_patch = metadata.get("head_sha") or metadata.get("merge_commit_sha")
            if not item_base or not item_patch:
                raise SystemExit(f"missing base or patch commit for {full_name}#{item.get('pr_number')}")
            ensure_commit(repo, item_base)
            ensure_commit(repo, item_patch)
            item_merge = metadata.get("merge_commit_sha")
            if item_merge:
                ensure_commit(repo, item_merge)
            pr_paths = fetch_pr_changed_paths(full_name, item.get("pr_number"))
            file_paths = pr_paths or changed_paths(repo, item_base, item_patch)
            item_test_paths = [path for path in file_paths if is_test_path(path)]
            changed_paths_by_pr.append(
                {
                    "pr": item.get("pr_number"),
                    "changed_file_paths": file_paths,
                    "changed_file_source": "github_pr_files_api" if pr_paths else "local_base_to_patch_commit_diff",
                    "test_file_paths": item_test_paths,
                }
            )
            gold_file_paths = [path for path in file_paths if path not in set(item_test_paths)]
            diff_bytes = git_diff(repo, item_base, item_patch, gold_file_paths) if gold_file_paths else b""
            append_patch_chunk(gold_chunks, diff_bytes)
            if item_test_paths:
                test_paths.extend(item_test_paths)
                hidden_bytes = git_diff(repo, item_base, item_patch, item_test_paths)
                append_patch_chunk(hidden_chunks, hidden_bytes)
        (out / gold_patch).write_bytes(b"\n".join(gold_chunks))
        test_paths = list(dict.fromkeys(test_paths))
        if test_paths:
            (out / hidden_patch).parent.mkdir(parents=True, exist_ok=True)
            (out / hidden_patch).write_bytes(b"\n".join(hidden_chunks))

        task_repos.append(
            {
                "name": short,
                "path": local_path,
                "role": items[0].get("role", "involved_repo"),
                "base_commit": base,
                "gold_commit": patch_commit,
                "merge_commit": merge_commit or patch_commit,
                "gold_patch": gold_patch,
            }
        )
        repos_yaml["repos"][short] = {
            "url": f"https://github.com/{full_name}.git",
            "local_path": local_path,
            "role": items[0].get("role", "involved_repo"),
            "included_in_task": True,
            "agent_editable": True,
            "snapshot_commit": base,
            "base_commit": base,
            "gold_commit": patch_commit,
            "merge_commit": merge_commit or patch_commit,
            "upstream_pr": items[0].get("pr_number"),
        }
        repos_yaml["repos"][short]["upstream_prs"] = [item.get("pr_number") for item in items]
        for item in items:
            metadata = item.get("metadata") or {}
            source_prs.append(
                {
                    "repo": full_name,
                    "pr": item.get("pr_number"),
                    "title": metadata.get("title"),
                    "url": metadata.get("html_url") or item.get("url"),
                    "merged_at": metadata.get("merged_at"),
                }
            )
        gold_meta["patches"][short] = {
            "base_commit": base,
            "gold_commit": patch_commit,
            "merge_commit": merge_commit,
            "patch_source": "concatenated_upstream_pr_diffs",
            "upstream_prs": [item.get("pr_number") for item in items],
        }
        for item, path_record in zip(items, changed_paths_by_pr):
            metadata = item.get("metadata") or {}
            item_patch = metadata.get("head_sha") or metadata.get("merge_commit_sha")
            pr_records.append(
                {
                    "repo": full_name,
                    "pr": item.get("pr_number"),
                    "html_url": metadata.get("html_url") or item.get("url"),
                    "title": metadata.get("title"),
                    "merged": True,
                    "merged_at": metadata.get("merged_at"),
                    "merge_commit_sha": metadata.get("merge_commit_sha"),
                    "base_sha": metadata.get("base_sha"),
                    "head_sha": metadata.get("head_sha"),
                    "patch_commit_sha": item_patch,
                    "changed_files_sample": item.get("changed_files_sample") or [],
                    **path_record,
                }
            )
        if test_paths:
            p_name = profile_name(short)
            hidden_patches.append(
                {
                    "repo": short,
                    "patch": hidden_patch,
                    "test_profile": p_name,
                    "origin": "upstream_test_diff",
                    "source_file": test_paths,
                    "hidden_file": test_paths,
                    "semantic_changes": "none",
                    "source": (
                        f"upstream test changes from {full_name} PR(s) "
                        + ", ".join(str(item.get("pr_number")) for item in items)
                    ),
                }
            )
            profiles[p_name] = {
                "repo": short,
                "workdir": local_path,
                "command": "TODO: fill an exact command that runs the upstream test patch's full test file/module",
                "status": "needs_manual_profile",
                "test_files": test_paths,
            }

    for context in normalize_context_repos(row):
        full_name = context["repo"]
        short = repo_short_name(full_name)
        if short in repos_yaml["repos"]:
            raise SystemExit(f"context repo short name collides with involved repo: {short}")
        commit = context["commit"]
        repo = ensure_repo(full_name)
        ensure_commit(repo, commit)
        local_path = rel(local_repo_path(full_name))
        task_repos.append(
            {
                "name": short,
                "path": local_path,
                "role": context["role"],
                "base_commit": commit,
                "gold_commit": commit,
                "merge_commit": commit,
                "gold_patch": None,
            }
        )
        repos_yaml["repos"][short] = {
            "url": f"https://github.com/{full_name}.git",
            "local_path": local_path,
            "role": context["role"],
            "included_in_task": context["included_in_task"],
            "agent_editable": context["agent_editable"],
            "snapshot_commit": commit,
            "base_commit": commit,
            "gold_commit": commit,
            "merge_commit": commit,
            "historical_snapshot_available": True,
            "context_only": True,
            "context_reason": context["reason"],
        }

    write_yaml(
        out / "task.yaml",
        {
            "id": task_id,
            "benchmark": "WIDESWE",
            "status": "candidate_pending_environment",
            "ecosystem": row["ecosystem"],
            "task_type": row.get("retain_category") or "cross_repo_change",
            "repositories": task_repos,
            "source_prs": source_prs,
            "source_unit": "pr_bundle",
            "source_provenance": {
                "construction_method": "upstream_pr_merge_commits",
                "data_collection_basis": "manual_audit_since20250601 + pr_mining_all_repos_since2024",
                "manual_audit_id": row["audit_id"],
            },
            "evaluation": {
                "harness": "harness.yaml",
                "gold": "gold/patches/",
                "hidden_tests": "hidden/patches/",
                "environment": "environment/",
            },
        },
    )
    write_yaml(out / "repos.yaml", repos_yaml)
    write_yaml(out / "gold" / "metadata.yaml", gold_meta)
    write_yaml(
        out / "harness.yaml",
        {
            "task_id": task_id,
            "hidden_test_patches": hidden_patches,
            "profile_sets": {
                "linux-docker": {
                    "description": "Candidate profiles generated from upstream test-file diffs; commands require manual completion.",
                    "hidden_tests": [item["test_profile"] for item in hidden_patches],
                    "cross_repo_contract": [],
                }
            },
            "matrix_expectations": {"base_hidden": "fail", "gold_hidden": "pass"},
        },
    )
    write_yaml(out / "environment" / "test_profiles.yaml", {"profiles": profiles})
    runner = """#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
echo "Profile ${profile} has not been manually implemented for this staged candidate." >&2
exit 2
"""
    runner_path = out / "environment" / "run_profile.sh"
    runner_path.parent.mkdir(parents=True, exist_ok=True)
    runner_path.write_text(runner, encoding="utf-8")
    runner_path.chmod(0o755)
    write_yaml(
        out / "validation.yaml",
        {
            "status": "candidate_pending_environment",
            "matrix": {
                "linux-docker": {
                    "status": "not_run",
                    "oracle_totals": {
                        "fail_to_pass": None,
                        "pass_to_pass": None,
                        "fail_to_fail": None,
                        "pass_to_fail": None,
                        "missing_in_base": None,
                        "missing_in_gold": None,
                        "skipped": None,
                    },
                }
            },
        },
    )
    write_json(
        out / "provenance" / "pr_records.json",
        {
            "schema_version": "ecosyncbench.pr_records.v1",
            "source": "manual_audit_since20250601",
            "task_id": task_id,
            "record_count": len(pr_records),
            "records": pr_records,
        },
    )
    readme = [
        f"# {task_id}",
        "",
        "This is a matrix-staging skeleton generated from manual audit metadata.",
        "Test profile commands and dependency images still require manual completion before matrix execution.",
        "",
        "## Source PRs",
        "",
    ]
    for pr in source_prs:
        readme.append(f"- `{pr['repo']}#{pr['pr']}`: {pr.get('title') or ''}")
    readme.extend(["", "## Hidden Test Files", ""])
    for record in pr_records:
        files = record.get("test_file_paths") or []
        readme.append(f"- `{record['repo']}`: " + (", ".join(f"`{path}`" for path in files) if files else "<none detected>"))
    (out / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-id")
    parser.add_argument("--audit-id")
    parser.add_argument("--out-root", type=Path, default=DEFAULT_STAGING_ROOT)
    parser.add_argument("--include-started", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    row = select_ledger_row(args.candidate_id, args.audit_id, args.include_started)
    candidate = find_mining_candidate(row)
    candidate = supplement_candidate_from_prompt_sources(row, candidate)
    out = materialize(row, candidate, args.out_root.resolve(), args.force)
    print(json.dumps({"candidate_id": row["candidate_id"], "out": rel(out)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
