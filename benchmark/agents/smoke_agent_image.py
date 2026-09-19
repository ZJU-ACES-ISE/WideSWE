#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def run(cmd: list[str]) -> None:
    print("$ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test an WIDESWE agent Docker image.")
    parser.add_argument("--image", required=True)
    parser.add_argument("--workspace", type=Path, default=Path("/tmp/ecosync-agent-image-smoke"))
    parser.add_argument("--sandbox-user", default=None)
    parser.add_argument(
        "--claude-online",
        action="store_true",
        help="Also run a tiny Claude Code stream-json request. Requires local Claude auth mounts.",
    )
    parser.add_argument("--claude-auth-dir", type=Path, default=Path.home() / ".claude")
    parser.add_argument("--claude-config", type=Path, default=Path.home() / ".claude.json")
    parser.add_argument("--claude-home", default="/tmp/ecosync-home")
    args = parser.parse_args()

    args.workspace.mkdir(parents=True, exist_ok=True)

    run(
        [
            "docker",
            "run",
            "--rm",
            args.image,
            "bash",
            "-lc",
            (
                "set -e; "
                "claude --version; "
                "node --version; "
                "npm --version; "
                "git --version; "
                "rg --version | head -1; "
                "python3 --version; "
                "bash --version | head -1; "
                "command -v sed awk grep find xargs curl ssh"
            ),
        ]
    )

    workspace_cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{args.workspace}:/workspace",
        "-w",
        "/workspace",
    ]
    if args.sandbox_user:
        workspace_cmd.extend(["--user", args.sandbox_user])
    workspace_cmd.extend(
        [
            args.image,
            "bash",
            "-lc",
            (
                "set -e; "
                "rm -rf repo trace.jsonl; "
                "mkdir repo; cd repo; "
                "git init -q; "
                "printf hello > a.txt; "
                "git add a.txt; "
                "git diff --cached -- a.txt >/workspace/diff.patch; "
                "rg hello .; "
                "python3 -c 'from pathlib import Path; Path(\"/workspace/trace.jsonl\").write_text(\"{}\\\\n\")'; "
                "test -s /workspace/diff.patch; "
                "test -s /workspace/trace.jsonl"
            ),
        ]
    )
    run(workspace_cmd)

    if args.claude_online:
        if not args.claude_auth_dir.exists():
            raise SystemExit(f"missing Claude auth dir: {args.claude_auth_dir}")
        if not args.claude_config.exists():
            raise SystemExit(f"missing Claude config: {args.claude_config}")
        online_cmd = [
            "docker",
            "run",
            "--rm",
            "-e",
            f"HOME={args.claude_home}",
            "-v",
            f"{args.workspace}:/workspace",
            "-v",
            f"{args.claude_auth_dir}:{args.claude_home}/.claude:rw",
            "-v",
            f"{args.claude_config}:{args.claude_home}/.claude.json:rw",
            "-w",
            "/workspace",
        ]
        if args.sandbox_user:
            online_cmd.extend(["--user", args.sandbox_user])
        online_cmd.extend(
            [
                args.image,
                "bash",
                "-lc",
                (
                    "set -o pipefail; "
                    "mkdir -p claude-smoke-trace; "
                    "printf 'Reply with exactly: ECOSYNC_CLAUDE_SMOKE_OK\\n' > claude-smoke-prompt.txt; "
                    "claude -p --permission-mode bypassPermissions --max-budget-usd 1 "
                    "--verbose --output-format stream-json < claude-smoke-prompt.txt "
                    "| tee claude-smoke-trace/claude.stream.jsonl; "
                    "test -s claude-smoke-trace/claude.stream.jsonl; "
                    "grep -q ECOSYNC_CLAUDE_SMOKE_OK claude-smoke-trace/claude.stream.jsonl"
                ),
            ]
        )
        run(online_cmd)

    print("agent image smoke passed")


if __name__ == "__main__":
    main()
