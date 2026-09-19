#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import signal
import shlex
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required: python3 -m pip install pyyaml") from exc


REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = Path(os.environ.get("ECOSYNC_TASKS_DIR", REPO_ROOT / "benchmark" / "tasks")).resolve()
SOURCE_REPOS_ROOT = Path(os.environ.get("ECOSYNC_SOURCE_REPOS_ROOT", REPO_ROOT / "repos")).resolve()
DEFAULT_TASK_ID = None
WORKSPACE_SCOPES = ("ecosystem", "involved", "single-repo")
AGENT_DIFF_EXCLUDED_PATHS = (
    "node_modules",
    "vendor",
    ".venv",
    "venv",
    ".gradle",
    ".pnpm-store",
    ".yarn",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
)

TMP_MKDTEMP_SEGMENT_RE = re.compile(r"(/[^/'\"\\\s\]]+-)[A-Za-z0-9]{6}(?=/|\b)")
DOCKER_LIFECYCLE_LOCK = Path(os.environ.get("ECOSYNC_DOCKER_LIFECYCLE_LOCK", "/run/lock/ecosyncbench-docker.lock"))
ISOLATED_GIT_CACHE_VERSION = "v1"
ISOLATED_GIT_BRANCH = "ecosync-base"
MANAGED_EVALUATOR_CONTAINER_PREFIX = "ecosync-evaluator-"
LEGACY_PROFILE_CONTAINER_PREFIX = "ecosync_"
_STALE_CONTAINER_CLEANUP_DONE = False


