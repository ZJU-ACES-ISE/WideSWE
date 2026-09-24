#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_root="/opt/ecosyncbench"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
reports_dir="$workspace/.ecosyncbench/test-reports"

sentry_image="ecosyncbench/deps/getsentry-sentry-py313:156299b1e2cd"
snuba_image="ecosyncbench/deps/getsentry-snuba-py313:811c8706e249-v2"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/getsentry-sentry-snuba-811c8706e249}"
uid="${ECOSYNC_UID:-$(id -u)}"
gid="${ECOSYNC_GID:-$(id -g)}"

mkdir -p "$cache_root/pip" "$cache_root/uv" "$cache_root/home" \
  "$cache_root/cargo" "$cache_root/rust-target" "$cache_root/rust-wheels" \
  "$cache_root/venvs" "$reports_dir"

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

source_tree_fingerprint() {
  local namespace="$1"
  local source_root="$2"
  {
    printf '%s\0' "$namespace"
    local path relative
    while IFS= read -r -d '' path; do
      relative="${path#"$source_root"/}"
      printf '%s\0' "$relative"
      sha256sum "$path" | cut -d ' ' -f1
    done < <(find "$source_root" -type f -not -path '*/target/*' -print0 | sort -z)
  } | sha256sum | cut -c1-20
}

sentry_venv_fingerprint="$(dependency_fingerprint \
  "$sentry_image" \
  "$workspace/repos/getsentry/sentry" \
  uv.lock pyproject.toml setup.cfg requirements-dev-frozen.txt)"
sentry_venv="$cache_root/venvs/sentry-$sentry_venv_fingerprint"

snuba_rust_fingerprint="$(source_tree_fingerprint \
  "$snuba_image" \
  "$workspace/repos/getsentry/snuba/rust_snuba")"
snuba_rust_venv="$cache_root/venvs/snuba-rust-$snuba_rust_fingerprint"
mkdir -p "$sentry_venv" "$snuba_rust_venv"

ensure_sentry_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$sentry_image" >/dev/null 2>&1; then
    docker build -t "$sentry_image" \
      -f "$task_dir/environment/getsentry-sentry-py313.Dockerfile" \
      "$task_dir/environment"
  fi
}

