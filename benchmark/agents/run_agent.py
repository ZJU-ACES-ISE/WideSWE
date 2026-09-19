#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmark.harness.task_selection import resolve_task_selection
from benchmark.harness import ecosync_harness
from benchmark.harness.test_command_proxy import TestProxyState, serve_test_proxy, write_proxy_shims
from benchmark.agents.case_env_services import (
    POSTGRES_BOOTSTRAP_MARKER,
    POSTGRES_DEBUG_SETTINGS,
    automation_reports_local_settings_base64,
    postgres_bootstrap_sql_base64,
)

DEFAULT_TASK_ID = None
TASKS_DIR = REPO_ROOT / "benchmark" / "tasks"
TASK_DIR_OVERRIDE: Path | None = None
SHELL_LIKE_AGENTS = {"shell", "claude-code", "codex"}
AGENT_ENVIRONMENTS = {"agent-only", "case-env", "host"}
SANDBOX_SCRATCH_CONTAINER = "/ecosync-scratch"
LLM_API_PROXY_CONTAINER_DIR = "/ecosync-llm-api"
LLM_API_PROXY_CONTAINER_SOCKET = f"{LLM_API_PROXY_CONTAINER_DIR}/proxy.sock"
LLM_API_PROXY_CONTAINER_SCRIPT = "/opt/ecosync/llm_api_proxy.py"
LLM_API_PROXY_CONTAINER_PORT = 18080
# Container/Python startup can exceed a minute while the shared Docker root is
# under heavy I/O. This wait occurs before the Agent timeout starts.
LLM_API_RELAY_START_ATTEMPTS = 12000
LLM_API_PROXY_NO_PROXY = "localhost,127.0.0.1,::1"
LLM_API_PROXY_SCRIPT = REPO_ROOT / "benchmark" / "harness" / "llm_api_proxy.py"
TEST_PROXY_SCRIPT = REPO_ROOT / "benchmark" / "harness" / "test_command_proxy.py"
TEST_PROXY_CONTAINER_DIR = "/ecosync-test-proxy"
TEST_PROXY_CONTAINER_SOCKET = f"{TEST_PROXY_CONTAINER_DIR}/proxy.sock"
TEST_PROXY_CONTAINER_SCRIPT = "/opt/ecosync/test_command_proxy.py"
AGENT_COMMAND_STARTED_MARKER = ".ecosync-agent-command-started"
AGENT_FINAL_RESULT_GRACE_SEC = 30
CASE_SERVICE_READY_CONTAINER_DIR = "/ecosync-services"
DOCKER_LIFECYCLE_LOCK = Path(os.environ.get("ECOSYNC_DOCKER_LIFECYCLE_LOCK", "/run/lock/ecosyncbench-docker.lock"))
SANDBOX_SCRATCH_ENV = {
    "HOME": f"{SANDBOX_SCRATCH_CONTAINER}/home",
    "CODEX_HOME": f"{SANDBOX_SCRATCH_CONTAINER}/codex",
    "XDG_CACHE_HOME": f"{SANDBOX_SCRATCH_CONTAINER}/cache/xdg",
    "XDG_CONFIG_HOME": f"{SANDBOX_SCRATCH_CONTAINER}/config",
    "XDG_DATA_HOME": f"{SANDBOX_SCRATCH_CONTAINER}/data",
    "TMPDIR": f"{SANDBOX_SCRATCH_CONTAINER}/tmp",
    "DOTNET_CLI_HOME": f"{SANDBOX_SCRATCH_CONTAINER}/dotnet",
    "PIP_CACHE_DIR": f"{SANDBOX_SCRATCH_CONTAINER}/cache/pip",
    "UV_CACHE_DIR": f"{SANDBOX_SCRATCH_CONTAINER}/cache/uv",
    "NPM_CONFIG_CACHE": f"{SANDBOX_SCRATCH_CONTAINER}/cache/npm",
    "YARN_CACHE_FOLDER": f"{SANDBOX_SCRATCH_CONTAINER}/cache/yarn",
    "PNPM_HOME": f"{SANDBOX_SCRATCH_CONTAINER}/pnpm",
    "PLAYWRIGHT_BROWSERS_PATH": f"{SANDBOX_SCRATCH_CONTAINER}/cache/ms-playwright",
    "GOMODCACHE": f"{SANDBOX_SCRATCH_CONTAINER}/cache/go-mod",
    "GOCACHE": f"{SANDBOX_SCRATCH_CONTAINER}/cache/go-build",
    "GOPATH": f"{SANDBOX_SCRATCH_CONTAINER}/go",
    "GRADLE_USER_HOME": f"{SANDBOX_SCRATCH_CONTAINER}/cache/gradle",
    "MAVEN_CONFIG": f"{SANDBOX_SCRATCH_CONTAINER}/maven",
    "COMPOSER_HOME": f"{SANDBOX_SCRATCH_CONTAINER}/composer/home",
    "COMPOSER_CACHE_DIR": f"{SANDBOX_SCRATCH_CONTAINER}/cache/composer",
}
MAX_AGENT_TIMEOUT_SEC = 18000
MAX_API_REQUESTS = 200
LLM_BASE_URL_ENV_NAMES = {
    "ANTHROPIC_BASE_URL",
    "LLM_BASE_URL",
    "DEEPSEEK_BASE_URL",
    "OPENAI_BASE_URL",
    "SWE_AGENT_API_BASE",
}


def agent_timeout_deadline(
    deadline: float | None,
    marker_exists: bool,
    now: float,
    timeout: int,
) -> float | None:
    if deadline is None and marker_exists:
        return now + timeout
    return deadline


