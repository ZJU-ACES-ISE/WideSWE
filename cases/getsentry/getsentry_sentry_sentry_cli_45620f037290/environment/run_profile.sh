#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
sentry_image="ecosyncbench/deps/getsentry-sentry-py313:45620f037290"
cli_image="ecosyncbench/deps/getsentry-sentry-cli-rust195:45620f037290"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/getsentry-sentry-sentry-cli-45620f037290}"
mkdir -p \
  "$cache_root/uv" \
  "$cache_root/pip" \
  "$cache_root/cargo-home" \
  "$cache_root/cargo-target" \
  "$cache_root/home" \
  "$cache_root/venvs" \
  "$workspace/.ecosyncbench/test-reports"

dependency_fingerprint() {
  local namespace="$1"
  local repo_root="$2"
  shift 2
  {
    printf '%s\0' "$namespace"
    local relative
    for relative in "$@"; do
      printf '%s\0' "$relative"
      if [[ -f "$repo_root/$relative" ]]; then
        sha256sum "$repo_root/$relative" | cut -d ' ' -f1
      else
        printf '<missing>\n'
      fi
    done
  } | sha256sum | cut -c1-20
}

sentry_venv_fingerprint="$(dependency_fingerprint \
  "$sentry_image" \
  "$workspace/repos/getsentry/sentry" \
  uv.lock pyproject.toml setup.cfg)"
sentry_venv="$cache_root/venvs/sentry-$sentry_venv_fingerprint"
mkdir -p "$sentry_venv"

ensure_local_git_repo_snippet='
ensure_local_git_repo() {
  local origin_url="$1"
  if [[ ! -d .git ]]; then
    rm -f .git
    git init -q
    git config user.email ecosyncbench@example.invalid
    git config user.name EcosyncBench
    git commit --allow-empty -q -m ecosyncbench-matrix-snapshot
  fi
  git config --global --add safe.directory "$PWD" || true
  if git remote get-url origin >/dev/null 2>&1; then
    git remote set-url origin "$origin_url"
  else
    git remote add origin "$origin_url"
  fi
}
'

ensure_sentry_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$sentry_image" >/dev/null 2>&1; then
    docker build -t "$sentry_image" -f "$task_dir/environment/getsentry-sentry-py313.Dockerfile" "$task_dir/environment"
  fi
}

ensure_cli_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$cli_image" >/dev/null 2>&1; then
    docker build -t "$cli_image" -f "$task_dir/environment/getsentry-rust-195.Dockerfile" "$task_dir/environment"
  fi
}

ensure_service_image() {
  local image_name="$1"
  if ! docker image inspect "$image_name" >/dev/null 2>&1; then
    for attempt in 1 2 3 4 5; do
      if docker pull "$image_name"; then
        return 0
      fi
      sleep $((attempt * 5))
    done
    docker pull "$image_name"
  fi
}

