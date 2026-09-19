#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    from .run_validity import completed_run_invalid_reason
except ImportError:  # Script execution from benchmark/harness.
    from run_validity import completed_run_invalid_reason

try:
    from .task_selection import resolve_task_selection
except ImportError:  # Script execution from benchmark/harness.
    from task_selection import resolve_task_selection

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required: python3 -m pip install pyyaml") from exc


REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS_DIR = REPO_ROOT / "benchmark" / "harness"
ECOSYNC_HARNESS = HARNESS_DIR / "ecosync_harness.py"
PULL_IMAGES = HARNESS_DIR / "pull_images.py"
RUN_AGENT_PIPELINE = HARNESS_DIR / "run_agent_pipeline.py"
CHECK_HIDDEN_PATCHES = HARNESS_DIR / "check_hidden_patches.py"
SUMMARIZE_RESULT = HARNESS_DIR / "summarize_result.py"
DEFAULT_TASK_ID = None
WORKSPACE_SCOPES = ("ecosystem", "involved", "single-repo")
TASK_DIR_OVERRIDE: Path | None = None


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"expected YAML mapping: {path}")
    return data


def docker_chown_tree(path: Path) -> bool:
    uid = os.getuid()
    gid = os.getgid()
    for image in (
        "ecosyncbench/base/android-sdk:36",
        "ecosyncbench/base/dotnet-sdk:8.0.100",
        "ecosyncbench/base/node:22-bookworm-slim",
        "ecosyncbench/base/python:3.14-slim",
        "ecosyncbench/deps/home-assistant-core-py:beaea2d99806",
        "ecosyncbench/deps/home-assistant-supervisor-py:2c6253e4b664",
        "ghcr.io/code-tmp/wideswe/agents/claude-code:2.1.139",
        "ghcr.io/code-tmp/wideswe/agents/codex:0.147.0",
    ):
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
                f"chown -R {uid}:{gid} /target && chmod -R u+rwX /target",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if proc.returncode == 0:
            return True
    return False


