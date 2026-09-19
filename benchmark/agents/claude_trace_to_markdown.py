#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def truncate_text(value: str, limit: int) -> str:
    if limit <= 0 or len(value) <= limit:
        return value
    omitted = len(value) - limit
    return value[:limit].rstrip() + f"\n\n...[truncated {omitted} chars]"


def fence(value: str, language: str = "text") -> str:
    if "```" in value:
        value = value.replace("```", "'''")
    return f"```{language}\n{value.rstrip()}\n```"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                events.append({"type": "raw", "line_no": line_no, "text": line})
                continue
            events.append(event)
    return events


def find_claude_stream(path: Path) -> Path:
    if path.is_file():
        return path
    matches = sorted(path.rglob("claude.stream.jsonl"))
    if not matches:
        raise SystemExit(f"no claude.stream.jsonl found under {path}")
    if len(matches) > 1:
        joined = "\n".join(str(match) for match in matches[:20])
        raise SystemExit(
            f"found multiple claude.stream.jsonl files under {path}; pass one explicitly:\n{joined}"
        )
    return matches[0]


def first_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def tool_input_markdown(name: str, tool_input: Any, max_chars: int) -> str:
    if isinstance(tool_input, dict) and name == "Bash" and isinstance(tool_input.get("command"), str):
        parts = []
        description = tool_input.get("description")
        if description:
            parts.append(f"Description: {description}")
        parts.append(fence(truncate_text(tool_input["command"], max_chars), "bash"))
        return "\n\n".join(parts)
    raw = json.dumps(tool_input, ensure_ascii=False, indent=2)
    return fence(truncate_text(raw, max_chars), "json")


def render_content_blocks(
    blocks: list[dict[str, Any]],
    lines: list[str],
    counters: dict[str, int],
    include_thinking: bool,
    max_tool_input_chars: int,
) -> None:
    for block in blocks:
        block_type = block.get("type")
        if block_type == "text":
            text = first_text(block.get("text")).strip()
            if text:
                counters["assistant"] += 1
                lines.append(f"## Assistant {counters['assistant']}")
                lines.append("")
                lines.append(text)
                lines.append("")
        elif block_type == "thinking":
            if include_thinking:
                thinking = first_text(block.get("thinking")).strip()
                if thinking:
                    counters["thinking"] += 1
                    lines.append(f"## Thinking {counters['thinking']}")
                    lines.append("")
                    lines.append(thinking)
                    lines.append("")
        elif block_type == "tool_use":
            counters["tool_call"] += 1
            name = first_text(block.get("name") or "tool")
            tool_id = first_text(block.get("id"))
            lines.append(f"## Tool Call {counters['tool_call']}: {name}")
            if tool_id:
                lines.append("")
                lines.append(f"ID: `{tool_id}`")
            lines.append("")
            lines.append(tool_input_markdown(name, block.get("input"), max_tool_input_chars))
            lines.append("")


def render_tool_result(
    content: dict[str, Any],
    lines: list[str],
    counters: dict[str, int],
    max_tool_result_chars: int,
) -> None:
    counters["tool_result"] += 1
    tool_id = first_text(content.get("tool_use_id"))
    is_error = bool(content.get("is_error"))
    title = f"## Tool Result {counters['tool_result']}"
    if is_error:
        title += " (error)"
    lines.append(title)
    if tool_id:
        lines.append("")
        lines.append(f"Tool call ID: `{tool_id}`")
    result_text = first_text(content.get("content")).strip()
    if result_text:
        lines.append("")
        lines.append(fence(truncate_text(result_text, max_tool_result_chars)))
    lines.append("")


def render_user_message(
    message: dict[str, Any],
    lines: list[str],
    counters: dict[str, int],
    max_tool_result_chars: int,
) -> None:
    contents = message.get("content")
    if not isinstance(contents, list):
        text = first_text(contents).strip()
        if text:
            counters["user"] += 1
            lines.append(f"## User {counters['user']}")
            lines.append("")
            lines.append(text)
            lines.append("")
        return
    for content in contents:
        if isinstance(content, dict) and content.get("type") == "tool_result":
            render_tool_result(content, lines, counters, max_tool_result_chars)
        else:
            text = first_text(content).strip()
            if text:
                counters["user"] += 1
                lines.append(f"## User {counters['user']}")
                lines.append("")
                lines.append(text)
                lines.append("")


