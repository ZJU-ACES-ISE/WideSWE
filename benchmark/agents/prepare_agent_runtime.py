#!/usr/bin/env python3
"""Build credential-free Claude Code or Codex runtimes for case containers."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


VERSIONS = {"claude-code": "2.1.139", "codex": "0.147.0"}
PACKAGES = {"claude-code": "@anthropic-ai/claude-code", "codex": "@openai/codex"}
DEFAULT_OUTPUT_ROOT = Path(
    os.environ.get(
        "WIDESWE_AGENT_RUNTIME_ROOT",
        Path.home() / ".cache" / "wideswe" / "agent-runtimes",
    )
)


def run(command: list[str]) -> None:
    print("$ " + " ".join(command), flush=True)
    subprocess.run(command, check=True)


def write_executable(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o111)


def require_binary(name: str) -> Path:
    value = shutil.which(name)
    if not value:
        raise SystemExit(f"{name} is required to build an agent runtime")
    return Path(value).resolve()


def build_runtime(agent: str, output_root: Path) -> Path:
    version = VERSIONS[agent]
    package = PACKAGES[agent]
    runtime_dir = output_root / f"{agent}-{version}"
    output_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=f".{agent}-runtime.", dir=output_root) as name:
        root = Path(name)
        install_root = root / "install"
        run(
            [
                "npm",
                "install",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                "--prefix",
                str(install_root),
                f"{package}@{version}",
            ]
        )
        package_root = install_root / "node_modules" / Path(*package.split("/"))
        metadata = json.loads((package_root / "package.json").read_text(encoding="utf-8"))
        if metadata.get("version") != version:
            raise SystemExit(f"installed {package} version does not match {version}")

        shutil.copytree(install_root / "node_modules", root / "lib" / "node_modules", symlinks=True)
        (root / "bin").mkdir(parents=True)
        shutil.copy2(require_binary("node"), root / "bin" / "node")
        shutil.copy2(require_binary("rg"), root / "bin" / "rg")

        if agent == "claude-code":
            entrypoint = "$runtime/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
            command = "claude"
        else:
            entrypoint = "$runtime/lib/node_modules/@openai/codex/bin/codex.js"
            command = "codex"

        write_executable(
            root / "shim-bin" / command,
            "\n".join(
                [
                    "#!/usr/bin/env bash",
                    "set -euo pipefail",
                    'runtime="${ECOSYNC_AGENT_RUNTIME:-/opt/ecosync/agent-runtime}"',
                    f'exec "$runtime/bin/node" "{entrypoint}" "$@"',
                    "",
                ]
            ),
        )
        write_executable(
            root / "shim-bin" / "rg",
            "\n".join(
                [
                    "#!/usr/bin/env bash",
                    "set -euo pipefail",
                    'runtime="${ECOSYNC_AGENT_RUNTIME:-/opt/ecosync/agent-runtime}"',
                    'exec "$runtime/bin/rg" "$@"',
                    "",
                ]
            ),
        )
        (root / "env.sh").write_text(
            "\n".join(
                [
                    "export ECOSYNC_AGENT_RUNTIME=/opt/ecosync/agent-runtime",
                    'export PATH="$ECOSYNC_AGENT_RUNTIME/shim-bin:$PATH"',
                    "export NO_COLOR=1",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (root / "VERSION").write_text(version + "\n", encoding="utf-8")
        if runtime_dir.exists():
            shutil.rmtree(runtime_dir)
        os.rename(root, runtime_dir)

    print(runtime_dir)
    return runtime_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=[*VERSIONS, "all"], default="all")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()

    agents = list(VERSIONS) if args.agent == "all" else [args.agent]
    for agent in agents:
        build_runtime(agent, args.output_root.expanduser().resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