def unmount_stale_dependency_views(path: Path) -> None:
    mount_root = path / "case_debug_deps" / "isolated-node-modules"
    for mountpoint in sorted(mount_root.glob("*/merged"), reverse=True):
        if not os.path.ismount(mountpoint):
            continue
        completed = subprocess.run(
            ["sudo", "-n", "umount", str(mountpoint)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise OSError(
                f"failed to unmount stale dependency view {mountpoint}: "
                f"{completed.stdout.strip()}"
            )


def remove_tree(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
        return
    unmount_stale_dependency_views(path)
    try:
        shutil.rmtree(path)
    except PermissionError:
        if not docker_chown_tree(path):
            raise
        shutil.rmtree(path)


def load_agent_profile(config_path: Path, profile_name: str) -> dict[str, Any]:
    config = load_yaml(config_path)
    profiles = config.get("profiles") or {}
    if profile_name not in profiles:
        raise SystemExit(f"missing profile '{profile_name}' in {config_path}")
    profile = profiles[profile_name] or {}
    if not isinstance(profile, dict):
        raise SystemExit(f"profile must be a YAML mapping: {profile_name}")
    return profile


def scope_run_id(run_id: str, workspace_scope: str, single_repo: str | None) -> str:
    if workspace_scope == "ecosystem":
        return run_id
    suffix = workspace_scope
    if single_repo:
        suffix = f"{suffix}-{single_repo}"
    return f"{run_id}__{suffix}"


def force_existing_run(cmd: list[str], run_dir: Path, requested: bool) -> None:
    if requested or run_dir.exists():
        cmd.append("--force")


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


def sandbox_image_source(profile: dict[str, Any], task: str, override: str | None) -> str:
    if override:
        return "cli"
    image_by_task = profile.get("sandbox_image_by_task") or {}
    if isinstance(image_by_task, dict) and task in image_by_task:
        return "profile.sandbox_image_by_task"
    if profile.get("sandbox_image"):
        return "profile.sandbox_image"
    task_root = TASK_DIR_OVERRIDE if TASK_DIR_OVERRIDE is not None else REPO_ROOT / "benchmark" / "tasks" / task
    lock_path = task_root / "environment" / "deps_image_lock.yaml"
    if lock_path.exists():
        lock = load_yaml(lock_path) or {}
        if isinstance(lock.get("case_base_image"), dict):
            return "task.deps_image_lock.case_base_image"
    dockerfile = task_root / "environment" / "case_env" / "Dockerfile.case-base"
    if dockerfile.exists():
        return "task.case_base_default_tag"
    return "unset"


def run_step(
    name: str,
    cmd: list[str],
    log_path: Path,
    *,
    allow_failure: bool = False,
    expected_exit_codes: set[int] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    started = time.time()
    print(f"[user-flow] {name}: {' '.join(cmd)}", flush=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n$ {' '.join(cmd)}\n")
        log.flush()
        proc = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            text=True,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
        )
        log.write(f"\n[exit_code={proc.returncode}]\n")
        log.flush()

    ok_codes = expected_exit_codes if expected_exit_codes is not None else {0}
    ok = proc.returncode in ok_codes
    step = {
        "name": name,
        "command": cmd,
        "exit_code": proc.returncode,
        "ok": ok,
        "duration_sec": round(time.time() - started, 3),
        "log": str(log_path),
    }
    if not ok and not allow_failure:
        raise StepFailure(step)
    return step


class StepFailure(RuntimeError):
    def __init__(self, step: dict[str, Any]) -> None:
        super().__init__(f"step failed: {step['name']} exit={step['exit_code']}")
        self.step = step


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def find_traces(run_dir: Path) -> list[str]:
    trace_dir = run_dir / "traces"
    if not trace_dir.exists():
        return []
    paths = [
        path
        for path in trace_dir.rglob("*")
        if path.is_file() and path.stat().st_size > 0
    ]
    return [str(path) for path in sorted(paths)]


def write_summary(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def publish_user_flow_summary(run_dir: Path, summary: dict[str, Any]) -> None:
    if not run_dir.exists():
        return
    write_summary(run_dir / "user_flow_summary.json", summary)


def duration_by_step(steps: list[dict[str, Any]], name: str) -> float | None:
    for step in steps:
        if step.get("name") == name:
            value = step.get("duration_sec")
            return float(value) if value is not None else None
    return None


def extend_workspace_root(command: list[str], workspace_root: Path | None) -> None:
    if workspace_root is not None:
        command.extend(["--workspace-root", str(workspace_root.resolve())])


def main() -> None:
    global TASK_DIR_OVERRIDE
    parser = argparse.ArgumentParser(
        description=(
            "Run a publish-style WIDESWE flow. Use --mode check for "
            "gold/noop environment validation, --mode evaluate for a real agent "
            "run, or --mode full for both."
        )
    )
    parser.add_argument("--task")
    parser.add_argument("--task-dir", type=Path, help="Explicit task directory outside benchmark/tasks.")
    parser.add_argument("--mode", choices=["check", "evaluate", "full"], default="full")
    parser.add_argument("--agent-config", type=Path)
    parser.add_argument("--agent-profile")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument(
        "--prompt-suffix-file",
        type=Path,
        help="Append this file to the real Agent prompt; controls remain unchanged.",
    )
    parser.add_argument("--profile-set", default=None)
    parser.add_argument(
        "--workspace-scope",
        choices=WORKSPACE_SCOPES,
        default="ecosystem",
        help="Repository visibility/edit scope passed to the agent pipeline.",
    )
    parser.add_argument("--single-repo", help="Included repo name required with --workspace-scope single-repo.")
    parser.add_argument("--agent-environment", choices=["agent-only", "case-env", "host"], default=None)
    parser.add_argument("--sandbox-image", help="Override the profile sandbox image for the real agent run.")
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
    parser.add_argument(
        "--workspace-root",
        type=Path,
        help="Optional fast local root for transient agent/evaluator workspaces.",
    )
    parser.add_argument("--skip-controls", action="store_true")
    parser.add_argument(
        "--skip-evaluation",
        action="store_true",
        help="Run the Agent and preserve its diff/trajectory without evaluator profiles.",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--reuse-agent-workspace",
        action="store_true",
        help=(
            "Resume an interrupted agent invocation using the workspace and "
            "agent scratch recorded in the existing run directory."
        ),
    )
    parser.add_argument(
        "--expect-agent-resolved",
        action="store_true",
        help="Fail the user flow if the real agent does not solve the task.",
    )
    args = parser.parse_args()
    if args.force and args.reuse_agent_workspace:
        raise SystemExit("--force and --reuse-agent-workspace are mutually exclusive")
    try:
        args.task, TASK_DIR_OVERRIDE = resolve_task_selection(args.task, args.task_dir, DEFAULT_TASK_ID)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    started = time.time()
    if args.mode in {"evaluate", "full"} and (not args.agent_config or not args.agent_profile):
        raise SystemExit("--agent-config and --agent-profile are required for --mode evaluate/full")

    profile: dict[str, Any] = {}
    if args.agent_config or args.agent_profile:
        if not args.agent_config or not args.agent_profile:
            raise SystemExit("--agent-config and --agent-profile must be provided together")
        profile = load_agent_profile(args.agent_config, args.agent_profile)

    profile_set = args.profile_set or str(profile.get("profile_set") or "linux-docker")
    explicit_agent_environment = args.agent_environment or profile.get("agent_environment")
    agent_environment = explicit_agent_environment or default_agent_environment(profile)
    if (
        agent_environment == "case-env"
        and explicit_agent_environment is None
        and sandbox_image_source(profile, args.task, args.sandbox_image) == "profile.sandbox_image"
    ):
        raise SystemExit(
            "Docker agent runs default to case-env, but the selected profile only "
            "has a generic sandbox_image. Omit sandbox_image to use the task "
            "case_base_image plus agent_runtime_mounts, use sandbox_image_by_task "
            "only for a legacy task-specific case-env image, or pass "
            "--agent-environment agent-only for pure agent-image mode."
        )
    run_id = args.run_id or time.strftime("user-flow-%Y%m%d-%H%M%S", time.gmtime())
    run_dir = (
        args.run_dir
        or default_run_dir(args.task, profile, run_id, args.mode, args.workspace_scope, args.single_repo)
    ).resolve()
    if run_dir.exists() and args.force:
        remove_tree(run_dir)
    elif run_dir.exists() and not args.reuse_agent_workspace:
        raise SystemExit(f"run dir already exists; use --force to replace it: {run_dir}")

    user_flow_dir = run_dir.parent / "_user_flow_logs" / run_dir.name
    flow_log = user_flow_dir / "user_flow.log"
    summary_path = user_flow_dir / "user_flow_summary.json"
    task_env = os.environ.copy()
    if TASK_DIR_OVERRIDE is not None:
        task_env["ECOSYNC_TASKS_DIR"] = str(TASK_DIR_OVERRIDE.parent)

    steps: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "task_id": args.task,
        "mode": args.mode,
        "agent_config": str(args.agent_config) if args.agent_config else None,
        "agent_profile": args.agent_profile,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "prompt_suffix_file": (
            str(args.prompt_suffix_file.resolve()) if args.prompt_suffix_file else None
        ),
        "profile_set": profile_set,
        "workspace_scope": args.workspace_scope,
        "single_repo": args.single_repo,
        "workspace_root": str(args.workspace_root.resolve()) if args.workspace_root else None,
        "agent_environment": agent_environment,
        "sandbox_image_override": args.sandbox_image,
        "skip_evaluation": args.skip_evaluation,
        "reuse_agent_workspace": args.reuse_agent_workspace,
        "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "steps": steps,
        "status": "running",
    }

    try:
        steps.append(
            run_step(
                "compile-harness",
                [
                    "python3",
                    "-m",
                    "py_compile",
                    str(ECOSYNC_HARNESS),
                    str(PULL_IMAGES),
                    str(RUN_AGENT_PIPELINE),
                    str(CHECK_HIDDEN_PATCHES),
                    str(SUMMARIZE_RESULT),
                    str(REPO_ROOT / "benchmark" / "agents" / "run_agent.py"),
                ],
                flow_log,
            )
        )
        if not args.skip_evaluation:
            steps.append(
                run_step(
                    "check-hidden-patches",
                    ["python3", str(CHECK_HIDDEN_PATCHES), "--task", args.task],
                    flow_log,
                    env=task_env,
                )
            )
        image_source = sandbox_image_source(profile, args.task, args.sandbox_image)
        pull_images_cmd = ["python3", str(PULL_IMAGES), "--task", args.task]
        if agent_environment == "case-env" and image_source == "profile.sandbox_image_by_task":
            pull_images_cmd.append("--include-case-agent-images")
            agent_id = profile.get("agent_id") or profile.get("agent")
            if agent_id:
                pull_images_cmd.extend(["--agent-id", str(agent_id)])
        steps.append(run_step("pull-release-images", pull_images_cmd, flow_log, env=task_env))
        steps.append(
            run_step(
                "check-task-env",
                ["python3", str(ECOSYNC_HARNESS), "check-env", "--task", args.task],
                flow_log,
                env=task_env,
            )
        )

        if args.mode in {"check", "full"} and not args.skip_controls:
            control_id = scope_run_id(run_id, args.workspace_scope, args.single_repo)
            control_root = (run_dir / "controls") if args.mode == "check" else (run_dir.parent / "_controls" / control_id)
            gold_dir = control_root / "gold"
            noop_dir = control_root / "noop"
            gold_cmd = [
                "python3",
                str(RUN_AGENT_PIPELINE),
                "--task",
                args.task,
                "--task-dir",
                str(TASK_DIR_OVERRIDE) if TASK_DIR_OVERRIDE is not None else str(REPO_ROOT / "benchmark" / "tasks" / args.task),
                "--agent",
                "gold",
                "--run-dir",
                str(gold_dir),
                "--profile-set",
                profile_set,
                "--workspace-scope",
                args.workspace_scope,
                "--keep-workspaces",
                args.keep_workspaces,
            ]
            noop_cmd = [
                "python3",
                str(RUN_AGENT_PIPELINE),
                "--task",
                args.task,
                "--task-dir",
                str(TASK_DIR_OVERRIDE) if TASK_DIR_OVERRIDE is not None else str(REPO_ROOT / "benchmark" / "tasks" / args.task),
                "--agent",
                "noop",
                "--run-dir",
                str(noop_dir),
                "--profile-set",
                profile_set,
                "--workspace-scope",
                args.workspace_scope,
                "--keep-workspaces",
                args.keep_workspaces,
            ]
            if args.single_repo:
                gold_cmd.extend(["--single-repo", args.single_repo])
                noop_cmd.extend(["--single-repo", args.single_repo])
            extend_workspace_root(gold_cmd, args.workspace_root)
            extend_workspace_root(noop_cmd, args.workspace_root)
            force_existing_run(gold_cmd, gold_dir, args.force)
            force_existing_run(noop_cmd, noop_dir, args.force)
            steps.append(run_step("gold-control", gold_cmd, flow_log))
            steps.append(
                run_step(
                    "noop-negative-control",
                    noop_cmd,
                    flow_log,
                    expected_exit_codes={1},
                )
            )
            summary["controls"] = {
                "gold": read_json(gold_dir / "pipeline_result.json"),
                "noop": read_json(noop_dir / "pipeline_result.json"),
            }

        if args.mode in {"evaluate", "full"}:
            agent_cmd = [
                "python3",
                str(RUN_AGENT_PIPELINE),
                "--task",
                args.task,
                "--task-dir",
                str(TASK_DIR_OVERRIDE) if TASK_DIR_OVERRIDE is not None else str(REPO_ROOT / "benchmark" / "tasks" / args.task),
                "--agent-config",
                str(args.agent_config),
                "--agent-profile",
                str(args.agent_profile),
                "--run-id",
                run_id,
                "--run-dir",
                str(run_dir),
                "--workspace-scope",
                args.workspace_scope,
                "--keep-workspaces",
                args.keep_workspaces,
            ]
            if args.single_repo:
                agent_cmd.extend(["--single-repo", args.single_repo])
            if args.prompt_suffix_file:
                agent_cmd.extend(
                    ["--prompt-suffix-file", str(args.prompt_suffix_file.resolve())]
                )
            extend_workspace_root(agent_cmd, args.workspace_root)
            if args.profile_set:
                agent_cmd.extend(["--profile-set", profile_set])
            if agent_environment:
                agent_cmd.extend(["--agent-environment", str(agent_environment)])
            if args.sandbox_image:
                agent_cmd.extend(["--sandbox-image", str(args.sandbox_image)])
            if args.force:
                agent_cmd.append("--force")
            if args.reuse_agent_workspace:
                agent_cmd.append("--reuse-agent-workspace")
            if args.skip_evaluation:
                agent_cmd.append("--skip-evaluation")
            agent_pipeline_step = run_step(
                "agent-pipeline",
                agent_cmd,
                flow_log,
                allow_failure=True,
            )
            steps.append(agent_pipeline_step)
            pipeline_result = read_json(run_dir / "pipeline_result.json")
            result = read_json(run_dir / "result.json")
            if args.skip_evaluation:
                invalid_reason = (
                    None
                    if pipeline_result and pipeline_result.get("agent_artifacts_valid") is True
                    else str(
                        (pipeline_result or {}).get("agent_artifacts_invalid_reason")
                        or "missing_valid_agent_artifacts"
                    )
                )
            else:
                invalid_reason = completed_run_invalid_reason(pipeline_result, result)
            if invalid_reason:
                invalid_step = dict(agent_pipeline_step)
                invalid_step.update(
                    {
                        "name": "validate-agent-run",
                        "ok": False,
                        "exit_code": agent_pipeline_step.get("exit_code") or 2,
                        "invalid_reason": invalid_reason,
                    }
                )
                raise StepFailure(invalid_step)
            if not args.skip_evaluation:
                steps.append(
                    run_step(
                        "summarize-result",
                        ["python3", str(SUMMARIZE_RESULT), "--run-dir", str(run_dir), "--write"],
                        flow_log,
                    )
                )

        pipeline_result = read_json(run_dir / "pipeline_result.json")
        result = read_json(run_dir / "result.json")
        evaluation_summary = read_json(run_dir / "evaluation_summary.json")
        traces = find_traces(run_dir)
        agent_resolved = bool((pipeline_result or {}).get("resolved"))
        if args.expect_agent_resolved and not agent_resolved:
            raise StepFailure(
                {
                    "name": "expect-agent-resolved",
                    "command": [],
                    "exit_code": 1,
                    "ok": False,
                    "duration_sec": 0,
                    "log": str(flow_log),
                }
            )

        summary.update(
            {
                "status": "completed",
                "resolved": agent_resolved,
                "agent_artifacts_valid": (pipeline_result or {}).get("agent_artifacts_valid"),
                "pipeline_result": pipeline_result,
                "result": result,
                "evaluation_summary": evaluation_summary,
                "trace_files": traces,
                "trace_available": bool(traces),
                "timing": {
                    "user_flow_total_sec": round(time.time() - started, 3),
                    "compile_harness_sec": duration_by_step(steps, "compile-harness"),
                    "check_hidden_patches_sec": duration_by_step(steps, "check-hidden-patches"),
                    "pull_release_images_sec": duration_by_step(steps, "pull-release-images"),
                    "check_task_env_sec": duration_by_step(steps, "check-task-env"),
                    "gold_control_sec": duration_by_step(steps, "gold-control"),
                    "noop_negative_control_sec": duration_by_step(steps, "noop-negative-control"),
                    "agent_pipeline_sec": duration_by_step(steps, "agent-pipeline"),
                    "summarize_result_sec": duration_by_step(steps, "summarize-result"),
                    "pipeline_timing": (pipeline_result or {}).get("timing"),
                    "result_timing": (result or {}).get("timing"),
                },
            }
        )
    except StepFailure as exc:
        if exc.step not in steps:
            steps.append(exc.step)
        summary.update({"status": "failed", "failed_step": exc.step})
        write_summary(summary_path, summary)
        publish_user_flow_summary(run_dir, summary)
        print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
        raise SystemExit(exc.step["exit_code"])
    finally:
        summary["duration_sec"] = round(time.time() - started, 3)
        summary["finished_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        write_summary(summary_path, summary)
        publish_user_flow_summary(run_dir, summary)

    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