ensure_snuba_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$snuba_image" >/dev/null 2>&1; then
    docker build -t "$snuba_image" -f "$task_dir/environment/getsentry-snuba-py313.Dockerfile" "$task_dir/environment"
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
  [[ "$need_redis" == "1" ]] && stale+=(ecosync_getsentry_811c_sentry_redis)
  [[ "$need_postgres" == "1" ]] && stale+=(ecosync_getsentry_811c_sentry_postgres)
  if (( ${#stale[@]} )); then
    docker rm -f "${stale[@]}" >/dev/null 2>&1 || true
  fi

  if [[ "$need_redis" == "1" ]]; then
    docker run -d --name ecosync_getsentry_811c_sentry_redis -p 6379:6379 redis:5.0-alpine >/dev/null
    ECOSYNC_STARTED_REDIS=1
  fi

  if [[ "$need_postgres" == "1" ]]; then
    docker run -d --name ecosync_getsentry_811c_sentry_postgres -p 5432:5432 \
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
  [[ "${ECOSYNC_STARTED_REDIS:-0}" == "1" ]] && started+=(ecosync_getsentry_811c_sentry_redis)
  [[ "${ECOSYNC_STARTED_POSTGRES:-0}" == "1" ]] && started+=(ecosync_getsentry_811c_sentry_postgres)
  if (( ${#started[@]} )); then
    docker rm -f "${started[@]}" >/dev/null 2>&1 || true
  fi
}

ensure_snuba_service_images() {
  docker network inspect cloudbuild >/dev/null 2>&1 || docker network create --attachable cloudbuild >/dev/null
  local images=(
    "ghcr.io/getsentry/image-mirror-altinity-clickhouse-server:25.3.6.10034.altinitystable"
    "ghcr.io/getsentry/image-mirror-confluentinc-cp-kafka:6.2.0"
    "ghcr.io/getsentry/image-mirror-confluentinc-cp-zookeeper:6.2.0"
    "ghcr.io/getsentry/docker-redis-cluster:7.0.10"
  )
  for image in "${images[@]}"; do
    if ! docker image inspect "$image" >/dev/null 2>&1; then
      docker pull "$image"
    fi
  done
}

snuba_compose() {
  (cd "$workspace/repos/getsentry/snuba" && \
    CLICKHOUSE_IMAGE="ghcr.io/getsentry/image-mirror-altinity-clickhouse-server:25.3.6.10034.altinitystable" \
    SNUBA_IMAGE="$snuba_image" \
    SNUBA_SETTINGS=test \
    docker compose -f docker-compose.gcb.yml "$@")
}

start_snuba_services() {
  ensure_snuba_image
  ensure_snuba_service_images
  snuba_compose up -d zookeeper kafka clickhouse redis-cluster
  for _ in $(seq 1 120); do
    if snuba_compose exec -T redis-cluster redis-cli -p 7000 cluster info 2>/dev/null \
        | grep -q "cluster_state:ok" \
      && snuba_compose exec -T clickhouse clickhouse-client --query 'SELECT 1' \
        2>/dev/null | grep -qx '1'; then
      return 0
    fi
    sleep 2
  done
  echo "Snuba Redis/ClickHouse services did not become ready" >&2
  snuba_compose logs redis-cluster clickhouse >&2 || true
  return 1
}

stop_snuba_services() {
  if [[ -d "$workspace/repos/getsentry/snuba" ]]; then
    snuba_compose down -v --remove-orphans >/dev/null 2>&1 || true
  fi
}

run_snuba_pytest() {
  local junit="$1"
  shift
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    ensure_snuba_image
    ensure_snuba_service_images
    exit 0
  fi
  start_snuba_services
  trap stop_snuba_services EXIT
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-2400}" docker run --rm \
    --network cloudbuild \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "$uid:$gid" \
    -e HOME=/cache/home \
    -e CI=1 \
    -e SNUBA_SETTINGS=test \
    -e CLICKHOUSE_HOST=clickhouse.local \
    -e USE_REDIS_CLUSTER=1 \
    -e REDIS_HOST=redis-cluster \
    -e REDIS_PORT=7000 \
    -e REDIS_DB=0 \
    -e DEFAULT_BROKERS=kafka:9092 \
    -e CARGO_HOME=/cache/cargo \
    -e CARGO_TARGET_DIR=/cache/rust-target \
    -e ECOSYNC_DOCKER_PREBUILD_ONLY="${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" \
    -e ECOSYNC_DOCKER_WARMUP_ONLY="${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" \
    -e PYTHONPATH=/workspace/repos/getsentry/snuba \
    -e PIP_CACHE_DIR=/cache/pip \
    -e UV_CACHE_DIR=/cache/uv \
    -e UV_LINK_MODE=copy \
    -e ECOSYNC_SNUBA_RUST_FINGERPRINT="$snuba_rust_fingerprint" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    -w /workspace/repos/getsentry/snuba \
    "$snuba_image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /cache/home
      python_cmd=(python)
      if [[ -d rust_snuba ]]; then
        wheel_dir="/cache/rust-wheels/$ECOSYNC_SNUBA_RUST_FINGERPRINT"
        venv="/cache/venvs/snuba-rust-$ECOSYNC_SNUBA_RUST_FINGERPRINT"
        mkdir -p "$wheel_dir"
        if ! find "$wheel_dir" -maxdepth 1 -name "rust_snuba*.whl" | grep -q .; then
          (cd rust_snuba && python -m maturin build --release --locked -o "$wheel_dir")
        fi
        if [[ ! -x "$venv/bin/python" ]]; then
          python -m venv --system-site-packages "$venv"
        fi
        marker="$venv/.ecosyncbench-wheel-$ECOSYNC_SNUBA_RUST_FINGERPRINT"
        if [[ ! -f "$marker" ]]; then
          "$venv/bin/python" -m pip install --no-deps --force-reinstall \
            "$(find "$wheel_dir" -maxdepth 1 -name "rust_snuba*.whl" | head -1)"
          touch "$marker"
        fi
        python_cmd=("$venv/bin/python")
        mv rust_snuba .ecosyncbench-rust-snuba-source
      fi
      "${python_cmd[@]}" -m pytest --version >/dev/null
      if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
        exit 0
      fi
      "${python_cmd[@]}" -m pytest -q "$@" --junitxml="'"$junit"'"
    ' bash "$@"
}

run_sentry_pytest() {
  ensure_sentry_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    exit 0
  fi
  start_sentry_services
  trap stop_sentry_services EXIT
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-3000}" docker run --rm \
    --network host \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "$uid:$gid" \
    -e HOME=/cache/home \
    -e CI=1 \
    -e SENTRY_SKIP_SERVICE_VALIDATION=1 \
    -e ECOSYNC_DOCKER_PREBUILD_ONLY="${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" \
    -e ECOSYNC_DOCKER_WARMUP_ONLY="${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" \
    -e PIP_CACHE_DIR=/cache/pip \
    -e UV_CACHE_DIR=/cache/uv \
    -e UV_PYTHON_INSTALL_DIR=/cache/uv/python \
    -e UV_LINK_MODE=copy \
    -e ECOSYNC_SENTRY_VENV_FINGERPRINT="$sentry_venv_fingerprint" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    -v "$sentry_venv:/workspace/repos/getsentry/sentry/.venv" \
    -w /workspace/repos/getsentry/sentry \
    "$sentry_image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /cache/home
      if [[ -f uv.lock ]]; then
        uv sync --group dev --frozen
        uv run python -m tools.fast_editable --path .
        python_cmd=(uv run python)
      else
        marker=".venv/.ecosyncbench-ready-$ECOSYNC_SENTRY_VENV_FINGERPRINT"
        if [[ ! -x .venv/bin/python || ! -f "$marker" ]]; then
          python -m venv .venv
          .venv/bin/python -m pip install --upgrade pip uv
          .venv/bin/uv pip install -r requirements-dev-frozen.txt
          touch "$marker"
        fi
        .venv/bin/python -m tools.fast_editable --path .
        python_cmd=(.venv/bin/python)
      fi
      if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
        "${python_cmd[@]}" -m pytest --version >/dev/null
        exit 0
      fi
      "${python_cmd[@]}" -m pytest -q \
        tests/sentry/middleware/test_access_log_middleware.py \
        tests/sentry/utils/test_snuba.py \
        --junitxml=/workspace/.ecosyncbench/test-reports/sentry-hidden.xml
    '
}

case "$profile" in
  sentry-hidden)
    run_sentry_pytest
    ;;
  snuba-hidden)
    run_snuba_pytest \
      /workspace/.ecosyncbench/test-reports/snuba-hidden.xml \
      tests/test_snql_api.py
    ;;
  *)
    echo "Unknown profile for getsentry_sentry_snuba_811c8706e249: $profile" >&2
    exit 2
    ;;
esac
