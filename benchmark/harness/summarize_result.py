#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return data


def profile_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        rows.append(
            {
                "profile": item.get("profile"),
                "passed": bool(item.get("passed")),
                "exit_code": item.get("exit_code"),
                "log": item.get("log"),
                "test_summary": item.get("test_summary"),
            }
        )
    return rows


def status_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        rows.append(
            {
                "repo": item.get("repo"),
                "status": item.get("status"),
                "error": item.get("error"),
                "patch": item.get("patch"),
            }
        )
    return rows


def find_traces(run_dir: Path) -> list[str]:
    trace_dir = run_dir / "traces"
    if not trace_dir.exists():
        return []
    return [
        str(path)
        for path in sorted(trace_dir.rglob("*"))
        if path.is_file() and path.stat().st_size > 0
    ]


def build_summary(run_dir: Path) -> dict[str, Any]:
    result = load_json(run_dir / "result.json") or {}
    pipeline = load_json(run_dir / "pipeline_result.json") or {}
    user_flow = load_json(run_dir / "user_flow_summary.json") or {}
    evaluation = result.get("evaluation") or {}
    auxiliary_scores = result.get("auxiliary_scores") or {}

    hidden_patch_success_statuses = {
        "applied",
        "applied_3way",
        "already_applied",
        "relocated",
        "overlay_from_clean_base",
        "skipped",
    }
    hidden_patch_results = status_rows(evaluation.get("hidden_patch_results") or [])
    hidden_test_oracle = evaluation.get("hidden_test_oracle") or {}
    hidden_patch_failed = [
        row
        for row in hidden_patch_results
        if row["status"] not in hidden_patch_success_statuses
    ]
    hidden_patch_applied = [row for row in hidden_patch_results if row["status"] in hidden_patch_success_statuses]
    if hidden_patch_failed:
        hidden_patch_injection_status = "failed"
    elif hidden_patch_applied:
        hidden_patch_injection_status = "applied"
    elif hidden_patch_results:
        hidden_patch_injection_status = "not_exercised"
    else:
        hidden_patch_injection_status = "missing"

    changed_repos = []
    for repo in ((result.get("agent_generated_changes") or {}).get("repos") or []):
        if repo.get("changed"):
            changed_repos.append(repo.get("repo"))

    trace_files = find_traces(run_dir)
    summary = {
        "run_dir": str(run_dir),
        "task_id": result.get("task_id") or pipeline.get("task_id") or user_flow.get("task_id"),
        "agent_id": pipeline.get("agent_id"),
        "model_id": pipeline.get("model_id"),
        "agent_environment": result.get("agent_environment") or pipeline.get("agent_environment") or user_flow.get("agent_environment"),
        "sandbox_image": result.get("sandbox_image") or pipeline.get("sandbox_image"),
        "run_id": pipeline.get("run_id") or user_flow.get("run_id"),
        "workspace_scope": result.get("workspace_scope") or pipeline.get("workspace_scope") or user_flow.get("workspace_scope"),
        "single_repo": result.get("single_repo") or pipeline.get("single_repo") or user_flow.get("single_repo"),
        "profile_set": result.get("profile_set") or pipeline.get("profile_set") or user_flow.get("profile_set"),
        "agent_exit_code": pipeline.get("agent_exit_code"),
        "resolved": bool(result.get("resolved")),
        "score": result.get("score"),
        "timing": result.get("timing") or pipeline.get("timing") or user_flow.get("timing"),
        "auxiliary_scores": auxiliary_scores,
        "agent_test_changes": auxiliary_scores.get("agent_test_changes"),
        "changed_repos": changed_repos,
        "applied_diffs": status_rows(result.get("applied_diffs") or []),
        "hidden_patch_results": hidden_patch_results,
        "hidden_patch_injection_status": hidden_patch_injection_status,
        "hidden_patch_injection_ok": hidden_patch_injection_status == "applied",
        "hidden_test_profiles": profile_rows(evaluation.get("hidden_test_profiles") or []),
        "hidden_test_oracle": hidden_test_oracle,
        "hidden_fail_to_pass": hidden_test_oracle.get("fail_to_pass"),
        "hidden_pass_to_pass": hidden_test_oracle.get("pass_to_pass"),
        "hidden_fail_to_pass_passed": bool(evaluation.get("hidden_fail_to_pass_passed")),
        "hidden_pass_to_pass_passed": bool(evaluation.get("hidden_pass_to_pass_passed")),
        "edit_policy_ok": evaluation.get("edit_policy_ok"),
        "edit_policy_violations": evaluation.get("edit_policy_violations") or [],
        "cross_repo_contract_profiles": profile_rows(evaluation.get("cross_repo_contract_profiles") or []),
        "hidden_tests_passed": bool(evaluation.get("hidden_tests_passed")),
        "cross_repo_contract_passed": bool(evaluation.get("cross_repo_contract_passed")),
        "all_evaluator_profiles_passed": bool(evaluation.get("all_evaluator_profiles_passed")),
        "trace_available": bool(trace_files),
        "trace_files": trace_files,
    }
    return summary


def yes_no(value: bool) -> str:
    return "yes" if value else "no"


def table(headers: list[str], rows: list[list[Any]]) -> str:
    rendered = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        rendered.append("| " + " | ".join("" if value is None else str(value) for value in row) + " |")
    return "\n".join(rendered)