def render_result(event: dict[str, Any], lines: list[str]) -> None:
    lines.append("## Final Result")
    lines.append("")
    status = event.get("subtype") or event.get("stop_reason")
    if status:
        lines.append(f"Status: `{status}`")
        lines.append("")
    result = first_text(event.get("result")).strip()
    if result:
        lines.append(result)
        lines.append("")
    usage = event.get("usage")
    if usage:
        lines.append("Usage:")
        lines.append("")
        lines.append(fence(json.dumps(usage, ensure_ascii=False, indent=2), "json"))
        lines.append("")
    cost = event.get("total_cost_usd")
    if cost is not None:
        lines.append(f"Total cost USD: `{cost}`")
        lines.append("")


def convert(
    input_path: Path,
    output_path: Path,
    include_thinking: bool,
    max_tool_input_chars: int,
    max_tool_result_chars: int,
) -> None:
    events = load_jsonl(input_path)
    counters = {"assistant": 0, "thinking": 0, "tool_call": 0, "tool_result": 0, "user": 0}
    lines: list[str] = [
        "# Claude Code Trajectory",
        "",
        f"Source: `{input_path}`",
        "",
    ]

    init = next((event for event in events if event.get("type") == "system" and event.get("subtype") == "init"), None)
    if init:
        lines.append("## Metadata")
        lines.append("")
        metadata = {
            "cwd": init.get("cwd"),
            "session_id": init.get("session_id"),
            "model": init.get("model"),
            "claude_code_version": init.get("claude_code_version"),
            "tools": init.get("tools"),
        }
        lines.append(fence(json.dumps(metadata, ensure_ascii=False, indent=2), "json"))
        lines.append("")

    for event in events:
        event_type = event.get("type")
        if event_type == "assistant":
            message = event.get("message") or {}
            blocks = message.get("content") or []
            if isinstance(blocks, list):
                render_content_blocks(blocks, lines, counters, include_thinking, max_tool_input_chars)
        elif event_type == "user":
            message = event.get("message") or {}
            if isinstance(message, dict):
                render_user_message(message, lines, counters, max_tool_result_chars)
        elif event_type == "result":
            render_result(event, lines)
        elif event_type == "raw":
            counters["tool_result"] += 1
            lines.append(f"## Raw Line {event.get('line_no')}")
            lines.append("")
            lines.append(fence(truncate_text(first_text(event.get("text")), max_tool_result_chars)))
            lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Assistant messages: {counters['assistant']}")
    lines.append(f"- Tool calls: {counters['tool_call']}")
    lines.append(f"- Tool results: {counters['tool_result']}")
    if include_thinking:
        lines.append(f"- Thinking blocks: {counters['thinking']}")
    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert Claude Code stream-json trace to trajectory.md.")
    parser.add_argument(
        "path",
        type=Path,
        help="Path to claude.stream.jsonl, a trace directory, or a run directory containing one Claude stream.",
    )
    parser.add_argument("--output", type=Path, help="Output markdown path. Defaults to trajectory.md next to the stream.")
    parser.add_argument("--include-thinking", action="store_true", help="Include Claude thinking blocks in the markdown.")
    parser.add_argument("--max-tool-input-chars", type=int, default=4000)
    parser.add_argument("--max-tool-result-chars", type=int, default=6000)
    args = parser.parse_args()

    input_path = find_claude_stream(args.path)
    output_path = args.output or (input_path.parent / "trajectory.md")
    convert(
        input_path=input_path,
        output_path=output_path,
        include_thinking=args.include_thinking,
        max_tool_input_chars=args.max_tool_input_chars,
        max_tool_result_chars=args.max_tool_result_chars,
    )
    print(output_path)


if __name__ == "__main__":
    main()