def trace_has_successful_result(
    trace_path: Path,
    *,
    start_offsets: dict[Path, int] | None = None,
    tail_bytes: int = 65536,
) -> bool:
    """Return whether the current agent invocation emitted a successful result."""
    start_offsets = start_offsets or {}
    for path in trace_path.glob("*.jsonl"):
        try:
            size = path.stat().st_size
            minimum_offset = start_offsets.get(path, 0)
            if size < minimum_offset:
                minimum_offset = 0
            if size <= minimum_offset:
                continue
            with path.open("rb") as stream:
                offset = max(minimum_offset, size - tail_bytes)
                stream.seek(offset)
                if offset > minimum_offset:
                    stream.readline()
                lines = stream.readlines()
        except OSError:
            continue
        for raw_line in reversed(lines):
            try:
                event = json.loads(raw_line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if event.get("type") != "result":
                continue
            return event.get("subtype") == "success" and event.get("is_error") is not True
    return False


def load_run(run_dir: Path) -> dict[str, Any]:
    run_file = run_dir / "run.json"
    if not run_file.exists():
        raise SystemExit(f"missing run metadata: {run_file}")
    return json.loads(run_file.read_text(encoding="utf-8"))


def prompt_file(workspace: Path) -> Path:
    path = workspace / ".ecosyncbench" / "prompt.md"
    if not path.exists():
        raise SystemExit(f"missing prompt: {path}")
    return path


@contextmanager
def agent_visible_manifest(workspace: Path):
    """Expose only filesystem navigation metadata while an agent is running."""
    manifest_path = workspace / ".ecosyncbench" / "manifest.json"
    if not manifest_path.exists():
        yield
        return

    original = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(original)
    public_manifest = {
        "repos": [
            {"name": repo["name"], "path": repo["path"]}
            for repo in manifest.get("repos", [])
        ]
    }
    manifest_path.write_text(json.dumps(public_manifest, indent=2) + "\n", encoding="utf-8")
    try:
        yield
    finally:
        manifest_path.write_text(original, encoding="utf-8")


def public_repo_env_mount(task_id: str, workspace: Path, run_dir: Path) -> str | None:
    """Expose dependency roots only for repositories with an exact mapping."""
    task_path = TASK_DIR_OVERRIDE or (TASKS_DIR / task_id)
    source = task_path / "environment" / "case_env" / "repo_envs.tsv"
    manifest_path = workspace / ".ecosyncbench" / "manifest.json"
    if not source.exists() or not manifest_path.exists():
        return None

    mappings: dict[str, str] = {}
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        relative_path, deps_root = line.split("\t", 1)
        mappings[relative_path] = deps_root
    if not mappings:
        return None

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = []
    for repo in manifest.get("repos", []):
        relative_path = str(repo["path"])
        if relative_path in mappings:
            rows.append(f"{relative_path}\t{mappings[relative_path]}")
    if not rows:
        return None

    public_path = run_dir / "agent_runtime" / "repo_envs.tsv"
    public_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return f"{public_path}:/opt/ecosync/repo-envs.tsv:ro"


def format_agent_command(command: str, values: dict[str, str]) -> str:
    formatted = command
    for name, value in values.items():
        formatted = formatted.replace("{" + name + "}", value)
    return formatted


def case_workspace_prepare_command() -> str:
    """Expose baked Node dependencies and optionally run the legacy copier.

    Modern case environments route repository-specific dependencies through
    mounted runtime roots. A repository-local node_modules symlink is required
    for Node ESM package resolution, which does not honor NODE_PATH. The
    mapping is public and repository-specific, so this does not copy one
    repository's dependency tree into another repository. The legacy copier
    remains available as an explicit opt-in for old images.
    """
    return (
        "workspace=${ECOSYNC_CONTAINER_WORKSPACE:-/workspace}; "
        "repo_envs=${ECOSYNC_REPO_ENVS:-/opt/ecosync/repo-envs.tsv}; "
        "if [ -r \"$repo_envs\" ]; then "
        "while IFS=$(printf '\\t') read -r rel root; do "
        "[ -n \"${rel:-}\" ] && [ -n \"${root:-}\" ] || continue; "
        "repo=$workspace/$rel; baked=$root/deps/node_modules; "
        "if [ -f \"$repo/package.json\" ] && [ ! -e \"$repo/node_modules\" ] "
        "&& [ -d \"$baked\" ]; then ln -s \"$baked\" \"$repo/node_modules\"; fi; "
        "done < \"$repo_envs\"; fi; "
        "elasticsearch_repo=$workspace/repos/elastic/elasticsearch; "
        "if [ -d \"$elasticsearch_repo\" ] "
        "&& [ -n \"${ECOSYNC_ELASTICSEARCH_BUILD_CACHE:-}\" ] "
        "&& [ -d \"$ECOSYNC_ELASTICSEARCH_BUILD_CACHE\" ]; then "
        "export GRADLE_USER_HOME=\"$ECOSYNC_ELASTICSEARCH_BUILD_CACHE\"; fi; "
        "if [ -f \"$elasticsearch_repo/branches.json\" ] "
        "&& [ -n \"${GRADLE_USER_HOME:-}\" ]; then "
        "mkdir -p \"$GRADLE_USER_HOME\"; "
        "elasticsearch_gradle_properties=$GRADLE_USER_HOME/gradle.properties; "
        "if ! grep -q '^org.elasticsearch.build.branches-file-location=' "
        "\"$elasticsearch_gradle_properties\" 2>/dev/null; then "
        "printf '%s\\n' \"org.elasticsearch.build.branches-file-location="
        "$elasticsearch_repo/branches.json\" >>\"$elasticsearch_gradle_properties\"; fi; "
        "if [ \"${ECOSYNC_PACKAGE_NETWORK_DISABLED:-0}\" = \"1\" ]; then "
        "mkdir -p \"$GRADLE_USER_HOME/init.d\"; "
        "printf '%s\\n' 'gradle.startParameter.offline = true' "
        ">\"$GRADLE_USER_HOME/init.d/ecosync-offline.gradle\"; fi; fi; "
        "kibana_repo=$workspace/repos/elastic/kibana; "
        "if [ -d \"$kibana_repo\" ] && command -v node >/dev/null 2>&1 "
        "&& [ ! -s \"$kibana_repo/src/platform/packages/private/kbn-repo-packages/package-map.json\" ] "
        "&& [ ! -s \"$kibana_repo/packages/kbn-repo-packages/package-map.json\" ]; then "
        "(cd \"$kibana_repo\" && node -e 'const fs=require(\"fs\"); "
        "const p=fs.existsSync(\"./src/platform/packages/private/kbn-repo-packages/index.js\")"
        "?\"./src/platform/packages/private/kbn-repo-packages\":"
        "fs.existsSync(\"./packages/kbn-repo-packages/index.js\")"
        "?\"./packages/kbn-repo-packages\":null; "
        "if(p){const m=require(p);m.updatePackageMap(process.cwd(),"
        "Array.from(m.getRepoRelsSync(process.cwd(),[\"**/kibana.jsonc\"])));"
        "const t=\"./packages/kbn-ts-projects/config-paths.json\";"
        "if(fs.existsSync(\"./packages/kbn-ts-projects\")){"
        "fs.writeFileSync(t,JSON.stringify(Array.from(m.getRepoRelsSync("
        "process.cwd(),[\"tsconfig.json\",\"**/tsconfig.json\"])).sort(),null,2));}}') "
        "|| echo '[ecosync] Kibana package map preparation failed; continuing' >&2; fi; "
        "eui_repo=$workspace/repos/elastic/eui; "
        "if [ -d \"$eui_repo/node_modules\" ] "
        "&& [ -f \"$eui_repo/packages/eui/package.json\" ] "
        "&& [ ! -e \"$eui_repo/packages/eui/node_modules\" ]; then "
        "ln -s ../../node_modules \"$eui_repo/packages/eui/node_modules\"; fi; "
        "android_repo=$workspace/repos/dotnet/android; "
        "java_interop_repo=$workspace/repos/dotnet/java-interop; "
        "if [ -f \"$android_repo/src/Microsoft.Android.Sdk.TrimmableTypeMap/"
        "Microsoft.Android.Sdk.TrimmableTypeMap.csproj\" ] "
        "&& [ -f \"$java_interop_repo/src/utils/NullableAttributes.cs\" ]; then "
        "rm -rf \"$android_repo/external/Java.Interop\"; "
        "ln -s \"$java_interop_repo\" \"$android_repo/external/Java.Interop\"; fi; "
        "diagnostics_repo=$workspace/repos/dotnet/diagnostics; "
        "diagnostics_dotnet=$diagnostics_repo/artifacts/dotnet-test; "
        "if [ -d \"$diagnostics_repo\" ] "
        "&& [ -n \"${ECOSYNC_DOTNET_TEST_ROOT:-}\" ] "
        "&& [ -x \"$ECOSYNC_DOTNET_TEST_ROOT/dotnet\" ]; then "
        "mkdir -p \"$diagnostics_repo/artifacts\"; "
        "if [ ! -e \"$diagnostics_dotnet\" ] || [ -L \"$diagnostics_dotnet\" ]; then "
        "rm -rf \"$diagnostics_dotnet\"; "
        "ln -s \"$ECOSYNC_DOTNET_TEST_ROOT\" \"$diagnostics_dotnet\"; fi; fi; "
        "auto_prepare=0; "
        "if [ -d \"$workspace/repos/dotnet/java-interop\" ]; then "
        "for dependency in /opt/ecosync/xamarin-android-tools-* "
        "/opt/ecosync/image-deps/*/xamarin-android-tools-*; do "
        "if [ -d \"$dependency\" ]; then auto_prepare=1; break; fi; done; fi; "
        "if { [ \"${ECOSYNC_SKIP_CASE_WORKSPACE_PREPARE:-1}\" != \"1\" ] "
        "|| [ \"$auto_prepare\" = \"1\" ]; } "
        "&& command -v ecosync-case-prepare-workspace >/dev/null 2>&1; then "
        "ecosync-case-prepare-workspace /workspace >&2 || "
        "echo '[ecosync] workspace dependency preparation failed; continuing' >&2; fi"
    )


def case_env_prestart_services_command() -> str:
    """Start common local services and provision repo-specific debug databases."""
    postgres_sql_base64 = shlex.quote(postgres_bootstrap_sql_base64())
    postgres_settings = shlex.quote("\n" + POSTGRES_DEBUG_SETTINGS)
    automation_reports_settings = shlex.quote(automation_reports_local_settings_base64())
    return (
        "if [ \"${ECOSYNC_CASE_ENV_PRESTART_SERVICES:-1}\" = \"1\" ]; then "
        "ecosync_timeout=''; command -v timeout >/dev/null 2>&1 && ecosync_timeout='timeout 20s'; "
        "if [ -d /workspace/repos/ansible/awx ]; then "
        "mkdir -p /var/log/tower && chmod 0777 /var/log/tower 2>/dev/null || true; "
        "fi; "
        "ecosync_has_postgres=0; "
        "if [ \"${ECOSYNC_CASE_ENV_POSTGRES:-0}\" = \"1\" ] && "
        "command -v service >/dev/null 2>&1 && [ -x /etc/init.d/postgresql ]; then "
        "ecosync_has_postgres=1; fi; "
        "if [ \"$ecosync_has_postgres\" = \"1\" ]; then "
        "for ecosync_pg_conf in /etc/postgresql/*/main/postgresql.conf; do "
        "[ -f \"$ecosync_pg_conf\" ] || continue; "
        f"printf %s {postgres_settings} >>\"$ecosync_pg_conf\"; done; "
        "( ${ecosync_timeout:-} service postgresql start >/tmp/ecosync-agent-postgres.log 2>&1 || "
        "cat /tmp/ecosync-agent-postgres.log >&2 || true ); "
        "if command -v su >/dev/null 2>&1 && id postgres >/dev/null 2>&1; then "
        "if [ \"${ECOSYNC_CASE_ENV_POSTGRES:-0}\" = \"1\" ] && "
        "{ [ -d /workspace/repos/ansible/automation-reports ] || "
        "[ -d /workspace/repos/ansible/metrics-service ]; }; then "
        f"if [ ! -f {POSTGRES_BOOTSTRAP_MARKER} ]; then "
        f"printf %s {postgres_sql_base64} | base64 -d | "
        "timeout 360s su postgres -c \"psql -v ON_ERROR_STOP=1\" "
        ">/tmp/ecosync-agent-postgres-bootstrap.log 2>&1 || "
        "cat /tmp/ecosync-agent-postgres-bootstrap.log >&2 || true; fi; "
        "else "
        "${ecosync_timeout:-} su postgres -c \"psql -v ON_ERROR_STOP=1 -c \\\"ALTER USER postgres PASSWORD 'password';\\\"\" >/dev/null 2>&1 || true; "
        "${ecosync_timeout:-} su postgres -c \"psql -tc \\\"SELECT 1 FROM pg_roles WHERE rolname='gw'\\\" | grep -q 1 || createuser gw\" >/dev/null 2>&1 || true; "
        "${ecosync_timeout:-} su postgres -c \"psql -v ON_ERROR_STOP=1 -c \\\"ALTER USER gw PASSWORD 'password';\\\"\" >/dev/null 2>&1 || true; "
        "${ecosync_timeout:-} su postgres -c \"psql -tc \\\"SELECT 1 FROM pg_database WHERE datname='gw_db'\\\" | grep -q 1 || createdb -O gw gw_db\" >/dev/null 2>&1 || true; "
        "fi; "
        "fi; fi; "
        "if [ -d /workspace/repos/ansible/automation-reports ] || "
        "[ -d /workspace/repos/ansible/metrics-service ]; then "
        "export DB_NAME=aapdashboard DB_USER=aapdashboard DB_PASSWORD=aapdashboard "
        "DB_HOST=127.0.0.1 DB_PORT=5432; "
        "export METRICS_SERVICE_MODE=test "
        "METRICS_SERVICE_DATABASES__DEFAULT__HOST=127.0.0.1 "
        "METRICS_SERVICE_DATABASES__DEFAULT__USER=metrics_service "
        "METRICS_SERVICE_DATABASES__DEFAULT__PASSWORD=metrics_service "
        "METRICS_SERVICE_DATABASES__AWX__HOST=127.0.0.1 "
        "METRICS_SERVICE_DATABASES__AWX__USER=awx "
        "METRICS_SERVICE_DATABASES__AWX__PASSWORD=awx; "
        "ecosync_reports_settings=/workspace/repos/ansible/automation-reports/src/backend/django_config/local_settings.py; "
        "if [ -d \"$(dirname \"$ecosync_reports_settings\")\" ] && "
        "[ ! -e \"$ecosync_reports_settings\" ]; then "
        f"printf %s {automation_reports_settings} | base64 -d >\"$ecosync_reports_settings\"; "
        "chmod 0644 \"$ecosync_reports_settings\"; fi; "
        "fi; "
        "if command -v redis-server >/dev/null 2>&1 && ! pgrep -x redis-server >/dev/null 2>&1; then "
        "( ${ecosync_timeout:-} redis-server --daemonize yes >/tmp/ecosync-agent-redis.log 2>&1 || true ); "
        "fi; "
        "if [ \"${ECOSYNC_CASE_ENV_POSTGRES_SIDECAR:-0}\" = \"1\" ]; then "
        "ecosync_postgres_wait=0; "
        f"until [ -f {CASE_SERVICE_READY_CONTAINER_DIR}/postgres.ready ]; do "
        "ecosync_postgres_wait=$((ecosync_postgres_wait + 1)); "
        "[ \"$ecosync_postgres_wait\" -lt 480 ] || "
        "{ echo 'timed out waiting for PostgreSQL sidecar readiness' >&2; exit 127; }; "
        "sleep 0.25; "
        "done; "
        "ecosync_postgres_wait=0; "
        "until bash -c '</dev/tcp/127.0.0.1/5432' >/dev/null 2>&1; do "
        "ecosync_postgres_wait=$((ecosync_postgres_wait + 1)); "
        "[ \"$ecosync_postgres_wait\" -lt 240 ] || "
        "{ echo 'timed out waiting for PostgreSQL sidecar on 127.0.0.1:5432' >&2; exit 127; }; "
        "sleep 0.25; "
        "done; "
        "ecosync_postgres_settle=${ECOSYNC_CASE_ENV_POSTGRES_SETTLE_SECONDS:-0}; "
        "case \"$ecosync_postgres_settle\" in ''|*[!0-9]*) ecosync_postgres_settle=0;; esac; "
        "[ \"$ecosync_postgres_settle\" -eq 0 ] || sleep \"$ecosync_postgres_settle\"; "
        "export PGHOST=127.0.0.1 PGPORT=5432 "
        "PGUSER=${ECOSYNC_CASE_ENV_POSTGRES_USER:-postgres} "
        "PGPASSWORD=${ECOSYNC_CASE_ENV_POSTGRES_PASSWORD:-} "
        "PGDATABASE=${ECOSYNC_CASE_ENV_POSTGRES_DB:-postgres}; "
        "if [ \"${ECOSYNC_CASE_ENV_POSTGRES_PRISMA:-0}\" = \"1\" ]; then "
        "export TEST_POSTGRES_URI=postgres://$PGUSER:$PGPASSWORD@127.0.0.1:5432/$PGDATABASE "
        "TEST_POSTGRES_URI_MIGRATE=postgres://$PGUSER:$PGPASSWORD@127.0.0.1:5432/tests-migrate "
        "TEST_POSTGRES_SHADOWDB_URI_MIGRATE=postgres://$PGUSER:$PGPASSWORD@127.0.0.1:5432/tests-migrate-shadowdb "
        "TEST_FUNCTIONAL_POSTGRES_URI=postgres://$PGUSER:$PGPASSWORD@127.0.0.1:5432/PRISMA_DB_NAME "
        "TEST_DATABASE_URL=postgresql://$PGUSER:$PGPASSWORD@127.0.0.1:5432/$PGDATABASE; "
        "fi; "
        "fi; "
        "if [ \"${ECOSYNC_CASE_ENV_MONGODB:-0}\" = \"1\" ]; then "
        "ecosync_mongo_wait=0; "
        f"until [ -f {CASE_SERVICE_READY_CONTAINER_DIR}/mongodb.ready ]; do "
        "ecosync_mongo_wait=$((ecosync_mongo_wait + 1)); "
        "[ \"$ecosync_mongo_wait\" -lt 800 ] || "
        "{ echo 'timed out waiting for MongoDB sidecar readiness' >&2; exit 127; }; "
        "sleep 0.25; "
        "done; "
        "ecosync_mongo_wait=0; "
        "until bash -c '</dev/tcp/127.0.0.1/27017' >/dev/null 2>&1; do "
        "ecosync_mongo_wait=$((ecosync_mongo_wait + 1)); "
        "[ \"$ecosync_mongo_wait\" -lt 120 ] || "
        "{ echo 'timed out waiting for MongoDB sidecar on 127.0.0.1:27017' >&2; exit 127; }; "
        "sleep 0.25; "
        "done; "
        "ecosync_mongo_settle=${ECOSYNC_CASE_ENV_MONGODB_SETTLE_SECONDS:-0}; "
        "case \"$ecosync_mongo_settle\" in ''|*[!0-9]*) ecosync_mongo_settle=0;; esac; "
        "[ \"$ecosync_mongo_settle\" -eq 0 ] || sleep \"$ecosync_mongo_settle\"; "
        "export DOCTRINE_MONGODB_SERVER=mongodb://127.0.0.1:27017 "
        "MONGODB_URI=mongodb://127.0.0.1:27017; "
        "fi; "
        "fi"
    )


def nested_shell_shim_profile() -> str:
    return "\n".join(
        [
            "_ecosync_reprepend_runtime_shims() {",
            "  local shim_dir",
            '  for shim_dir in "${ECOSYNC_AGENT_RUNTIME:+$ECOSYNC_AGENT_RUNTIME/shim-bin}" "${ECOSYNC_AGENT_RUNTIME:+$ECOSYNC_AGENT_RUNTIME/bin}" "${ECOSYNC_DEBUG_SHIMS:-}" "${ECOSYNC_TEST_PROXY_SHIMS:-}"; do',
            '    [ -n "$shim_dir" ] && [ -d "$shim_dir" ] || continue',
            '    PATH="$(printf "%s" "$PATH" | awk -v RS=: -v ORS=: -v skip="$shim_dir" \'$0 != skip { print }\')"',
            '    PATH="${PATH%:}"',
            "  done",
            '  if [ -n "${ECOSYNC_AGENT_RUNTIME:-}" ]; then',
            '    [ -d "$ECOSYNC_AGENT_RUNTIME/bin" ] && PATH="$ECOSYNC_AGENT_RUNTIME/bin:$PATH"',
            '    [ -d "$ECOSYNC_AGENT_RUNTIME/shim-bin" ] && PATH="$ECOSYNC_AGENT_RUNTIME/shim-bin:$PATH"',
            "  fi",
            '  if [ -n "${ECOSYNC_DEBUG_SHIMS:-}" ] && [ -d "$ECOSYNC_DEBUG_SHIMS" ]; then',
            '    PATH="$ECOSYNC_DEBUG_SHIMS:$PATH"',
            "  fi",
            '  if [ -n "${ECOSYNC_TEST_PROXY_SHIMS:-}" ] && [ -d "$ECOSYNC_TEST_PROXY_SHIMS" ]; then',
            '    PATH="$ECOSYNC_TEST_PROXY_SHIMS:$PATH"',
            "  fi",
            "  export PATH",
            "  hash -r 2>/dev/null || true",
            "}",
            "cd() {",
            '  if builtin cd "$@"; then',
            "    if type _ecosync_activate_repo_env >/dev/null 2>&1; then",
            "      _ecosync_activate_repo_env >/dev/null 2>&1 || true",
            "    fi",
            "    _ecosync_reprepend_runtime_shims",
            "    return 0",
            "  fi",
            "  return $?",
            "}",
            "_ecosync_reprepend_runtime_shims",
            "",
        ]
    )


def log_paths(run_dir: Path) -> tuple[Path, Path, Path]:
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir / "agent.stdout.log", logs_dir / "agent.stderr.log", logs_dir / "agent.json"


def safe_slug(value: str) -> str:
    cleaned = []
    for char in value.strip():
        if char.isalnum() or char in ("-", "_", "."):
            cleaned.append(char)
        else:
            cleaned.append("_")
    return "".join(cleaned).strip("._") or "unknown"


def docker_safe_name(*parts: str) -> str:
    raw = "-".join(safe_slug(part) for part in parts if part)
    name = f"ecosync-{raw}-{os.getpid()}"
    return name[:120].rstrip("-_.")


def trace_dir(run_dir: Path, agent_id: str, model_id: str) -> Path:
    path = run_dir / "traces" / safe_slug(agent_id) / safe_slug(model_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def sandbox_scratch_dir(run_dir: Path) -> Path:
    path = run_dir / "agent_scratch"
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def case_env_tmpfs_spec(agent_environment: str) -> str | None:
    if agent_environment != "case-env":
        return None
    size = os.environ.get("ECOSYNC_CASE_TMPFS_SIZE", "8g").strip()
    if not size or size.lower() in {"0", "off", "none"}:
        return None
    return f"/tmp:rw,exec,nosuid,nodev,size={size},mode=1777"


@contextmanager
def single_repo_test_executor(
    task_id: str,
    run_dir: Path,
    run_meta: dict[str, Any],
    agent_workspace: Path,
    sandbox: str,
    agent_environment: str,
    sandbox_image: str | None,
    sandbox_mounts: list[str],
    sandbox_env: list[str],
    sandbox_user: str | None,
):
    if (
        run_meta.get("workspace_scope") != "single-repo"
        or not run_meta.get("single_repo")
        or sandbox != "docker"
        or agent_environment != "case-env"
        or not sandbox_image
    ):
        yield {"enabled": False, "mounts": [], "env": []}
        return

    single_repo = str(run_meta["single_repo"])
    task_path = TASK_DIR_OVERRIDE or (TASKS_DIR / task_id)
    ecosync_harness.TASKS_DIR = task_path.parent.resolve()
    executor_workspace = run_dir / "agent_debug_workspace"
    repo_modes = {
        name: ("base" if name == single_repo else "gold")
        for name in ecosync_harness.load_repos(task_id)["repos"]
    }
    ecosync_harness.prepare_workspace(
        task_id,
        executor_workspace,
        "base",
        force=True,
        workspace_scope="ecosystem",
        repo_modes=repo_modes,
    )
    agent_manifest = json.loads((agent_workspace / ".ecosyncbench" / "manifest.json").read_text(encoding="utf-8"))
    target_record = next(repo for repo in agent_manifest["repos"] if repo["name"] == single_repo)
    target_repo_path = Path(target_record["path"])
    executor_manifest = json.loads(
        (executor_workspace / ".ecosyncbench" / "manifest.json").read_text(encoding="utf-8")
    )
    topology_excludes = []
    for item in executor_manifest.get("workspace_topology") or []:
        if item.get("repo") != single_repo or not item.get("path"):
            continue
        try:
            topology_excludes.append(Path(str(item["path"])).relative_to(target_repo_path))
        except ValueError:
            continue

    proxy_root = Path(tempfile.mkdtemp(prefix="ecosync-test-proxy-"))
    scratch_tmp = run_dir / "agent_scratch" / "tmp"
    scratch_tmp.mkdir(parents=True, exist_ok=True)
    scratch_tmp.chmod(0o777)
    socket_path = proxy_root / "proxy.sock"
    allow_shell_test_proxy = "ECOSYNC_RQ2_SHELL_TEST_PROXY=1" in sandbox_env
    shims = write_proxy_shims(
        proxy_root,
        TEST_PROXY_CONTAINER_SCRIPT,
        TEST_PROXY_CONTAINER_SOCKET,
        extra_tools=("ecosync-test-shell",) if allow_shell_test_proxy else (),
    )
    state = TestProxyState(
        agent_workspace=agent_workspace,
        executor_workspace=executor_workspace,
        target_repo_path=target_repo_path,
        topology_excludes=topology_excludes,
        sandbox_image=sandbox_image,
        sandbox_mounts=sandbox_mounts,
        sandbox_env=sandbox_env,
        sandbox_user=sandbox_user,
        scratch_tmp=scratch_tmp,
        log_path=run_dir / "logs" / "test-proxy.jsonl",
        timeout=int(os.environ.get("ECOSYNC_AGENT_TEST_TIMEOUT_SEC", "1800")),
        allow_shell=allow_shell_test_proxy,
    )
    metadata = {
        "enabled": True,
        "executor_workspace": str(executor_workspace),
        "target_repo": single_repo,
        "target_repo_path": str(target_repo_path),
        "dependency_modes": repo_modes,
        "topology_excludes": [str(path) for path in topology_excludes],
        "mounts": [
            f"{proxy_root}:{TEST_PROXY_CONTAINER_DIR}:ro",
            f"{TEST_PROXY_SCRIPT}:{TEST_PROXY_CONTAINER_SCRIPT}:ro",
        ],
        "env": [f"ECOSYNC_TEST_PROXY_SHIMS={TEST_PROXY_CONTAINER_DIR}/shims"],
        "shims": str(shims),
        "shell_test_proxy": allow_shell_test_proxy,
    }
    try:
        with serve_test_proxy(socket_path, state):
            yield metadata
    finally:
        shutil.rmtree(proxy_root, ignore_errors=True)


def env_assignment_key(item: str) -> str:
    return item.split("=", 1)[0]


def sandbox_scratch_env(sandbox_env: list[str]) -> list[str]:
    explicit = {env_assignment_key(item) for item in sandbox_env}
    return [f"{key}={value}" for key, value in SANDBOX_SCRATCH_ENV.items() if key not in explicit]


def sandbox_scratch_path_vars(sandbox_env: list[str]) -> list[str]:
    explicit = {env_assignment_key(item) for item in sandbox_env}
    return [key for key in SANDBOX_SCRATCH_ENV if key not in explicit]


def sandbox_scratch_setup_command(sandbox_env: list[str]) -> str:
    path_vars = sandbox_scratch_path_vars(sandbox_env)
    values = " ".join(f'"${{{key}:-}}"' for key in path_vars) or '"${TMPDIR:-}"'
    return (
        f"for ecosync_scratch_path in {values}; do "
        '[ -z "$ecosync_scratch_path" ] || mkdir -p "$ecosync_scratch_path"; '
        '[ -z "$ecosync_scratch_path" ] || chmod 0777 "$ecosync_scratch_path" 2>/dev/null || true; '
        "done"
    )


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def workspace_repo_paths(workspace: Path) -> list[Path]:
    manifest_path = workspace / ".ecosyncbench" / "manifest.json"
    if not manifest_path.exists():
        return []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    paths = []
    for repo in manifest.get("repos", []):
        relative_path = repo.get("path")
        if relative_path:
            paths.append((workspace / relative_path).resolve())
    return paths


def parse_gitdir(repo_path: Path) -> Path | None:
    git_path = repo_path / ".git"
    if git_path.is_dir():
        return git_path.resolve()
    if not git_path.is_file():
        return None
    first_line = git_path.read_text(encoding="utf-8").splitlines()[0].strip()
    if not first_line.startswith("gitdir:"):
        return None
    gitdir_value = first_line.split(":", 1)[1].strip()
    gitdir = Path(gitdir_value)
    if not gitdir.is_absolute():
        gitdir = (repo_path / gitdir).resolve()
    return gitdir


def common_git_dir(gitdir: Path) -> Path:
    common_file = gitdir / "commondir"
    if not common_file.exists():
        return gitdir.resolve()
    common_value = common_file.read_text(encoding="utf-8").strip()
    common = Path(common_value)
    if not common.is_absolute():
        common = (gitdir / common).resolve()
    return common


def sandbox_git_metadata_mounts(workspace: Path) -> list[str]:
    """Reject unsafe workspaces whose Git object database lives outside the sandbox."""
    unsafe: list[str] = []
    for repo_path in workspace_repo_paths(workspace):
        gitdir = parse_gitdir(repo_path)
        if gitdir is not None and not is_relative_to(gitdir, workspace):
            unsafe.append(f"{repo_path}: gitdir={gitdir}, common={common_git_dir(gitdir)}")
    if unsafe:
        details = "\n".join(f"- {item}" for item in unsafe)
        raise SystemExit(
            "refusing to expose external Git metadata to the agent sandbox; "
            "recreate the run with the isolated workspace preparer:\n" + details
        )
    return []


def write_metadata(path: Path, metadata: dict[str, Any]) -> None:
    path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


@contextmanager
def docker_lifecycle_lock():
    """Serialize container create/run/remove work against the external Docker root."""
    DOCKER_LIFECYCLE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with DOCKER_LIFECYCLE_LOCK.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(f"pid={os.getpid()} acquired_at={int(time.time())}\n")
            handle.flush()
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def llm_gateway_settings(sandbox_env: list[str], pass_env: list[str]) -> tuple[str, list[str]]:
    values: dict[str, str] = {}
    for name in pass_env:
        if name in LLM_BASE_URL_ENV_NAMES and os.environ.get(name):
            values[name] = os.environ[name]
    for item in sandbox_env:
        if "=" not in item:
            continue
        name, value = item.split("=", 1)
        if name in LLM_BASE_URL_ENV_NAMES:
            values[name] = value
    parsed_values = [(name, value, urlsplit(value)) for name, value in values.items()]
    loopback_hosts = {"127.0.0.1", "::1", "localhost"}
    valid = [
        (name, value, parsed)
        for name, value, parsed in parsed_values
        if parsed.hostname
        and (
            parsed.scheme == "https"
            or (parsed.scheme == "http" and parsed.hostname in loopback_hosts)
        )
    ]
    if not valid:
        raise SystemExit(
            "llm-api-only requires an explicit HTTPS API base URL or loopback HTTP base URL "
            "in sandbox_env/pass_env "
            f"({', '.join(sorted(LLM_BASE_URL_ENV_NAMES))})"
        )
    origins = {
        (parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        for _name, _value, parsed in valid
    }
    if len(origins) != 1:
        raise SystemExit("llm-api-only requires all configured API base URLs to share one origin")
    scheme, host, port = next(iter(origins))
    default_port = 443 if scheme == "https" else 80
    upstream = f"{scheme}://{host}" + (f":{port}" if port != default_port else "")
    overrides = []
    for name, _value, parsed in valid:
        local = urlunsplit(("http", f"127.0.0.1:{LLM_API_PROXY_CONTAINER_PORT}", parsed.path, parsed.query, ""))
        overrides.append(f"{name}={local}")
    return upstream, overrides


def llm_api_relay_start_command() -> str:
    ready_file = "/tmp/ecosync-llm-api-relay.ready"
    log_file = "/tmp/ecosync-llm-api-relay.log"
    return (
        f"rm -f {ready_file}; "
        f"python3 {LLM_API_PROXY_CONTAINER_SCRIPT} relay "
        f"--socket {LLM_API_PROXY_CONTAINER_SOCKET} "
        f"--host 127.0.0.1 --port {LLM_API_PROXY_CONTAINER_PORT} "
        f"--ready-file {ready_file} >{log_file} 2>&1 & "
        "ecosync_relay_pid=$!; "
        "ecosync_relay_wait=0; "
        f"while [ ! -f {ready_file} ]; do "
        "kill -0 \"$ecosync_relay_pid\" 2>/dev/null || "
        f"{{ cat {log_file} >&2; exit 127; }}; "
        "ecosync_relay_wait=$((ecosync_relay_wait + 1)); "
        f"[ \"$ecosync_relay_wait\" -lt {LLM_API_RELAY_START_ATTEMPTS} ] || "
        f"{{ echo 'timed out starting LLM API relay' >&2; cat {log_file} >&2; exit 127; }}; "
        "sleep 0.05; "
        "done; "
    )


@contextmanager
def llm_api_proxy_server(
    run_dir: Path,
    allowed_hosts: list[str],
    proxy_dir: Path,
    upstream_url: str,
    max_api_requests: int,
    request_isolation_key: str,
):
    if not allowed_hosts:
        raise SystemExit("--llm-api-allowed-host is required with --sandbox-network llm-api-only")
    proxy_dir.chmod(0o755)
    socket_path = proxy_dir / "proxy.sock"
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = logs_dir / "llm-api-proxy.stdout.log"
    stderr_path = logs_dir / "llm-api-proxy.stderr.log"
    cmd = [
        sys.executable,
        str(LLM_API_PROXY_SCRIPT),
        "gateway",
        "--socket",
        str(socket_path),
        "--upstream-url",
        upstream_url,
        "--max-requests",
        str(max_api_requests),
        "--log",
        str(logs_dir / "llm-api-requests.jsonl"),
        "--request-isolation-key",
        request_isolation_key,
    ]
    upstream_host = urlsplit(upstream_url).hostname
    if upstream_host not in allowed_hosts:
        raise SystemExit(f"LLM API upstream host is not allowlisted: {upstream_host}")
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        proc = subprocess.Popen(cmd, stdout=stdout, stderr=stderr, text=True)
        try:
            deadline = time.monotonic() + 10
            while not socket_path.exists():
                if proc.poll() is not None:
                    raise SystemExit(f"LLM API proxy exited during startup; see {stderr_path}")
                if time.monotonic() >= deadline:
                    raise SystemExit(f"timed out starting LLM API proxy; see {stderr_path}")
                time.sleep(0.05)
            socket_path.chmod(0o666)
            yield proxy_dir
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            if socket_path.exists():
                socket_path.unlink()
            proxy_dir.rmdir()


def docker_rm_force(
    container_name: str | None,
    *,
    timeout: int = 20,
    wait_for_container: int = 5,
) -> None:
    if not container_name:
        return
    deadline = time.monotonic() + wait_for_container
    while True:
        try:
            inspect = subprocess.run(
                ["docker", "container", "inspect", container_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except subprocess.TimeoutExpired:
            inspect = None
        if inspect is not None and inspect.returncode == 0:
            break
        if time.monotonic() >= deadline:
            return
        time.sleep(0.25)
    try:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pass


def docker_sidecar_name(container_name: str, service: str) -> str:
    suffix = f"-{safe_slug(service)}"
    return f"{container_name[:120 - len(suffix)]}{suffix}".rstrip("-_.")


def sandbox_env_enabled(sandbox_env: list[str], name: str) -> bool:
    prefix = f"{name}="
    for item in reversed(sandbox_env):
        if item.startswith(prefix):
            return item[len(prefix) :].strip().lower() in {"1", "true", "yes", "on"}
    return False


def sandbox_env_value(sandbox_env: list[str], name: str, default: str = "") -> str:
    prefix = f"{name}="
    for item in reversed(sandbox_env):
        if item.startswith(prefix):
            return item[len(prefix) :]
    return default


def docker_map_container_hostname_to_loopback(container_name: str) -> None:
    """Make an isolated container hostname resolve for local replica sets."""
    completed = subprocess.run(
        [
            "docker",
            "exec",
            "--user",
            "0",
            container_name,
            "sh",
            "-c",
            'name="$(hostname)"; '
            'grep -Eq "(^|[[:space:]])${name}([[:space:]]|$)" /etc/hosts '
            '|| printf "127.0.0.1\\t%s\\n" "$name" >> /etc/hosts',
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"failed to map agent container hostname to loopback: {completed.stdout.strip()}"
        )


def docker_start_mongodb_sidecar(
    container_name: str,
    sidecar_name: str,
    *,
    image: str = "mongo:7",
    wait_for_container: int = 15,
) -> None:
    """Attach MongoDB to an agent's network namespace without exposing Docker."""
    deadline = time.monotonic() + wait_for_container
    while True:
        try:
            inspect = subprocess.run(
                ["docker", "container", "inspect", container_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except subprocess.TimeoutExpired:
            inspect = None
        if inspect is not None and inspect.returncode == 0:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError(f"agent container was not created: {container_name}")
        time.sleep(0.1)

    if "mongodb-atlas-local" in image:
        docker_map_container_hostname_to_loopback(container_name)

    docker_rm_force(sidecar_name, wait_for_container=0)
    image_command = ["--quiet"] if image.startswith("mongo:") else []
    try:
        started = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                sidecar_name,
                "--label",
                "ecosyncbench.managed=true",
                "--label",
                f"ecosyncbench.sidecar_for={container_name}",
                "--network",
                f"container:{container_name}",
                image,
                *image_command,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("timed out starting MongoDB sidecar") from exc
    if started.returncode != 0:
        raise RuntimeError(f"failed to start MongoDB sidecar: {started.stdout.strip()}")

    ready_deadline = time.monotonic() + (180 if "mongodb-atlas-local" in image else 90)
    while True:
        try:
            if "mongodb-atlas-local" in image:
                ready = subprocess.run(
                    [
                        "docker",
                        "container",
                        "inspect",
                        "--format",
                        "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
                        sidecar_name,
                    ],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                )
                is_ready = ready.stdout.strip() == "healthy"
            else:
                ready = subprocess.run(
                    [
                        "docker",
                        "exec",
                        sidecar_name,
                        "mongosh",
                        "--quiet",
                        "--eval",
                        "db.adminCommand({ping:1}).ok",
                    ],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                )
                is_ready = ready.returncode == 0 and ready.stdout.strip() == "1"
        except subprocess.TimeoutExpired:
            is_ready = False
        if is_ready:
            return
        if time.monotonic() >= ready_deadline:
            raise RuntimeError(f"MongoDB sidecar did not become ready: {image}")
        time.sleep(0.5)


def _docker_start_postgres_sidecar_once(
    container_name: str,
    sidecar_name: str,
    *,
    image: str,
    user: str,
    password: str,
    database: str,
    trust: bool,
    extra_databases: list[str],
    wait_for_container: int = 90,
) -> None:
    """Attach the task's PostgreSQL service to an agent network namespace."""
    deadline = time.monotonic() + wait_for_container
    while True:
        try:
            inspect = subprocess.run(
                ["docker", "container", "inspect", container_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except subprocess.TimeoutExpired:
            inspect = None
        if inspect is not None and inspect.returncode == 0:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError(f"agent container was not created: {container_name}")
        time.sleep(0.1)

    docker_rm_force(sidecar_name, wait_for_container=0)
    command = [
        "docker",
        "run",
        "-d",
        "--rm",
        "--name",
        sidecar_name,
        "--label",
        "ecosyncbench.managed=true",
        "--label",
        f"ecosyncbench.sidecar_for={container_name}",
        "--network",
        f"container:{container_name}",
        "-e",
        f"POSTGRES_USER={user}",
        "-e",
        f"POSTGRES_DB={database}",
        "-e",
        "POSTGRES_INITDB_ARGS=--no-sync",
    ]
    if trust:
        command.extend(["-e", "POSTGRES_HOST_AUTH_METHOD=trust"])
    else:
        command.extend(["-e", f"POSTGRES_PASSWORD={password}"])
    command.append(image)
    try:
        started = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("timed out starting PostgreSQL sidecar") from exc
    if started.returncode != 0:
        raise RuntimeError(f"failed to start PostgreSQL sidecar: {started.stdout.strip()}")

    ready_deadline = time.monotonic() + 90
    while True:
        try:
            ready = subprocess.run(
                [
                    "docker",
                    "exec",
                    sidecar_name,
                    "psql",
                    "-U",
                    user,
                    "-d",
                    database,
                    "-tAc",
                    "SELECT 1",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except subprocess.TimeoutExpired:
            ready = None
        if ready is not None and ready.returncode == 0:
            break
        if time.monotonic() >= ready_deadline:
            raise RuntimeError("PostgreSQL sidecar did not become query-ready within 90 seconds")
        time.sleep(0.5)

    for extra_database in extra_databases:
        bootstrap_deadline = time.monotonic() + 30
        last_error = ""
        while True:
            try:
                exists = subprocess.run(
                    [
                        "docker",
                        "exec",
                        sidecar_name,
                        "psql",
                        "-U",
                        user,
                        "-d",
                        database,
                        "-tAc",
                        f"SELECT 1 FROM pg_database WHERE datname = '{extra_database}'",
                    ],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=10,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                exists = None
                last_error = "database existence check timed out"
            if exists is not None and exists.returncode == 0 and exists.stdout.strip() == "1":
                break
            if exists is not None and exists.returncode == 0:
                try:
                    created = subprocess.run(
                        ["docker", "exec", sidecar_name, "createdb", "-U", user, extra_database],
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        timeout=15,
                        check=False,
                    )
                except subprocess.TimeoutExpired:
                    created = None
                    last_error = "database creation timed out"
                if created is not None and created.returncode == 0:
                    break
                if created is not None:
                    last_error = created.stdout.strip()
            elif exists is not None:
                last_error = exists.stdout.strip()
            if time.monotonic() >= bootstrap_deadline:
                raise RuntimeError(
                    f"failed to create PostgreSQL database {extra_database}: {last_error}"
                )
            time.sleep(0.5)


def docker_start_postgres_sidecar(
    container_name: str,
    sidecar_name: str,
    *,
    image: str,
    user: str,
    password: str,
    database: str,
    trust: bool,
    extra_databases: list[str],
    wait_for_container: int = 90,
) -> None:
    """Start PostgreSQL, rebuilding it once if bootstrap terminates transiently."""
    first_error: RuntimeError | None = None
    for attempt in range(2):
        try:
            _docker_start_postgres_sidecar_once(
                container_name,
                sidecar_name,
                image=image,
                user=user,
                password=password,
                database=database,
                trust=trust,
                extra_databases=extra_databases,
                wait_for_container=wait_for_container,
            )
            return
        except RuntimeError as exc:
            if attempt == 1:
                if first_error is None:
                    raise
                raise RuntimeError(
                    f"PostgreSQL sidecar bootstrap failed twice; first error: {first_error}; "
                    f"second error: {exc}"
                ) from exc
            first_error = exc
            docker_rm_force(sidecar_name, wait_for_container=0)
            time.sleep(0.5)


def docker_container_exit_code(container_name: str | None, *, timeout: int = 5) -> int | None:
    """Return the real exit code once a named container has stopped."""
    if not container_name:
        return None
    try:
        result = subprocess.run(
            ["docker", "container", "inspect", "--format", "{{json .State}}", container_name],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
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
    try:
        return int(state["ExitCode"])
    except (KeyError, TypeError, ValueError):
        return None


def stop_docker_cli(proc: subprocess.Popen[str], *, timeout: int = 5) -> None:
    """Stop a Docker CLI process without waiting indefinitely on daemon I/O."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass


def capture_docker_container_state(container_name: str | None, path: Path, docker_cli_exit_code: int) -> dict[str, Any]:
    record: dict[str, Any] = {
        "container_name": container_name,
        "docker_cli_exit_code": docker_cli_exit_code,
        "captured_at": int(time.time()),
        "inspect_available": False,
    }
    if container_name:
        try:
            result = subprocess.run(
                ["docker", "container", "inspect", container_name],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=15,
                check=False,
            )
        except subprocess.TimeoutExpired:
            record["inspect_error"] = "docker inspect timed out after 15s"
            write_metadata(path, record)
            return record
        record["inspect_exit_code"] = result.returncode
        if result.returncode == 0:
            try:
                inspected = json.loads(result.stdout)[0]
            except (json.JSONDecodeError, IndexError, TypeError):
                record["inspect_error"] = result.stdout.strip()
            else:
                state = inspected.get("State") or {}
                record.update(
                    {
                        "inspect_available": True,
                        "status": state.get("Status"),
                        "running": state.get("Running"),
                        "oom_killed": state.get("OOMKilled"),
                        "dead": state.get("Dead"),
                        "exit_code": state.get("ExitCode"),
                        "error": state.get("Error"),
                        "started_at": state.get("StartedAt"),
                        "finished_at": state.get("FinishedAt"),
                    }
                )
        else:
            record["inspect_error"] = result.stdout.strip()
    write_metadata(path, record)
    return record


def apply_gold(task_id: str, workspace: Path, stdout_path: Path, stderr_path: Path) -> int:
    cmd = [
        "python3",
        str(REPO_ROOT / "benchmark" / "harness" / "ecosync_harness.py"),
        "apply-gold",
        "--task",
        task_id,
        "--workspace",
        str(workspace),
        "--allow-already-applied",
    ]
    env = os.environ.copy()
    if TASK_DIR_OVERRIDE is not None:
        env["ECOSYNC_TASKS_DIR"] = str(TASK_DIR_OVERRIDE.parent)
    with stdout_path.open("w", encoding="utf-8") as out, stderr_path.open("w", encoding="utf-8") as err:
        proc = subprocess.run(cmd, cwd=REPO_ROOT, env=env, text=True, stdout=out, stderr=err)
    return proc.returncode


def run_shell(
    task_id: str,
    workspace: Path,
    command: str,
    timeout: int,
    stdout_path: Path,
    stderr_path: Path,
    trace_path: Path,
    agent_id: str,
    model_id: str,
    run_id: str,
    sandbox: str,
    sandbox_image: str | None,
    sandbox_network: str,
    llm_api_allowed_hosts: list[str],
    pass_env: list[str],
    sandbox_mounts: list[str],
    sandbox_user: str | None,
    sandbox_env: list[str],
    sandbox_scratch: Path | None,
    sandbox_container_name: str | None,
    agent_environment: str,
    max_api_requests: int,
) -> tuple[int, bool]:
    public_task_id = "task"
    prompt = prompt_file(workspace)
    prompt_text = prompt.read_text(encoding="utf-8")
    if sandbox == "docker":
        if not sandbox_image:
            raise SystemExit("--sandbox-image is required when --sandbox docker is used")
        format_workspace = "/workspace"
        format_prompt = "/workspace/.ecosyncbench/prompt.md"
        format_trace = "/traces"
    else:
        format_workspace = str(workspace)
        format_prompt = str(prompt)
        format_trace = str(trace_path)

    formatted = format_agent_command(
        command,
        {
            "task_id": public_task_id,
            "workspace": format_workspace,
            "prompt_file": format_prompt,
            "prompt": shlex.quote(prompt_text),
            "trace_dir": format_trace,
            "agent_id": shlex.quote(agent_id),
            "model_id": shlex.quote(model_id),
            "run_id": shlex.quote(run_id),
        },
    )
    env = os.environ.copy()
    env.update(
        {
            "ECOSYNC_TASK_ID": public_task_id,
            "ECOSYNC_WORKSPACE": str(workspace),
            "ECOSYNC_PROMPT_FILE": str(prompt),
            "ECOSYNC_TRACE_DIR": str(trace_path),
            "ECOSYNC_AGENT_ID": agent_id,
            "ECOSYNC_MODEL_ID": model_id,
            "ECOSYNC_RUN_ID": run_id,
            "ECOSYNC_MAX_API_REQUESTS": str(max_api_requests),
            "ECOSYNC_AGENT_TIMEOUT_SEC": str(timeout),
        }
    )
    with stdout_path.open("w", encoding="utf-8") as out, stderr_path.open("w", encoding="utf-8") as err:
        try:
            if sandbox == "docker":
                mongodb_sidecar_requested = sandbox_env_enabled(
                    sandbox_env,
                    "ECOSYNC_CASE_ENV_MONGODB",
                )
                postgres_sidecar_requested = sandbox_env_enabled(
                    sandbox_env,
                    "ECOSYNC_CASE_ENV_POSTGRES_SIDECAR",
                )
                service_ready_dir = None
                if mongodb_sidecar_requested or postgres_sidecar_requested:
                    service_ready_dir = Path(stdout_path).parent.parent / "agent_runtime" / "services"
                    service_ready_dir.mkdir(parents=True, exist_ok=True)
                    for service in ("mongodb", "postgres"):
                        (service_ready_dir / f"{service}.ready").unlink(missing_ok=True)
                agent_started_marker = trace_path / AGENT_COMMAND_STARTED_MARKER
                agent_started_marker.unlink(missing_ok=True)
                trace_start_offsets = {
                    path: path.stat().st_size
                    for path in trace_path.glob("*.jsonl")
                    if path.is_file()
                }
                auto_sandbox_env = sandbox_scratch_env(sandbox_env)
                agent_command = (
                    "if [ -r /etc/profile.d/ecosync-tools.sh ]; then "
                    ". /etc/profile.d/ecosync-tools.sh; fi "
                    "&& if [ -n \"${ECOSYNC_DEBUG_SHIMS:-}\" ] && [ -d \"$ECOSYNC_DEBUG_SHIMS\" ]; then "
                    "PATH=\"$ECOSYNC_DEBUG_SHIMS:$PATH\"; export PATH; fi "
                    "&& if [ -n \"${ECOSYNC_TEST_PROXY_SHIMS:-}\" ] && [ -d \"$ECOSYNC_TEST_PROXY_SHIMS\" ]; then "
                    "PATH=\"$ECOSYNC_TEST_PROXY_SHIMS:$PATH\"; export PATH; fi "
                    "&& if [ -r /etc/profile.d/zz-ecosync-debug-shims.sh ]; then "
                    "export BASH_ENV=/etc/profile.d/zz-ecosync-debug-shims.sh; fi "
                    "&& if [ -n \"${SWE_AGENT_TRAJECTORY_DIR:-}\" ]; then "
                    "mkdir -p \"$SWE_AGENT_TRAJECTORY_DIR\" && "
                    "( chmod 0777 \"$SWE_AGENT_TRAJECTORY_DIR\" 2>/dev/null || true ); fi "
                    f"&& {case_workspace_prepare_command()} "
                    "&& if command -v git >/dev/null 2>&1 && [ -d /workspace/repos ]; then "
                    "find /workspace/repos -mindepth 2 -maxdepth 2 -type d "
                    "-exec git config --global --add safe.directory {} \\; 2>/dev/null || true; fi "
                    "&& umask 000 "
                    f"&& printf '%s\\n' \"$(date +%s)\" > /traces/{AGENT_COMMAND_STARTED_MARKER} "
                    f"&& {formatted}"
                )
                relay_start = ""
                if sandbox_network == "llm-api-only":
                    relay_start = llm_api_relay_start_command()
                prestart_services = sandbox_scratch_setup_command(sandbox_env) + "; " + relay_start + (
                    "if { [ -n \"${ECOSYNC_DEBUG_SHIMS:-}\" ] && [ -d \"$ECOSYNC_DEBUG_SHIMS\" ]; } "
                    "|| { [ -n \"${ECOSYNC_TEST_PROXY_SHIMS:-}\" ] && [ -d \"$ECOSYNC_TEST_PROXY_SHIMS\" ]; }; then "
                    "if [ -w /etc/profile.d ]; then "
                    f"printf %s {shlex.quote(nested_shell_shim_profile())} "
                    "> /etc/profile.d/zz-ecosync-debug-shims.sh; "
                    "fi; fi; "
                    f"{case_env_prestart_services_command()}"
                )
                run_as_user = bool(sandbox_user and agent_environment == "case-env")
                if run_as_user:
                    if ":" in sandbox_user:
                        uid, gid = sandbox_user.split(":", 1)
                    else:
                        uid, gid = sandbox_user, sandbox_user
                    quoted_agent_command = shlex.quote(agent_command)
                    container_command = (
                        f"{prestart_services} && "
                        "if command -v setpriv >/dev/null 2>&1; then "
                        f"exec setpriv --reuid {shlex.quote(uid)} --regid {shlex.quote(gid)} --clear-groups "
                        f"bash -lc {quoted_agent_command}; "
                        "else "
                        "echo 'setpriv is required for non-root case-env agent runs' >&2; exit 127; "
                        "fi"
                    )
                else:
                    container_command = f"{prestart_services} && {agent_command}"
                docker_network = "none" if sandbox_network == "llm-api-only" else sandbox_network
                llm_gateway_upstream = None
                llm_gateway_overrides: list[str] = []
                if sandbox_network == "llm-api-only":
                    llm_gateway_upstream, llm_gateway_overrides = llm_gateway_settings(sandbox_env, pass_env)
                proxy_dir = (
                    Path(tempfile.mkdtemp(prefix="ecosync-llm-api-"))
                    if sandbox_network == "llm-api-only"
                    else None
                )
                docker_cmd = [
                    "docker",
                    "run",
                    "--name",
                    sandbox_container_name,
                    "--label",
                    "ecosyncbench.managed=true",
                    "--label",
                    f"ecosyncbench.task_id={task_id}",
                    "--label",
                    f"ecosyncbench.run_id={run_id}",
                    "--label",
                    f"ecosyncbench.agent_id={agent_id}",
                    "--label",
                    f"ecosyncbench.model_id={model_id}",
                    "--network",
                    docker_network,
                    "-v",
                    f"{workspace}:/workspace:rw",
                    "-v",
                    f"{trace_path}:/traces:rw",
                    "-w",
                    "/workspace",
                ]
                if service_ready_dir is not None:
                    docker_cmd.extend(
                        ["-v", f"{service_ready_dir}:{CASE_SERVICE_READY_CONTAINER_DIR}:ro"]
                    )
                tmpfs_spec = case_env_tmpfs_spec(agent_environment)
                if tmpfs_spec:
                    docker_cmd.extend(["--tmpfs", tmpfs_spec])
                if sandbox_scratch is not None:
                    docker_cmd.extend([
                        "-v",
                        f"{sandbox_scratch}:{SANDBOX_SCRATCH_CONTAINER}:rw",
                    ])
                if sandbox_network == "llm-api-only":
                    assert proxy_dir is not None
                    docker_cmd.extend([
                        "-v",
                        f"{proxy_dir}:{LLM_API_PROXY_CONTAINER_DIR}:ro",
                        "-v",
                        f"{LLM_API_PROXY_SCRIPT}:{LLM_API_PROXY_CONTAINER_SCRIPT}:ro",
                        "-e",
                        f"HTTP_PROXY=http://127.0.0.1:{LLM_API_PROXY_CONTAINER_PORT}",
                        "-e",
                        f"HTTPS_PROXY=http://127.0.0.1:{LLM_API_PROXY_CONTAINER_PORT}",
                        "-e",
                        f"http_proxy=http://127.0.0.1:{LLM_API_PROXY_CONTAINER_PORT}",
                        "-e",
                        f"https_proxy=http://127.0.0.1:{LLM_API_PROXY_CONTAINER_PORT}",
                        "-e",
                        f"NO_PROXY={LLM_API_PROXY_NO_PROXY}",
                        "-e",
                        f"no_proxy={LLM_API_PROXY_NO_PROXY}",
                    ])
                if sandbox_user and not run_as_user:
                    docker_cmd.extend(["--user", sandbox_user])
                docker_cmd.extend([
                    "-e",
                    f"ECOSYNC_TASK_ID={public_task_id}",
                    "-e",
                    "ECOSYNC_WORKSPACE=/workspace",
                    "-e",
                    "ECOSYNC_PROMPT_FILE=/workspace/.ecosyncbench/prompt.md",
                    "-e",
                    "ECOSYNC_TRACE_DIR=/traces",
                    "-e",
                    f"ECOSYNC_AGENT_ID={agent_id}",
                    "-e",
                    f"ECOSYNC_MODEL_ID={model_id}",
                    "-e",
                    f"ECOSYNC_RUN_ID={run_id}",
                ])
                for item in auto_sandbox_env:
                    docker_cmd.extend(["-e", item])
                for item in sandbox_env:
                    docker_cmd.extend(["-e", item])
                for name in pass_env:
                    if name in env:
                        docker_cmd.extend(["-e", name])
                docker_cmd.extend(["-e", f"ECOSYNC_MAX_API_REQUESTS={max_api_requests}"])
                docker_cmd.extend(["-e", f"ECOSYNC_AGENT_TIMEOUT_SEC={timeout}"])
                for item in llm_gateway_overrides:
                    docker_cmd.extend(["-e", item])
                for mount in sandbox_mounts:
                    docker_cmd.extend(["-v", mount])
                docker_cmd.extend([sandbox_image, "sh", "-lc", container_command])
                proxy_context = (
                    llm_api_proxy_server(
                        Path(stdout_path).parent.parent,
                        llm_api_allowed_hosts,
                        proxy_dir,
                        str(llm_gateway_upstream),
                        max_api_requests,
                        hashlib.sha256(
                            f"{task_id}:{run_id}:{agent_id}:{model_id}".encode("utf-8")
                        ).hexdigest()[:32],
                    )
                    if sandbox_network == "llm-api-only"
                    else nullcontext(None)
                )
                with proxy_context:
                    mongodb_sidecar_name = (
                        docker_sidecar_name(sandbox_container_name, "mongodb")
                        if mongodb_sidecar_requested
                        else None
                    )
                    postgres_sidecar_name = (
                        docker_sidecar_name(sandbox_container_name, "postgres")
                        if postgres_sidecar_requested
                        else None
                    )
                    exit_code = 1
                    agent_timed_out = False
                    proc: subprocess.Popen[str] | None = None
                    deadline: float | None = None
                    next_container_check = time.monotonic() + 1
                    successful_result_seen_at: float | None = None
                    try:
                        with docker_lifecycle_lock():
                            docker_rm_force(sandbox_container_name, wait_for_container=0)
                            if mongodb_sidecar_name:
                                docker_rm_force(mongodb_sidecar_name, wait_for_container=0)
                            if postgres_sidecar_name:
                                docker_rm_force(postgres_sidecar_name, wait_for_container=0)
                            proc = subprocess.Popen(
                                docker_cmd,
                                cwd=workspace,
                                text=True,
                                stdout=out,
                                stderr=err,
                            )
                            if mongodb_sidecar_name:
                                docker_start_mongodb_sidecar(
                                    sandbox_container_name,
                                    mongodb_sidecar_name,
                                    image=sandbox_env_value(
                                        sandbox_env,
                                        "ECOSYNC_CASE_ENV_MONGODB_IMAGE",
                                        "mongo:7",
                                    ),
                                )
                                assert service_ready_dir is not None
                                (service_ready_dir / "mongodb.ready").touch()
                            if postgres_sidecar_name:
                                docker_start_postgres_sidecar(
                                    sandbox_container_name,
                                    postgres_sidecar_name,
                                    image=sandbox_env_value(
                                        sandbox_env,
                                        "ECOSYNC_CASE_ENV_POSTGRES_IMAGE",
                                        "postgres:16-alpine",
                                    ),
                                    user=sandbox_env_value(
                                        sandbox_env,
                                        "ECOSYNC_CASE_ENV_POSTGRES_USER",
                                        "postgres",
                                    ),
                                    password=sandbox_env_value(
                                        sandbox_env,
                                        "ECOSYNC_CASE_ENV_POSTGRES_PASSWORD",
                                    ),
                                    database=sandbox_env_value(
                                        sandbox_env,
                                        "ECOSYNC_CASE_ENV_POSTGRES_DB",
                                        "postgres",
                                    ),
                                    trust=sandbox_env_enabled(
                                        sandbox_env,
                                        "ECOSYNC_CASE_ENV_POSTGRES_TRUST",
                                    ),
                                    extra_databases=[
                                        item
                                        for item in sandbox_env_value(
                                            sandbox_env,
                                            "ECOSYNC_CASE_ENV_POSTGRES_EXTRA_DATABASES",
                                        ).split(",")
                                        if item
                                    ],
                                )
                                assert service_ready_dir is not None
                                (service_ready_dir / "postgres.ready").touch()
                        while True:
                            assert proc is not None
                            docker_cli_exit = proc.poll()
                            if docker_cli_exit is not None:
                                exit_code = docker_cli_exit
                                break
                            now = time.monotonic()
                            if now >= next_container_check:
                                container_exit = docker_container_exit_code(sandbox_container_name)
                                next_container_check = time.monotonic() + 2
                                if container_exit is not None:
                                    exit_code = container_exit
                                    stop_docker_cli(proc)
                                    break
                                if trace_has_successful_result(
                                    trace_path,
                                    start_offsets=trace_start_offsets,
                                ):
                                    successful_result_seen_at = successful_result_seen_at or now
                                if (
                                    successful_result_seen_at is not None
                                    and now - successful_result_seen_at >= AGENT_FINAL_RESULT_GRACE_SEC
                                ):
                                    err.write(
                                        "\nAgent emitted a successful final result but its container "
                                        "did not exit within 30 seconds; closing the completed run.\n"
                                    )
                                    stop_docker_cli(proc)
                                    exit_code = 0
                                    break
                            deadline = agent_timeout_deadline(
                                deadline,
                                agent_started_marker.exists(),
                                now,
                                timeout,
                            )
                            if deadline is not None and now >= deadline:
                                err.write(f"\nAgent timed out after {timeout} seconds.\n")
                                stop_docker_cli(proc)
                                exit_code = 124
                                agent_timed_out = True
                                break
                            time.sleep(0.25)
                    except RuntimeError as exc:
                        err.write(f"\nAgent service startup failed: {exc}\n")
                        if proc is not None:
                            stop_docker_cli(proc)
                        exit_code = 125
                    except KeyboardInterrupt:
                        err.write("\nAgent interrupted; removing Docker container.\n")
                        if proc is not None:
                            stop_docker_cli(proc)
                        exit_code = 130
                    finally:
                        with docker_lifecycle_lock():
                            capture_docker_container_state(
                                sandbox_container_name,
                                Path(stdout_path).parent / "docker-container-state.json",
                                exit_code,
                            )
                            docker_rm_force(mongodb_sidecar_name, wait_for_container=0)
                            docker_rm_force(postgres_sidecar_name, wait_for_container=0)
                            docker_rm_force(sandbox_container_name)
                    return exit_code, agent_timed_out
            else:
                proc = subprocess.run(
                    formatted,
                    cwd=workspace,
                    env=env,
                    shell=True,
                    text=True,
                    stdout=out,
                    stderr=err,
                    timeout=timeout,
                )
            return proc.returncode, False
        except subprocess.TimeoutExpired:
            err.write(f"\nAgent timed out after {timeout} seconds.\n")
            return 124, True


def main() -> None:
    global TASK_DIR_OVERRIDE
    parser = argparse.ArgumentParser(description="Run a coding agent in an WIDESWE agent workspace.")
    parser.add_argument("--agent", required=True, choices=["noop", "gold", *sorted(SHELL_LIKE_AGENTS)])
    parser.add_argument("--task")
    parser.add_argument("--task-dir", type=Path, help="Explicit task directory outside benchmark/tasks.")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--max-api-requests", type=int, default=MAX_API_REQUESTS)
    parser.add_argument("--agent-id", help="Stable agent identifier for result metadata and trace paths.")
    parser.add_argument("--model-id", default="unspecified-model", help="Model identifier, e.g. claude-opus-4.6.")
    parser.add_argument("--run-id", default="run", help="Repeat/run identifier within an agent+model condition.")
    parser.add_argument(
        "--agent-environment",
        choices=sorted(AGENT_ENVIRONMENTS),
        default=None,
        help="Recorded runtime environment type for the agent sandbox image.",
    )
    parser.add_argument(
        "--sandbox",
        choices=["none", "docker"],
        default="none",
        help="Run shell agents directly on the host or inside a Docker sandbox.",
    )
    parser.add_argument("--sandbox-image", help="Docker image used when --sandbox docker is selected.")
    parser.add_argument(
        "--sandbox-network",
        default="bridge",
        choices=["bridge", "host", "none", "llm-api-only"],
        help="Docker network mode. llm-api-only blocks all egress except allowlisted LLM API hosts.",
    )
    parser.add_argument(
        "--llm-api-allowed-host",
        action="append",
        default=[],
        help="HTTPS host allowed by llm-api-only mode. May be repeated.",
    )
    parser.add_argument(
        "--pass-env",
        action="append",
        default=[],
        help="Environment variable name to pass into Docker sandbox. May be repeated.",
    )
    parser.add_argument(
        "--sandbox-mount",
        action="append",
        default=[],
        help="Extra Docker volume mount for local-only agent sandbox runs, e.g. /host:/container:ro.",
    )
    parser.add_argument("--sandbox-user", help="Docker --user value for sandbox runs, e.g. node or 1000:1000.")
    parser.add_argument(
        "--sandbox-env",
        action="append",
        default=[],
        help="Extra KEY=VALUE environment assignment for Docker sandbox runs. May be repeated.",
    )
    parser.add_argument(
        "--command",
        help=(
            "Shell command for command-driven agents such as claude-code and codex. "
            "Placeholders: {workspace}, {prompt_file}, "
            "{task_id}, {prompt}, {trace_dir}, {agent_id}, {model_id}, {run_id}. "
            "Environment variables are also set."
        ),
    )
    args = parser.parse_args()
    requested_timeout = args.timeout
    requested_max_api_requests = args.max_api_requests
    args.timeout = min(max(1, args.timeout), MAX_AGENT_TIMEOUT_SEC)
    args.max_api_requests = (
        0
        if args.max_api_requests == 0
        else min(max(1, args.max_api_requests), MAX_API_REQUESTS)
    )
    try:
        args.task, TASK_DIR_OVERRIDE = resolve_task_selection(args.task, args.task_dir, DEFAULT_TASK_ID)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    run_dir = args.run_dir.resolve()
    run_meta = load_run(run_dir)
    workspace = Path(run_meta["agent_workspace"]).resolve()
    if not workspace.exists():
        raise SystemExit(f"missing agent workspace: {workspace}")

    stdout_path, stderr_path, agent_json = log_paths(run_dir)
    agent_id = args.agent_id or args.agent
    model_id = args.model_id
    run_id = args.run_id
    agent_environment = args.agent_environment or ("host" if args.sandbox == "none" else "case-env")
    if args.sandbox == "none" and agent_environment != "host":
        raise SystemExit("--agent-environment must be host when --sandbox none is used")
    if args.sandbox == "docker" and agent_environment == "host":
        raise SystemExit("--agent-environment host is only valid with --sandbox none")
    if args.sandbox_network == "llm-api-only" and not args.llm_api_allowed_host:
        raise SystemExit("--llm-api-allowed-host is required with --sandbox-network llm-api-only")
    trace_path = trace_dir(run_dir, agent_id, model_id)
    scratch_path = sandbox_scratch_dir(run_dir) if args.sandbox == "docker" else None
    auto_git_mounts = sandbox_git_metadata_mounts(workspace) if args.sandbox == "docker" else []
    repo_env_mount = (
        public_repo_env_mount(args.task, workspace, run_dir)
        if args.sandbox == "docker" and agent_environment == "case-env"
        else None
    )
    sandbox_mounts = [*auto_git_mounts, *([repo_env_mount] if repo_env_mount else []), *args.sandbox_mount]
    sandbox_container_name = (
        docker_safe_name(args.task, agent_id, model_id, run_id)
        if args.sandbox == "docker"
        else None
    )
    started = time.time()
    test_executor_metadata: dict[str, Any] = {"enabled": False}
    agent_timed_out = False

    if args.agent == "noop":
        stdout_path.write_text("noop agent: left workspace unchanged\n", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        exit_code = 0
    elif args.agent == "gold":
        exit_code = apply_gold(args.task, workspace, stdout_path, stderr_path)
    elif args.agent in SHELL_LIKE_AGENTS:
        if not args.command:
            raise SystemExit(f"--command is required for --agent {args.agent}")
        with single_repo_test_executor(
            args.task,
            run_dir,
            run_meta,
            workspace,
            args.sandbox,
            agent_environment,
            args.sandbox_image,
            sandbox_mounts,
            args.sandbox_env,
            args.sandbox_user,
        ) as test_executor_metadata:
            effective_mounts = [*sandbox_mounts, *test_executor_metadata.get("mounts", [])]
            effective_env = [*args.sandbox_env, *test_executor_metadata.get("env", [])]
            with agent_visible_manifest(workspace):
                exit_code, agent_timed_out = run_shell(
                    args.task,
                    workspace,
                    args.command,
                    args.timeout,
                    stdout_path,
                    stderr_path,
                    trace_path,
                    agent_id,
                    model_id,
                    run_id,
                    args.sandbox,
                    args.sandbox_image,
                    args.sandbox_network,
                    args.llm_api_allowed_host,
                    args.pass_env,
                    effective_mounts,
                    args.sandbox_user,
                    effective_env,
                    scratch_path,
                    sandbox_container_name,
                    agent_environment,
                    args.max_api_requests,
                )
    else:  # pragma: no cover
        raise SystemExit(f"unsupported agent: {args.agent}")

    finished = time.time()
    docker_state_path = stdout_path.parent / "docker-container-state.json"
    docker_state = (
        json.loads(docker_state_path.read_text(encoding="utf-8"))
        if docker_state_path.exists()
        else None
    )
    metadata = {
        "task_id": args.task,
        "agent": args.agent,
        "agent_id": agent_id,
        "model_id": model_id,
        "run_id": run_id,
        "agent_environment": agent_environment,
        "run_dir": str(run_dir),
        "agent_workspace": str(workspace),
        "exit_code": exit_code,
        "timed_out": agent_timed_out,
        "started_at": int(started),
        "finished_at": int(finished),
        "duration_sec": round(finished - started, 3),
        "requested_timeout_sec": requested_timeout,
        "effective_timeout_sec": args.timeout,
        "requested_max_api_requests": requested_max_api_requests,
        "effective_max_api_requests": args.max_api_requests,
        "api_request_limit_enforcement": (
            "counting_gateway"
            if args.sandbox == "docker" and args.sandbox_network == "llm-api-only"
            else "not_gateway_enforced"
        ),
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "trace_dir": str(trace_path),
        "sandbox_scratch": str(scratch_path) if scratch_path else None,
        "sandbox_scratch_container": SANDBOX_SCRATCH_CONTAINER if scratch_path else None,
        "sandbox_container_name": sandbox_container_name,
        "docker_container_state": docker_state,
        "auto_sandbox_env": sorted(sandbox_scratch_env(args.sandbox_env)) if args.sandbox == "docker" else [],
        "sandbox": args.sandbox,
        "sandbox_image": args.sandbox_image,
        "sandbox_network": args.sandbox_network if args.sandbox == "docker" else None,
        "docker_network": (
            "none"
            if args.sandbox == "docker" and args.sandbox_network == "llm-api-only"
            else args.sandbox_network if args.sandbox == "docker" else None
        ),
        "llm_api_allowed_hosts": sorted(set(args.llm_api_allowed_host)),
        "pass_env": sorted(args.pass_env),
        "auto_git_metadata_mounts": sorted(auto_git_mounts),
        "public_repo_env_mount": repo_env_mount,
        "sandbox_mounts": sorted(sandbox_mounts),
        "user_sandbox_mounts": sorted(args.sandbox_mount),
        "sandbox_user": args.sandbox_user,
        "sandbox_env": sorted(args.sandbox_env),
        "single_repo_test_executor": test_executor_metadata,
    }
    if args.agent in SHELL_LIKE_AGENTS:
        metadata["command"] = args.command
    write_metadata(agent_json, metadata)
    write_metadata(trace_path / "agent.json", metadata)
    print(json.dumps(metadata, indent=2))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
