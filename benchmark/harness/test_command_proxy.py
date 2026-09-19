#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import socketserver
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


PROXY_TOOLS = (
    "cargo",
    "composer",
    "dotnet",
    "go",
    "gradle",
    "make",
    "mvn",
    "npm",
    "npx",
    "phpunit",
    "pnpm",
    "pytest",
    "python",
    "python3",
    "yarn",
)

FORWARDED_ENV_NAMES = {
    "AWX_LOGGING_MODE",
    "AWX_MODE",
    "CGO_ENABLED",
    "CI",
    "CYPRESS_INSTALL_BINARY",
    "DATABASE_URL",
    "DJANGO_SETTINGS_MODULE",
    "GOFLAGS",
    "MONGODB_URI",
    "NODE_ENV",
    "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD",
    "PUPPETEER_SKIP_DOWNLOAD",
    "PYTEST_ADDOPTS",
    "REDIS_URL",
    "RUST_BACKTRACE",
    "RUST_LOG",
    "SKIP_PG_VERSION_CHECK",
    "SKIP_SECRET_KEY_CHECK",
    "TEST_DATABASE_URL",
}
FORWARDED_ENV_PREFIXES = (
    "ANSIBLE_",
    "AWX_",
    "DJANGO_",
    "ECOSYNC_TEST_",
    "PYTEST_",
    "SETUPTOOLS_SCM_PRETEND_VERSION",
    "TEST_",
)
SAFE_PYTHONPATH_PREFIXES = ("/workspace", "/ecosync-scratch/tmp")


def is_test_invocation(tool: str, args: list[str]) -> bool:
    lowered = [item.lower() for item in args]
    if tool == "ecosync-test-shell":
        return len(args) == 2 and args[0] == "-lc" and bool(args[1].strip())
    if tool == "go":
        return bool(lowered and lowered[0] == "test")
    if tool == "pytest" or tool == "phpunit":
        return True
    if tool in {"python", "python3"}:
        return len(lowered) >= 2 and lowered[:2] == ["-m", "pytest"]
    if tool == "cargo" or tool == "dotnet":
        return bool(lowered and lowered[0] == "test")
    if tool in {"npm", "pnpm", "yarn"}:
        return any(item == "test" or item.startswith("test:") for item in lowered)
    if tool == "npx":
        return any(item in {"jest", "mocha", "pytest", "vitest"} for item in lowered[:3])
    if tool == "mvn":
        return any(item in {"test", "verify", "integration-test"} or item.endswith(":test") for item in lowered)
    if tool == "gradle":
        return any("test" in item or item == "check" for item in lowered if not item.startswith("-"))
    if tool == "composer":
        return any(item == "test" or item.startswith("test-") for item in lowered)
    if tool == "make":
        return any("test" in item or item in {"check", "verify"} for item in lowered if not item.startswith("-"))
    return False


