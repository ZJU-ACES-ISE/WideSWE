#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
reports_dir="$workspace/.ecosyncbench/test-reports"

rust_image="ecosyncbench/deps/getsentry-rust195:6ff581b566d2"
sentry_image="ecosyncbench/deps/getsentry-sentry-py313:506f7e5653e8"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/getsentry-sentry-symbolicator-6ff581b566d2}"
uid="${ECOSYNC_UID:-$(id -u)}"
gid="${ECOSYNC_GID:-$(id -g)}"

mkdir -p \
  "$cache_root/cargo-home" \
  "$cache_root/cargo-target" \
  "$cache_root/home" \
  "$cache_root/pip" \
  "$cache_root/uv" \
  "$cache_root/venvs" \
  "$reports_dir"

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
  uv.lock pyproject.toml setup.cfg requirements-dev-frozen.txt)"
sentry_venv="$cache_root/venvs/sentry-$sentry_venv_fingerprint"
mkdir -p "$sentry_venv"

ensure_rust_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$rust_image" >/dev/null 2>&1; then
    docker build -t "$rust_image" -f "$task_dir/environment/getsentry-rust-195.Dockerfile" "$task_dir/environment"
  fi
}

ensure_sentry_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$sentry_image" >/dev/null 2>&1; then
    docker build -t "$sentry_image" -f "$task_dir/environment/getsentry-sentry-py313.Dockerfile" "$task_dir/environment"
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
  local need_redis=0
  local need_postgres=0
  redis-cli -h 127.0.0.1 ping >/dev/null 2>&1 || need_redis=1
  pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1 || need_postgres=1

  local stale=()
  [[ "$need_redis" == "1" ]] && stale+=(ecosync_getsentry_symbolicator_redis)
  [[ "$need_postgres" == "1" ]] && stale+=(ecosync_getsentry_symbolicator_postgres)
  if (( ${#stale[@]} )); then
    docker rm -f "${stale[@]}" >/dev/null 2>&1 || true
  fi

  if [[ "$need_redis" == "1" ]]; then
    docker run -d --name ecosync_getsentry_symbolicator_redis -p 6379:6379 \
      redis:5.0-alpine >/dev/null
    ECOSYNC_STARTED_REDIS=1
  fi

  if [[ "$need_postgres" == "1" ]]; then
    docker run -d --name ecosync_getsentry_symbolicator_postgres -p 5432:5432 \
      -e POSTGRES_HOST_AUTH_METHOD=trust \
      -e POSTGRES_DB=sentry \
      postgres:14-alpine >/dev/null
    ECOSYNC_STARTED_POSTGRES=1
  fi

  for _ in $(seq 1 60); do
    redis-cli -h 127.0.0.1 ping >/dev/null 2>&1 && break
    sleep 1
  done
  redis-cli -h 127.0.0.1 ping >/dev/null

  for _ in $(seq 1 90); do
    pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1 && break
    sleep 1
  done
  pg_isready -h 127.0.0.1 -U postgres >/dev/null
}

stop_sentry_services() {
  local started=()
  [[ "${ECOSYNC_STARTED_REDIS:-0}" == "1" ]] && started+=(ecosync_getsentry_symbolicator_redis)
  [[ "${ECOSYNC_STARTED_POSTGRES:-0}" == "1" ]] && started+=(ecosync_getsentry_symbolicator_postgres)
  if (( ${#started[@]} )); then
    docker rm -f "${started[@]}" >/dev/null 2>&1 || true
  fi
}

ensure_local_git_repo_snippet='
ensure_local_git_repo() {
  if [[ -f .git ]]; then
    rm -f .git
    git config --global --add safe.directory "$PWD" || true
    git init -q
    git config user.email ecosyncbench@example.invalid
    git config user.name EcosyncBench
    git add -A >/dev/null 2>&1 || true
    git commit --allow-empty -q -m ecosyncbench-matrix-snapshot
  fi
}
ensure_local_git_repo
'

run_rust() {
  local workdir="$1"
  shift
  ensure_rust_image
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-1800}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "$uid:$gid" \
    -e HOME=/cache/home \
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
    -w "$workdir" \
    "$rust_image" \
    bash -c "$*"
}

run_rust_proguard() {
  run_rust "/workspace/repos/getsentry/rust-proguard" '
    set -euo pipefail
    mkdir -p /workspace/.ecosyncbench/test-reports /cache/home
    '"$ensure_local_git_repo_snippet"'
    if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
      cargo test --test r8 --no-run
      exit 0
    fi
    cargo test --test r8 -- --nocapture 2>&1 | tee /workspace/.ecosyncbench/test-reports/rust_proguard-hidden.log
  '
}

run_symbolicator() {
  run_rust "/workspace/repos/getsentry/symbolicator" '
    set -euo pipefail
    mkdir -p /workspace/.ecosyncbench/test-reports /cache/home
    '"$ensure_local_git_repo_snippet"'
    if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
      cargo test -p symbolicator-proguard --test integration --no-run
      exit 0
    fi
    cargo test -p symbolicator-proguard --test integration -- --nocapture 2>&1 | tee /workspace/.ecosyncbench/test-reports/symbolicator-hidden.log
  '
}

run_sentry() {
  ensure_sentry_image
  start_sentry_services
  trap stop_sentry_services EXIT
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-1800}" docker run --rm \
    --network host \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "$uid:$gid" \
    -e HOME=/cache/home \
    -e CI=1 \
    -e ECOSYNC_DOCKER_PREBUILD_ONLY="${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" \
    -e ECOSYNC_DOCKER_WARMUP_ONLY="${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" \
    -e SENTRY_SKIP_SERVICE_VALIDATION=1 \
    -e PIP_CACHE_DIR=/cache/pip \
    -e UV_CACHE_DIR=/cache/uv \
    -e UV_PYTHON_INSTALL_DIR=/cache/uv/python \
    -e UV_LINK_MODE=copy \
    -e ECOSYNC_SENTRY_VENV_FINGERPRINT="$sentry_venv_fingerprint" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    -v "$sentry_venv:/workspace/repos/getsentry/sentry/.venv" \
    -w "/workspace/repos/getsentry/sentry" \
    "$sentry_image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /cache/home
      '"$ensure_local_git_repo_snippet"'
      uv sync --group dev --frozen
      uv run python tools/fast_editable.py
      if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
        uv run python -m pytest --version >/dev/null
        exit 0
      fi
      uv run python -m pytest -q \
        tests/sentry/test_stacktraces.py \
        --junitxml=/workspace/.ecosyncbench/test-reports/sentry-hidden.xml
    '
}

case "$profile" in
  rust_proguard-hidden)
    run_rust_proguard
    ;;
  sentry-hidden)
    run_sentry
    ;;
  symbolicator-hidden)
    run_symbolicator
    ;;
  *)
    echo "Unknown profile for getsentry_sentry_symbolicator_6ff581b566d2: $profile" >&2
    exit 2
    ;;
esac