start_sentry_services() {
  ensure_service_image redis:5.0-alpine
  ensure_service_image postgres:14-alpine
  ECOSYNC_STARTED_REDIS=0
  ECOSYNC_STARTED_POSTGRES=0
  ECOSYNC_STARTED_OBJECTSTORE=0
  if docker run --rm --network host redis:5.0-alpine redis-cli -h 127.0.0.1 ping >/dev/null 2>&1; then
    :
  else
    docker rm -f ecosync_getsentry_sentry_cli_redis >/dev/null 2>&1 || true
    docker run -d --name ecosync_getsentry_sentry_cli_redis -p 6379:6379 \
      redis:5.0-alpine >/dev/null
    ECOSYNC_STARTED_REDIS=1
    for _ in $(seq 1 60); do
      if docker exec ecosync_getsentry_sentry_cli_redis redis-cli ping >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
    docker exec ecosync_getsentry_sentry_cli_redis redis-cli ping >/dev/null
  fi

  if docker run --rm --network host postgres:14-alpine pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1; then
    :
  else
    docker rm -f ecosync_getsentry_sentry_cli_postgres >/dev/null 2>&1 || true
    docker run -d --name ecosync_getsentry_sentry_cli_postgres -p 5432:5432 \
      -e POSTGRES_HOST_AUTH_METHOD=trust \
      -e POSTGRES_DB=sentry \
      postgres:14-alpine >/dev/null
    ECOSYNC_STARTED_POSTGRES=1
    for _ in $(seq 1 300); do
      if docker exec ecosync_getsentry_sentry_cli_postgres pg_isready -U postgres >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
    docker exec ecosync_getsentry_sentry_cli_postgres pg_isready -U postgres >/dev/null
  fi

  if docker run --rm --network host "$sentry_image" python -c 'import socket; socket.create_connection(("127.0.0.1", 8888), timeout=1).close()' >/dev/null 2>&1
  then
    :
  else
    docker rm -f ecosync_getsentry_sentry_cli_objectstore >/dev/null 2>&1 || true
    docker run -d --name ecosync_getsentry_sentry_cli_objectstore --network host \
      "$sentry_image" bash -lc 'cat > /tmp/ecosync_objectstore.py <<'"'"'PY'"'"'
import http.server
import json
import urllib.parse
import uuid

STORE = {}
PREFIX = "/v1/objects/"
INTROSPECTION_PATH = "/__ecosync__/objects"


def split_key(path):
    path = urllib.parse.unquote(path.split("?", 1)[0])
    if not path.startswith(PREFIX):
        return None, None
    rest = path[len(PREFIX):]
    parts = rest.split("/", 2)
    if len(parts) < 2:
        return None, None
    key = parts[2] if len(parts) == 3 and parts[2] else uuid.uuid4().hex
    return "/".join([parts[0], parts[1], key]), key


class Handler(http.server.BaseHTTPRequestHandler):
    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self._write_object()

    def do_PUT(self):
        self._write_object()

    def _write_object(self):
        storage_key, user_key = split_key(self.path)
        if not storage_key:
            self._json(404, {"error": "invalid objectstore path"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        STORE[storage_key] = self.rfile.read(length)
        self._json(200, {"key": user_key})

    def do_GET(self):
        if self.path == INTROSPECTION_PATH:
            self._json(200, {"count": len(STORE)})
            return
        storage_key, _ = split_key(self.path)
        if storage_key not in STORE:
            self._json(404, {"error": "not found"})
            return
        body = STORE[storage_key]
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_DELETE(self):
        if self.path == INTROSPECTION_PATH:
            STORE.clear()
            self.send_response(204)
            self.end_headers()
            return
        storage_key, _ = split_key(self.path)
        if storage_key:
            STORE.pop(storage_key, None)
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        return


http.server.ThreadingHTTPServer(("127.0.0.1", 8888), Handler).serve_forever()
PY
python /tmp/ecosync_objectstore.py' >/dev/null
    ECOSYNC_STARTED_OBJECTSTORE=1
    for _ in $(seq 1 30); do
      if docker run --rm --network host "$sentry_image" python -c 'import socket; socket.create_connection(("127.0.0.1", 8888), timeout=1).close()' >/dev/null 2>&1
      then
        break
      fi
      sleep 1
    done
  fi
}

stop_sentry_services() {
  if [[ "${ECOSYNC_STARTED_REDIS:-0}" == "1" ]]; then
    docker rm -f ecosync_getsentry_sentry_cli_redis >/dev/null 2>&1 || true
  fi
  if [[ "${ECOSYNC_STARTED_POSTGRES:-0}" == "1" ]]; then
    docker rm -f ecosync_getsentry_sentry_cli_postgres >/dev/null 2>&1 || true
  fi
  if [[ "${ECOSYNC_STARTED_OBJECTSTORE:-0}" == "1" ]]; then
    docker rm -f ecosync_getsentry_sentry_cli_objectstore >/dev/null 2>&1 || true
  fi
}

run_sentry() {
  ensure_sentry_image
  start_sentry_services
  trap stop_sentry_services EXIT
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-1800}" docker run --rm \
    --network host \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/cache/home \
    -e CI=1 \
    -e ECOSYNC_DOCKER_PREBUILD_ONLY="${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" \
    -e ECOSYNC_DOCKER_WARMUP_ONLY="${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" \
    -e SENTRY_SKIP_SERVICE_VALIDATION=1 \
    -e PIP_CACHE_DIR=/cache/pip \
    -e UV_CACHE_DIR=/cache/uv \
    -e UV_PYTHON_INSTALL_DIR=/cache/uv/python \
    -e UV_LINK_MODE=copy \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    -v "$sentry_venv:/workspace/repos/getsentry/sentry/.venv" \
    -w "/workspace/repos/getsentry/sentry" \
    "$sentry_image" \
    bash -lc '
      set -euo pipefail
      export PATH=/usr/local/cargo/bin:$PATH
      mkdir -p /workspace/.ecosyncbench/test-reports /cache/home
      '"$ensure_local_git_repo_snippet"'
      ensure_local_git_repo https://github.com/getsentry/sentry.git
      uv sync --group dev --frozen
      uv run python tools/fast_editable.py
      if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
        uv run python -m pytest --version >/dev/null
        exit 0
      fi
      uv run python -m pytest -q \
        tests/sentry/preprod/api/endpoints/test_preprod_artifact_snapshot.py \
        --junitxml=/workspace/.ecosyncbench/test-reports/sentry-hidden.xml
    '
}

run_sentry_cli() {
  ensure_cli_image
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-1800}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e ECOSYNC_DOCKER_PREBUILD_ONLY="${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" \
    -e ECOSYNC_DOCKER_WARMUP_ONLY="${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" \
    -e CARGO_HOME=/cache/cargo-home \
    -e CARGO_TARGET_DIR=/cache/cargo-target \
    -e RUSTUP_HOME=/usr/local/rustup \
    -e RUSTUP_TOOLCHAIN=1.95.0-x86_64-unknown-linux-gnu \
    -e PATH=/usr/local/cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    -w "/workspace/repos/getsentry/sentry-cli" \
    "$cli_image" \
    bash -lc '
      set -euo pipefail
      export PATH=/usr/local/cargo/bin:$PATH
      export RUSTUP_TOOLCHAIN=1.95.0-x86_64-unknown-linux-gnu
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      '"$ensure_local_git_repo_snippet"'
      ensure_local_git_repo https://github.com/getsentry/sentry-cli.git
      if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
        cargo --version >/dev/null
        exit 0
      fi
      set +e
      cargo test --test mod integration::build -- --test-threads=1 2>&1 | tee /tmp/ecosync-sentry-cli-cargo-test.log
      code=$?
      set -e
      python3 - "$code" /tmp/ecosync-sentry-cli-cargo-test.log > /workspace/.ecosyncbench/test-reports/sentry-cli-hidden.xml <<'"'"'PY'"'"'
import re
import sys
from xml.sax.saxutils import escape

code = int(sys.argv[1])
text = open(sys.argv[2], encoding="utf-8", errors="replace").read()
text = re.sub(r"\x1b\[[0-9;]*m", "", text)
names = list(dict.fromkeys(
    name.strip()
    for name in re.findall(r"^test\s+([^\n]+?)\s+\.\.\.", text, re.M)
))
summary = re.search(
    r"^test result: (ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored;",
    text,
    re.M,
)
cases = []
if summary and names:
    failed_names = set()
    failure_section = re.search(
        r"^failures:\s*\n((?:    [^\n]+\n)+)\s*^test result:",
        text,
        re.M,
    )
    if failure_section:
        failed_names.update(re.findall(r"^    (.+)$", failure_section.group(1), re.M))
    ignored_names = {
        name.strip()
        for name in re.findall(r"^test\s+([^\n]+?)\s+\.\.\.\s+ignored$", text, re.M)
    }
    passed_count = int(summary.group(2))
    failed_count = int(summary.group(3))
    ignored_count = int(summary.group(4))
    if (
        len(names) == passed_count + failed_count + ignored_count
        and len(failed_names) == failed_count
        and len(ignored_names) == ignored_count
    ):
        for name in names:
            if name in failed_names:
                status = "FAILED"
            elif name in ignored_names:
                status = "ignored"
            else:
                status = "ok"
            cases.append((name, status))

if not cases:
    cases.append(("integration::build", "ok" if code == 0 else "FAILED"))

failures = sum(1 for _, status in cases if status == "FAILED")
skipped = sum(1 for _, status in cases if status == "ignored")
print("<?xml version=\"1.0\" encoding=\"utf-8\"?>")
print(f"<testsuite name=\"sentry-cli-hidden\" tests=\"{len(cases)}\" failures=\"{failures}\" errors=\"0\" skipped=\"{skipped}\">")
for name, status in cases:
    attrs = f"classname=\"tests.integration.build\" name=\"{escape(name)}\""
    if status == "FAILED":
        print(f"  <testcase {attrs}><failure message=\"cargo test failed\">{escape(name)} failed</failure></testcase>")
    elif status == "ignored":
        print(f"  <testcase {attrs}><skipped /></testcase>")
    else:
        print(f"  <testcase {attrs} />")
print("</testsuite>")
PY
      exit "$code"
    '
}

case "$profile" in
  sentry-hidden)
    run_sentry
    ;;
  sentry_cli-hidden)
    run_sentry_cli
    ;;
  *)
    echo "Unknown profile for getsentry_sentry_sentry_cli_45620f037290: $profile" >&2
    exit 2
    ;;
esac
