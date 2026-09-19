from __future__ import annotations

from pathlib import Path
from typing import Any


EVALUATOR_RESOURCE_EXIT_CODES = {124, 126, 127, 137, 143}
FORMAL_TRACE_FILES = {
    "claude-code": "claude.stream.jsonl",
    "codex": "codex.events.jsonl",
}


def agent_execution_invalid_reason(
    agent_id: str,
    run_dir: Path,
    *,
    agent_exit_code: int,
    agent_timed_out: bool = False,
) -> str | None:
    """Reject successful-looking formal runs where the agent never actually ran."""
    if agent_id not in FORMAL_TRACE_FILES:
        return None
    expected_timeout = agent_timed_out and agent_exit_code == 124
    if agent_exit_code != 0 and not expected_timeout:
        return None

    request_log = run_dir / "logs" / "llm-api-requests.jsonl"
    if not request_log.is_file() or request_log.stat().st_size == 0:
        return "agent_no_api_requests"

    trace_name = FORMAL_TRACE_FILES[agent_id]
    traces = list((run_dir / "traces").glob(f"**/{trace_name}"))
    if not any(path.is_file() and path.stat().st_size > 0 for path in traces):
        return "agent_empty_trace"
    return None


def evaluation_invalid_reason(
    agent_exit_code: int,
    result: dict[str, Any] | None,
    *,
    agent_timed_out: bool = False,
) -> str | None:
    expected_timeout = agent_timed_out and agent_exit_code == 124
    if agent_exit_code != 0 and not expected_timeout:
        return f"agent_exit_{agent_exit_code}"
    if not result:
        return "missing_result"
    evaluation = result.get("evaluation")
    if not isinstance(evaluation, dict):
        return "missing_evaluation"
    skipped_reason = evaluation.get("evaluation_skipped_reason")
    if skipped_reason:
        return str(skipped_reason)
    if evaluation.get("agent_diff_apply_ok") is not True:
        return "agent_patch_apply_failed"
    hidden_patch_results = evaluation.get("hidden_patch_results") or []
    if any(isinstance(item, dict) and item.get("status") == "failed" for item in hidden_patch_results):
        return "hidden_patch_apply_failed"
    for group in ("hidden_test_profiles", "cross_repo_contract_profiles"):
        for profile in evaluation.get(group) or []:
            if not isinstance(profile, dict):
                continue
            try:
                exit_code = int(profile.get("exit_code"))
            except (TypeError, ValueError):
                continue
            if exit_code in EVALUATOR_RESOURCE_EXIT_CODES:
                return f"evaluator_profile_exit_{exit_code}"
    return None


def completed_run_invalid_reason(
    pipeline: dict[str, Any] | None,
    result: dict[str, Any] | None,
) -> str | None:
    if not pipeline:
        return "missing_pipeline_result"
    explicit_valid = pipeline.get("run_valid")
    if explicit_valid is False:
        return str(pipeline.get("run_invalid_reason") or "pipeline_marked_invalid")
    try:
        agent_exit_code = int(pipeline.get("agent_exit_code"))
    except (TypeError, ValueError):
        return "missing_agent_exit_code"
    agent_timed_out = pipeline.get("agent_timed_out", pipeline.get("timed_out", False)) is True
    reason = evaluation_invalid_reason(agent_exit_code, result, agent_timed_out=agent_timed_out)
    if reason:
        return reason
    if explicit_valid is not None and explicit_valid is not True:
        return "invalid_run_valid_flag"
    return None


def completed_run_is_valid(
    pipeline: dict[str, Any] | None,
    result: dict[str, Any] | None,
) -> bool:
    return completed_run_invalid_reason(pipeline, result) is None