def forwarded_test_environment(environ: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, value in environ.items():
        if name == "PYTHONPATH":
            safe_entries = [
                entry
                for entry in value.split(os.pathsep)
                if entry and any(entry == prefix or entry.startswith(prefix + "/") for prefix in SAFE_PYTHONPATH_PREFIXES)
            ]
            if safe_entries:
                result[name] = os.pathsep.join(safe_entries)
            continue
        if name in FORWARDED_ENV_NAMES or name.startswith(FORWARDED_ENV_PREFIXES):
            result[name] = value
    return result


def split_mount_spec(mount: str) -> tuple[str, str, str]:
    parts = mount.rsplit(":", 2)
    if len(parts) == 3 and parts[2] in {"ro", "rw", "z", "Z"}:
        return parts[0], parts[1], parts[2]
    parts = mount.rsplit(":", 1)
    if len(parts) == 2:
        return parts[0], parts[1], "rw"
    return mount, "", "rw"


def executor_cache_mounts(mounts: list[str]) -> list[str]:
    excluded_prefixes = (
        "/workspace",
        "/traces",
        "/ecosync-scratch",
        "/ecosync-llm-api",
        "/ecosync-test-proxy",
        "/opt/ecosync/agent-runtime",
    )
    result = []
    for mount in mounts:
        _host, container, _mode = split_mount_spec(mount)
        if not container or any(container == prefix or container.startswith(prefix + "/") for prefix in excluded_prefixes):
            continue
        result.append(mount)
    return result


def write_proxy_shims(
    root: Path,
    client_script: str,
    socket_path: str,
    *,
    extra_tools: tuple[str, ...] = (),
) -> Path:
    shims = root / "shims"
    shims.mkdir(parents=True, exist_ok=True)
    shim = shims / "ecosync-test-tool"
    shim.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'tool="$(basename "$0")"',
                f"client={client_script!r}",
                f"socket_path={socket_path!r}",
                'python_bin=/usr/bin/python3',
                '[ -x "$python_bin" ] || python_bin=/usr/local/bin/python3',
                'proxy_python=("$python_bin" -E)',
                'if "${proxy_python[@]}" "$client" should-proxy "$tool" "$@"; then',
                '  exec "${proxy_python[@]}" "$client" client --socket "$socket_path" --cwd "$PWD" --tool "$tool" -- "$@"',
                "fi",
                'shim_dir="$(cd "$(dirname "$0")" && pwd)"',
                'PATH="$(printf "%s" "$PATH" | awk -v RS=: -v ORS=: -v skip="$shim_dir" \'$0 != skip { print }\')"',
                'PATH="${PATH%:}"',
                "export PATH",
                'exec "$tool" "$@"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    shim.chmod(0o755)
    for tool in (*PROXY_TOOLS, *extra_tools):
        link = shims / tool
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(shim.name)
    return shims


def sync_target_repo(source: Path, destination: Path, excluded: list[Path]) -> None:
    command = ["rsync", "-a", "--delete", "--exclude", "/.git"]
    for relative in excluded:
        command.extend(["--exclude", "/" + relative.as_posix().lstrip("/")])
    command.extend([str(source) + "/", str(destination) + "/"])
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


class TestProxyState:
    def __init__(
        self,
        *,
        agent_workspace: Path,
        executor_workspace: Path,
        target_repo_path: Path,
        topology_excludes: list[Path],
        sandbox_image: str,
        sandbox_mounts: list[str],
        sandbox_env: list[str],
        sandbox_user: str | None,
        scratch_tmp: Path | None,
        log_path: Path,
        timeout: int,
        allow_shell: bool = False,
    ) -> None:
        self.agent_workspace = agent_workspace
        self.executor_workspace = executor_workspace
        self.target_repo_path = target_repo_path
        self.topology_excludes = topology_excludes
        self.sandbox_image = sandbox_image
        self.sandbox_mounts = executor_cache_mounts(sandbox_mounts)
        self.sandbox_env = sandbox_env
        self.sandbox_user = sandbox_user
        self.scratch_tmp = scratch_tmp
        self.log_path = log_path
        self.timeout = timeout
        self.allow_shell = allow_shell
        self.lock = threading.Lock()
        self.active_containers: set[str] = set()
        self.active_containers_lock = threading.Lock()

    def append_log(self, record: dict[str, Any]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def remove_container(self, name: str) -> bool:
        try:
            result = subprocess.run(
                ["docker", "rm", "-f", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False
        if result.returncode == 0:
            with self.active_containers_lock:
                self.active_containers.discard(name)
            return True
        return False

    def cleanup_containers(self) -> None:
        with self.active_containers_lock:
            names = list(self.active_containers)
        for name in names:
            self.remove_container(name)

    def build_container_command(
        self,
        container_name: str,
        container_target: Path,
        relative_cwd: Path,
        tool: str,
        args: list[str],
        env: dict[str, str],
    ) -> list[str]:
        command = [
            "docker",
            "run",
            "--name",
            container_name,
            "--network",
            "none",
            "-v",
            f"{self.executor_workspace}:/workspace:rw",
            "-w",
            str(container_target / relative_cwd),
        ]
        if self.sandbox_user:
            command.extend(["--user", self.sandbox_user])
        for item in self.sandbox_env:
            command.extend(["-e", item])
        for mount in self.sandbox_mounts:
            command.extend(["-v", mount])
        if self.scratch_tmp is not None:
            command.extend(["-v", f"{self.scratch_tmp}:/ecosync-scratch/tmp:rw"])
        for name, value in sorted(env.items()):
            command.extend(["-e", f"{name}={value}"])
        executor_tool = "bash" if tool == "ecosync-test-shell" else tool
        command.extend(
            [
                self.sandbox_image,
                "sh",
                "-lc",
                'if [ -r /etc/profile.d/ecosync-tools.sh ]; then . /etc/profile.d/ecosync-tools.sh; fi; '
                'if [ -n "${ECOSYNC_DEBUG_SHIMS:-}" ] && [ -d "$ECOSYNC_DEBUG_SHIMS" ]; then '
                'PATH="$ECOSYNC_DEBUG_SHIMS:$PATH"; export PATH; fi; '
                'exec "$@"',
                "sh",
                executor_tool,
                *args,
            ]
        )
        return command

    def execute(self, cwd: str, tool: str, args: list[str], env: dict[str, str], send: Any) -> int:
        if tool == "ecosync-test-shell" and not self.allow_shell:
            raise ValueError("shell test proxy is disabled")
        if not is_test_invocation(tool, args):
            raise ValueError(f"not an allowed test invocation: {tool} {args}")
        container_target = Path("/workspace") / self.target_repo_path
        requested = Path(cwd)
        try:
            relative_cwd = requested.relative_to(container_target)
        except ValueError as exc:
            raise ValueError(f"test command cwd is outside the target repo: {cwd}") from exc
        source = self.agent_workspace / self.target_repo_path
        destination = self.executor_workspace / self.target_repo_path
        started = time.time()
        with self.lock:
            sync_target_repo(source, destination, self.topology_excludes)
            container_name = f"ecosync-test-{os.getpid()}-{uuid.uuid4().hex[:12]}"
            command = self.build_container_command(
                container_name,
                container_target,
                relative_cwd,
                tool,
                args,
                env,
            )
            proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            with self.active_containers_lock:
                self.active_containers.add(container_name)
            assert proc.stdout is not None
            output_errors: list[Exception] = []

            def forward_output() -> None:
                try:
                    for line in proc.stdout:
                        try:
                            send({"type": "output", "data": line})
                        except (BrokenPipeError, ConnectionError, OSError) as exc:
                            output_errors.append(exc)
                finally:
                    proc.stdout.close()

            output_thread = threading.Thread(target=forward_output, daemon=True)
            output_thread.start()
            try:
                exit_code = proc.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                self.remove_container(container_name)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                try:
                    send({"type": "output", "data": f"\n[ecosync] test command timed out after {self.timeout}s\n"})
                except (BrokenPipeError, ConnectionError, OSError):
                    pass
                exit_code = 124
            finally:
                output_thread.join(timeout=10)
                self.remove_container(container_name)
        self.append_log(
            {
                "timestamp": int(started),
                "cwd": cwd,
                "tool": tool,
                "args": args,
                "exit_code": exit_code,
                "duration_sec": round(time.time() - started, 3),
                "output_connection_lost": bool(output_errors),
            }
        )
        return exit_code


class TestProxyHandler(socketserver.StreamRequestHandler):
    def send_record(self, record: dict[str, Any]) -> None:
        self.wfile.write(json.dumps(record).encode("utf-8") + b"\n")
        self.wfile.flush()

    def handle(self) -> None:
        try:
            request = json.loads(self.rfile.readline().decode("utf-8"))
            state: TestProxyState = self.server.state  # type: ignore[attr-defined]
            exit_code = state.execute(
                str(request.get("cwd") or ""),
                str(request.get("tool") or ""),
                [str(item) for item in request.get("args") or []],
                {str(name): str(value) for name, value in (request.get("env") or {}).items()},
                self.send_record,
            )
            self.send_record({"type": "exit", "code": exit_code})
        except Exception as exc:
            self.send_record({"type": "error", "message": str(exc), "code": 125})


class ThreadingUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


@contextmanager
def serve_test_proxy(socket_path: Path, state: TestProxyState) -> Iterator[None]:
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()
    server = ThreadingUnixServer(str(socket_path), TestProxyHandler)
    socket_path.chmod(0o777)
    server.state = state  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield
    finally:
        server.shutdown()
        state.cleanup_containers()
        server.server_close()
        thread.join(timeout=5)
        if socket_path.exists():
            socket_path.unlink()


def client(socket_path: Path, cwd: str, tool: str, args: list[str]) -> int:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(str(socket_path))
        request = json.dumps(
            {
                "cwd": cwd,
                "tool": tool,
                "args": args,
                "env": forwarded_test_environment(dict(os.environ)),
            }
        ).encode("utf-8") + b"\n"
        connection.sendall(request)
        stream = connection.makefile("r", encoding="utf-8")
        for line in stream:
            record = json.loads(line)
            if record.get("type") == "output":
                print(record.get("data", ""), end="", flush=True)
            elif record.get("type") == "exit":
                return int(record.get("code", 1))
            elif record.get("type") == "error":
                print(f"[ecosync] test executor error: {record.get('message', 'unknown error')}", file=os.sys.stderr)
                return int(record.get("code", 125))
    return 125


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("should-proxy")
    check.add_argument("tool")
    check.add_argument("args", nargs=argparse.REMAINDER)
    run = subparsers.add_parser("client")
    run.add_argument("--socket", required=True, type=Path)
    run.add_argument("--cwd", required=True)
    run.add_argument("--tool", required=True)
    run.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command == "should-proxy":
        raise SystemExit(0 if is_test_invocation(args.tool, args.args) else 1)
    forwarded = args.args[1:] if args.args[:1] == ["--"] else args.args
    raise SystemExit(client(args.socket, args.cwd, args.tool, forwarded))


if __name__ == "__main__":
    main()
