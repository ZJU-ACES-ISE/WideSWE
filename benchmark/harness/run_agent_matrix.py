#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required: python3 -m pip install pyyaml") from exc


REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_USER_FLOW = REPO_ROOT / "benchmark" / "harness" / "run_user_flow.py"


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"expected YAML mapping: {path}")
    return data


def read_items(values: list[str] | None, path: Path | None) -> list[str]:
    items = list(values or [])
    if path:
        for line in path.read_text(encoding="utf-8").splitlines():
            value = line.strip()
            if value and not value.startswith("#"):
                items.append(value)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped


def scope_run_id(run_id: str, workspace_scope: str, single_repo: str | None) -> str:
    if workspace_scope == "ecosystem":
        return run_id
    suffix = workspace_scope
    if single_repo:
        suffix = f"{suffix}-{single_repo}"
    return f"{run_id}__{suffix}"


def default_run_dir(
    task: str,
    profile: dict[str, Any],
    run_id: str,
    mode: str,
    workspace_scope: str = "ecosystem",
    single_repo: str | None = None,
) -> Path:
    scoped_run_id = scope_run_id(run_id, workspace_scope, single_repo)
    if mode == "check":
        return REPO_ROOT / "results" / task / "_checks" / scoped_run_id
    agent_id = str(profile.get("agent_id") or profile.get("agent") or "agent")
    model_id = str(profile.get("model_id") or "unspecified-model")
    return REPO_ROOT / "results" / task / agent_id / model_id / scoped_run_id


def default_agent_environment(profile: dict[str, Any]) -> str:
    sandbox = str(profile.get("sandbox") or "none")
    return "host" if sandbox == "none" else "case-env"


def task_dir(task_id: str) -> Path:
    return REPO_ROOT / "benchmark" / "tasks" / task_id


def case_base_image(task: str) -> str | None:
    lock_path = task_dir(task) / "environment" / "deps_image_lock.yaml"
    if lock_path.exists():
        lock = load_yaml(lock_path) or {}
        case_base = lock.get("case_base_image") or {}
        if isinstance(case_base, dict):
            image = case_base.get("local_tag") or case_base.get("release_tag")
            if image:
                return str(image)
    dockerfile = task_dir(task) / "environment" / "case_env" / "Dockerfile.case-base"
    if dockerfile.exists():
        return f"ecosyncbench/cases/{task}/case-base:deps-env"
    return None