@contextmanager
def docker_lifecycle_lock():
    """Serialize profile container lifecycle against the external Docker root."""
    DOCKER_LIFECYCLE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with DOCKER_LIFECYCLE_LOCK.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def cleanup_stale_profile_containers(
    log_file: Path | None = None,
    *,
    total_timeout_sec: int = 120,
    operation_timeout_sec: int = 10,
    include_legacy: bool = False,
    include_running: bool = False,
) -> dict[str, Any]:
    """Remove stale evaluator/profile containers without an unbounded Docker call."""
    started = time.monotonic()
    result: dict[str, Any] = {
        "found": 0,
        "removed": [],
        "skipped_running": [],
        "skipped_nonterminal": [],
        "failed": [],
        "timed_out": [],
        "deferred": 0,
        "list_error": None,
    }
    try:
        listed = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.ID}}\t{{.Names}}\t{{.State}}\t{{.Labels}}"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=min(15, total_timeout_sec),
            check=False,
        )
    except subprocess.TimeoutExpired:
        result["list_error"] = "docker ps timed out"
        return result
    if listed.returncode != 0:
        result["list_error"] = listed.stderr.strip() or f"docker ps exited {listed.returncode}"
        return result

    prefixes = (MANAGED_EVALUATOR_CONTAINER_PREFIX,)
    if include_legacy:
        prefixes += (LEGACY_PROFILE_CONTAINER_PREFIX,)
    containers = []
    for line in listed.stdout.splitlines():
        container_id, separator, remainder = line.partition("\t")
        name, second_separator, remainder = remainder.partition("\t")
        state, _third_separator, labels = remainder.partition("\t")
        managed_evaluator = "ecosyncbench.owner=evaluator" in labels.split(",")
        if not separator or not second_separator or (not name.startswith(prefixes) and not managed_evaluator):
            continue
        if state == "running" and not include_running:
            result["skipped_running"].append({"id": container_id, "name": name, "state": state})
            continue
        if state not in {"running", "exited", "dead"}:
            # A concurrently starting evaluator is briefly visible as "created".
            # Removing nonterminal containers here races with docker start.
            result["skipped_nonterminal"].append({"id": container_id, "name": name, "state": state})
            continue
        containers.append((container_id, name, state))
    containers.sort(key=lambda item: (item[2], item[1]))
    result["found"] = len(containers) + len(result["skipped_running"]) + len(result["skipped_nonterminal"])

    for index, (container_id, name, state) in enumerate(containers):
        remaining = total_timeout_sec - (time.monotonic() - started)
        if remaining <= 0:
            result["deferred"] = len(containers) - index
            break
        timeout = max(1, min(operation_timeout_sec, int(remaining)))
        try:
            removed = subprocess.run(
                ["docker", "rm", "-f", container_id],
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            result["timed_out"].append({"id": container_id, "name": name, "state": state, "reason": "remove timeout"})
            result["deferred"] = len(containers) - index - 1
            break
        if removed.returncode == 0:
            result["removed"].append({"id": container_id, "name": name, "state": state})
        else:
            result["failed"].append(
                {
                    "id": container_id,
                    "name": name,
                    "state": state,
                    "error": removed.stderr.strip() or f"docker rm exited {removed.returncode}",
                }
            )

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write("[ecosync] stale container cleanup: " + json.dumps(result, ensure_ascii=False) + "\n")
    return result


def docker_chown_tree(path: Path) -> bool:
    uid = os.getuid()
    gid = os.getgid()
    candidate_images = [
        "alpine:latest",
        "busybox:latest",
        "ecosyncbench/base/dotnet-sdk:8.0.100",
        "ecosyncbench/base/node:22-bookworm-slim",
        "ecosyncbench/base/android-sdk:36",
        "ecosyncbench/base/python:3.14-slim",
        "ecosyncbench/deps/home-assistant-core-py:beaea2d99806",
        "ecosyncbench/deps/home-assistant-supervisor-py:2c6253e4b664",
    ]
    for image in candidate_images:
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
                f"{path.resolve()}:/target",
                "--entrypoint",
                "sh",
                image,
                "-c",
                f"chown -R {uid}:{gid} /target",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if proc.returncode == 0:
            return True
    return False


def remove_tree(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
        return
    try:
        shutil.rmtree(path)
    except PermissionError:
        if not docker_chown_tree(path):
            raise
        shutil.rmtree(path)


def run(
    cmd: list[str],
    *,
    cwd: Path = REPO_ROOT,
    env: dict[str, str] | None = None,
    check: bool = True,
    umask: int = -1,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, env=env, text=True, check=check, umask=umask)


def capture(cmd: list[str], *, cwd: Path = REPO_ROOT, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def capture_structured_output(
    cmd: list[str], *, cwd: Path = REPO_ROOT, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Capture machine-readable stdout without mixing in diagnostic stderr."""
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def capture_binary_output(
    cmd: list[str], *, cwd: Path = REPO_ROOT, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    """Capture byte-sensitive output without newline or encoding conversion."""
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def task_dir(task_id: str) -> Path:
    path = TASKS_DIR / task_id
    if not path.is_dir():
        raise SystemExit(f"unknown task: {task_id}")
    return path


def load_task(task_id: str) -> dict[str, Any]:
    return load_yaml(task_dir(task_id) / "task.yaml")


def load_repos(task_id: str) -> dict[str, Any]:
    return load_yaml(task_dir(task_id) / "repos.yaml")


def load_harness(task_id: str) -> dict[str, Any]:
    return load_yaml(task_dir(task_id) / "harness.yaml")


def task_included_repo_names(task_id: str) -> set[str]:
    task = load_task(task_id)
    return {str(repo["name"]) for repo in task.get("repositories", [])}


def validate_workspace_scope(task_id: str, workspace_scope: str, single_repo: str | None) -> None:
    if workspace_scope not in WORKSPACE_SCOPES:
        raise SystemExit(f"unsupported workspace scope: {workspace_scope}")
    if workspace_scope == "single-repo":
        if not single_repo:
            raise SystemExit("--single-repo is required when --workspace-scope single-repo")
        if single_repo not in task_included_repo_names(task_id):
            raise SystemExit(f"--single-repo must be an included task repo: {single_repo}")
    elif single_repo:
        raise SystemExit("--single-repo is only valid with --workspace-scope single-repo")


def prompt_path_for_scope(task_id: str, workspace_scope: str, single_repo: str | None) -> Path:
    validate_workspace_scope(task_id, workspace_scope, single_repo)
    if workspace_scope == "single-repo":
        path = task_dir(task_id) / "prompts" / f"{single_repo}.md"
        if path.exists():
            return path
        fallback = task_dir(task_id) / "prompt.md"
        if fallback.exists():
            return fallback
        raise SystemExit(
            f"missing single-repo prompt for {task_id}/{single_repo}: {path}; "
            f"also missing fallback ecosystem prompt: {fallback}"
        )

    path = task_dir(task_id) / "prompt.md"
    if not path.exists():
        raise SystemExit(f"missing ecosystem prompt for {task_id}: {path}")
    return path


def source_repo_path(local_path: str) -> Path:
    path = Path(local_path)
    try:
        relative = path.relative_to("repos")
    except ValueError:
        relative = path
    return SOURCE_REPOS_ROOT / relative


def repo_entries(
    task_id: str,
    mode: str,
    workspace_scope: str = "ecosystem",
    single_repo: str | None = None,
    repo_modes: dict[str, str] | None = None,
    repo_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    validate_workspace_scope(task_id, workspace_scope, single_repo)
    repos_data = load_repos(task_id)
    entries = []
    included_names = task_included_repo_names(task_id)
    for name, spec in repos_data["repos"].items():
        if repo_names is not None and name not in repo_names:
            continue
        included = bool(spec.get("included_in_task", name in included_names))
        if workspace_scope == "involved" and not included:
            continue
        if workspace_scope == "single-repo" and name != single_repo:
            continue
        selected_mode = (repo_modes or {}).get(name, mode)
        commit = spec.get("snapshot_commit")
        if selected_mode == "base":
            commit = spec.get("base_commit", commit)
        elif selected_mode == "gold":
            commit = spec.get("gold_commit", spec.get("base_commit", commit))
        else:
            raise SystemExit(f"unsupported prepare mode for {name}: {selected_mode}")
        if not commit and spec.get("historical_snapshot_available") is False:
            continue
        if not commit:
            raise SystemExit(f"missing commit for repo {name} in mode {selected_mode}")
        entries.append(
            {
                "name": name,
                "url": spec["url"],
                "source_path": source_repo_path(spec["local_path"]),
                "dest_path": Path(spec["local_path"]),
                "commit": commit,
                "mode": selected_mode,
                "agent_editable": bool(spec.get("agent_editable", included)),
                "included_in_task": included,
                "role": spec.get("role"),
            }
        )
    return entries


def workspace_manifest(workspace: Path) -> dict[str, Any] | None:
    manifest_path = workspace.resolve() / ".ecosyncbench" / "manifest.json"
    if not manifest_path.exists():
        return None
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def workspace_scope_from_run(run_dir: Path) -> tuple[str, str | None]:
    run_file = run_dir / "run.json"
    if not run_file.exists():
        return "ecosystem", None
    run_data = json.loads(run_file.read_text(encoding="utf-8"))
    return str(run_data.get("workspace_scope") or "ecosystem"), run_data.get("single_repo")


def evaluator_workspace_plan(
    task_id: str,
    workspace_scope: str,
    single_repo: str | None,
) -> tuple[str, str | None, dict[str, str] | None]:
    if workspace_scope != "single-repo":
        return workspace_scope, single_repo, None

    included = task_included_repo_names(task_id)
    repo_modes = {
        name: ("base" if name == single_repo else "gold")
        for name in included
    }
    return "involved", None, repo_modes


def evaluator_repo_names(
    task_id: str,
    workspace_scope: str,
    diff_summary: dict[str, Any],
) -> set[str] | None:
    if workspace_scope != "ecosystem":
        return None
    selected = set(task_included_repo_names(task_id))
    selected.update(
        str(repo.get("repo") or repo.get("name"))
        for repo in diff_summary.get("repos") or []
        if repo.get("changed") and (repo.get("repo") or repo.get("name"))
    )
    return selected


def agent_workspace_from_run(run_dir: Path) -> Path:
    run_file = run_dir / "run.json"
    if not run_file.exists():
        return run_dir / "agent_workspace"
    run_data = json.loads(run_file.read_text(encoding="utf-8"))
    workspace = run_data.get("agent_workspace")
    return Path(workspace).resolve() if workspace else run_dir / "agent_workspace"


def evaluator_workspace_from_run(run_dir: Path) -> Path:
    run_file = run_dir / "run.json"
    if not run_file.exists():
        return run_dir / "evaluator_workspace"
    run_data = json.loads(run_file.read_text(encoding="utf-8"))
    workspace = run_data.get("evaluator_workspace")
    return Path(workspace).resolve() if workspace else run_dir / "evaluator_workspace"


def manifest_repo_entries(
    task_id: str,
    workspace: Path,
    workspace_scope: str = "ecosystem",
    single_repo: str | None = None,
) -> list[dict[str, Any]]:
    manifest = workspace_manifest(workspace)
    if manifest is None:
        return repo_entries(task_id, "base", workspace_scope, single_repo)

    manifest_repos = manifest.get("repos") or []
    has_private_metadata = all(
        "agent_editable" in repo and "included_in_task" in repo
        for repo in manifest_repos
    )
    if not has_private_metadata:
        # A killed agent process can leave the intentionally redacted public
        # manifest in place. Recover policy metadata from the task definition
        # while retaining exactly the repositories visible in the workspace.
        authoritative = {
            entry["name"]: entry
            for entry in repo_entries(task_id, "base", workspace_scope, single_repo)
        }
        recovered = []
        for repo in manifest_repos:
            entry = authoritative.get(repo["name"])
            if entry is None:
                continue
            recovered.append(entry)
        return recovered

    entries = []
    for repo in manifest_repos:
        entries.append(
            {
                "name": repo["name"],
                "dest_path": Path(repo["path"]),
                "commit": repo.get("commit"),
                "agent_editable": bool(repo.get("agent_editable", False)),
                "included_in_task": bool(repo.get("included_in_task", False)),
                "role": repo.get("role"),
            }
        )
    return entries


def visible_repo_names(workspace: Path) -> set[str]:
    manifest = workspace_manifest(workspace)
    if manifest is None:
        return set()
    return {str(repo["name"]) for repo in manifest.get("repos") or []}


def ensure_clean_workspace(workspace: Path, force: bool) -> None:
    if workspace.exists():
        if not force:
            raise SystemExit(f"workspace already exists: {workspace}; pass --force to replace it")
        remove_tree(workspace)
    workspace.mkdir(parents=True)


def filesystem_mount_point(path: Path) -> Path:
    candidate = path.resolve()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    device = candidate.stat().st_dev
    while candidate != candidate.parent and candidate.parent.stat().st_dev == device:
        candidate = candidate.parent
    return candidate


def isolated_git_cache_root(workspace: Path) -> Path:
    configured = os.environ.get("ECOSYNC_ISOLATED_GIT_CACHE")
    if configured:
        return Path(configured).expanduser().resolve()
    for ancestor in (workspace.resolve(), *workspace.resolve().parents):
        if ancestor.name == "experiment_results" and os.access(ancestor, os.W_OK):
            return ancestor / ".ecosyncbench" / "isolated-git" / ISOLATED_GIT_CACHE_VERSION
    mount_point = filesystem_mount_point(workspace)
    if mount_point != Path("/") and os.access(mount_point, os.W_OK):
        return mount_point / ".ecosyncbench" / "isolated-git" / ISOLATED_GIT_CACHE_VERSION
    return REPO_ROOT / ".cache" / "ecosyncbench" / "isolated-git" / ISOLATED_GIT_CACHE_VERSION


def source_git_object_directories(source_path: Path) -> list[Path]:
    primary = subprocess.run(
        ["git", "-C", str(source_path), "rev-parse", "--path-format=absolute", "--git-path", "objects"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    pending = [Path(primary).resolve()]
    result: list[Path] = []
    seen: set[Path] = set()
    while pending:
        objects = pending.pop(0)
        if objects in seen:
            continue
        seen.add(objects)
        result.append(objects)
        alternates = objects / "info" / "alternates"
        if not alternates.exists():
            continue
        for line in alternates.read_text(encoding="utf-8", errors="replace").splitlines():
            value = line.strip()
            if not value:
                continue
            alternate = Path(value)
            if not alternate.is_absolute():
                alternate = (objects / alternate).resolve()
            pending.append(alternate)
    return result


def hydrate_snapshot_objects(source_path: Path, commit: str) -> None:
    """Materialize blobs omitted by a partial clone before building an isolated snapshot."""
    missing_result = subprocess.run(
        ["git", "-C", str(source_path), "rev-list", "--objects", "--missing=print", f"{commit}^{{tree}}"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if missing_result.returncode != 0:
        raise RuntimeError(
            f"cannot enumerate snapshot objects for {source_path}@{commit}: {missing_result.stderr.strip()}"
        )
    missing = [line[1:] for line in missing_result.stdout.splitlines() if line.startswith("?")]
    if not missing:
        return

    promisor_result = subprocess.run(
        ["git", "-C", str(source_path), "config", "--get-regexp", r"^remote\..*\.promisor$"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    promisor_remotes = []
    for line in promisor_result.stdout.splitlines():
        key, separator, value = line.partition(" ")
        match = re.fullmatch(r"remote\.(.+)\.promisor", key)
        if separator and match and value.strip().lower() == "true":
            promisor_remotes.append(match.group(1))

    fetch_errors = []
    hydrated = False
    for remote in promisor_remotes:
        fetch = subprocess.run(
            [
                "git",
                "-C",
                str(source_path),
                "-c",
                "fetch.negotiationAlgorithm=noop",
                "fetch",
                remote,
                "--no-tags",
                "--no-write-fetch-head",
                "--recurse-submodules=no",
                "--filter=blob:none",
                "--stdin",
            ],
            input="".join(f"{oid}\n" for oid in missing),
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        if fetch.returncode == 0:
            hydrated = True
            break
        fetch_errors.append(f"{remote}: {fetch.stderr.strip()}")

    if not hydrated:
        detail = "; ".join(fetch_errors) or "no promisor remote is configured"
        raise RuntimeError(
            f"cannot batch-hydrate {len(missing)} snapshot objects for {source_path}@{commit}: {detail}"
        )

    verify_env = os.environ.copy()
    verify_env["GIT_NO_LAZY_FETCH"] = "1"
    remaining_result = subprocess.run(
        ["git", "-C", str(source_path), "rev-list", "--objects", "--missing=print", f"{commit}^{{tree}}"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=verify_env,
        check=False,
    )
    remaining = [line for line in remaining_result.stdout.splitlines() if line.startswith("?")]
    if remaining_result.returncode != 0 or remaining:
        detail = remaining_result.stderr.strip() or f"{len(remaining)} objects remain missing"
        raise RuntimeError(f"incomplete snapshot hydration for {source_path}@{commit}: {detail}")


def isolated_git_cache_key(source_path: Path, commit: str) -> str:
    payload = f"{ISOLATED_GIT_CACHE_VERSION}\0{source_path.resolve()}\0{commit}\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_isolated_git_cache(entry: Path, expected_commit: str) -> dict[str, Any] | None:
    manifest_path = entry / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if manifest.get("format_version") != ISOLATED_GIT_CACHE_VERSION:
        return None
    if manifest.get("source_commit") != expected_commit:
        return None
    root_commit = str(manifest.get("isolated_commit") or "")
    if not re.fullmatch(r"[0-9a-f]{40,64}", root_commit):
        return None
    pack_dir = entry / "objects" / "pack"
    if not list(pack_dir.glob("*.pack")) or not list(pack_dir.glob("*.idx")):
        return None
    check = subprocess.run(
        ["git", f"--git-dir={entry}", "cat-file", "-e", f"{root_commit}^{{commit}}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return manifest if check.returncode == 0 else None


def build_isolated_git_cache(source_path: Path, commit: str, cache_root: Path) -> tuple[Path, dict[str, Any]]:
    key = isolated_git_cache_key(source_path, commit)
    entry = cache_root / key[:2] / key
    entry.parent.mkdir(parents=True, exist_ok=True)
    lock_path = cache_root / "locks" / f"{key}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        manifest = validate_isolated_git_cache(entry, commit)
        if manifest is not None:
            return entry, manifest
        if entry.exists():
            remove_tree(entry)

        temp_entry = Path(tempfile.mkdtemp(prefix=f".{key}.tmp-", dir=entry.parent))
        try:
            hydrate_snapshot_objects(source_path, commit)
            run(["git", "init", "--bare", "--quiet", str(temp_entry)])
            tree = subprocess.run(
                ["git", "-C", str(source_path), "rev-parse", f"{commit}^{{tree}}"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            ).stdout.strip()
            object_dirs = source_git_object_directories(source_path)
            git_env = os.environ.copy()
            git_env.update(
                {
                    "GIT_DIR": str(temp_entry),
                    "GIT_ALTERNATE_OBJECT_DIRECTORIES": os.pathsep.join(str(path) for path in object_dirs),
                    "GIT_AUTHOR_NAME": "WIDESWE",
                    "GIT_AUTHOR_EMAIL": "benchmark@local",
                    "GIT_COMMITTER_NAME": "WIDESWE",
                    "GIT_COMMITTER_EMAIL": "benchmark@local",
                    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
                    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
                }
            )
            root_commit = subprocess.run(
                ["git", "commit-tree", tree],
                input="WIDESWE isolated base snapshot\n",
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=git_env,
                check=True,
            ).stdout.strip()
            run(["git", f"--git-dir={temp_entry}", "update-ref", f"refs/heads/{ISOLATED_GIT_BRANCH}", root_commit])
            run(["git", f"--git-dir={temp_entry}", "symbolic-ref", "HEAD", f"refs/heads/{ISOLATED_GIT_BRANCH}"])
            pack_prefix = temp_entry / "objects" / "pack" / "pack"
            pack_result = subprocess.run(
                ["git", "pack-objects", "--revs", str(pack_prefix)],
                input=f"{root_commit}\n",
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=git_env,
                check=True,
            )
            fsck = subprocess.run(
                ["git", f"--git-dir={temp_entry}", "fsck", "--full", "--no-reflogs"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            if fsck.returncode != 0:
                raise RuntimeError(f"isolated Git cache fsck failed for {source_path}@{commit}: {fsck.stdout}")
            manifest = {
                "format_version": ISOLATED_GIT_CACHE_VERSION,
                "source_path": str(source_path.resolve()),
                "source_commit": commit,
                "source_tree": tree,
                "isolated_commit": root_commit,
                "pack_hash": pack_result.stdout.strip(),
                "created_at": int(time.time()),
            }
            (temp_entry / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            temp_entry.rename(entry)
        except Exception:
            if temp_entry.exists():
                remove_tree(temp_entry)
            raise
        return entry, manifest


def link_or_copy_file(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def prepare_isolated_git_repo(source_path: Path, commit: str, destination: Path, cache_root: Path) -> dict[str, Any]:
    cache_entry, cache_manifest = build_isolated_git_cache(source_path, commit, cache_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "init", "--quiet", f"--initial-branch={ISOLATED_GIT_BRANCH}", str(destination)])
    destination_pack_dir = destination / ".git" / "objects" / "pack"
    destination_pack_dir.mkdir(parents=True, exist_ok=True)
    for source in sorted((cache_entry / "objects" / "pack").iterdir()):
        if source.is_file() and source.suffix in {".pack", ".idx", ".rev", ".bitmap"}:
            link_or_copy_file(source, destination_pack_dir / source.name)
    root_commit = str(cache_manifest["isolated_commit"])
    run(["git", "-C", str(destination), "update-ref", f"refs/heads/{ISOLATED_GIT_BRANCH}", root_commit])
    run(["git", "-C", str(destination), "symbolic-ref", "HEAD", f"refs/heads/{ISOLATED_GIT_BRANCH}"])
    checkout_env = os.environ.copy()
    checkout_env.setdefault("GIT_LFS_SKIP_SMUDGE", "1")
    run(
        ["git", "-C", str(destination), "reset", "--hard", "--quiet", "HEAD"],
        env=checkout_env,
        umask=0o022,
    )
    run(["git", "-C", str(destination), "config", "gc.auto", "0"])
    run(["git", "-C", str(destination), "config", "core.untrackedCache", "true"])
    flutter_metadata = flutter_snapshot_metadata(source_path, commit)
    if flutter_metadata:
        run(
            [
                "git",
                "-C",
                str(destination),
                "config",
                "ecosync.flutterFrameworkTag",
                flutter_metadata["framework_tag"],
            ]
        )
        run(
            [
                "git",
                "-C",
                str(destination),
                "config",
                "ecosync.flutterEngineRevision",
                flutter_metadata["engine_revision"],
            ]
        )
    return {
        "cache_entry": str(cache_entry),
        "source_commit": commit,
        "source_tree": cache_manifest["source_tree"],
        "isolated_commit": root_commit,
        "flutter_metadata": flutter_metadata,
    }


def flutter_snapshot_metadata(source_path: Path, commit: str) -> dict[str, str] | None:
    """Preserve the minimum Git-derived metadata required by the Flutter tool.

    Agent workspaces intentionally contain a synthetic root commit with no
    history or tags. Flutter otherwise reports version 0.0.0-unknown and may
    request engine artifacts for the synthetic commit instead of the frozen
    source snapshot.
    """
    flutter_marker = subprocess.run(
        ["git", "-C", str(source_path), "cat-file", "-e", f"{commit}:bin/flutter"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    tools_marker = subprocess.run(
        [
            "git",
            "-C",
            str(source_path),
            "cat-file",
            "-e",
            f"{commit}:packages/flutter_tools/pubspec.yaml",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if flutter_marker.returncode != 0 or tools_marker.returncode != 0:
        return None

    framework_tag = subprocess.run(
        [
            "git",
            "-C",
            str(source_path),
            "describe",
            "--match",
            "*.*.*",
            "--abbrev=0",
            "--tags",
            commit,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    ).stdout.strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-\d+\.\d+\.pre)?", framework_tag):
        return None

    engine_revision = ""
    pinned_engine = subprocess.run(
        ["git", "-C", str(source_path), "show", f"{commit}:bin/internal/engine.version"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if pinned_engine.returncode == 0:
        engine_revision = pinned_engine.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", engine_revision):
        engine_revision = subprocess.run(
            [
                "git",
                "-C",
                str(source_path),
                "log",
                "-1",
                "--format=%H",
                commit,
                "--",
                "DEPS",
                "engine",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        ).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", engine_revision):
        return None
    return {
        "framework_tag": framework_tag,
        "engine_revision": engine_revision,
    }


def go_module_name(repo_path: Path) -> str | None:
    go_mod = repo_path / "go.mod"
    if not go_mod.exists():
        return None
    for line in go_mod.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"\s*module\s+(\S+)", line)
        if match:
            return match.group(1)
    return None


def relative_go_replacements(repo_path: Path) -> list[tuple[str, str]]:
    go_mod = repo_path / "go.mod"
    if not go_mod.exists():
        return []
    replacements = []
    for line in go_mod.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.split("//", 1)[0].strip()
        match = re.match(r"replace\s+(\S+)(?:\s+\S+)?\s+=>\s+(\.[^\s]+)$", line)
        if match:
            replacements.append((match.group(1), match.group(2)))
    return replacements


def add_git_info_exclude(repo_path: Path, relative_path: Path) -> None:
    exclude = repo_path / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    pattern = "/" + relative_path.as_posix().lstrip("/")
    existing = exclude.read_text(encoding="utf-8", errors="replace").splitlines() if exclude.exists() else []
    if pattern not in existing:
        with exclude.open("a", encoding="utf-8") as handle:
            if existing and existing[-1] != "":
                handle.write("\n")
            handle.write(pattern + "\n")


def ignore_gitlink_worktree(repo_path: Path, relative_path: Path) -> None:
    submodule_name = relative_path.as_posix()
    gitmodules = repo_path / ".gitmodules"
    if gitmodules.exists():
        result = subprocess.run(
            ["git", "-C", str(repo_path), "config", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        for line in result.stdout.splitlines():
            key, separator, value = line.partition(" ")
            if not separator or value.strip() != relative_path.as_posix():
                continue
            match = re.fullmatch(r"submodule\.(.+)\.path", key)
            if match:
                submodule_name = match.group(1)
                break
    run(["git", "-C", str(repo_path), "config", f"submodule.{submodule_name}.ignore", "all"])


def gitlink_checkout_commit(repo_path: Path, relative_path: Path) -> str | None:
    destination = repo_path / relative_path
    if not destination.is_dir() or any(destination.iterdir()):
        return None
    result = subprocess.run(
        ["git", "-C", str(repo_path), "ls-files", "--stage", "--", relative_path.as_posix()],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        metadata, separator, path = line.partition("\t")
        fields = metadata.split()
        if separator and path == relative_path.as_posix() and len(fields) >= 2 and fields[0] == "160000":
            return fields[1]
    return None


def empty_gitlink_checkout(repo_path: Path, relative_path: Path) -> bool:
    return gitlink_checkout_commit(repo_path, relative_path) is not None


def prepare_cross_repo_topology(
    workspace: Path,
    manifest_repos: list[dict[str, Any]],
    source_entries: list[dict[str, Any]] | None = None,
    cache_root: Path | None = None,
) -> list[dict[str, str]]:
    workspace = workspace.resolve()
    repo_records = {str(repo["name"]): repo for repo in manifest_repos}
    repo_paths = {name: workspace / str(repo["path"]) for name, repo in repo_records.items()}
    source_records = {
        str(entry["name"]): entry for entry in (source_entries or [])
    }
    modules = {
        module: (name, path)
        for name, path in repo_paths.items()
        if (module := go_module_name(path)) is not None
    }
    prepared = []
    for repo_name, repo_path in repo_paths.items():
        for module, relative_target in relative_go_replacements(repo_path):
            target = modules.get(module)
            destination = Path(os.path.abspath(repo_path / relative_target))
            try:
                destination.relative_to(workspace)
            except ValueError:
                continue
            try:
                relative_to_repo = destination.relative_to(repo_path)
            except ValueError:
                relative_to_repo = None
            if target is None:
                continue
            target_name, target_repo = target
            replaced_gitlink = False
            pinned_commit = (
                gitlink_checkout_commit(repo_path, relative_to_repo)
                if relative_to_repo is not None
                else None
            )
            link_target = target_repo
            topology_strategy = "workspace-repo"
            target_commit = str(repo_records[target_name].get("commit") or "")
            if pinned_commit and pinned_commit != target_commit:
                source_entry = source_records.get(target_name)
                source_path = source_entry.get("source_path") if source_entry else None
                if isinstance(source_path, Path) and source_path.exists():
                    dependency_root = (
                        workspace
                        / ".ecosyncbench"
                        / "dependencies"
                        / safe_slug(target_name)
                        / pinned_commit[:16]
                    )
                    prepare_isolated_git_repo(
                        source_path,
                        pinned_commit,
                        dependency_root,
                        cache_root or isolated_git_cache_root(workspace),
                    )
                    link_target = dependency_root
                    topology_strategy = "pinned-gitlink"
            if pinned_commit is not None:
                destination.rmdir()
                replaced_gitlink = True
                ignore_gitlink_worktree(repo_path, relative_to_repo)
            elif destination.exists() or destination.is_symlink():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.symlink_to(Path(os.path.relpath(link_target, destination.parent)))
            if relative_to_repo is not None:
                add_git_info_exclude(repo_path, relative_to_repo)
            prepared.append(
                {
                    "repo": repo_name,
                    "module": module,
                    "path": str(destination.relative_to(workspace)),
                    "target": str(link_target.relative_to(workspace)),
                    "target_repo": target_name,
                    "target_commit": pinned_commit or target_commit,
                    "strategy": topology_strategy,
                    "replaced_gitlink": str(replaced_gitlink).lower(),
                }
            )
    return prepared


def safe_slug(value: str) -> str:
    cleaned = []
    for char in value.strip():
        if char.isalnum() or char in ("-", "_", "."):
            cleaned.append(char)
        else:
            cleaned.append("_")
    return "".join(cleaned).strip("._") or "unknown"


def evaluator_compose_project_name(task_id: str) -> str:
    """Return a stable per-task Compose namespace for concurrent evaluators."""
    prefix = safe_slug(task_id).lower().replace("_", "-").replace(".", "-")
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:10]
    return f"ecosync-{prefix[:40]}-{digest}"


def external_workspace_parent(run_dir: Path, task_id: str, workspace_root: Path) -> Path:
    digest = hashlib.sha256(str(run_dir).encode("utf-8")).hexdigest()[:12]
    return workspace_root.resolve() / safe_slug(task_id) / f"{safe_slug(run_dir.name)}-{digest}"


def external_agent_workspace(run_dir: Path, task_id: str, workspace_root: Path | None) -> Path:
    if workspace_root is None:
        return run_dir / "agent_workspace"
    return external_workspace_parent(run_dir, task_id, workspace_root) / "agent_workspace"


def external_evaluator_workspace(run_dir: Path, task_id: str, workspace_root: Path | None) -> Path:
    if workspace_root is None:
        return run_dir / "evaluator_workspace"
    return external_workspace_parent(run_dir, task_id, workspace_root) / "evaluator_workspace"


def remove_previous_external_workspaces(run_dir: Path) -> None:
    run_file = run_dir / "run.json"
    if not run_file.exists():
        return
    try:
        metadata = json.loads(run_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    for key in ("agent_workspace", "evaluator_workspace"):
        workspace_value = metadata.get(key)
        if not workspace_value:
            continue
        workspace = Path(workspace_value).resolve()
        try:
            workspace.relative_to(run_dir.resolve())
            continue
        except ValueError:
            pass
        if workspace.exists() or workspace.is_symlink():
            remove_tree(workspace)


def prepare_workspace(
    task_id: str,
    workspace: Path,
    mode: str,
    force: bool,
    workspace_scope: str = "ecosystem",
    single_repo: str | None = None,
    repo_modes: dict[str, str] | None = None,
    repo_names: set[str] | None = None,
) -> None:
    workspace = workspace.resolve()
    ensure_clean_workspace(workspace, force)
    entries = repo_entries(task_id, mode, workspace_scope, single_repo, repo_modes, repo_names)
    cache_root = isolated_git_cache_root(workspace)
    for entry in entries:
        if not entry["source_path"].exists():
            raise SystemExit(f"missing local source repo for {entry['name']}: {entry['source_path']}")

    manifest = {
        "task_id": task_id,
        "mode": mode,
        "workspace_scope": workspace_scope,
        "single_repo": single_repo,
        "created_at": int(time.time()),
        "prompt": {},
        "repos": [],
    }

    for entry in entries:
        dest = workspace / entry["dest_path"]
        git_isolation = prepare_isolated_git_repo(entry["source_path"], entry["commit"], dest, cache_root)
        manifest["repos"].append(
            {
                "name": entry["name"],
                "path": str(entry["dest_path"]),
                "commit": entry["commit"],
                "mode": entry["mode"],
                "agent_editable": entry["agent_editable"],
                "included_in_task": entry["included_in_task"],
                "role": entry["role"],
                "git_isolation": git_isolation,
            }
        )

    manifest["workspace_topology"] = prepare_cross_repo_topology(
        workspace,
        manifest["repos"],
        entries,
        cache_root,
    )

    metadata_dir = workspace / ".ecosyncbench"
    metadata_dir.mkdir(exist_ok=True)
    source_prompt_path = prompt_path_for_scope(task_id, workspace_scope, single_repo)
    task_prompt = source_prompt_path.read_text(encoding="utf-8").rstrip()
    prompt = task_prompt + "\n"
    manifest["prompt"] = {
        "source": str(source_prompt_path.relative_to(task_dir(task_id))),
        "workspace_prompt": ".ecosyncbench/prompt.md",
    }
    (metadata_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    (metadata_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "workspace": str(workspace),
                "task_id": task_id,
                "mode": mode,
                "workspace_scope": workspace_scope,
                "single_repo": single_repo,
                "repo_count": len(entries),
            },
            indent=2,
        )
    )


def prepare_agent_run(
    task_id: str,
    run_dir: Path,
    force: bool,
    workspace_scope: str = "ecosystem",
    single_repo: str | None = None,
    workspace_root: Path | None = None,
) -> None:
    validate_workspace_scope(task_id, workspace_scope, single_repo)
    run_dir = run_dir.resolve()
    if run_dir.exists():
        if not force:
            raise SystemExit(f"run dir already exists: {run_dir}; pass --force to replace it")
        remove_previous_external_workspaces(run_dir)
        remove_tree(run_dir)
    run_dir.mkdir(parents=True)

    workspace = external_agent_workspace(run_dir, task_id, workspace_root)
    evaluator_workspace = external_evaluator_workspace(run_dir, task_id, workspace_root)
    prepare_workspace(
        task_id,
        workspace,
        "base",
        force=force,
        workspace_scope=workspace_scope,
        single_repo=single_repo,
    )
    metadata = {
        "task_id": task_id,
        "workspace_scope": workspace_scope,
        "single_repo": single_repo,
        "created_at": int(time.time()),
        "agent_workspace": str(workspace),
        "agent_workspace_root": str(workspace_root.resolve()) if workspace_root else None,
        "evaluator_workspace": str(evaluator_workspace),
        "diff_dir": str(run_dir / "diffs"),
        "logs_dir": str(run_dir / "logs"),
    }
    (run_dir / "run.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"run_dir": str(run_dir), **metadata}, indent=2))


def task_repo_by_name(task: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {repo["name"]: repo for repo in task.get("repositories", [])}


def apply_patch_file(repo_path: Path, patch_file: Path, allow_already_applied: bool) -> str:
    if allow_already_applied:
        reverse_result = subprocess.run(
            ["git", "-C", str(repo_path), "apply", "--reverse", "--check", str(patch_file)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if reverse_result.returncode == 0:
            return "already_applied"

    check_result = subprocess.run(
        ["git", "-C", str(repo_path), "apply", "--check", str(patch_file)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if check_result.returncode == 0:
        run(["git", "-C", str(repo_path), "apply", str(patch_file)], umask=0o022)
        return "applied"

    three_way_check = subprocess.run(
        ["git", "-C", str(repo_path), "apply", "--3way", "--check", str(patch_file)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if three_way_check.returncode == 0:
        run(["git", "-C", str(repo_path), "apply", "--3way", str(patch_file)], umask=0o022)
        return "applied_3way"

    run(["git", "-C", str(repo_path), "apply", str(patch_file)], umask=0o022)
    return "applied"


def try_apply_patch_file(repo_path: Path, patch_file: Path, allow_already_applied: bool) -> tuple[str, str]:
    try:
        return apply_patch_file(repo_path, patch_file, allow_already_applied), ""
    except subprocess.CalledProcessError as exc:
        diagnostics = []
        for args in (["--check"], ["--3way", "--check"]):
            result = capture(["git", "-C", str(repo_path), "apply", *args, str(patch_file)])
            output = result.stdout.strip()
            diagnostics.append(
                f"git apply {' '.join(args)} exit={result.returncode}"
                + (f": {output}" if output else "")
            )
        return "failed", "; ".join(diagnostics) or str(exc)


def path_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def patch_target_files(patch_file: Path) -> list[str]:
    result = subprocess.run(
        ["git", "apply", "--numstat", "-z", str(patch_file)],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = (
            result.stderr.decode("utf-8", errors="replace").strip()
            or result.stdout.decode("utf-8", errors="replace").strip()
            or "unknown git apply error"
        )
        raise ValueError(f"cannot parse hidden patch {patch_file}: {detail}")
    fields = result.stdout.split(b"\0")
    paths: list[str] = []
    seen: set[str] = set()

    def add_path(raw: bytes) -> None:
        path = raw.decode("utf-8", errors="surrogateescape")
        if path and path not in seen:
            paths.append(path)
            seen.add(path)

    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if not record:
            continue
        parts = record.split(b"\t", 2)
        if len(parts) != 3:
            raise ValueError(f"cannot parse hidden patch numstat record in {patch_file}: {record!r}")
        path = parts[2]
        if path:
            add_path(path)
            continue

        # With -z, rename/copy records put the old and new path in the next
        # two NUL-delimited fields. Overlay both so deletions and additions are
        # materialized exactly like git apply.
        if index + 1 >= len(fields):
            raise ValueError(f"incomplete rename/copy record in hidden patch {patch_file}")
        add_path(fields[index])
        add_path(fields[index + 1])
        index += 2

    # `git apply --numstat -z` reports only the destination path for a rename
    # whose contents also changed. The clean overlay must hydrate the source
    # path as well so applying the patch can remove/rename it correctly.
    pending_source: str | None = None
    for line in patch_file.read_text(encoding="utf-8", errors="surrogateescape").splitlines():
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
        if pending_source not in seen:
            insert_at = paths.index(destination) if destination in seen else len(paths)
            paths.insert(insert_at, pending_source)
            seen.add(pending_source)
        pending_source = None
    if not paths:
        raise ValueError(f"hidden patch has no target files: {patch_file}")
    return paths


def hidden_patch_relocation_pairs(item: dict[str, Any], patch_file: Path) -> list[tuple[str, str]]:
    source_files = path_list(item.get("source_file"))
    hidden_files = path_list(item.get("hidden_file"))
    if not source_files or len(source_files) != len(hidden_files):
        return []
    pairs = list(zip(source_files, hidden_files))
    if not any(source != hidden for source, hidden in pairs):
        return []

    targets = set(patch_target_files(patch_file))
    source_targets = {source for source, _hidden in pairs if source in targets}
    hidden_targets = {hidden for _source, hidden in pairs if hidden in targets}
    if source_targets and not hidden_targets:
        return pairs
    return []


def git_show_file(repo_path: Path, git_ref: str, relative_path: str) -> bytes | None:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "show", f"{git_ref}:{relative_path}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def relocated_hidden_content(source: str, hidden: str, content: bytes) -> bytes:
    """Materialize an upstream test patch as an evaluator-only hidden file.

    Kotlin and Swift test class names are globally visible during compilation.
    When a hidden file is copied next to the original test file, rename only the
    enclosing test type to avoid duplicate declarations; assertions and test
    method bodies remain the upstream PR content.
    """
    source_path = Path(source)
    hidden_path = Path(hidden)
    if source_path.suffix == ".kt" and hidden_path.suffix == ".kt":
        source_name = source_path.stem
        hidden_name = hidden_path.stem
        if source_name != hidden_name:
            text = content.decode("utf-8")
            text = re.sub(rf"\b(class|object)\s+{re.escape(source_name)}\b", rf"\1 {hidden_name}", text, count=1)
            return text.encode("utf-8")
    if source_path.suffix == ".swift" and hidden_path.suffix == ".swift":
        text = content.decode("utf-8")
        hidden_name = hidden_path.name
        for suffix in (".test.swift", ".swift"):
            if hidden_name.endswith(suffix):
                hidden_name = hidden_name[: -len(suffix)]
                break
        hidden_type = re.sub(r"[^0-9A-Za-z_]", "_", hidden_name)
        match = re.search(r"\b(class|struct)\s+([A-Za-z_][0-9A-Za-z_]*)\s*:\s*XCTestCase\b", text)
        if match and match.group(2) != hidden_type:
            text = text[: match.start(2)] + hidden_type + text[match.end(2) :]
            return text.encode("utf-8")
    if source_path.suffix == ".php" and hidden_path.suffix == ".php":
        source_name = source_path.stem
        hidden_name = re.sub(r"[^0-9A-Za-z_]", "_", hidden_path.stem)
        if source_name != hidden_name:
            text = content.decode("utf-8")
            text = re.sub(rf"\b(class|trait)\s+{re.escape(source_name)}\b", rf"\1 {hidden_name}", text, count=1)
            return text.encode("utf-8")
    return content


def apply_relocated_hidden_patch(
    repo_path: Path,
    patch_file: Path,
    item: dict[str, Any],
    allow_already_applied: bool,
) -> tuple[str, str]:
    try:
        pairs = hidden_patch_relocation_pairs(item, patch_file)
        if not pairs:
            return try_apply_patch_file(repo_path, patch_file, allow_already_applied)

        with tempfile.TemporaryDirectory(prefix="ecosync-hidden-relocate-") as tmp:
            tmp_path = Path(tmp)
            for target in patch_target_files(patch_file):
                content = git_show_file(repo_path, "HEAD", target)
                if content is None:
                    continue
                target_path = tmp_path / target
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_bytes(content)

            subprocess.run(
                ["git", "apply", str(patch_file.resolve())],
                cwd=tmp_path,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )

            already_applied = True
            for source, hidden in pairs:
                materialized = tmp_path / source
                if not materialized.exists():
                    return "failed", f"relocated hidden source was not materialized: {source}"
                content = relocated_hidden_content(source, hidden, materialized.read_bytes())
                hidden_path = repo_path / hidden
                if hidden_path.exists():
                    if allow_already_applied and hidden_path.read_bytes() == content:
                        continue
                    return "failed", f"hidden file already exists with different content: {hidden}"
                already_applied = False
                hidden_path.parent.mkdir(parents=True, exist_ok=True)
                hidden_path.write_bytes(content)

            return ("already_applied" if already_applied else "relocated"), ""
    except (subprocess.CalledProcessError, ValueError) as exc:
        return "failed", str(exc)


def overlay_hidden_patch_from_clean_base(repo_path: Path, patch_file: Path, allow_already_applied: bool) -> tuple[str, str]:
    """Apply a hidden test patch against clean HEAD, then overlay its target files.

    Evaluator workspaces contain the agent patch as uncommitted changes. If the
    agent also edits an upstream test file, applying an upstream hidden-test diff
    can conflict even though the production solution should still be evaluated.
    This fallback materializes the hidden test files from clean base HEAD and
    copies only the patch targets into the evaluator workspace.
    """
    try:
        targets = patch_target_files(patch_file)
        if not targets:
            return "failed", "hidden patch has no target files"

        with tempfile.TemporaryDirectory(prefix="ecosync-hidden-overlay-") as tmp:
            tmp_path = Path(tmp)
            for target in targets:
                content = git_show_file(repo_path, "HEAD", target)
                if content is None:
                    continue
                target_path = tmp_path / target
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_bytes(content)

            subprocess.run(
                ["git", "apply", str(patch_file.resolve())],
                cwd=tmp_path,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )

            changed = False
            for target in targets:
                materialized = tmp_path / target
                destination = repo_path / target
                if materialized.exists():
                    content = materialized.read_bytes()
                    if destination.exists() and destination.read_bytes() == content:
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(content)
                    changed = True
                elif destination.exists():
                    destination.unlink()
                    changed = True
                else:
                    # A successfully applied clean-base patch may delete this
                    # target. If the agent patch already deleted it too, the
                    # hidden state is already materialized.
                    continue

            return ("overlay_from_clean_base" if changed else "already_applied"), ""
    except (subprocess.CalledProcessError, ValueError) as exc:
        return "failed", str(exc)


def apply_gold(task_id: str, workspace: Path, allow_already_applied: bool) -> None:
    task = load_task(task_id)
    visible = visible_repo_names(workspace)
    results = []
    for repo in task.get("repositories", []):
        if visible and repo["name"] not in visible:
            results.append({"repo": repo["name"], "patch": repo["gold_patch"], "status": "skipped_not_visible"})
            continue
        patch = task_dir(task_id) / repo["gold_patch"]
        repo_path = workspace.resolve() / repo["path"]
        status = apply_patch_file(repo_path, patch, allow_already_applied)
        results.append({"repo": repo["name"], "patch": str(patch), "status": status})
    print(json.dumps({"applied_gold": results}, indent=2))


def apply_hidden(
    task_id: str,
    workspace: Path,
    allow_already_applied: bool,
    profiles: list[str] | None = None,
    prefer_clean_overlay: bool = False,
) -> list[dict[str, Any]]:
    harness = load_harness(task_id)
    task = load_task(task_id)
    repos = task_repo_by_name(task)
    results = []
    selected_profiles = set(profiles) if profiles is not None else None
    visible = visible_repo_names(workspace)
    clean_overlay_targets: dict[str, set[str]] = {}
    for item in harness.get("hidden_test_patches", []):
        test_profile = item.get("test_profile")
        if selected_profiles is not None and test_profile not in selected_profiles:
            results.append(
                {
                    "repo": item["repo"],
                    "patch": item["patch"],
                    "test_profile": test_profile,
                    "status": "skipped",
                }
            )
            continue
        repo_name = item["repo"]
        if visible and repo_name not in visible:
            results.append(
                {
                    "repo": repo_name,
                    "patch": item["patch"],
                    "test_profile": test_profile,
                    "status": "skipped_not_visible",
                }
            )
            continue
        repo = repos[repo_name]
        patch = task_dir(task_id) / item["patch"]
        repo_path = workspace.resolve() / repo["path"]
        status = "failed"
        error = ""
        direct_origin = item.get("origin") in {
            "upstream_test_diff",
            "upstream_test_direct",
            "upstream_added_test_file",
            "benchmark_oracle_from_upstream_behavior",
            "contract_aligned_test",
            "contract_aligned_hidden_test",
            "contract_alignment",
            "contract_test",
            "independent_behavior_contract",
            "prompt_aligned_behavioral_test",
        }
        clean_overlay = direct_origin or bool(item.get("clean_overlay"))
        patch_targets = set(patch_target_files(patch)) if prefer_clean_overlay and clean_overlay else set()
        layered_on_clean_overlay = bool(
            patch_targets & clean_overlay_targets.get(repo_name, set())
        )
        if prefer_clean_overlay and clean_overlay and not layered_on_clean_overlay:
            # Evaluator tests must be independent of agent-authored tests. A
            # direct apply can succeed while retaining an agent test with the
            # same declaration, which then makes the package fail to compile.
            # Always materialize upstream hidden-test targets from clean HEAD.
            status, error = overlay_hidden_patch_from_clean_base(repo_path, patch, allow_already_applied)
        else:
            status, error = apply_relocated_hidden_patch(repo_path, patch, item, allow_already_applied)
        if status == "failed" and not (prefer_clean_overlay and clean_overlay) and item.get("origin") in {
            "upstream_test_diff",
            "upstream_test_direct",
            "upstream_added_test_file",
            "benchmark_oracle_from_upstream_behavior",
        }:
            status, error = overlay_hidden_patch_from_clean_base(repo_path, patch, allow_already_applied)
        if prefer_clean_overlay and clean_overlay and status in {
            "overlay_from_clean_base",
            "already_applied",
            "applied",
            "applied_3way",
        }:
            clean_overlay_targets.setdefault(repo_name, set()).update(patch_targets)
        item_result = {"repo": repo_name, "patch": str(patch), "test_profile": test_profile, "status": status}
        if error:
            item_result["error"] = error
        results.append(item_result)
    print(json.dumps({"applied_hidden": results}, indent=2))
    return results


def collectable_repos(
    task_id: str,
    workspace: Path | None = None,
    workspace_scope: str = "ecosystem",
    single_repo: str | None = None,
) -> list[dict[str, Any]]:
    if workspace is not None:
        return manifest_repo_entries(task_id, workspace, workspace_scope, single_repo)
    return repo_entries(task_id, "base", workspace_scope, single_repo)


def repo_is_dirty(repo_path: Path) -> bool:
    proc = capture_structured_output(
        [
            "git",
            "-C",
            str(repo_path),
            "-c",
            "submodule.recurse=false",
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--ignore-submodules=all",
            *agent_diff_pathspecs(),
        ]
    )
    return bool(proc.stdout.strip())


def agent_diff_pathspecs() -> list[str]:
    pathspecs = ["--", "."]
    for path in AGENT_DIFF_EXCLUDED_PATHS:
        pathspecs.extend([f":(exclude){path}", f":(exclude){path}/**", f":(exclude)**/{path}", f":(exclude)**/{path}/**"])
    return pathspecs


def is_agent_diff_excluded(path: str) -> bool:
    parts = Path(path).parts
    return any(part in AGENT_DIFF_EXCLUDED_PATHS for part in parts)


def mark_untracked_for_diff(repo_path: Path) -> None:
    proc = subprocess.run(
        ["git", "-C", str(repo_path), "ls-files", "--others", "--exclude-standard", "-z", *agent_diff_pathspecs()],
        stdout=subprocess.PIPE,
        check=True,
    )
    paths = [item for item in proc.stdout.split(b"\0") if item and not is_agent_diff_excluded(item.decode("utf-8", errors="ignore"))]
    if paths:
        subprocess.run(["git", "-C", str(repo_path), "add", "-N", "--", *[path.decode("utf-8") for path in paths]], check=True)


def is_test_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    parts = normalized.lower().split("/")
    name = parts[-1] if parts else normalized.lower()
    return (
        "test" in parts
        or "tests" in parts
        or "__tests__" in parts
        or name.startswith("test_")
        or name.endswith("_test.go")
        or name.endswith("_test.py")
        or name.endswith("_tests.py")
        or name.endswith(".test.ts")
        or name.endswith(".test.tsx")
        or name.endswith(".spec.ts")
        or name.endswith(".spec.tsx")
        or name.endswith("test.kt")
        or name.endswith("tests.kt")
        or name.endswith("test.swift")
        or name.endswith("tests.swift")
        or name.endswith("test.cs")
        or name.endswith("tests.cs")
    )


def changed_file_rows(repo_path: Path) -> list[dict[str, str]]:
    proc = capture_structured_output(
        [
            "git",
            "-C",
            str(repo_path),
            "-c",
            "submodule.recurse=false",
            "diff",
            "--ignore-submodules=all",
            "--name-status",
            "HEAD",
            *agent_diff_pathspecs(),
        ]
    )
    if proc.returncode != 0:
        return []
    rows: list[dict[str, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if not parts:
            continue
        status = parts[0]
        path = parts[-1]
        if is_agent_diff_excluded(path):
            continue
        rows.append({"status": status, "path": path, "is_test": is_test_path(path)})
    return rows


def collect_diff(task_id: str, workspace: Path, output_dir: Path) -> None:
    workspace = workspace.resolve()
    output_dir = output_dir.resolve()
    manifest = workspace_manifest(workspace) or {}
    run_scope, run_single_repo = workspace_scope_from_run(output_dir.parent)
    workspace_scope = str(manifest.get("workspace_scope") or run_scope)
    single_repo = manifest.get("single_repo", run_single_repo)
    if output_dir.exists():
        remove_tree(output_dir)
    output_dir.mkdir(parents=True)
    status_dir = output_dir / "status"
    status_dir.mkdir()

    results = []
    for repo in collectable_repos(task_id, workspace, workspace_scope, single_repo):
        repo_path = workspace / repo["dest_path"]
        if not repo_path.exists():
            results.append(
                {
                    "repo": repo["name"],
                    "path": str(repo["dest_path"]),
                    "changed": False,
                    "dirty": False,
                    "status": "missing_from_workspace",
                    "agent_editable": repo["agent_editable"],
                    "included_in_task": repo["included_in_task"],
                    "edit_policy_violation": False,
                }
            )
            continue
        mark_untracked_for_diff(repo_path)
        patch_file = output_dir / f"{repo['name']}.patch"
        diff = capture_binary_output(
            [
                "git",
                "-C",
                str(repo_path),
                "-c",
                "submodule.recurse=false",
                "diff",
                "--ignore-submodules=all",
                "--binary",
                "HEAD",
                *agent_diff_pathspecs(),
            ]
        )
        if diff.returncode != 0:
            patch_file.write_bytes(b"")
            status_path = status_dir / f"{repo['name']}.status"
            status_path.write_text(diff.stdout.decode("utf-8", errors="replace"), encoding="utf-8")
            results.append(
                {
                    "repo": repo["name"],
                    "path": str(repo["dest_path"]),
                    "patch": str(patch_file),
                    "changed": False,
                    "dirty": repo_is_dirty(repo_path),
                    "changed_files": [],
                    "changed_file_count": 0,
                    "agent_test_files": [],
                    "agent_test_file_count": 0,
                    "agent_modified_tests": False,
                    "agent_editable": repo["agent_editable"],
                    "included_in_task": repo["included_in_task"],
                    "edit_policy_violation": False,
                    "status": str(status_path),
                    "invalid_diff_summary": True,
                    "diff_error": (diff.stderr or diff.stdout).decode("utf-8", errors="replace").strip(),
                }
            )
            continue
        patch_file.write_bytes(diff.stdout)
        status = capture_structured_output(
            [
                "git",
                "-C",
                str(repo_path),
                "-c",
                "submodule.recurse=false",
                "status",
                "--short",
                "--untracked-files=all",
                "--ignore-submodules=all",
                *agent_diff_pathspecs(),
            ]
        )
        if status.returncode != 0:
            status_path = status_dir / f"{repo['name']}.status"
            status_path.write_text(status.stdout, encoding="utf-8")
            results.append(
                {
                    "repo": repo["name"],
                    "path": str(repo["dest_path"]),
                    "patch": str(patch_file),
                    "changed": bool(diff.stdout.strip()),
                    "dirty": repo_is_dirty(repo_path),
                    "changed_files": [],
                    "changed_file_count": 0,
                    "agent_test_files": [],
                    "agent_test_file_count": 0,
                    "agent_modified_tests": False,
                    "agent_editable": repo["agent_editable"],
                    "included_in_task": repo["included_in_task"],
                    "edit_policy_violation": False,
                    "status": str(status_path),
                    "invalid_diff_summary": True,
                    "diff_error": (status.stderr or status.stdout).strip(),
                }
            )
            continue
        (status_dir / f"{repo['name']}.status").write_text(status.stdout, encoding="utf-8")
        changed_files = changed_file_rows(repo_path)
        test_files = [row for row in changed_files if row["is_test"]]
        changed = bool(diff.stdout.strip())
        results.append(
            {
                "repo": repo["name"],
                "path": str(repo["dest_path"]),
                "patch": str(patch_file),
                "changed": changed,
                "dirty": repo_is_dirty(repo_path),
                "changed_files": changed_files,
                "changed_file_count": len(changed_files),
                "agent_test_files": test_files,
                "agent_test_file_count": len(test_files),
                "agent_modified_tests": bool(test_files),
                "agent_editable": repo["agent_editable"],
                "included_in_task": repo["included_in_task"],
                "edit_policy_violation": changed and not repo["agent_editable"],
                "status": str(status_dir / f"{repo['name']}.status"),
            }
        )

    violations = [repo for repo in results if repo.get("edit_policy_violation")]
    summary = {
        "task_id": task_id,
        "workspace_scope": workspace_scope,
        "single_repo": single_repo,
        "repos": results,
        "edit_policy_violations": violations,
        "edit_policy_ok": not violations,
    }
    (output_dir / "diffs.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"diff_dir": str(output_dir), **summary}, indent=2))


def load_diff_summary(diff_dir: Path) -> dict[str, Any]:
    path = diff_dir / "diffs.json"
    if not path.exists():
        return {"repos": []}
    return json.loads(path.read_text(encoding="utf-8"))


def apply_agent_diffs(task_id: str, workspace: Path, diff_dir: Path) -> list[dict[str, Any]]:
    workspace = workspace.resolve()
    diff_dir = diff_dir.resolve()
    diff_summary = load_diff_summary(diff_dir)
    results = []
    for repo in diff_summary.get("repos") or []:
        repo_name = repo.get("repo") or repo.get("name")
        repo_path_value = repo.get("path") or repo.get("dest_path")
        if not repo_name or not repo_path_value:
            results.append({"repo": repo_name, "status": "invalid_diff_summary", "error": "missing repo name or path"})
            continue
        if repo.get("invalid_diff_summary"):
            results.append(
                {
                    "repo": repo_name,
                    "status": "invalid_diff_summary",
                    "error": repo.get("diff_error") or "agent diff collection failed",
                }
            )
            continue
        patch_file = diff_dir / f"{repo_name}.patch"
        repo_path = workspace / repo_path_value
        patch_text = patch_file.read_text(encoding="utf-8", errors="replace") if patch_file.exists() else ""
        if not patch_text.strip():
            results.append({"repo": repo_name, "patch": str(patch_file), "status": "empty"})
            continue
        if not looks_like_git_patch(patch_text):
            results.append(
                {
                    "repo": repo_name,
                    "patch": str(patch_file),
                    "status": "invalid_diff_summary",
                    "error": patch_text.strip().splitlines()[0][:500],
                }
            )
            continue
        status, error = try_apply_patch_file(repo_path, patch_file, allow_already_applied=True)
        result = {"repo": repo_name, "patch": str(patch_file), "status": status}
        if error:
            result["error"] = error
        results.append(result)
    return results


def looks_like_git_patch(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("diff --git ") or stripped.startswith("GIT binary patch")


def agent_diffs_applied(applied_diffs: list[dict[str, Any]]) -> bool:
    failed_statuses = {"failed", "invalid_diff_summary"}
    return not any(item.get("status") in failed_statuses for item in applied_diffs)


def run_profile(task_id: str, workspace: Path, profile: str, log_file: Path | None = None) -> int:
    global _STALE_CONTAINER_CLEANUP_DONE
    if not _STALE_CONTAINER_CLEANUP_DONE:
        with docker_lifecycle_lock():
            cleanup_stale_profile_containers(log_file)
        _STALE_CONTAINER_CLEANUP_DONE = True
    try:
        return _run_profile_unlocked(task_id, workspace, profile, log_file)
    finally:
        with docker_lifecycle_lock():
            cleanup_stale_profile_containers(log_file)


def task_cache_root_from_runner(task_id: str) -> Path | None:
    configured = os.environ.get("ECOSYNC_CACHE_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    configured_base = os.environ.get("ECOSYNC_CACHE_BASE_ROOT")
    if configured_base:
        return (Path(configured_base).expanduser() / task_id).resolve()
    script = task_dir(task_id) / "environment" / "run_profile.sh"
    if not script.exists():
        return None
    text = script.read_text(encoding="utf-8", errors="replace")
    match = re.search(r'cache_root=["\']([^"\']+)["\']', text)
    if not match:
        return None
    value = match.group(1)
    default_match = re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-(.+)\}", value)
    if default_match:
        value = default_match.group(1)
    value = re.sub(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-([^}]+)\}", r"\1", value)
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else None


def evaluator_node_modules_cache_root(profile: dict[str, Any] | None = None) -> Path:
    configured = str((profile or {}).get("evaluator_node_modules_cache_root") or "").strip()
    if not configured:
        configured = os.environ.get(
            "ECOSYNC_EVALUATOR_NODE_MODULES_CACHE_ROOT",
            str(Path.home() / ".cache" / "wideswe" / "evaluator-node-modules"),
        )
    root = Path(configured).expanduser()
    if not root.is_absolute():
        raise RuntimeError(f"evaluator node_modules cache root must be absolute: {configured!r}")
    return root


def evaluator_node_modules_mounts(
    task_id: str,
    profile_name: str,
    workspace: Path,
) -> list[dict[str, str]]:
    """Return isolated evaluator node_modules mounts for one profile."""
    current_task_dir = task_dir(task_id)
    profiles_path = current_task_dir / "environment" / "test_profiles.yaml"
    if not profiles_path.is_file():
        return []
    document = load_yaml(profiles_path) or {}
    profiles = document.get("profiles") if isinstance(document, dict) else None
    profile = profiles.get(profile_name) if isinstance(profiles, dict) else None
    if not isinstance(profile, dict):
        return []
    profile_mode = str(profile.get("evaluator_node_modules_mode") or "").strip().lower()
    if not profile_mode and not bool(profile.get("persist_node_modules")):
        return []
    workdir = str(profile.get("workdir") or "").strip().strip("/")
    workdir_path = Path(workdir)
    if not workdir or workdir_path.is_absolute() or ".." in workdir_path.parts:
        raise RuntimeError(
            f"invalid workdir for persistent node_modules: task={task_id} "
            f"profile={profile_name} workdir={workdir!r}"
        )
    configured_paths = profile.get(
        "evaluator_node_modules_paths",
        profile.get("persist_node_modules_paths"),
    )
    if configured_paths is None:
        relative_paths = ["."]
    elif isinstance(configured_paths, list):
        relative_paths = list(
            dict.fromkeys(str(item).strip().strip("/") or "." for item in configured_paths)
        )
    else:
        raise RuntimeError(
            f"persist_node_modules_paths must be a list: task={task_id} profile={profile_name}"
        )

    mode = profile_mode or os.environ.get(
        "ECOSYNC_EVALUATOR_NODE_MODULES_MODE",
        "persistent",
    ).strip().lower()
    if mode == "off":
        return []
    if mode not in {"tmpfs", "persistent"}:
        raise RuntimeError(f"unsupported ECOSYNC_EVALUATOR_NODE_MODULES_MODE={mode!r}")

    namespace = ""
    cache_base: Path | None = None
    if mode == "persistent":
        fingerprint = hashlib.sha256()
        fingerprint.update(task_id.encode())
        fingerprint.update(b"\0")
        fingerprint.update(profile_name.encode())
        fingerprint.update(b"\0")
        fingerprint.update(json.dumps(profile, sort_keys=True, separators=(",", ":")).encode())
        for relative in ("repos.yaml", "environment/deps_image_lock.yaml"):
            path = current_task_dir / relative
            if path.is_file():
                fingerprint.update(b"\0")
                fingerprint.update(relative.encode())
                fingerprint.update(b"\0")
                fingerprint.update(path.read_bytes())
        dependency_inputs = (
            "package.json",
            "package-lock.json",
            "npm-shrinkwrap.json",
            "yarn.lock",
            "pnpm-lock.yaml",
            "bun.lock",
            "bun.lockb",
        )
        evaluator_workdir = workspace / workdir_path
        for relative_path in relative_paths:
            dependency_root = (
                evaluator_workdir
                if relative_path == "."
                else evaluator_workdir / relative_path
            )
            for name in dependency_inputs:
                path = dependency_root / name
                relative = f"{relative_path}/{name}"
                fingerprint.update(b"\0")
                fingerprint.update(relative.encode())
                fingerprint.update(b"\0")
                if path.is_file():
                    fingerprint.update(path.read_bytes())
                else:
                    fingerprint.update(b"<missing>")
        namespace = fingerprint.hexdigest()[:20]
        cache_base = (
            evaluator_node_modules_cache_root(profile)
            / safe_slug(task_id)
            / safe_slug(profile_name)
            / namespace
        )
    mounts: list[dict[str, str]] = []
    for relative_path in relative_paths:
        path = Path(relative_path)
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError(
                f"invalid persistent node_modules path: task={task_id} "
                f"profile={profile_name} path={relative_path!r}"
            )
        relative_container = "" if relative_path == "." else f"/{relative_path}"
        item = {
            "kind": mode,
            "container": f"/workspace/{workdir}{relative_container}/node_modules",
            "workdir": f"/workspace/{workdir}",
        }
        if mode == "persistent":
            assert cache_base is not None
            host = cache_base / safe_slug(relative_path) / "node_modules"
            host.mkdir(parents=True, exist_ok=True)
            item.update({"host": str(host.resolve()), "fingerprint": namespace})
        else:
            item["size"] = str(
                profile.get("evaluator_node_modules_tmpfs_size")
                or os.environ.get("ECOSYNC_EVALUATOR_NODE_MODULES_TMPFS_SIZE", "8g")
            )
        mounts.append(item)
    return mounts


def gradle_cache_corruption_in_log(log_file: Path) -> bool:
    if not log_file.exists():
        return False
    text = log_file.read_text(encoding="utf-8", errors="replace")
    return (
        "NoClassDefFoundError: build_" in text
        and "ClassNotFoundException: build_" in text
    ) or "Could not load compiled classes for build file" in text or bool(
        re.search(r"(?m)^> build_[^\s]+\$_run_closure[^\s]+\s*$", text)
    )


def quarantine_corrupt_gradle_script_caches(task_id: str) -> list[dict[str, str]]:
    cache_root = task_cache_root_from_runner(task_id)
    if cache_root is None or cache_root == Path("/") or not cache_root.is_dir():
        return []
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    candidates: list[Path] = []
    for root, dirs, _files in os.walk(cache_root):
        relative_depth = len(Path(root).relative_to(cache_root).parts)
        dirs[:] = [name for name in dirs if ".corrupt-" not in name and relative_depth < 8]
        for name in dirs:
            if name in {"groovy-dsl", "jars-9"}:
                candidates.append(Path(root) / name)
    results: list[dict[str, str]] = []
    for source in sorted(set(candidates), key=lambda path: len(path.parts), reverse=True):
        if not source.exists():
            continue
        destination = source.with_name(f"{source.name}.corrupt-{stamp}-{os.getpid()}")
        source.rename(destination)
        results.append({"source": str(source), "quarantine": str(destination)})
    return results


def run_profile_process(
    command: list[str],
    env: dict[str, str],
    timeout_sec: int | None,
    log_file: Path | None,
    mode: str,
) -> int:
    handle = None
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handle = log_file.open(mode, encoding="utf-8")
    proc = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=handle,
        stderr=subprocess.STDOUT if handle else None,
        start_new_session=True,
    )
    previous_handlers = {
        signum: signal.getsignal(signum)
        for signum in (signal.SIGTERM, signal.SIGINT)
    }

    def interrupted(signum: int, _frame: Any) -> None:
        raise SystemExit(128 + signum)

    for signum in previous_handlers:
        signal.signal(signum, interrupted)
    try:
        return proc.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        if handle:
            handle.write(f"\n[ecosync] profile timed out after {timeout_sec}s\n")
            handle.flush()
        terminate_process_tree(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            terminate_process_tree(proc.pid, signal.SIGKILL)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                pass
        return 124
    except BaseException:
        terminate_process_tree(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            terminate_process_tree(proc.pid, signal.SIGKILL)
        raise
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        if handle:
            handle.close()


def process_tree_groups(root_pid: int) -> list[int]:
    processes: dict[int, tuple[int, int]] = {}
    for stat_path in Path("/proc").glob("[0-9]*/stat"):
        try:
            text = stat_path.read_text(encoding="utf-8")
            fields = text[text.rfind(")") + 2 :].split()
            pid = int(stat_path.parent.name)
            processes[pid] = (int(fields[1]), int(fields[2]))
        except (OSError, ValueError, IndexError):
            continue
    descendants = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, (parent, _group) in processes.items():
            if parent in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True
    current_group = os.getpgrp()
    groups = {
        processes[pid][1]
        for pid in descendants
        if pid in processes and processes[pid][1] > 0 and processes[pid][1] != current_group
    }
    root_group = processes.get(root_pid, (0, root_pid))[1]
    return sorted(groups, key=lambda group: group == root_group)


def terminate_process_tree(root_pid: int, signum: int) -> None:
    for group in process_tree_groups(root_pid):
        try:
            os.killpg(group, signum)
        except ProcessLookupError:
            pass


def profile_docker_shim_env(workspace: Path, env: dict[str, str]) -> dict[str, str]:
    real_docker = shutil.which("docker", path=os.defpath)
    if not real_docker:
        return env
    shim_dir = workspace / ".ecosyncbench" / "harness-bin"
    shim_dir.mkdir(parents=True, exist_ok=True)
    git_config = workspace / ".ecosyncbench" / "evaluator.gitconfig"
    git_config.write_text("[safe]\n\tdirectory = *\n", encoding="utf-8")
    shim_log = workspace / ".ecosyncbench" / "docker-shim.log"
    shim = shim_dir / "docker"
    shim.write_text(
        """#!/usr/bin/env python3
import json
import os
import shlex
import signal
import subprocess
import sys
import time

real_docker = os.environ["ECOSYNC_REAL_DOCKER"]
git_config = os.environ["ECOSYNC_EVALUATOR_GIT_CONFIG"]
original = sys.argv[1:]
with open(os.environ["ECOSYNC_DOCKER_SHIM_LOG"], "a", encoding="utf-8") as handle:
    handle.write(shlex.join(original) + "\\n")


def container_exit_code(name):
    try:
        result = subprocess.run(
            [real_docker, "inspect", "--format", "{{json .State}}", name],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    try:
        state = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if state.get("Status") not in {"exited", "dead"}:
        return None
    return int(state.get("ExitCode", 1))


def cleanup(name):
    try:
        subprocess.run(
            [real_docker, "rm", "-f", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pass


def managed_run_args(args):
    result = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg == "--rm":
            continue
        if arg == "--name":
            skip_next = True
            continue
        if arg.startswith("--name="):
            continue
        result.append(arg)
    return result


def is_detached_run(args):
    return "-d" in args or "--detach" in args


def has_container_env(args, name):
    for index, arg in enumerate(args):
        if arg in {"-e", "--env"} and index + 1 < len(args):
            value = args[index + 1]
            if value == name or value.startswith(name + "="):
                return True
        if arg.startswith("--env="):
            value = arg.split("=", 1)[1]
            if value == name or value.startswith(name + "="):
                return True
    return False


def container_env_value(args, name):
    value = None
    for index, arg in enumerate(args):
        if arg in {"-e", "--env"} and index + 1 < len(args):
            candidate = args[index + 1]
            if candidate.startswith(name + "="):
                value = candidate.split("=", 1)[1]
        elif arg.startswith("--env="):
            candidate = arg.split("=", 1)[1]
            if candidate.startswith(name + "="):
                value = candidate.split("=", 1)[1]
    return value


def remove_container_env(args, name):
    result = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"-e", "--env"} and index + 1 < len(args):
            candidate = args[index + 1]
            if candidate == name or candidate.startswith(name + "="):
                index += 2
                continue
        if arg.startswith("--env="):
            candidate = arg.split("=", 1)[1]
            if candidate == name or candidate.startswith(name + "="):
                index += 1
                continue
        result.append(arg)
        index += 1
    args[:] = result


def evaluator_offline_env(args):
    # Evaluator containers cannot answer package-manager confirmation prompts.
    # Override any profile-provided value so installs fail or proceed
    # deterministically instead of aborting because no TTY is attached.
    remove_container_env(args, "CI")
    result = ["-e", "CI=true"]
    if not has_container_env(args, "GOSUMDB"):
        result.extend(["-e", "GOSUMDB=off"])
    if has_container_env(args, "CARGO_HOME") and not has_container_env(args, "CARGO_NET_OFFLINE"):
        result.extend(["-e", "CARGO_NET_OFFLINE=true"])
    return result


def evaluator_go_workspace_args(args):
    gowork = os.environ.get("ECOSYNC_EVALUATOR_GOWORK", "")
    if not gowork:
        return []
    result = []
    if not has_container_env(args, "GOWORK"):
        result.extend(["-e", f"GOWORK={gowork}"])
    if gowork != "off":
        goflags = container_env_value(args, "GOFLAGS")
        if goflags is None and has_container_env(args, "GOFLAGS"):
            goflags = os.environ.get("GOFLAGS", "")
        filtered = [flag for flag in shlex.split(goflags or "") if not flag.startswith("-mod=")]
        remove_container_env(args, "GOFLAGS")
        # Override image-level GOFLAGS as well as explicit docker arguments.
        result.extend(["-e", "GOFLAGS=" + shlex.join(filtered)])
    gomodcache = os.environ.get("ECOSYNC_EVALUATOR_GOMODCACHE", "")
    gomodcache_host = os.environ.get("ECOSYNC_EVALUATOR_GOMODCACHE_HOST", "")
    mounted = volume_destinations(args)
    if gomodcache and gomodcache_host and gomodcache not in mounted:
        result.extend(["-v", f"{gomodcache_host}:{gomodcache}:rw"])
    if gomodcache and not has_container_env(args, "GOMODCACHE"):
        result.extend(["-e", f"GOMODCACHE={gomodcache}"])
    if not has_container_env(args, "GOPROXY"):
        result.extend(["-e", "GOPROXY=off"])
    if not has_container_env(args, "GOSUMDB"):
        result.extend(["-e", "GOSUMDB=off"])
    return result


def option_value(args, names):
    for index, arg in enumerate(args):
        if arg in names and index + 1 < len(args):
            return args[index + 1]
        for name in names:
            prefix = name + "="
            if arg.startswith(prefix):
                return arg[len(prefix):]
    return None


def volume_destinations(args):
    destinations = set()
    index = 0
    while index < len(args):
        arg = args[index]
        value = None
        if arg in {"-v", "--volume", "--tmpfs"} and index + 1 < len(args):
            value = args[index + 1]
            index += 2
        elif arg.startswith("--volume="):
            value = arg.split("=", 1)[1]
            index += 1
        else:
            index += 1
        if value:
            if arg == "--tmpfs":
                destinations.add(value.split(":", 1)[0])
                continue
            parts = value.split(":")
            if len(parts) >= 2:
                destinations.add(parts[1])
    return destinations


def evaluator_dependency_mount_args(args):
    raw = os.environ.get("ECOSYNC_EVALUATOR_DEPENDENCY_MOUNTS", "")
    if not raw:
        return []
    try:
        configured = json.loads(raw)
    except json.JSONDecodeError:
        return []
    workdir = option_value(args, {"-w", "--workdir"})
    if not workdir:
        return []
    mounted = volume_destinations(args)
    result = []
    for item in configured if isinstance(configured, list) else []:
        if not isinstance(item, dict):
            continue
        expected_workdir = str(item.get("workdir") or "").rstrip("/")
        host = str(item.get("host") or "")
        container = str(item.get("container") or "")
        kind = str(item.get("kind") or "persistent")
        if not expected_workdir or not container:
            continue
        normalized_workdir = workdir.rstrip("/")
        if normalized_workdir != expected_workdir and not normalized_workdir.startswith(
            expected_workdir + "/"
        ):
            continue
        if container in mounted:
            continue
        if kind == "tmpfs":
            size = str(item.get("size") or "8g")
            result.extend(["--tmpfs", f"{container}:rw,exec,mode=1777,size={size}"])
        elif kind == "persistent" and host:
            result.extend(["-v", f"{host}:{container}:rw"])
        else:
            continue
        mounted.add(container)
    return result


def route_docker_socket_mounts(args):
    socket_source = os.environ.get("ECOSYNC_DOCKER_SOCKET_SOURCE", "")
    default_socket = "/var/run/docker.sock"
    if not socket_source or socket_source == default_socket:
        return args

    def route_volume(value):
        parts = value.split(":")
        if len(parts) >= 2 and parts[0] == default_socket and parts[1] == default_socket:
            parts[0] = socket_source
        return ":".join(parts)

    def route_mount(value):
        fields = value.split(",")
        destinations = {
            field.split("=", 1)[1]
            for field in fields
            if "=" in field and field.split("=", 1)[0] in {"dst", "destination", "target"}
        }
        if default_socket not in destinations:
            return value
        routed = []
        for field in fields:
            if "=" in field:
                key, field_value = field.split("=", 1)
                if key in {"src", "source"} and field_value == default_socket:
                    field = f"{key}={socket_source}"
            routed.append(field)
        return ",".join(routed)

    result = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"-v", "--volume"} and index + 1 < len(args):
            result.extend([arg, route_volume(args[index + 1])])
            index += 2
            continue
        if arg.startswith("--volume="):
            result.append("--volume=" + route_volume(arg.split("=", 1)[1]))
        elif arg == "--mount" and index + 1 < len(args):
            result.extend([arg, route_mount(args[index + 1])])
            index += 2
            continue
        elif arg.startswith("--mount="):
            result.append("--mount=" + route_mount(arg.split("=", 1)[1]))
        else:
            result.append(arg)
        index += 1
    return result


def run_tracked(command, name):
    proc = subprocess.Popen(command)

    def interrupted(signum, _frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    exit_code = 1
    try:
        while True:
            cli_code = proc.poll()
            if cli_code is not None:
                exit_code = cli_code
                break
            container_code = container_exit_code(name)
            if container_code is not None:
                exit_code = container_code
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break
            time.sleep(1)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        cleanup(name)
    return exit_code


git_mount = f"{git_config}:/etc/gitconfig:ro"
container_name = f"ecosync-evaluator-{os.getpid()}-{time.time_ns()}"[:120]
managed_labels = ["--label", "ecosyncbench.managed=true", "--label", "ecosyncbench.owner=evaluator"]
if original and original[0] in {"run", "create", "compose"}:
    original = [original[0], *route_docker_socket_mounts(original[1:])]
if original and original[0] == "run":
    if is_detached_run(original[1:]):
        os.execv(real_docker, [real_docker, "run", *managed_labels, *original[1:]])
    args = managed_run_args(original[1:])
    offline_env = evaluator_offline_env(args)
    go_workspace = evaluator_go_workspace_args(args)
    dependency_mounts = evaluator_dependency_mount_args(args)
    command = [real_docker, "run", "--name", container_name, *managed_labels, "-v", git_mount, *offline_env, *go_workspace, *dependency_mounts, *args]
    raise SystemExit(run_tracked(command, container_name))
if original and original[0] == "create":
    args = original[1:]
    dependency_mounts = evaluator_dependency_mount_args(args)
    os.execv(real_docker, [real_docker, "create", "-v", git_mount, *dependency_mounts, *args])
if original and original[0] == "compose" and "run" in original[1:]:
    run_index = original.index("run")
    compose_args = original[1:run_index]
    run_args = managed_run_args(original[run_index + 1:])
    if is_detached_run(original[run_index + 1:]):
        os.execv(
            real_docker,
            [real_docker, "compose", *compose_args, "run", *managed_labels, *original[run_index + 1:]],
        )
    offline_env = evaluator_offline_env(run_args)
    go_workspace = evaluator_go_workspace_args(run_args)
    dependency_mounts = evaluator_dependency_mount_args(run_args)
    command = [real_docker, "compose", *compose_args, "run", "--name", container_name, *managed_labels, "-v", git_mount, *offline_env, *go_workspace, *dependency_mounts, *run_args]
    raise SystemExit(run_tracked(command, container_name))
os.execv(real_docker, [real_docker, *original])
""",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    result = env.copy()
    result["ECOSYNC_REAL_DOCKER"] = real_docker
    result["ECOSYNC_EVALUATOR_GIT_CONFIG"] = str(git_config)
    result["ECOSYNC_DOCKER_SHIM_LOG"] = str(shim_log)
    docker_host = env.get("DOCKER_HOST", "")
    if docker_host.startswith("unix://"):
        socket_source = docker_host.removeprefix("unix://")
        if socket_source and socket_source != "/var/run/docker.sock":
            result["ECOSYNC_DOCKER_SOCKET_SOURCE"] = socket_source
    result["PATH"] = f"{shim_dir}:{env.get('PATH', '')}"
    return result


def _run_profile_unlocked(task_id: str, workspace: Path, profile: str, log_file: Path | None = None) -> int:
    script = task_dir(task_id) / "environment" / "run_profile.sh"
    env = os.environ.copy()
    # Evaluator profiles are always non-interactive. Package managers such as
    # pnpm otherwise abort when they need to refresh a mounted modules tree.
    env["CI"] = "true"
    env["COMPOSE_PROJECT_NAME"] = evaluator_compose_project_name(task_id)
    env["ECOSYNC_WORKSPACE"] = str(workspace.resolve())
    if not env.get("ECOSYNC_CACHE_ROOT") and env.get("ECOSYNC_CACHE_BASE_ROOT"):
        env["ECOSYNC_CACHE_ROOT"] = str(
            (Path(env["ECOSYNC_CACHE_BASE_ROOT"]).expanduser() / task_id).resolve()
        )
    dependency_mounts = evaluator_node_modules_mounts(task_id, profile, workspace)
    if dependency_mounts:
        env["ECOSYNC_EVALUATOR_DEPENDENCY_MOUNTS"] = json.dumps(
            dependency_mounts,
            sort_keys=True,
        )
        metadata_path = (
            workspace
            / ".ecosyncbench"
            / "evaluator-dependency-cache"
            / f"{safe_slug(profile)}.json"
        )
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "profile": profile,
                    "mounts": dependency_mounts,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    boxo_module = workspace / "repos/ipfs/boxo/go.mod"
    kubo_module = workspace / "repos/ipfs/kubo/go.mod"
    profiles_path = task_dir(task_id) / "environment" / "test_profiles.yaml"
    profiles_data = load_yaml(profiles_path) if profiles_path.exists() else {}
    profile_data = (
        (profiles_data.get("profiles") or {}).get(profile, {})
        if isinstance(profiles_data, dict)
        else {}
    )
    profile_repo = str(profile_data.get("repo") or "") if isinstance(profile_data, dict) else ""
    if boxo_module.exists() and kubo_module.exists() and profile_repo in {"boxo", "kubo"}:
        if profile_repo == "boxo":
            env["ECOSYNC_EVALUATOR_GOWORK"] = "off"
        else:
            versions: list[tuple[tuple[int, int, int], str]] = []
            for module in (boxo_module, kubo_module):
                text = module.read_text(encoding="utf-8", errors="replace")
                match = re.search(r"^[ \t]*go[ \t]+(\d+(?:\.\d+){1,2})[ \t]*$", text, re.M)
                if match:
                    value = match.group(1)
                    parts = tuple(int(part) for part in value.split("."))
                    versions.append((parts + (0,) * (3 - len(parts)), value))
            go_version = max(versions)[1] if versions else "1.25.0"
            go_work = workspace / ".ecosyncbench" / "kubo-evaluator.work"
            go_work.parent.mkdir(parents=True, exist_ok=True)
            go_work.write_text(
                f"go {go_version}\n\n"
                "use /workspace/repos/ipfs/kubo\n\n"
                "replace github.com/ipfs/boxo => /workspace/repos/ipfs/boxo\n",
                encoding="utf-8",
            )
            env["ECOSYNC_EVALUATOR_GOWORK"] = "/workspace/.ecosyncbench/kubo-evaluator.work"
            cache_root = task_cache_root_from_runner(task_id)
            if cache_root is not None and (cache_root / "agent-go-mod").exists():
                env["ECOSYNC_EVALUATOR_GOMODCACHE_HOST"] = str(cache_root / "agent-go-mod")
                env["ECOSYNC_EVALUATOR_GOMODCACHE"] = "/cache/agent-go-mod"
    env = profile_docker_shim_env(workspace, env)
    timeout_value = env.get("ECOSYNC_PROFILE_TIMEOUT_SEC", "1800")
    timeout_sec = int(timeout_value)
    command = ["bash", str(script), profile]
    code = run_profile_process(command, env, timeout_sec, log_file, "w")
    if code == 0 or log_file is None or not gradle_cache_corruption_in_log(log_file):
        return code
    quarantined = quarantine_corrupt_gradle_script_caches(task_id)
    if not quarantined:
        return code
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write("\n[ecosync] detected corrupt Gradle script cache; quarantined and retrying once:\n")
        for item in quarantined:
            handle.write(f"[ecosync] {item['source']} -> {item['quarantine']}\n")
    return run_profile_process(command, env, timeout_sec, log_file, "a")


def build_env(task_id: str, services: list[str]) -> None:
    script = task_dir(task_id) / "environment" / "build_env.sh"
    if not script.exists():
        raise SystemExit(f"missing environment build script: {script}")
    cmd = [str(script), *services]
    code = subprocess.run(cmd, cwd=REPO_ROOT, text=True).returncode
    raise SystemExit(code)


def check_env(task_id: str) -> None:
    compose_file = task_dir(task_id) / "environment" / "docker-compose.yml"
    if not compose_file.exists():
        lock_file = task_dir(task_id) / "environment" / "deps_image_lock.yaml"
        lock = load_yaml(lock_file) if lock_file.exists() else {}
        case_base = lock.get("case_base_image") if isinstance(lock, dict) else None
        image = None
        if isinstance(case_base, dict):
            image = case_base.get("local_tag") or case_base.get("release_tag")
        if not image:
            raise SystemExit(f"missing environment docker-compose.yml and case_base_image lock: {task_id}")
        proc = subprocess.run(["docker", "image", "inspect", str(image)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        item: dict[str, Any] = {"service": "case_base_image", "image": str(image), "present": proc.returncode == 0}
        if proc.returncode == 0:
            info = json.loads(capture(["docker", "image", "inspect", str(image)]).stdout)[0]
            item["id"] = info.get("Id")
            item["repo_digests"] = info.get("RepoDigests") or []
            item["labels"] = (info.get("Config") or {}).get("Labels") or {}
        print(json.dumps({"task_id": task_id, "ok": item["present"], "images": [item]}, indent=2))
        if not item["present"]:
            raise SystemExit(1)
        return
    compose = load_yaml(compose_file)
    results = []
    for service, spec in compose.get("services", {}).items():
        if "build" not in spec:
            continue
        image = spec.get("image")
        if not image:
            continue
        proc = subprocess.run(["docker", "image", "inspect", image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        item: dict[str, Any] = {"service": service, "image": image, "present": proc.returncode == 0}
        if proc.returncode == 0:
            info = json.loads(capture(["docker", "image", "inspect", image]).stdout)[0]
            item["id"] = info.get("Id")
            item["repo_digests"] = info.get("RepoDigests") or []
            item["labels"] = (info.get("Config") or {}).get("Labels") or {}
        results.append(item)
    ok = all(item["present"] for item in results)
    print(json.dumps({"task_id": task_id, "ok": ok, "images": results}, indent=2))
    if not ok:
        raise SystemExit(1)


def run_profiles(task_id: str, workspace: Path, profiles: list[str], log_dir: Path, prefix: str) -> list[dict[str, Any]]:
    results = []
    for profile in profiles:
        log_file = log_dir / f"{prefix}-{profile}.log"
        before_reports = test_report_snapshot(workspace)
        raw_code = run_profile(task_id, workspace, profile, log_file)
        report_files = changed_test_reports(workspace, before_reports)
        test_summary = parse_test_summary(log_file, workspace=workspace, report_files=report_files)
        report_artifacts = []
        artifact_root = log_dir / "test-reports" / f"{prefix}-{profile}"
        for report_file in report_files:
            if not report_file.is_file():
                continue
            try:
                relative = report_file.relative_to(workspace)
            except ValueError:
                relative = Path(report_file.name)
            artifact = artifact_root / relative
            artifact.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(report_file, artifact)
            report_artifacts.append(str(artifact))
        # A container that exits from a native crash can make bind-mounted
        # reports visible just before their final contents are readable. The
        # artifact copy above gives the mount a chance to settle; retry parsing
        # briefly so completed test cases are not misclassified as missing.
        if test_summary is None and report_files:
            for _ in range(5):
                time.sleep(0.05)
                test_summary = parse_test_summary(
                    log_file,
                    workspace=workspace,
                    report_files=report_files,
                )
                if test_summary is not None:
                    break
        if isinstance(test_summary, dict):
            test_summary["report_artifacts"] = report_artifacts
        timeout_report_failure = bool(
            raw_code == 124
            and isinstance(test_summary, dict)
            and test_summary.get("case_level")
            and (
                int(test_summary.get("failed") or 0) > 0
                or int(test_summary.get("errors") or 0) > 0
                or any(
                    str(case.get("status") or "") in {"failed", "error"}
                    for case in test_summary.get("test_cases") or []
                )
            )
        )
        timeout_log_failure = False
        if raw_code == 124 and log_file.is_file():
            log_tail = log_file.read_text(encoding="utf-8", errors="replace")[-200_000:]
            timeout_log_failure = any(
                marker in log_tail
                for marker in (
                    "-- END OF C++ BACKTRACE --",
                    "GDScript backtrace (most recent call first):",
                    "AddressSanitizer:",
                    "Segmentation fault",
                    "core dumped",
                )
            )
        timeout_reported_failure = timeout_report_failure or timeout_log_failure
        timeout_reported_success = bool(
            raw_code == 124
            and not timeout_reported_failure
            and isinstance(test_summary, dict)
            and test_summary.get("case_level")
            and test_summary.get("count_reliable")
            and int(test_summary.get("total") or 0) > 0
            and int(test_summary.get("failed") or 0) == 0
            and int(test_summary.get("errors") or 0) == 0
            and int(test_summary.get("passed") or 0) + int(test_summary.get("skipped") or 0)
            == int(test_summary.get("total") or 0)
        )
        code = 1 if timeout_reported_failure else 0 if timeout_reported_success else raw_code
        results.append(
            {
                "profile": profile,
                "exit_code": code,
                "raw_exit_code": raw_code,
                "log": str(log_file),
                "passed": code == 0,
                "profile_timeout": raw_code == 124,
                "timeout_recovered_from_test_report": timeout_reported_success,
                "timeout_failure_evidence": (
                    "test_report" if timeout_report_failure else "runtime_crash_log" if timeout_log_failure else None
                ),
                "test_summary": test_summary,
                "report_artifacts": report_artifacts,
            }
        )
    return results


def is_ignored_report_path(path: Path) -> bool:
    ignored_parts = {
        ".git",
        "node_modules",
        ".gradle",
        ".pnpm-store",
        ".yarn",
    }
    return any(part in ignored_parts for part in path.parts)


def candidate_test_report(path: Path) -> bool:
    if path.suffix.lower() not in {".xml", ".json", ".jsonl", ".log", ".trx"} or is_ignored_report_path(path):
        return False
    parts = [part.lower() for part in path.parts]
    for index, part in enumerate(parts[:-1]):
        if part == "target" and parts[index + 1] in {"classes", "test-classes"}:
            return False
    name = path.name.lower()
    path_text = str(path).lower()
    if ".ecosyncbench/test-reports" in path_text:
        return True
    return (
        name.startswith("test-")
        or name.startswith("tests-")
        or name.startswith("junit")
        or "test-results" in path_text
        or "test-reports" in path_text
        or "testresults" in path_text
        or "testreports" in path_text
        or "junit" in path_text
    )


def collect_test_reports(workspace: Path) -> list[Path]:
    return sorted(path for path in workspace.rglob("*") if path.is_file() and candidate_test_report(path))


def test_report_snapshot(workspace: Path) -> dict[Path, tuple[int, int]]:
    snapshot = {}
    for path in collect_test_reports(workspace):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        snapshot[path] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def changed_test_reports(workspace: Path, before: dict[Path, tuple[int, int]]) -> list[Path]:
    changed = []
    for path in collect_test_reports(workspace):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        current = (stat.st_mtime_ns, stat.st_size)
        if before.get(path) != current:
            changed.append(path)
    return changed


def parse_int_attr(node: ET.Element, names: list[str]) -> int:
    for name in names:
        value = node.attrib.get(name)
        if value not in (None, ""):
            try:
                return int(value)
            except ValueError:
                return 0
    return 0


def xml_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def report_path(workspace: Path, path: Path) -> str:
    try:
        return str(path.relative_to(workspace))
    except ValueError:
        return str(path)


def safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def make_test_id(*parts: str | None) -> str:
    cleaned = [part.strip() for part in parts if part and part.strip()]
    return "::".join(cleaned)


def normalize_test_id(test_id: str) -> str:
    """Stabilize runtime-generated path fragments before base/gold matching.

    Some upstream JUnit reporters include full assertion text in testcase names.
    When that assertion text contains mkdtemp-generated paths, identical tests
    can look different between base and gold runs. Normalize only /tmp-style
    six-character mkdtemp suffixes so test identity still reflects the real
    test file/name while ignoring per-run random directories.
    """
    if "/tmp/" not in test_id:
        return test_id
    return TMP_MKDTEMP_SEGMENT_RE.sub(r"\1<TMP>", test_id)


def strip_occurrence_test_id_suffix(test_id: str) -> str:
    """Remove deterministic duplicate occurrence suffixes added during parsing.

    Runtime summaries append ``::#N`` when one report contains repeated ids.
    The hidden oracle may have been generated from a base/gold run where the
    same logical test did not need an occurrence suffix. During evaluation, an
    oracle id without the suffix should still match these concrete runtime rows
    instead of being counted as missing.
    """
    return re.sub(r"::#\d+$", "", test_id)


def canonical_test_id(test_id: str) -> str:
    return strip_occurrence_test_id_suffix(normalize_test_id(test_id))


def normalize_go_test_name(name: str) -> str:
    normalized = re.sub(r"0x[0-9a-fA-F]+", "0xADDR", name)
    normalized = re.sub(r"(https?://(?:127\.0\.0\.1|localhost)):\d+", r"\1:PORT", normalized)
    normalized = re.sub(r"(https?://\[::1\]):\d+", r"\1:PORT", normalized)
    return normalized


def normalize_go_package_name(name: str) -> str:
    """Strip the test-binary suffix used by Go build-fail JSON events."""
    return name.split(" [", 1)[0].strip()


def xml_test_status(node: ET.Element) -> str:
    child_tags = {xml_name(child.tag) for child in list(node)}
    if "error" in child_tags:
        return "error"
    if "failure" in child_tags:
        return "failed"
    if "skipped" in child_tags or "skip" in child_tags:
        return "skipped"
    raw = (node.attrib.get("result") or node.attrib.get("status") or "").lower()
    if raw in {"fail", "failed", "failure"}:
        return "failed"
    if raw == "error":
        return "error"
    if raw in {"skip", "skipped", "pending", "ignored"}:
        return "skipped"
    return "passed"


def xml_testcase_has_collection_failure(node: ET.Element) -> bool:
    for child in list(node):
        if xml_name(child.tag) not in {"error", "failure"}:
            continue
        message = str(child.attrib.get("message") or "").lower()
        text = str(child.text or "").lower()
        if "collection failure" in message or "error collecting" in text or "error collecting" in message:
            return True
    return False


def summarize_test_cases(source: str, report_files: list[str], cases: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {"total": len(cases), "passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for case in cases:
        status = case.get("status")
        if status == "passed":
            totals["passed"] += 1
        elif status == "failed":
            totals["failed"] += 1
        elif status == "error":
            totals["errors"] += 1
        elif status == "skipped":
            totals["skipped"] += 1
    return {
        "source": source,
        **totals,
        "case_level": True,
        "count_reliable": True,
        "report_files": report_files,
        "test_cases": cases,
    }


def unique_test_cases_by_id(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Preserve same-named test cases while still deduplicating duplicate reports.

    Some runners, notably Jest, allow multiple test cases with the same rendered
    name in a file. Report merging must keep those as distinct cases, but JSON
    and JUnit reports for the same run should still collapse onto the same row.
    We therefore append a deterministic occurrence suffix only when an id appears
    more than once in a single report/summary.
    """
    id_counts = Counter(str(item.get("id")) for item in cases if item.get("id"))
    duplicate_ids = {test_id for test_id, count in id_counts.items() if count > 1}
    seen: dict[str, int] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for item in cases:
        raw_id = item.get("id")
        if not raw_id:
            continue
        test_id = str(raw_id)
        case = dict(item)
        if test_id in duplicate_ids:
            seen[test_id] = seen.get(test_id, 0) + 1
            test_id = f"{test_id}::#{seen[test_id]}"
            case["id"] = test_id
        by_id[test_id] = case
    return by_id


def parse_xml_report_counts(root: ET.Element) -> tuple[int, int, int, int]:
    total = failed = skipped = errors = 0

    junit_suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    for suite in junit_suites:
        suite_total = parse_int_attr(suite, ["tests", "total"])
        suite_failed = parse_int_attr(suite, ["failures", "failed"])
        suite_errors = parse_int_attr(suite, ["errors"])
        suite_skipped = parse_int_attr(suite, ["skipped", "skip"])
        if suite_total or suite_failed or suite_errors or suite_skipped:
            total += suite_total
            failed += suite_failed
            errors += suite_errors
            skipped += suite_skipped

    xunit_nodes = []
    if root.tag in {"assembly", "collection"}:
        xunit_nodes.append(root)
    xunit_nodes.extend(root.iter("collection"))
    if not junit_suites:
        for node in xunit_nodes:
            node_total = parse_int_attr(node, ["total", "tests"])
            node_failed = parse_int_attr(node, ["failed", "failures"])
            node_errors = parse_int_attr(node, ["errors"])
            node_skipped = parse_int_attr(node, ["skipped"])
            if node_total or node_failed or node_errors or node_skipped:
                total += node_total
                failed += node_failed
                errors += node_errors
                skipped += node_skipped

    return total, failed, errors, skipped


def parse_junit_xml_file(workspace: Path, xml_file: Path) -> dict[str, Any] | None:
    try:
        root = ET.parse(xml_file).getroot()
    except (ET.ParseError, OSError):
        return None

    report = report_path(workspace, xml_file)
    cases = []
    for node in root.iter():
        if xml_name(node.tag) != "testcase":
            continue
        file_attr = node.attrib.get("file") or node.attrib.get("filename")
        classname = node.attrib.get("classname") or node.attrib.get("class") or node.attrib.get("type")
        name = node.attrib.get("name") or node.attrib.get("method")
        test_id = make_test_id(file_attr, classname, name) or make_test_id(report, name)
        cases.append(
            {
                "id": normalize_test_id(test_id),
                "raw_id": test_id,
                "name": name,
                "classname": classname,
                "file": file_attr,
                "status": xml_test_status(node),
                "collection_failure": xml_testcase_has_collection_failure(node),
                "duration": safe_float(node.attrib.get("time") or node.attrib.get("duration")),
                "source_report": report,
            }
        )
    if cases:
        return summarize_test_cases("junit-xml", [report], cases)

    total, failed, errors, skipped = parse_xml_report_counts(root)
    if total == 0 and failed == 0 and errors == 0 and skipped == 0:
        return None
    return {
        "source": "junit-xml",
        "total": total,
        "passed": max(total - failed - errors - skipped, 0),
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "case_level": False,
        "count_reliable": True,
        "report_files": [report],
    }


def parse_trx_status(value: str | None) -> str:
    raw = (value or "").lower()
    if raw in {"passed", "completed"}:
        return "passed"
    if raw in {"failed", "timeout"}:
        return "failed"
    if raw in {"notexecuted", "not executed", "skipped"}:
        return "skipped"
    return raw or "unknown"


def parse_trx_file(workspace: Path, trx_file: Path) -> dict[str, Any] | None:
    try:
        root = ET.parse(trx_file).getroot()
    except (ET.ParseError, OSError):
        return None

    report = report_path(workspace, trx_file)
    definitions: dict[str, dict[str, Any]] = {}
    for node in root.iter():
        if xml_name(node.tag) != "UnitTest":
            continue
        test_id = node.attrib.get("id")
        method = None
        for child in node.iter():
            if xml_name(child.tag) == "TestMethod":
                method = child
                break
        definitions[str(test_id)] = {
            "name": node.attrib.get("name"),
            "classname": method.attrib.get("className") if method is not None else None,
            "file": method.attrib.get("codeBase") if method is not None else None,
        }

    cases = []
    for node in root.iter():
        if xml_name(node.tag) != "UnitTestResult":
            continue
        test_id = str(node.attrib.get("testId") or "")
        definition = definitions.get(test_id) or {}
        name = node.attrib.get("testName") or definition.get("name")
        classname = definition.get("classname")
        case_id = make_test_id(classname, name) or make_test_id(report, name)
        cases.append(
            {
                "id": case_id,
                "name": name,
                "classname": classname,
                "file": definition.get("file"),
                "status": parse_trx_status(node.attrib.get("outcome")),
                "duration": None,
                "source_report": report,
            }
        )

    if cases:
        return summarize_test_cases("vstest-trx", [report], cases)
    return None


def normalize_report_file_name(workspace: Path, name: str | None) -> str | None:
    if not name:
        return None
    path = Path(name)
    if path.is_absolute():
        try:
            return str(path.relative_to(workspace))
        except ValueError:
            return str(path)
    return name


def parse_jest_json_file(workspace: Path, json_file: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(json_file.read_text(encoding="utf-8", errors="ignore"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("testResults"), list):
        return None

    report = report_path(workspace, json_file)
    cases = []
    for file_result in data.get("testResults") or []:
        if not isinstance(file_result, dict):
            continue
        test_file = normalize_report_file_name(workspace, file_result.get("name"))
        assertions = file_result.get("assertionResults") or []
        if not assertions and str(file_result.get("status") or "").lower() in {"failed", "fail", "error"}:
            message = str(file_result.get("message") or file_result.get("failureMessage") or "").strip()
            cases.append(
                {
                    "id": make_test_id(test_file, "<collection>") or make_test_id(report, "<collection>"),
                    "name": "<collection>",
                    "classname": None,
                    "file": test_file,
                    "status": "error",
                    "duration": safe_float(file_result.get("endTime")),
                    "source_report": report,
                    "collection_failure": True,
                    "message": message[:1000] if message else None,
                }
            )
            continue
        for assertion in assertions:
            if not isinstance(assertion, dict):
                continue
            raw_status = str(assertion.get("status") or "").lower()
            if raw_status in {"passed", "pass"}:
                status = "passed"
            elif raw_status in {"failed", "fail", "failure"}:
                status = "failed"
            elif raw_status in {"pending", "skipped", "todo", "disabled"}:
                status = "skipped"
            else:
                status = raw_status or "unknown"
            full_name = assertion.get("fullName") or " ".join(
                [*map(str, assertion.get("ancestorTitles") or []), str(assertion.get("title") or "")]
            ).strip()
            cases.append(
                {
                    "id": make_test_id(test_file, full_name) or make_test_id(report, full_name),
                    "name": full_name,
                    "classname": None,
                    "file": test_file,
                    "status": status,
                    "duration": safe_float(assertion.get("duration")),
                    "source_report": report,
                }
            )
    if cases:
        return summarize_test_cases("jest-json", [report], cases)

    total = int(data.get("numTotalTests") or 0)
    if not total:
        return None
    failed = int(data.get("numFailedTests") or 0)
    skipped = int(data.get("numPendingTests") or 0) + int(data.get("numTodoTests") or 0)
    return {
        "source": "jest-json",
        "total": total,
        "passed": int(data.get("numPassedTests") or max(total - failed - skipped, 0)),
        "failed": failed,
        "errors": 0,
        "skipped": skipped,
        "case_level": False,
        "count_reliable": True,
        "report_files": [report],
    }


def parse_go_json_file(workspace: Path, json_file: Path) -> dict[str, Any] | None:
    report = report_path(workspace, json_file)
    cases_by_id: dict[str, dict[str, Any]] = {}
    package_failures: dict[str, dict[str, Any]] = {}
    saw_go_event = False
    try:
        lines = json_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or "Action" not in event:
            continue
        saw_go_event = True
        action = event.get("Action")
        package = event.get("Package") or event.get("ImportPath")
        if package:
            package = normalize_go_package_name(str(package))
        test = event.get("Test")
        if test and action in {"pass", "fail", "skip"}:
            status = {"pass": "passed", "fail": "failed", "skip": "skipped"}[action]
            test_name = normalize_go_test_name(str(test))
            case_id = make_test_id(str(package) if package else None, test_name)
            cases_by_id[case_id] = {
                "id": case_id,
                "name": test_name,
                "classname": str(package) if package else None,
                "file": None,
                "status": status,
                "duration": safe_float(event.get("Elapsed")),
                "source_report": report,
            }
        elif not test and action in {"fail", "build-fail"} and package:
            case_id = make_test_id(str(package), "<package-build>")
            package_failures[case_id] = {
                "id": case_id,
                "name": "<package-build>",
                "classname": str(package),
                "file": None,
                "status": "error",
                "duration": safe_float(event.get("Elapsed")),
                "source_report": report,
            }
    if cases_by_id or package_failures:
        summary = summarize_test_cases(
            "go-test-json",
            [report],
            [*cases_by_id.values(), *package_failures.values()],
        )
        if not cases_by_id:
            summary["case_level"] = False
        if package_failures:
            summary["note"] = (
                "One or more Go packages failed before test functions ran; "
                "classified at package-build granularity."
            )
        return summary
    if saw_go_event:
        return {
            "source": "go-test-json",
            "total": 0,
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
            "case_level": False,
            "count_reliable": False,
            "report_files": [report],
        }
    return None


def parse_ginkgo_log_file(workspace: Path, log_file: Path) -> dict[str, Any] | None:
    text = strip_ansi(log_file.read_text(encoding="utf-8", errors="ignore"))
    match = re.search(
        r"Ran\s+(\d+)\s+of\s+(\d+)\s+Specs.*?(\d+)\s+Passed.*?(\d+)\s+Failed.*?(\d+)\s+Pending.*?(\d+)\s+Skipped",
        text,
        flags=re.DOTALL,
    )
    if not match:
        return None
    _ran, total, passed, failed, pending, skipped = [int(value) for value in match.groups()]
    return {
        "source": "ginkgo-log",
        "total": total,
        "passed": passed,
        "failed": failed,
        "errors": 0,
        "skipped": pending + skipped,
        "case_level": False,
        "count_reliable": True,
        "report_files": [report_path(workspace, log_file)],
        "note": "Ginkgo log exposes aggregate counts only; no stable per-spec ids were available.",
    }


def parse_flutter_machine_jsonl_file(workspace: Path, jsonl_file: Path) -> dict[str, Any] | None:
    report = report_path(workspace, jsonl_file)
    suites: dict[int, dict[str, Any]] = {}
    tests: dict[int, dict[str, Any]] = {}
    cases: list[dict[str, Any]] = []
    saw_machine_event = False
    try:
        lines = jsonl_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or "type" not in event:
            continue
        event_type = event.get("type")
        if event_type == "suite" and isinstance(event.get("suite"), dict):
            suite = event["suite"]
            suite_id = suite.get("id")
            if isinstance(suite_id, int):
                suites[suite_id] = suite
            saw_machine_event = True
        elif event_type == "testStart" and isinstance(event.get("test"), dict):
            test = event["test"]
            test_id = test.get("id")
            if isinstance(test_id, int):
                tests[test_id] = test
            saw_machine_event = True
        elif event_type == "testDone":
            test_id = event.get("testID")
            test = tests.get(test_id) if isinstance(test_id, int) else None
            if not test:
                continue
            result = str(event.get("result") or "").lower()
            if event.get("skipped") or result == "skipped":
                status = "skipped"
            elif result in {"success", "pass", "passed"}:
                status = "passed"
            elif result in {"failure", "fail", "failed"}:
                status = "failed"
            elif result == "error":
                status = "error"
            else:
                status = result or "unknown"
            suite = suites.get(test.get("suiteID")) if isinstance(test.get("suiteID"), int) else None
            test_file = normalize_report_file_name(workspace, suite.get("path")) if suite else None
            name = str(test.get("name") or f"test-{test_id}")
            cases.append(
                {
                    "id": make_test_id(test_file, name) or make_test_id(report, name),
                    "name": name,
                    "classname": None,
                    "file": test_file,
                    "status": status,
                    "duration": safe_float(event.get("time")),
                    "source_report": report,
                }
            )
            saw_machine_event = True
    if cases:
        return summarize_test_cases("flutter-machine-jsonl", [report], cases)
    if saw_machine_event:
        return {
            "source": "flutter-machine-jsonl",
            "total": 0,
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
            "case_level": False,
            "count_reliable": False,
            "report_files": [report],
        }
    return None


def combine_test_summaries(summaries: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not summaries:
        return None
    report_files: list[str] = []
    sources: list[str] = []
    test_cases_by_id: dict[str, dict[str, Any]] = {}
    totals = {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    count_reliable = True
    notes: list[str] = []
    for summary in summaries:
        report_files.extend(summary.get("report_files") or [])
        source = summary.get("source")
        if source and source not in sources:
            sources.append(source)
        for key in totals:
            value = summary.get(key)
            if isinstance(value, int):
                totals[key] += value
        for case_id, case in unique_test_cases_by_id(summary.get("test_cases") or []).items():
            test_cases_by_id[case_id] = case
        count_reliable = count_reliable and bool(summary.get("count_reliable", False))
        if summary.get("note"):
            notes.append(str(summary["note"]))

    if test_cases_by_id:
        combined = summarize_test_cases("+".join(sources), report_files, list(test_cases_by_id.values()))
        combined["count_reliable"] = count_reliable
    else:
        combined = {
            "source": "+".join(sources),
            **totals,
            "case_level": False,
            "count_reliable": count_reliable,
            "report_files": report_files,
        }
    if notes:
        combined["notes"] = sorted(set(notes))
    return combined


def parse_report_files_summary(workspace: Path, report_files: list[Path]) -> dict[str, Any] | None:
    summaries: list[dict[str, Any]] = []
    for report_file in report_files:
        parsed = None
        if report_file.suffix.lower() == ".xml":
            parsed = parse_junit_xml_file(workspace, report_file)
        elif report_file.suffix.lower() == ".trx":
            parsed = parse_trx_file(workspace, report_file)
        elif report_file.suffix.lower() == ".json":
            parsed = parse_jest_json_file(workspace, report_file) or parse_go_json_file(workspace, report_file)
        elif report_file.suffix.lower() == ".log":
            parsed = parse_ginkgo_log_file(workspace, report_file)
        elif report_file.suffix.lower() == ".jsonl":
            parsed = parse_flutter_machine_jsonl_file(workspace, report_file)
        if parsed is not None:
            summaries.append(parsed)
    return combine_test_summaries(summaries)


def parse_junit_xml_summary(workspace: Path, report_files: list[Path]) -> dict[str, Any] | None:
    return combine_test_summaries(
        [summary for report in report_files if (summary := parse_junit_xml_file(workspace, report)) is not None]
    )


def parse_test_summary(log_file: Path, workspace: Path, report_files: list[Path] | None = None) -> dict[str, Any] | None:
    if not log_file.exists():
        return None
    text = log_file.read_text(encoding="utf-8", errors="ignore")

    if report_files:
        report_summary = parse_report_files_summary(workspace, report_files)
        if report_summary is not None:
            return report_summary

    dotnet = re.findall(
        r"(Passed|Failed)!\s+-\s+Failed:\s+(\d+),\s+Passed:\s+(\d+),\s+Skipped:\s+(\d+),\s+Total:\s+(\d+)",
        text,
    )
    if dotnet:
        _, failed, passed, skipped, total = dotnet[-1]
        return {
            "source": "dotnet",
            "total": int(total),
            "passed": int(passed),
            "failed": int(failed),
            "skipped": int(skipped),
            "case_level": False,
            "count_reliable": True,
        }

    gradle = re.findall(r"(\d+)\s+tests completed(?:,\s+(\d+)\s+failed)?(?:,\s+(\d+)\s+skipped)?", text)
    if gradle:
        total, failed, skipped = gradle[-1]
        total_i = int(total)
        failed_i = int(failed or 0)
        skipped_i = int(skipped or 0)
        return {
            "source": "gradle",
            "total": total_i,
            "passed": max(total_i - failed_i - skipped_i, 0),
            "failed": failed_i,
            "skipped": skipped_i,
            "case_level": False,
            "count_reliable": True,
        }

    if "BUILD SUCCESSFUL" in text:
        return {
            "source": "gradle",
            "status": "build_successful",
            "total": None,
            "passed": None,
            "failed": 0,
            "skipped": None,
            "case_level": False,
            "count_reliable": False,
        }

    if "BUILD FAILED" in text:
        return {
            "source": "gradle",
            "status": "build_failed",
            "total": None,
            "passed": None,
            "failed": None,
            "skipped": None,
            "case_level": False,
            "count_reliable": False,
        }

    cargo_cases: list[dict[str, Any]] = []
    for match in re.finditer(r"^test\s+(.+?)\s+\.\.\.\s+(ok|FAILED|ignored)\s*$", text, flags=re.MULTILINE):
        name = match.group(1).strip()
        status_text = match.group(2)
        status = {"ok": "passed", "FAILED": "failed", "ignored": "skipped"}[status_text]
        cargo_cases.append(
            {
                "id": f"cargo::{name}",
                "name": name,
                "classname": None,
                "file": None,
                "status": status,
                "duration": None,
                "source_report": str(log_file.name),
            }
        )
    cargo_totals = re.findall(
        r"test result:\s+(ok|FAILED)\.\s+(\d+)\s+passed;\s+(\d+)\s+failed;\s+(\d+)\s+ignored;",
        text,
    )
    if cargo_cases:
        summary = summarize_test_cases("cargo-test-text", [str(log_file.name)], cargo_cases)
        summary["count_reliable"] = bool(cargo_totals)
        return summary
    if cargo_totals:
        _, passed, failed, skipped = cargo_totals[-1]
        total = int(passed) + int(failed) + int(skipped)
        return {
            "source": "cargo-test-text",
            "total": total,
            "passed": int(passed),
            "failed": int(failed),
            "skipped": int(skipped),
            "case_level": False,
            "count_reliable": True,
        }

    node_tap_cases: list[dict[str, Any]] = []
    node_tap_profile_match = re.match(
        r"(?:base|gold|agent)-hidden-test-(.+)\.log$",
        log_file.name,
    )
    node_tap_profile = node_tap_profile_match.group(1) if node_tap_profile_match else None
    if "TAP version" in text and node_tap_profile:
        for match in re.finditer(
            r"^(not )?ok\s+\d+\s+(?:-\s+)?([^\r\n]+?)\s*$",
            text,
            flags=re.MULTILINE,
        ):
            failed_marker, raw_name = match.groups()
            name = re.sub(r"\s+#\s+(?:SKIP|TODO)\b.*$", "", raw_name, flags=re.IGNORECASE).strip()
            if not name.startswith(("parallel/", "sequential/", "internet/", "pummel/")):
                continue
            directive = raw_name[len(name):].upper()
            status = "skipped" if "# SKIP" in directive else "failed" if failed_marker else "passed"
            file_name = name if Path(name).suffix else f"{name}.js"
            node_tap_cases.append(
                {
                    "id": make_test_id(file_name, node_tap_profile, name),
                    "name": name,
                    "classname": node_tap_profile,
                    "file": file_name,
                    "status": status,
                    "duration": None,
                    "source_report": str(log_file.name),
                }
            )
    if node_tap_cases:
        summary = summarize_test_cases("node-core-tap", [str(log_file.name)], node_tap_cases)
        plan = re.findall(r"^1\.\.(\d+)\s*$", text, flags=re.MULTILINE)
        summary["count_reliable"] = bool(plan and int(plan[-1]) == len(node_tap_cases))
        return summary

    jest = re.findall(
        r"Tests:\s+(?:(\d+)\s+failed,\s+)?(?:(\d+)\s+skipped,\s+)?(?:(\d+)\s+passed,\s+)?(\d+)\s+total",
        text,
    )
    if jest:
        failed, skipped, passed, total = jest[-1]
        return {
            "source": "jest",
            "total": int(total),
            "passed": int(passed or 0),
            "failed": int(failed or 0),
            "skipped": int(skipped or 0),
            "case_level": False,
            "count_reliable": True,
        }

    return None


def profile_groups(profile_set: dict[str, Any]) -> dict[str, list[str]]:
    hidden_tests = list(profile_set.get("hidden_tests", []))
    cross_repo_contract = list(profile_set.get("cross_repo_contract", []))

    # Backward compatibility for older case manifests that used one mixed
    # "hidden" list for both evaluator-only tests and static contract oracles.
    if not hidden_tests and not cross_repo_contract and "hidden" in profile_set:
        for profile in profile_set.get("hidden", []):
            if profile == "static-contract" or profile.endswith("-contract"):
                cross_repo_contract.append(profile)
            else:
                hidden_tests.append(profile)

    return {
        "hidden_tests": hidden_tests,
        "cross_repo_contract": cross_repo_contract,
    }


def profile_repos_by_name(task_id: str) -> dict[str, str]:
    mapping = {}
    for item in load_harness(task_id).get("hidden_test_patches") or []:
        profile = item.get("test_profile")
        repo = item.get("repo")
        if profile and repo:
            mapping[str(profile)] = str(repo)
    return mapping


def scoped_profile_groups(
    task_id: str,
    profile_set_data: dict[str, Any],
    workspace_scope: str,
    single_repo: str | None,
) -> dict[str, list[str]]:
    groups = profile_groups(profile_set_data)
    if workspace_scope != "single-repo":
        return groups

    repo_by_profile = profile_repos_by_name(task_id)
    return {
        "hidden_tests": [
            profile
            for profile in groups["hidden_tests"]
            if repo_by_profile.get(profile) == single_repo
        ],
        "cross_repo_contract": [],
    }


def load_profile_set(harness: dict[str, Any], profile_set: str) -> dict[str, Any]:
    profile_sets = harness.get("profile_sets") or {}
    if profile_set not in profile_sets:
        available = ", ".join(sorted(profile_sets)) or "<none>"
        raise SystemExit(f"unknown profile set '{profile_set}'. Available profile sets: {available}")
    return profile_sets[profile_set]


def profile_result_by_name(profile_results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("profile")): item for item in profile_results}


def testcase_by_id(profile_result: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not profile_result:
        return {}
    summary = profile_result.get("test_summary")
    if not isinstance(summary, dict):
        return {}
    cases = summary.get("test_cases") or []
    return unique_test_cases_by_id(cases)


def oracle_case_row(test_id: str, base_case: dict[str, Any] | None, gold_case: dict[str, Any] | None) -> dict[str, Any]:
    source = gold_case or base_case or {}
    return {
        "id": test_id,
        "name": source.get("name"),
        "classname": source.get("classname"),
        "file": source.get("file"),
        "base_status": (base_case or {}).get("status"),
        "gold_status": (gold_case or {}).get("status"),
        "source_report": source.get("source_report"),
    }


def profile_level_oracle_row(profile: str, base_result: dict[str, Any] | None, gold_result: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "id": f"profile::{profile}",
        "name": profile,
        "classname": None,
        "file": None,
        "base_status": "passed" if base_result and base_result.get("passed") else "failed",
        "gold_status": "passed" if gold_result and gold_result.get("passed") else "failed",
        "source_report": None,
        "granularity": "profile",
    }


def profile_failed_before_reporting_test_cases(profile_result: dict[str, Any] | None) -> bool:
    if not profile_result or profile_result.get("passed"):
        return False
    summary = profile_result.get("test_summary")
    if summary is None:
        return False
    if not isinstance(summary, dict) or not summary.get("case_level"):
        return False
    cases = summary.get("test_cases") or []
    if not cases:
        return True
    for case in cases:
        name = str(case.get("name") or "")
        test_id = str(case.get("id") or "")
        if case.get("collection_failure"):
            continue
        if name != "<package-build>" and "<package-build>" not in test_id:
            return False
    return True


def build_failed_reports(profile_result: dict[str, Any] | None) -> set[str]:
    """Return report files where base reported package/collection build failures.

    A profile can emit concrete test cases for some packages and package-build
    errors for other packages. Gold tests from the failed package report should
    count as concrete F2P rows, not missing_in_base.
    """
    if not isinstance(profile_result, dict) or profile_result.get("passed"):
        return set()
    summary = profile_result.get("test_summary")
    if not isinstance(summary, dict) or not summary.get("case_level"):
        return set()
    reports: set[str] = set()
    for case in summary.get("test_cases") or []:
        name = str(case.get("name") or "")
        test_id = str(case.get("id") or "")
        if case.get("collection_failure") or name == "<package-build>" or "<package-build>" in test_id:
            report = case.get("source_report")
            if isinstance(report, str) and report:
                reports.add(report)
    return reports


def classify_hidden_profile_oracle(
    profile: str,
    base_result: dict[str, Any] | None,
    gold_result: dict[str, Any] | None,
) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {
        "fail_to_pass": [],
        "pass_to_pass": [],
        "fail_to_fail": [],
        "pass_to_fail": [],
        "skipped": [],
        "missing_in_base": [],
        "missing_in_gold": [],
    }

    base_cases = testcase_by_id(base_result)
    gold_cases = testcase_by_id(gold_result)
    base_summary = (base_result or {}).get("test_summary") if isinstance(base_result, dict) else None
    gold_summary = (gold_result or {}).get("test_summary") if isinstance(gold_result, dict) else None
    base_case_level = bool(isinstance(base_summary, dict) and base_summary.get("case_level") and base_cases)
    gold_case_level = bool(isinstance(gold_summary, dict) and gold_summary.get("case_level") and gold_cases)
    case_level = base_case_level or gold_case_level

    if case_level:
        base_failed_before_cases = profile_failed_before_reporting_test_cases(base_result)
        base_failed_reports = build_failed_reports(base_result)
        for test_id in sorted(set(base_cases) | set(gold_cases)):
            base_case = base_cases.get(test_id)
            gold_case = gold_cases.get(test_id)
            row = oracle_case_row(test_id, base_case, gold_case)
            base_status = row["base_status"]
            gold_status = row["gold_status"]
            if base_case is None:
                gold_report = (gold_case or {}).get("source_report") if isinstance(gold_case, dict) else None
                if gold_status == "skipped":
                    row["classification_note"] = (
                        "Gold reported this test only as skipped while base did not report the same concrete test id; "
                        "skipped-only missing rows are tracked as skipped instead of hard missing tests."
                    )
                    groups["skipped"].append(row)
                elif gold_status == "passed" and (
                    base_failed_before_cases
                    or (isinstance(gold_report, str) and gold_report in base_failed_reports)
                ):
                    row["base_status"] = "not_reported_failed_profile"
                    if base_failed_before_cases:
                        row["classification_note"] = (
                            "Gold reported this concrete test as passed, while base failed during collection/build "
                            "before reporting individual test cases."
                        )
                    else:
                        row["classification_note"] = (
                            "Gold reported this concrete test as passed, while base reported a package build/collection "
                            "failure for the same test report."
                        )
                    groups["fail_to_pass"].append(row)
                else:
                    groups["missing_in_base"].append(row)
            elif gold_case is None:
                if base_status == "skipped":
                    row["classification_note"] = (
                        "Base reported this test only as skipped while gold did not report the same concrete test id; "
                        "skipped-only missing rows are tracked as skipped instead of hard missing tests."
                    )
                    groups["skipped"].append(row)
                elif base_status in {"failed", "error", "unknown"} and (gold_result or {}).get("passed"):
                    row["gold_status"] = "not_reported_passed_profile"
                    row["classification_note"] = "Base reported a failing collection/build-level item that disappeared after the gold patch passed."
                    groups["skipped"].append(row)
                else:
                    groups["missing_in_gold"].append(row)
            elif base_status in {"failed", "error", "unknown"} and gold_status == "passed":
                groups["fail_to_pass"].append(row)
            elif base_status == "passed" and gold_status == "passed":
                groups["pass_to_pass"].append(row)
            elif base_status in {"failed", "error", "unknown"} and gold_status in {"failed", "error", "unknown"}:
                groups["fail_to_fail"].append(row)
            elif base_status == "passed" and gold_status in {"failed", "error", "unknown"}:
                groups["pass_to_fail"].append(row)
            elif base_status == "skipped" or gold_status == "skipped":
                groups["skipped"].append(row)
    else:
        row = profile_level_oracle_row(profile, base_result, gold_result)
        if row["base_status"] != "passed" and row["gold_status"] == "passed":
            groups["fail_to_pass"].append(row)
        elif row["base_status"] == "passed" and row["gold_status"] == "passed":
            groups["pass_to_pass"].append(row)
        elif row["base_status"] != "passed" and row["gold_status"] != "passed":
            groups["fail_to_fail"].append(row)
        else:
            groups["pass_to_fail"].append(row)

    counts = {name: len(items) for name, items in groups.items()}
    return {
        "profile": profile,
        "case_level": case_level,
        "base_exit_code": (base_result or {}).get("exit_code"),
        "gold_exit_code": (gold_result or {}).get("exit_code"),
        "base_passed": bool(base_result and base_result.get("passed")),
        "gold_passed": bool(gold_result and gold_result.get("passed")),
        "counts": counts,
        **groups,
    }


def build_hidden_test_oracle(
    task_id: str,
    profile_set: str,
    hidden_profiles: list[str],
    base_hidden_results: list[dict[str, Any]],
    gold_hidden_results: list[dict[str, Any]],
) -> dict[str, Any]:
    base_by_name = profile_result_by_name(base_hidden_results)
    gold_by_name = profile_result_by_name(gold_hidden_results)
    profile_to_repo = profile_repos_by_name(task_id)
    profiles = [
        classify_hidden_profile_oracle(profile, base_by_name.get(profile), gold_by_name.get(profile))
        for profile in hidden_profiles
    ]
    for profile in profiles:
        profile["repo"] = profile_to_repo.get(str(profile.get("profile")))
        native_f2p = len(profile.get("fail_to_pass") or [])
        m2p = len(missing_to_pass_rows(profile))
        profile["scoring_counts"] = {
            "native_fail_to_pass": native_f2p,
            "missing_to_pass": m2p,
            "fail_to_pass": native_f2p + m2p,
            "pass_to_pass": len(profile.get("pass_to_pass") or []),
        }
    totals = {
        "fail_to_pass": sum(item["counts"]["fail_to_pass"] for item in profiles),
        "pass_to_pass": sum(item["counts"]["pass_to_pass"] for item in profiles),
        "fail_to_fail": sum(item["counts"]["fail_to_fail"] for item in profiles),
        "pass_to_fail": sum(item["counts"]["pass_to_fail"] for item in profiles),
        "skipped": sum(item["counts"]["skipped"] for item in profiles),
        "missing_in_base": sum(item["counts"]["missing_in_base"] for item in profiles),
        "missing_in_gold": sum(item["counts"]["missing_in_gold"] for item in profiles),
    }
    scoring_totals = {
        key: sum(int(item["scoring_counts"][key]) for item in profiles)
        for key in ("native_fail_to_pass", "missing_to_pass", "fail_to_pass", "pass_to_pass")
    }
    unscored_missing_in_base = totals["missing_in_base"] - scoring_totals["missing_to_pass"]
    return {
        "schema_version": 2,
        "task_id": task_id,
        "profile_set": profile_set,
        "oracle_type": "swe-bench-style-hidden-tests",
        "description": "Derived by comparing base+hidden-test-patch with gold+hidden-test-patch.",
        "totals": totals,
        "scoring_policy": {
            "fail_to_pass": "native_fail_to_pass + missing_to_pass(gold_status=passed)",
            "pass_to_pass": "all fixed pass_to_pass rows; an empty set passes vacuously",
        },
        "scoring_totals": scoring_totals,
        "profiles": profiles,
        "ok": (
            scoring_totals["fail_to_pass"] > 0
            and totals["fail_to_fail"] == 0
            and totals["pass_to_fail"] == 0
            and unscored_missing_in_base == 0
            and totals["missing_in_gold"] == 0
        ),
    }


def write_hidden_test_oracle(task_id: str, oracle: dict[str, Any]) -> Path:
    oracle_dir = task_dir(task_id) / "oracles"
    oracle_dir.mkdir(parents=True, exist_ok=True)
    oracle_file = oracle_dir / "hidden_test_oracle.json"
    oracle_file.write_text(json.dumps(oracle, indent=2) + "\n", encoding="utf-8")
    return oracle_file


def load_hidden_test_oracle(
    task_id: str,
    workspace_scope: str = "ecosystem",
    single_repo: str | None = None,
) -> dict[str, Any] | None:
    oracle_file = task_dir(task_id) / "oracles" / "hidden_test_oracle.json"
    if not oracle_file.exists():
        return None
    return json.loads(oracle_file.read_text(encoding="utf-8"))


def verify_scoring_manifest(task_id: str) -> dict[str, Any] | None:
    manifest_file = task_dir(task_id) / "oracles" / "scoring_manifest.json"
    if not manifest_file.exists():
        return None
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    oracle_file = task_dir(task_id) / str((manifest.get("oracle") or {}).get("path") or "oracles/hidden_test_oracle.json")
    expected_sha256 = str((manifest.get("oracle") or {}).get("sha256") or "")
    actual_sha256 = hashlib.sha256(oracle_file.read_bytes()).hexdigest()
    if not expected_sha256 or actual_sha256 != expected_sha256:
        raise SystemExit(
            f"scoring manifest oracle checksum mismatch for {task_id}: "
            f"expected {expected_sha256 or '<missing>'}, got {actual_sha256}"
        )
    if not manifest.get("valid"):
        raise SystemExit(f"invalid scoring manifest for {task_id}: {manifest.get('errors') or []}")
    return {
        "path": str(manifest_file),
        "schema_version": manifest.get("schema_version"),
        "oracle_sha256": actual_sha256,
        "verified": True,
        "totals": manifest.get("totals") or {},
        "repos": manifest.get("repos") or [],
    }


def filter_hidden_test_oracle(oracle: dict[str, Any], profiles: list[str]) -> dict[str, Any]:
    selected = set(profiles)
    filtered_profiles = [
        profile for profile in oracle.get("profiles") or [] if str(profile.get("profile")) in selected
    ]
    totals = {
        "fail_to_pass": sum(len(item.get("fail_to_pass") or []) for item in filtered_profiles),
        "pass_to_pass": sum(len(item.get("pass_to_pass") or []) for item in filtered_profiles),
        "fail_to_fail": sum(len(item.get("fail_to_fail") or []) for item in filtered_profiles),
        "pass_to_fail": sum(len(item.get("pass_to_fail") or []) for item in filtered_profiles),
        "skipped": sum(len(item.get("skipped") or []) for item in filtered_profiles),
        "missing_in_base": sum(len(item.get("missing_in_base") or []) for item in filtered_profiles),
        "missing_in_gold": sum(len(item.get("missing_in_gold") or []) for item in filtered_profiles),
    }
    filtered = dict(oracle)
    filtered["profiles"] = filtered_profiles
    filtered["totals"] = totals
    filtered["scope_filtered"] = True
    filtered["selected_profiles"] = sorted(selected)
    return filtered


def status_for_oracle_row(profile_result: dict[str, Any] | None, row: dict[str, Any]) -> str | None:
    match = match_oracle_row(profile_result, row)
    return match.get("status") if match else None


def aggregate_matched_status(cases: list[dict[str, Any]]) -> str | None:
    statuses = [str(case.get("status") or "") for case in cases if case.get("status")]
    if not statuses:
        return None
    if any(status in {"failed", "error", "unknown"} for status in statuses):
        return "failed"
    if all(status == "passed" for status in statuses):
        return "passed"
    if any(status == "skipped" for status in statuses):
        return "skipped"
    return statuses[0]


def match_oracle_row(profile_result: dict[str, Any] | None, row: dict[str, Any]) -> dict[str, Any] | None:
    if not profile_result:
        return None
    if row.get("granularity") == "profile":
        return {
            "status": "passed" if profile_result.get("passed") else "failed",
            "matched_id": row.get("id"),
            "match_type": "profile",
        }

    row_id = str(row.get("id") or "")
    if not row_id:
        return None

    cases = testcase_by_id(profile_result)
    exact = cases.get(row_id)
    if exact:
        return {
            "status": exact.get("status"),
            "matched_id": exact.get("id"),
            "match_type": "exact",
        }

    row_canonical = canonical_test_id(row_id)
    canonical_matches = [
        case for case_id, case in cases.items() if canonical_test_id(str(case_id)) == row_canonical
    ]
    if canonical_matches:
        return {
            "status": aggregate_matched_status(canonical_matches),
            "matched_id": ",".join(str(case.get("id") or "") for case in canonical_matches[:3]),
            "match_type": "canonical_occurrence_suffix",
            "matched_count": len(canonical_matches),
        }

    summary = profile_result.get("test_summary")
    if isinstance(summary, dict) and summary.get("source") == "go-test-json":
        row_package = str(row.get("classname") or row_id.split("::", 1)[0])
        for case in cases.values():
            case_id = str(case.get("id") or "")
            if case.get("name") != "<package-build>" and "<package-build>" not in case_id:
                continue
            failed_package = str(case.get("classname") or case_id.split("::", 1)[0])
            if normalize_go_package_name(row_package) == normalize_go_package_name(failed_package):
                return {
                    "status": "failed",
                    "matched_id": case_id,
                    "match_type": "go_package_build_failure",
                }
    return None


def score_oracle_rows(profile_result: dict[str, Any] | None, rows: list[dict[str, Any]]) -> dict[str, Any]:
    details = []
    passed = 0
    missing = 0
    for row in rows:
        match = match_oracle_row(profile_result, row)
        status = match.get("status") if match else None
        ok = status == "passed"
        if ok:
            passed += 1
        if status is None:
            missing += 1
        detail = {
            "id": row.get("id"),
            "name": row.get("name"),
            "file": row.get("file"),
            "expected": "passed",
            "actual": status,
            "passed": ok,
        }
        if match:
            detail["matched_id"] = match.get("matched_id")
            detail["match_type"] = match.get("match_type")
            if match.get("matched_count") is not None:
                detail["matched_count"] = match.get("matched_count")
        details.append(detail)
    total = len(rows)
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "missing": missing,
        "ok": passed == total,
        "details": details,
    }


def missing_to_pass_rows(profile_oracle: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in profile_oracle.get("missing_in_base") or []
        if row.get("gold_status") == "passed"
    ]


def add_oracle_scores(target: dict[str, int], scores: dict[str, Any]) -> None:
    for key in ("total", "passed", "failed", "missing"):
        target[key] += int(scores.get(key) or 0)


def add_repo_oracle_scores(
    target: dict[str, Any],
    scores: dict[str, Any],
    profile: str,
) -> None:
    add_oracle_scores(target, scores)
    details = target.setdefault("details", [])
    details.extend({**item, "profile": profile} for item in scores.get("details") or [])


def evaluate_hidden_test_oracle(
    hidden_test_results: list[dict[str, Any]],
    oracle: dict[str, Any],
    profile_to_repo: dict[str, str] | None = None,
) -> dict[str, Any]:
    by_name = profile_result_by_name(hidden_test_results)
    profile_rows = []
    totals = {
        "fail_to_pass": {"total": 0, "passed": 0, "failed": 0, "missing": 0},
        "native_fail_to_pass": {"total": 0, "passed": 0, "failed": 0, "missing": 0},
        "missing_to_pass": {"total": 0, "passed": 0, "failed": 0, "missing": 0},
        "pass_to_pass": {"total": 0, "passed": 0, "failed": 0, "missing": 0},
    }
    by_repo: dict[str, dict[str, Any]] = {}
    for profile_oracle in oracle.get("profiles") or []:
        profile = str(profile_oracle.get("profile"))
        repo = str(profile_oracle.get("repo") or (profile_to_repo or {}).get(profile) or profile)
        result = by_name.get(profile)
        m2p_rows = missing_to_pass_rows(profile_oracle)
        native_f2p = score_oracle_rows(result, profile_oracle.get("fail_to_pass") or [])
        m2p = score_oracle_rows(result, m2p_rows)
        f2p = score_oracle_rows(
            result,
            [*(profile_oracle.get("fail_to_pass") or []), *m2p_rows],
        )
        p2p = score_oracle_rows(result, profile_oracle.get("pass_to_pass") or [])
        for key, scores in [
            ("fail_to_pass", f2p),
            ("native_fail_to_pass", native_f2p),
            ("missing_to_pass", m2p),
            ("pass_to_pass", p2p),
        ]:
            add_oracle_scores(totals[key], scores)
        repo_row = by_repo.setdefault(
            repo,
            {
                "profiles": [],
                "fail_to_pass": {"total": 0, "passed": 0, "failed": 0, "missing": 0},
                "native_fail_to_pass": {"total": 0, "passed": 0, "failed": 0, "missing": 0},
                "missing_to_pass": {"total": 0, "passed": 0, "failed": 0, "missing": 0},
                "pass_to_pass": {"total": 0, "passed": 0, "failed": 0, "missing": 0},
            },
        )
        repo_row["profiles"].append(profile)
        for key, scores in [
            ("fail_to_pass", f2p),
            ("native_fail_to_pass", native_f2p),
            ("missing_to_pass", m2p),
            ("pass_to_pass", p2p),
        ]:
            add_repo_oracle_scores(repo_row[key], scores, profile)
        profile_rows.append(
            {
                "profile": profile,
                "repo": repo,
                "case_level": profile_oracle.get("case_level"),
                "profile_exit_code": (result or {}).get("exit_code"),
                "profile_passed": bool(result and result.get("passed")),
                "fail_to_pass": f2p,
                "native_fail_to_pass": native_f2p,
                "missing_to_pass": m2p,
                "pass_to_pass": p2p,
            }
        )

    fail_to_pass_ok = totals["fail_to_pass"]["total"] > 0 and totals["fail_to_pass"]["failed"] == 0
    pass_to_pass_ok = totals["pass_to_pass"]["failed"] == 0
    for repo_row in by_repo.values():
        repo_row["fail_to_pass_passed"] = (
            repo_row["fail_to_pass"]["total"] > 0 and repo_row["fail_to_pass"]["failed"] == 0
        )
        repo_row["pass_to_pass_passed"] = repo_row["pass_to_pass"]["failed"] == 0
        repo_row["ok"] = repo_row["fail_to_pass_passed"] and repo_row["pass_to_pass_passed"]
    return {
        "oracle_type": oracle.get("oracle_type"),
        "oracle_profile_set": oracle.get("profile_set"),
        "scoring_policy": {
            "fail_to_pass": "native_fail_to_pass + missing_to_pass(gold_status=passed)",
            "pass_to_pass": "all fixed pass_to_pass rows; an empty set passes vacuously",
        },
        "fail_to_pass": totals["fail_to_pass"],
        "native_fail_to_pass": totals["native_fail_to_pass"],
        "missing_to_pass": totals["missing_to_pass"],
        "pass_to_pass": totals["pass_to_pass"],
        "fail_to_pass_passed": fail_to_pass_ok,
        "pass_to_pass_passed": pass_to_pass_ok,
        "profiles": profile_rows,
        "by_repo": by_repo,
        "ok": fail_to_pass_ok and pass_to_pass_ok,
    }


def validate_matrix(task_id: str, workdir: Path, profile_set: str, force: bool) -> None:
    harness = load_harness(task_id)
    profiles = profile_groups(load_profile_set(harness, profile_set))
    hidden_test_profiles = profiles["hidden_tests"]
    contract_profiles = profiles["cross_repo_contract"]

    if force and workdir.exists():
        remove_tree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    results: dict[str, Any] = {"task_id": task_id, "profile_set": profile_set, "steps": []}

    def step(name: str, workspace: Path, step_profiles: list[str], expected: str) -> None:
        if not step_profiles:
            results["steps"].append({"name": name, "expected": expected, "ok": True, "profiles": []})
            return
        profile_results = run_profiles(task_id, workspace, step_profiles, workdir / "logs", name)
        if expected == "pass":
            ok = all(item["passed"] for item in profile_results)
        elif expected == "fail":
            ok = any(not item["passed"] for item in profile_results)
        else:
            raise SystemExit(f"unknown expectation: {expected}")
        results["steps"].append({"name": name, "expected": expected, "ok": ok, "profiles": profile_results})

    base_hidden = workdir / "base-hidden"
    prepare_workspace(task_id, base_hidden, "base", force=True, workspace_scope="involved")
    apply_hidden(
        task_id,
        base_hidden,
        allow_already_applied=True,
        profiles=hidden_test_profiles,
        prefer_clean_overlay=True,
    )
    step("base-hidden-tests", base_hidden, hidden_test_profiles, "fail")
    step("base-cross-repo-contract", base_hidden, contract_profiles, "fail")

    gold = workdir / "gold"
    prepare_workspace(task_id, gold, "base", force=True, workspace_scope="involved")
    apply_gold(task_id, gold, allow_already_applied=True)
    apply_hidden(
        task_id,
        gold,
        allow_already_applied=True,
        profiles=hidden_test_profiles,
        prefer_clean_overlay=True,
    )
    step("gold-hidden-tests", gold, hidden_test_profiles, "pass")
    step("gold-cross-repo-contract", gold, contract_profiles, "pass")

    step_by_name = {item["name"]: item for item in results["steps"]}
    hidden_oracle = build_hidden_test_oracle(
        task_id,
        profile_set,
        hidden_test_profiles,
        step_by_name["base-hidden-tests"]["profiles"],
        step_by_name["gold-hidden-tests"]["profiles"],
    )
    oracle_file = write_hidden_test_oracle(task_id, hidden_oracle)
    results["hidden_test_oracle"] = hidden_oracle
    results["hidden_test_oracle_file"] = str(oracle_file)
    results["ok"] = all(step_result["ok"] for step_result in results["steps"]) and hidden_oracle["ok"]
    result_file = workdir / "result.json"
    result_file.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": results["ok"], "result_file": str(result_file)}, indent=2))
    if not results["ok"]:
        raise SystemExit(1)


def score_profiles(profile_results: list[dict[str, Any]]) -> float:
    if not profile_results:
        return 1.0
    passed = sum(1 for item in profile_results if item["passed"])
    return passed / len(profile_results)


def agent_test_change_summary(diff_summary: dict[str, Any]) -> dict[str, Any]:
    repos = diff_summary.get("repos") or []
    rows = []
    total = 0
    for repo in repos:
        test_files = repo.get("agent_test_files") or []
        if not test_files:
            continue
        total += len(test_files)
        rows.append(
            {
                "repo": repo.get("repo"),
                "path": repo.get("path"),
                "test_file_count": len(test_files),
                "test_files": test_files,
            }
        )
    return {
        "included_in_resolved": False,
        "score": None,
        "changed": total > 0,
        "changed_test_file_count": total,
        "repos": rows,
        "note": "Auxiliary analysis signal only; correctness is scored by hidden FAIL_TO_PASS/PASS_TO_PASS oracle tests and optional cross-repo contract profiles.",
    }


def profiles_passed(profile_results: list[dict[str, Any]]) -> bool:
    return all(item["passed"] for item in profile_results)


def evaluate_agent(task_id: str, run_dir: Path, profile_set: str, force: bool) -> None:
    run_dir = run_dir.resolve()
    workspace_scope, single_repo = workspace_scope_from_run(run_dir)
    validate_workspace_scope(task_id, workspace_scope, single_repo)
    agent_workspace = agent_workspace_from_run(run_dir)
    diff_dir = run_dir / "diffs"
    evaluator_workspace = evaluator_workspace_from_run(run_dir)
    logs_dir = run_dir / "logs"

    if agent_workspace.exists():
        collect_diff(task_id, agent_workspace, diff_dir)
    elif not (diff_dir / "diffs.json").exists():
        raise SystemExit(f"missing agent workspace and collected diffs: {agent_workspace}")
    diff_summary = load_diff_summary(diff_dir)
    if evaluator_workspace.exists():
        if not force:
            raise SystemExit(f"evaluator workspace already exists: {evaluator_workspace}; pass --force to replace it")
        try:
            evaluator_workspace.relative_to(run_dir)
        except ValueError:
            remove_tree(evaluator_workspace)
        else:
            evaluator_workspace = run_dir / f"evaluator_workspace_{int(time.time())}"
    # The agent sees only the selected repository. Evaluation also materializes
    # the other repositories involved in this case because repo-local tests may
    # depend on their gold versions, but unrelated ecosystem context repositories
    # are not needed and can make a single-repository checkout prohibitively expensive.
    evaluator_workspace_scope, evaluator_single_repo, evaluator_repo_modes = evaluator_workspace_plan(
        task_id,
        workspace_scope,
        single_repo,
    )
    evaluator_repos = evaluator_repo_names(task_id, workspace_scope, diff_summary)
    prepare_workspace(
        task_id,
        evaluator_workspace,
        "base",
        force=False,
        workspace_scope=evaluator_workspace_scope,
        single_repo=evaluator_single_repo,
        repo_modes=evaluator_repo_modes,
        repo_names=evaluator_repos,
    )
    applied_diffs = apply_agent_diffs(task_id, evaluator_workspace, diff_dir)

    harness = load_harness(task_id)
    profiles = scoped_profile_groups(task_id, load_profile_set(harness, profile_set), workspace_scope, single_repo)
    hidden_test_profiles = profiles["hidden_tests"]
    contract_profiles = profiles["cross_repo_contract"]
    hidden_test_oracle = load_hidden_test_oracle(task_id, workspace_scope, single_repo)
    if hidden_test_oracle is None:
        raise SystemExit(f"missing hidden test oracle for {task_id}; run validate-matrix first")
    scoring_manifest = verify_scoring_manifest(task_id)
    if workspace_scope == "single-repo":
        hidden_test_oracle = filter_hidden_test_oracle(hidden_test_oracle, hidden_test_profiles)
        if scoring_manifest:
            selected_repo_rows = [
                row for row in scoring_manifest["repos"] if str(row.get("repo")) == single_repo
            ]
            if len(selected_repo_rows) != 1:
                raise SystemExit(f"scoring manifest does not contain exactly one row for {task_id}/{single_repo}")
            selected = selected_repo_rows[0]
            projected = {
                **selected,
            }
            scoring_manifest = {
                **scoring_manifest,
                "scope": "single-repo",
                "single_repo": single_repo,
                "totals": {
                    key: int(selected.get(key) or 0)
                    for key in ("native_fail_to_pass", "missing_to_pass", "fail_to_pass", "pass_to_pass")
                },
                "repos": [projected],
            }
    agent_diff_apply_ok = agent_diffs_applied(applied_diffs)

    if agent_diff_apply_ok:
        hidden_patch_results = apply_hidden(
            task_id,
            evaluator_workspace,
            allow_already_applied=True,
            profiles=hidden_test_profiles,
            prefer_clean_overlay=True,
        )
        hidden_patch_status_by_profile = {
            item.get("test_profile"): item.get("status")
            for item in hidden_patch_results
            if item.get("test_profile")
        }
        hidden_profiles_to_run = [
            profile
            for profile in hidden_test_profiles
            if hidden_patch_status_by_profile.get(profile, "no_patch")
            in {"applied", "applied_3way", "already_applied", "relocated", "overlay_from_clean_base", "no_patch"}
        ]
        hidden_test_results = run_profiles(task_id, evaluator_workspace, hidden_profiles_to_run, logs_dir, "agent-hidden-test")
        for patch_result in hidden_patch_results:
            if patch_result["status"] == "failed":
                hidden_test_results.append(
                    {
                        "profile": f"inject-hidden:{patch_result['repo']}",
                        "exit_code": 1,
                        "log": None,
                        "passed": False,
                        "error": patch_result.get("error", "hidden patch failed"),
                        "test_summary": None,
                    }
                )
        contract_results = run_profiles(task_id, evaluator_workspace, contract_profiles, logs_dir, "agent-cross-repo-contract")
        evaluation_skipped_reason = None
    else:
        hidden_patch_results = []
        hidden_test_results = [
            {
                "profile": "apply-agent-diffs",
                "exit_code": 1,
                "log": None,
                "passed": False,
                "error": "one or more non-empty agent patches failed to apply",
                "test_summary": None,
            }
        ]
        contract_results = []
        evaluation_skipped_reason = "agent_patch_apply_failed"

    hidden_tests_passed = agent_diff_apply_ok and profiles_passed(hidden_test_results)
    cross_repo_contract_passed = profiles_passed(contract_results)
    hidden_oracle_evaluation = evaluate_hidden_test_oracle(
        hidden_test_results,
        hidden_test_oracle,
        profile_repos_by_name(task_id),
    )
    if scoring_manifest:
        expected_totals = scoring_manifest["totals"]
        actual_totals = {
            "native_fail_to_pass": hidden_oracle_evaluation["native_fail_to_pass"]["total"],
            "missing_to_pass": hidden_oracle_evaluation["missing_to_pass"]["total"],
            "fail_to_pass": hidden_oracle_evaluation["fail_to_pass"]["total"],
            "pass_to_pass": hidden_oracle_evaluation["pass_to_pass"]["total"],
        }
        if actual_totals != expected_totals:
            raise SystemExit(
                f"scoring manifest count mismatch for {task_id}: expected {expected_totals}, got {actual_totals}"
            )
        hidden_oracle_evaluation["scoring_manifest"] = scoring_manifest
    edit_policy_ok = bool(diff_summary.get("edit_policy_ok", True)) and agent_diff_apply_ok
    all_evaluator_profiles_passed = hidden_oracle_evaluation["ok"] and cross_repo_contract_passed and edit_policy_ok

    result = {
        "task_id": task_id,
        "profile_set": profile_set,
        "workspace_scope": workspace_scope,
        "single_repo": single_repo,
        "run_dir": str(run_dir),
        "agent_workspace": str(agent_workspace),
        "evaluator_workspace": str(evaluator_workspace),
        "diff_dir": str(diff_dir),
        "agent_generated_changes": diff_summary,
        "applied_diffs": applied_diffs,
        "evaluation": {
            "hidden_test_profiles": hidden_test_results,
            "cross_repo_contract_profiles": contract_results,
            "hidden_patch_results": hidden_patch_results,
            "hidden_test_oracle": hidden_oracle_evaluation,
            "evaluation_skipped_reason": evaluation_skipped_reason,
            "hidden_tests_passed": hidden_tests_passed,
            "hidden_fail_to_pass_passed": hidden_oracle_evaluation["fail_to_pass_passed"],
            "hidden_pass_to_pass_passed": hidden_oracle_evaluation["pass_to_pass_passed"],
            "cross_repo_contract_passed": cross_repo_contract_passed,
            "agent_diff_apply_ok": agent_diff_apply_ok,
            "edit_policy_ok": edit_policy_ok,
            "edit_policy_violations": diff_summary.get("edit_policy_violations", []),
            "all_evaluator_profiles_passed": all_evaluator_profiles_passed,
        },
        "score": {
            "fail_to_pass": (
                hidden_oracle_evaluation["fail_to_pass"]["passed"] / hidden_oracle_evaluation["fail_to_pass"]["total"]
                if hidden_oracle_evaluation["fail_to_pass"]["total"]
                else 0.0
            ),
            "pass_to_pass": (
                hidden_oracle_evaluation["pass_to_pass"]["passed"] / hidden_oracle_evaluation["pass_to_pass"]["total"]
                if hidden_oracle_evaluation["pass_to_pass"]["total"]
                else 1.0
            ),
            "hidden_profiles": score_profiles(hidden_test_results),
            "cross_repo_contract": score_profiles(contract_results),
        },
        "auxiliary_scores": {
            "agent_test_changes": agent_test_change_summary(diff_summary),
        },
    }
    result["resolved"] = all_evaluator_profiles_passed

    result_file = run_dir / "result.json"
    result_file.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"resolved": result["resolved"], "result_file": str(result_file)}, indent=2))
    if not result["resolved"]:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="WIDESWE local harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_task_workspace(p: argparse.ArgumentParser) -> None:
        p.add_argument("--task", default=DEFAULT_TASK_ID)
        p.add_argument("--workspace", required=True, type=Path)

    prepare = subparsers.add_parser("prepare")
    add_task_workspace(prepare)
    prepare.add_argument("--mode", choices=["base", "gold"], default="base")
    prepare.add_argument("--workspace-scope", choices=WORKSPACE_SCOPES, default="ecosystem")
    prepare.add_argument("--single-repo")
    prepare.add_argument("--force", action="store_true")

    gold = subparsers.add_parser("apply-gold")
    add_task_workspace(gold)
    gold.add_argument("--allow-already-applied", action="store_true")

    hidden = subparsers.add_parser("apply-hidden")
    add_task_workspace(hidden)
    hidden.add_argument("--allow-already-applied", action="store_true")

    profile = subparsers.add_parser("run-profile")
    add_task_workspace(profile)
    profile.add_argument("--profile", required=True)

    build = subparsers.add_parser("build-env")
    build.add_argument("--task", default=DEFAULT_TASK_ID)
    build.add_argument("services", nargs="*")

    check = subparsers.add_parser("check-env")
    check.add_argument("--task", default=DEFAULT_TASK_ID)

    matrix = subparsers.add_parser("validate-matrix")
    matrix.add_argument("--task", default=DEFAULT_TASK_ID)
    matrix.add_argument("--workdir", required=True, type=Path)
    matrix.add_argument("--profile-set", default="linux-docker")
    matrix.add_argument("--force", action="store_true")

    agent = subparsers.add_parser("prepare-agent-run")
    agent.add_argument("--task", default=DEFAULT_TASK_ID)
    agent.add_argument("--run-dir", required=True, type=Path)
    agent.add_argument(
        "--workspace-root",
        type=Path,
        help="Optional root for transient agent/evaluator workspaces; run metadata/logs stay under --run-dir.",
    )
    agent.add_argument("--workspace-scope", choices=WORKSPACE_SCOPES, default="ecosystem")
    agent.add_argument("--single-repo")
    agent.add_argument("--force", action="store_true")

    diff = subparsers.add_parser("collect-diff")
    add_task_workspace(diff)
    diff.add_argument("--output-dir", required=True, type=Path)

    eval_agent = subparsers.add_parser("evaluate-agent")
    eval_agent.add_argument("--task", default=DEFAULT_TASK_ID)
    eval_agent.add_argument("--run-dir", required=True, type=Path)
    eval_agent.add_argument("--profile-set", default="linux-docker")
    eval_agent.add_argument("--force", action="store_true")

    args = parser.parse_args()

    if args.command == "prepare":
        prepare_workspace(args.task, args.workspace, args.mode, args.force, args.workspace_scope, args.single_repo)
    elif args.command == "prepare-agent-run":
        prepare_agent_run(args.task, args.run_dir, args.force, args.workspace_scope, args.single_repo, args.workspace_root)
    elif args.command == "apply-gold":
        apply_gold(args.task, args.workspace, args.allow_already_applied)
    elif args.command == "apply-hidden":
        apply_hidden(args.task, args.workspace, args.allow_already_applied)
    elif args.command == "run-profile":
        raise SystemExit(run_profile(args.task, args.workspace, args.profile))
    elif args.command == "build-env":
        build_env(args.task, args.services)
    elif args.command == "check-env":
        check_env(args.task)
    elif args.command == "validate-matrix":
        validate_matrix(args.task, args.workdir, args.profile_set, args.force)
    elif args.command == "collect-diff":
        collect_diff(args.task, args.workspace, args.output_dir)
    elif args.command == "evaluate-agent":
        evaluate_agent(args.task, args.run_dir, args.profile_set, args.force)
    else:  # pragma: no cover
        raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
