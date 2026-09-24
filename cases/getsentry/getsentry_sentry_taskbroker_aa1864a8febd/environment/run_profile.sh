#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
sentry_image="ecosyncbench/deps/getsentry-sentry-py313:aa1864a8febd"
taskbroker_image="ecosyncbench/deps/getsentry-taskbroker-rust-py311:aa1864a8febd"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/getsentry-sentry-taskbroker-aa1864a8febd}"
uid="${ECOSYNC_UID:-$(id -u)}"
gid="${ECOSYNC_GID:-$(id -g)}"
mkdir -p \
  "$cache_root/uv" \
  "$cache_root/pip" \
  "$cache_root/cargo" \
  "$cache_root/rust-target" \
  "$cache_root/home" \
  "$cache_root/venvs" \
  "$workspace/.ecosyncbench/test-reports"

dependency_tree_fingerprint() {
  local namespace="$1"
  local repo_root="$2"
  shift 2
  {
    printf '%s\0' "$namespace"
    local pattern file
    for pattern in "$@"; do
      printf '%s\0' "$pattern"
      while IFS= read -r -d '' file; do
        printf '%s\0' "${file#"$repo_root/"}"
        sha256sum "$file" | cut -d ' ' -f1
      done < <(find "$repo_root" -type f -name "$pattern" \
        -not -path '*/.git/*' \
        -not -path '*/.venv/*' \
        -not -path '*/target/*' \
        -print0 | sort -z)
    done
  } | sha256sum | cut -c1-20
}

sentry_venv_fingerprint="$(dependency_tree_fingerprint \
  "$sentry_image" \
  "$workspace/repos/getsentry/sentry" \
  uv.lock pyproject.toml setup.cfg requirements-dev-frozen.txt)"
taskbroker_venv_fingerprint="$(dependency_tree_fingerprint \
  "$taskbroker_image" \
  "$workspace/repos/getsentry/taskbroker" \
  uv.lock pyproject.toml setup.cfg setup.py requirements.txt)"
sentry_venv="$cache_root/venvs/sentry-$sentry_venv_fingerprint"
taskbroker_venv="$cache_root/venvs/taskbroker-$taskbroker_venv_fingerprint"
mkdir -p "$sentry_venv" "$taskbroker_venv"

ensure_sentry_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$sentry_image" >/dev/null 2>&1; then
    docker build -t "$sentry_image" -f "$task_dir/environment/getsentry-sentry-py313.Dockerfile" "$task_dir/environment"
  fi
}