def resolve_sandbox_image(
    profile: dict[str, Any],
    task: str,
    override: str | None,
    agent_environment: str,
) -> str | None:
    if override:
        return override
    image_by_task = profile.get("sandbox_image_by_task") or {}
    if isinstance(image_by_task, dict) and task in image_by_task:
        return str(image_by_task[task])
    image = profile.get("sandbox_image")
    if image:
        return str(image)
    if agent_environment == "case-env":
        return case_base_image(task)
    return None


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a task/profile/run-id matrix through the standard WIDESWE user flow."
    )
    parser.add_argument("--task", action="append", help="Task id. May be repeated.")
    parser.add_argument("--task-file", type=Path, help="Newline-delimited task ids. Comments start with #.")
    parser.add_argument("--agent-config", required=True, type=Path)
    parser.add_argument("--agent-profile", action="append", help="Profile name. May be repeated.")
    parser.add_argument("--agent-profile-file", type=Path, help="Newline-delimited profile names. Comments start with #.")
    parser.add_argument("--run-id", action="append", help="Run id. May be repeated. Default: seed001.")
    parser.add_argument("--mode", choices=["evaluate", "full"], default="evaluate")
    parser.add_argument("--profile-set", help="Override profile_set for every run.")
    parser.add_argument(
        "--workspace-scope",
        choices=["ecosystem", "involved", "single-repo"],
        default="ecosystem",
        help="Repository visibility/edit scope for every run.",
    )
    parser.add_argument("--single-repo", help="Included repo name required with --workspace-scope single-repo.")
    parser.add_argument("--agent-environment", choices=["agent-only", "case-env", "host"], help="Override agent_environment for every run.")
    parser.add_argument("--sandbox-image", help="Override sandbox image for every run.")
    parser.add_argument(
        "--keep-workspaces",
        choices=["always", "failed", "never"],
        default="never",
        help=(
            "Workspace retention policy. Default/recommended release mode is "
            "'never', which prunes workspaces even for failed runs; 'failed' and "
            "'always' are debugging modes."
        ),
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--expect-agent-resolved", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument(
        "--summary",
        type=Path,
        help="Matrix summary JSON path. Default: results/agent_matrix/<timestamp>.json",
    )
    args = parser.parse_args()

    tasks = read_items(args.task, args.task_file)
    profiles = read_items(args.agent_profile, args.agent_profile_file)
    run_ids = args.run_id or ["seed001"]
    if not tasks:
        raise SystemExit("at least one --task or --task-file entry is required")
    if not profiles:
        raise SystemExit("at least one --agent-profile or --agent-profile-file entry is required")

    config = load_yaml(args.agent_config)
    profile_map = config.get("profiles") or {}
    if not isinstance(profile_map, dict):
        raise SystemExit(f"missing profiles mapping in {args.agent_config}")
    for profile_name in profiles:
        if profile_name not in profile_map:
            raise SystemExit(f"missing agent profile '{profile_name}' in {args.agent_config}")

    started = time.time()
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(started))
    summary_path = (args.summary or REPO_ROOT / "results" / "agent_matrix" / f"{timestamp}.json").resolve()
    rows: list[dict[str, Any]] = []

    for task in tasks:
        for profile_name in profiles:
            profile = profile_map[profile_name] or {}
            if not isinstance(profile, dict):
                raise SystemExit(f"profile must be a YAML mapping: {profile_name}")
            for run_id in run_ids:
                run_dir = default_run_dir(task, profile, run_id, args.mode, args.workspace_scope, args.single_repo)
                cmd = [
                    "python3",
                    str(RUN_USER_FLOW),
                    "--mode",
                    args.mode,
                    "--task",
                    task,
                    "--agent-config",
                    str(args.agent_config),
                    "--agent-profile",
                    profile_name,
                    "--run-id",
                    run_id,
                    "--workspace-scope",
                    args.workspace_scope,
                    "--keep-workspaces",
                    args.keep_workspaces,
                ]
                if args.single_repo:
                    cmd.extend(["--single-repo", args.single_repo])
                if args.profile_set:
                    cmd.extend(["--profile-set", args.profile_set])
                if args.agent_environment:
                    cmd.extend(["--agent-environment", args.agent_environment])
                if args.sandbox_image:
                    cmd.extend(["--sandbox-image", args.sandbox_image])
                if args.force:
                    cmd.append("--force")
                if args.expect_agent_resolved:
                    cmd.append("--expect-agent-resolved")

                run_started = time.time()
                print(f"[matrix] task={task} profile={profile_name} run_id={run_id}", flush=True)
                proc = subprocess.run(cmd, cwd=REPO_ROOT, text=True)
                user_flow_summary = read_json(run_dir / "user_flow_summary.json")
                pipeline_result = read_json(run_dir / "pipeline_result.json")
                result = read_json(run_dir / "result.json")
                row_duration = round(time.time() - run_started, 3)
                row = {
                    "task_id": task,
                    "agent_profile": profile_name,
                    "agent": profile.get("agent"),
                    "agent_id": profile.get("agent_id"),
                    "model_id": profile.get("model_id"),
                    "agent_environment": args.agent_environment or profile.get("agent_environment") or default_agent_environment(profile),
                    "sandbox_image": resolve_sandbox_image(
                        profile,
                        task,
                        args.sandbox_image,
                        args.agent_environment or profile.get("agent_environment") or default_agent_environment(profile),
                    ),
                    "run_id": run_id,
                    "workspace_scope": args.workspace_scope,
                    "single_repo": args.single_repo,
                    "run_dir": str(run_dir),
                    "exit_code": proc.returncode,
                    "duration_sec": row_duration,
                    "resolved": (pipeline_result or result or {}).get("resolved"),
                    "user_flow_status": (user_flow_summary or {}).get("status"),
                    "timing": {
                        "matrix_row_sec": row_duration,
                        "user_flow_timing": (user_flow_summary or {}).get("timing"),
                        "pipeline_timing": (pipeline_result or {}).get("timing"),
                        "result_timing": (result or {}).get("timing"),
                    },
                    "command": cmd,
                }
                rows.append(row)
                summary = {
                    "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
                    "finished_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "agent_config": str(args.agent_config),
                    "tasks": tasks,
                    "agent_profiles": profiles,
                    "run_ids": run_ids,
                    "workspace_scope": args.workspace_scope,
                    "single_repo": args.single_repo,
                    "rows": rows,
                }
                summary_path.parent.mkdir(parents=True, exist_ok=True)
                summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

                if proc.returncode != 0 and not args.continue_on_error:
                    print(f"[matrix] failed; summary written to {summary_path}", flush=True)
                    raise SystemExit(proc.returncode)

    print(f"[matrix] summary written to {summary_path}", flush=True)


if __name__ == "__main__":
    main()