def format_test_summary(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return (
        f"{value.get('passed', 0)}/{value.get('total', 0)} passed"
        f", {value.get('failed', 0)} failed"
        f", {value.get('skipped', 0)} skipped"
    )


def build_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Evaluation Summary",
        "",
        f"- task: `{summary.get('task_id')}`",
        f"- agent: `{summary.get('agent_id')}`",
        f"- model: `{summary.get('model_id')}`",
        f"- agent environment: `{summary.get('agent_environment')}`",
        f"- sandbox image: `{summary.get('sandbox_image')}`",
        f"- run: `{summary.get('run_id')}`",
        f"- workspace scope: `{summary.get('workspace_scope')}`",
        f"- single repo: `{summary.get('single_repo')}`",
        f"- profile set: `{summary.get('profile_set')}`",
        f"- resolved: `{str(summary.get('resolved')).lower()}`",
        f"- score: `{summary.get('score')}`",
        f"- timing: `{summary.get('timing')}`",
        f"- auxiliary scores: `{summary.get('auxiliary_scores')}`",
        f"- hidden tests passed: `{str(summary.get('hidden_tests_passed')).lower()}`",
        f"- hidden FAIL_TO_PASS passed: `{str(summary.get('hidden_fail_to_pass_passed')).lower()}`",
        f"- hidden PASS_TO_PASS passed: `{str(summary.get('hidden_pass_to_pass_passed')).lower()}`",
        f"- edit policy ok: `{summary.get('edit_policy_ok')}`",
        f"- cross-repo contract passed: `{str(summary.get('cross_repo_contract_passed')).lower()}`",
        f"- hidden patch injection status: `{summary.get('hidden_patch_injection_status')}`",
        f"- hidden patch injection ok: `{yes_no(bool(summary.get('hidden_patch_injection_ok')))}`",
        f"- traces available: `{yes_no(bool(summary.get('trace_available')))}`",
        "",
        "## Changed Repos",
        "",
        ", ".join(f"`{repo}`" for repo in summary.get("changed_repos") or []) or "none",
        "",
        "## Agent Test Changes",
        "",
        table(
            ["repo", "test_file_count", "test_files"],
            [
                [
                    row.get("repo"),
                    row.get("test_file_count"),
                    ", ".join(item.get("path", "") for item in row.get("test_files") or []),
                ]
                for row in ((summary.get("agent_test_changes") or {}).get("repos") or [])
            ],
        ),
        "",
        "## Edit Policy Violations",
        "",
        table(
            ["repo", "path", "changed_file_count"],
            [
                [row.get("repo"), row.get("path"), row.get("changed_file_count")]
                for row in summary.get("edit_policy_violations") or []
            ],
        ),
        "",
        "## Applied Agent Diffs",
        "",
        table(
            ["repo", "status", "error"],
            [[row.get("repo"), row.get("status"), row.get("error")] for row in summary.get("applied_diffs") or []],
        ),
        "",
        "## Hidden Patch Injection",
        "",
        table(
            ["repo", "status", "error"],
            [[row.get("repo"), row.get("status"), row.get("error")] for row in summary.get("hidden_patch_results") or []],
        ),
        "",
        "## Hidden Test Profiles",
        "",
        table(
            ["profile", "passed", "tests", "exit_code", "log"],
            [
                [
                    row.get("profile"),
                    row.get("passed"),
                    format_test_summary(row.get("test_summary")),
                    row.get("exit_code"),
                    row.get("log"),
                ]
                for row in summary.get("hidden_test_profiles") or []
            ],
        ),
        "",
        "## Hidden Test Oracle",
        "",
        table(
            ["split", "passed", "total", "failed", "missing"],
            [
                [
                    "FAIL_TO_PASS",
                    (summary.get("hidden_fail_to_pass") or {}).get("passed"),
                    (summary.get("hidden_fail_to_pass") or {}).get("total"),
                    (summary.get("hidden_fail_to_pass") or {}).get("failed"),
                    (summary.get("hidden_fail_to_pass") or {}).get("missing"),
                ],
                [
                    "PASS_TO_PASS",
                    (summary.get("hidden_pass_to_pass") or {}).get("passed"),
                    (summary.get("hidden_pass_to_pass") or {}).get("total"),
                    (summary.get("hidden_pass_to_pass") or {}).get("failed"),
                    (summary.get("hidden_pass_to_pass") or {}).get("missing"),
                ],
            ],
        ),
        "",
        "## Cross-Repo Contract Profiles",
        "",
        table(
            ["profile", "passed", "tests", "exit_code", "log"],
            [
                [
                    row.get("profile"),
                    row.get("passed"),
                    format_test_summary(row.get("test_summary")),
                    row.get("exit_code"),
                    row.get("log"),
                ]
                for row in summary.get("cross_repo_contract_profiles") or []
            ],
        ),
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize one WIDESWE run directory.")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--write", action="store_true", help="Write evaluation_summary.json and evaluation_summary.md.")
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    summary = build_summary(run_dir)
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if args.write:
        (run_dir / "evaluation_summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (run_dir / "evaluation_summary.md").write_text(build_markdown(summary), encoding="utf-8")


if __name__ == "__main__":
    main()