ensure_taskbroker_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$taskbroker_image" >/dev/null 2>&1; then
    docker build \
      --build-arg "HTTP_PROXY=${ECOSYNC_DOCKER_PROXY:-}" \
      --build-arg "HTTPS_PROXY=${ECOSYNC_DOCKER_PROXY:-}" \
      --build-arg "ALL_PROXY=${ECOSYNC_DOCKER_PROXY:-}" \
      --build-arg "http_proxy=${ECOSYNC_DOCKER_PROXY:-}" \
      --build-arg "https_proxy=${ECOSYNC_DOCKER_PROXY:-}" \
      --build-arg "all_proxy=${ECOSYNC_DOCKER_PROXY:-}" \
      --build-arg "NO_PROXY=${NO_PROXY:-localhost,127.0.0.1}" \
      --build-arg "no_proxy=${no_proxy:-localhost,127.0.0.1}" \
      -t "$taskbroker_image" \
      -f "$task_dir/environment/taskbroker-rust-py311.Dockerfile" \
      "$task_dir/environment"
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
  [[ "$need_redis" == "1" ]] && stale+=(ecosync_getsentry_taskbroker_redis)
  [[ "$need_postgres" == "1" ]] && stale+=(ecosync_getsentry_taskbroker_postgres)
  if (( ${#stale[@]} )); then
    docker rm -f "${stale[@]}" >/dev/null 2>&1 || true
  fi

  if [[ "$need_redis" == "1" ]]; then
    docker run -d --name ecosync_getsentry_taskbroker_redis -p 6379:6379 redis:5.0-alpine >/dev/null
    ECOSYNC_STARTED_REDIS=1
  fi

  if [[ "$need_postgres" == "1" ]]; then
    docker run -d --name ecosync_getsentry_taskbroker_postgres -p 5432:5432 \
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
  [[ "${ECOSYNC_STARTED_REDIS:-0}" == "1" ]] && started+=(ecosync_getsentry_taskbroker_redis)
  [[ "${ECOSYNC_STARTED_POSTGRES:-0}" == "1" ]] && started+=(ecosync_getsentry_taskbroker_postgres)
  if (( ${#started[@]} )); then
    docker rm -f "${started[@]}" >/dev/null 2>&1 || true
  fi
}

start_taskbroker_redis() {
  ensure_service_image redis:5.0-alpine
  ECOSYNC_STARTED_TASKBROKER_REDIS=0
  if ! redis-cli -h 127.0.0.1 ping >/dev/null 2>&1; then
    docker rm -f ecosync_getsentry_taskbroker_redis >/dev/null 2>&1 || true
    docker run -d --name ecosync_getsentry_taskbroker_redis -p 6379:6379 \
      redis:5.0-alpine >/dev/null
    ECOSYNC_STARTED_TASKBROKER_REDIS=1
  fi
  for _ in $(seq 1 60); do
    redis-cli -h 127.0.0.1 ping >/dev/null 2>&1 && break
    sleep 1
  done
  redis-cli -h 127.0.0.1 ping >/dev/null
}

stop_taskbroker_redis() {
  if [[ "${ECOSYNC_STARTED_TASKBROKER_REDIS:-0}" == "1" ]]; then
    docker rm -f ecosync_getsentry_taskbroker_redis >/dev/null 2>&1 || true
  fi
}

ensure_local_git_repo_snippet='
if [[ -f .git ]]; then
  rm -f .git
  git config --global --add safe.directory "$PWD" || true
  git init -q
  git config user.email ecosyncbench@example.invalid
  git config user.name EcosyncBench
  git commit --allow-empty -q -m ecosyncbench-matrix-snapshot
fi
'

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
        tests/sentry/taskworker/test_adapters.py \
        --junitxml=/workspace/.ecosyncbench/test-reports/sentry-hidden.xml
    '
}

run_taskbroker() {
  ensure_taskbroker_image
  start_taskbroker_redis
  trap stop_taskbroker_redis EXIT
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-1800}" docker run --rm \
    --network host \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "$uid:$gid" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e ECOSYNC_DOCKER_PREBUILD_ONLY="${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" \
    -e ECOSYNC_DOCKER_WARMUP_ONLY="${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" \
    -e PIP_CACHE_DIR=/cache/pip \
    -e UV_CACHE_DIR=/cache/uv \
    -e UV_PYTHON_INSTALL_DIR=/cache/uv/python \
    -e UV_LINK_MODE=copy \
    -e PATH=/usr/local/cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -e RUSTUP_HOME=/usr/local/rustup \
    -e CARGO_HOME=/cache/cargo \
    -e CARGO_TARGET_DIR=/cache/rust-target \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    -v "$taskbroker_venv:/workspace/repos/getsentry/taskbroker/.venv" \
    -w "/workspace/repos/getsentry/taskbroker" \
    "$taskbroker_image" \
    bash -c '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home /cache/cargo /cache/rust-target
      uv sync --all-packages --all-groups --frozen
      if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
        uv run pytest --version >/dev/null
        cargo test --lib --no-run
        exit 0
      fi
      uv run pytest -q \
        clients/python/tests/test_task.py \
        clients/python/tests/worker/test_worker.py \
        --junitxml=/workspace/.ecosyncbench/test-reports/taskbroker-hidden.xml
    '
}

case "$profile" in
  sentry-hidden)
    run_sentry
    ;;
  taskbroker-hidden)
    run_taskbroker
    ;;
  *)
    echo "Unknown profile for getsentry_sentry_taskbroker_aa1864a8febd: $profile" >&2
    exit 2
    ;;
esac
