#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
repo_root="${ECOSYNC_REPO_ROOT:-/opt/ecosyncbench}"

sentry_image="ecosyncbench/deps/getsentry-sentry-py313:506f7e5653e8"
relay_image="ecosyncbench/deps/getsentry-relay-rust-py:5947"
relay_base_image="ecosyncbench/base/getsentry-rust-py314-cmake:20260630"
sentry_base_image="ecosyncbench/base/python:3.13-uv"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/getsentry_relay_sentry_c32abe62a9e3}"
git_mirror_root="${ECOSYNC_GIT_MIRROR_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/evaluator-dependency-cache/git-mirrors}}"
mkdir -p "$cache_root/uv" "$cache_root/cargo" "$cache_root/target" "$git_mirror_root/locks" "$workspace/.ecosyncbench/test-reports"

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

sentry_venv_fingerprint="$(dependency_fingerprint "$sentry_image" "$workspace/repos/getsentry/sentry" uv.lock pyproject.toml setup.cfg)"
relay_venv_fingerprint="$(dependency_fingerprint "$relay_image" "$workspace/repos/getsentry/relay" uv.lock pyproject.toml requirements-dev.txt)"
sentry_venv="$cache_root/venvs/sentry-$sentry_venv_fingerprint"
relay_venv="$cache_root/venvs/relay-$relay_venv_fingerprint"
mkdir -p "$sentry_venv" "$relay_venv"

ensure_sentry_image() {
  if ! docker image inspect "$sentry_image" >/dev/null 2>&1; then
    docker build -t ecosyncbench/base/python:3.13-slim \
      -f "$repo_root/benchmark/images/base/python-3.13-slim/Dockerfile" \
      "$repo_root/benchmark/images/base/python-3.13-slim"
    docker build -t "$sentry_base_image" \
      -f "$repo_root/benchmark/images/base/python-3.13-uv/Dockerfile" \
      "$repo_root/benchmark/images/base/python-3.13-uv"
    docker build -t "$sentry_image" \
      -f "$repo_root/benchmark/images/deps/python-uv-project/Dockerfile" \
      --build-arg ECOSYNC_BASE_IMAGE="$sentry_base_image" \
      --build-arg ECOSYNC_TASK_ID=getsentry_relay_sentry_c32abe62a9e3 \
      --build-arg ECOSYNC_REPO=getsentry/sentry \
      --build-arg ECOSYNC_REPO_PATH=. \
      --build-arg ECOSYNC_BASE_COMMIT=c32abe62a9e3 \
      --build-arg ECOSYNC_DEPENDENCY_FINGERPRINT=sentry-uv \
      --build-arg ECOSYNC_UV_PROJECT_DIR=. \
      --build-arg 'ECOSYNC_UV_SYNC_FLAGS=--frozen --all-extras --dev --no-install-project' \
      "$workspace/repos/getsentry/sentry"
  fi
}

ensure_relay_base_image() {
  if ! docker image inspect "$relay_base_image" >/dev/null 2>&1; then
    docker build -t "$relay_base_image" \
      -f "$repo_root/benchmark/images/base/getsentry-rust-py314-cmake/Dockerfile" \
      "$repo_root/benchmark/images/base/getsentry-rust-py314-cmake"
  fi
}

ensure_relay_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$relay_image" >/dev/null 2>&1; then
    ensure_relay_base_image
    docker build -t "$relay_image" -f "$task_dir/environment/relay-rust-py.Dockerfile" "$task_dir/environment"
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

ensure_git_mirror() {
  local url="$1"
  local key mirror lock tmp
  key="$(printf '%s' "$url" | sha256sum | cut -c1-20)"
  mirror="$git_mirror_root/$key.git"
  lock="$git_mirror_root/locks/$key.lock"
  (
    flock 9
    if git --git-dir="$mirror" rev-parse --is-bare-repository >/dev/null 2>&1; then
      exit 0
    fi
    tmp="$mirror.tmp.$$"
    rm -rf "$tmp"
    timeout 600 git -c http.version=HTTP/1.1 clone --mirror "$url" "$tmp"
    mv "$tmp" "$mirror"
  ) 9>"$lock"
  printf '%s\n' "$mirror"
}

relay_submodule_git_args() {
  local urls=(
    https://github.com/getsentry/sentry-native.git
    https://github.com/ua-parser/uap-core.git
    https://github.com/getsentry/breakpad.git
    https://github.com/getsentry/crashpad.git
    https://github.com/getsentry/libunwindstack-ndk
    https://chromium.googlesource.com/linux-syscall-support
    https://github.com/getsentry/mini_chromium.git
    https://chromium.googlesource.com/chromium/src/third_party/zlib
  )
  local url mirror
  printf '%s\0' -c protocol.file.allow=always
  mirror="$(ensure_git_mirror https://github.com/getsentry/sentry-conventions.git)"
  printf '%s\0' -c "url.file://$mirror.insteadOf=https://github.com/getsentry/sentry-conventions"
  for url in "${urls[@]}"; do
    mirror="$(ensure_git_mirror "$url")"
    printf '%s\0' -c "url.file://$mirror.insteadOf=$url"
  done
}

start_sentry_services() {
  ensure_service_image redis:5.0-alpine
  ensure_service_image postgres:14-alpine
  ECOSYNC_STARTED_REDIS=0
  ECOSYNC_STARTED_POSTGRES=0
  if docker run --rm --network host redis:5.0-alpine redis-cli -h 127.0.0.1 ping >/dev/null 2>&1; then
    :
  else
    docker rm -f ecosync_getsentry_relay_sentry_redis >/dev/null 2>&1 || true
    docker run -d --name ecosync_getsentry_relay_sentry_redis -p 6379:6379 redis:5.0-alpine >/dev/null
    ECOSYNC_STARTED_REDIS=1
    for _ in $(seq 1 60); do
      if docker exec ecosync_getsentry_relay_sentry_redis redis-cli ping >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
    docker exec ecosync_getsentry_relay_sentry_redis redis-cli ping >/dev/null
  fi

  if docker run --rm --network host postgres:14-alpine pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1; then
    :
  else
    docker rm -f ecosync_getsentry_relay_sentry_postgres >/dev/null 2>&1 || true
    docker run -d --name ecosync_getsentry_relay_sentry_postgres -p 5432:5432 \
      -e POSTGRES_HOST_AUTH_METHOD=trust \
      -e POSTGRES_DB=sentry \
      postgres:14-alpine >/dev/null
    ECOSYNC_STARTED_POSTGRES=1
    for _ in $(seq 1 90); do
      if docker exec ecosync_getsentry_relay_sentry_postgres pg_isready -U postgres >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
    docker exec ecosync_getsentry_relay_sentry_postgres pg_isready -U postgres >/dev/null
  fi
}

stop_sentry_services() {
  if [[ "${ECOSYNC_STARTED_REDIS:-0}" == "1" ]]; then
    docker rm -f ecosync_getsentry_relay_sentry_redis >/dev/null 2>&1 || true
  fi
  if [[ "${ECOSYNC_STARTED_POSTGRES:-0}" == "1" ]]; then
    docker rm -f ecosync_getsentry_relay_sentry_postgres >/dev/null 2>&1 || true
  fi
}

start_relay_services() {
  ensure_service_image redpandadata/redpanda:v24.3.7
  ensure_service_image redis:5.0-alpine
  ECOSYNC_STARTED_REDPANDA=0
  ECOSYNC_STARTED_RELAY_REDIS=0
  if ! docker run --rm --network host redis:5.0-alpine redis-cli -h 127.0.0.1 ping >/dev/null 2>&1; then
    docker rm -f ecosync_getsentry_c32abe_relay_redis >/dev/null 2>&1 || true
    docker run -d --name ecosync_getsentry_c32abe_relay_redis -p 6379:6379 redis:5.0-alpine >/dev/null
    ECOSYNC_STARTED_RELAY_REDIS=1
    for _ in $(seq 1 60); do
      if docker exec ecosync_getsentry_c32abe_relay_redis redis-cli ping >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
    docker exec ecosync_getsentry_c32abe_relay_redis redis-cli ping >/dev/null
  fi
  if docker run --rm --network host redpandadata/redpanda:v24.3.7 \
      rpk cluster info -X brokers=127.0.0.1:9092 >/dev/null 2>&1; then
    return 0
  fi

  docker rm -f ecosync_getsentry_relay_sentry_redpanda >/dev/null 2>&1 || true
  docker run -d --name ecosync_getsentry_relay_sentry_redpanda -p 9092:9092 \
    redpandadata/redpanda:v24.3.7 \
    redpanda start \
      --overprovisioned \
      --smp 1 \
      --memory 512M \
      --reserve-memory 0M \
      --node-id 0 \
      --check=false \
      --kafka-addr PLAINTEXT://0.0.0.0:9092 \
      --advertise-kafka-addr PLAINTEXT://127.0.0.1:9092 >/dev/null
  ECOSYNC_STARTED_REDPANDA=1

  for _ in $(seq 1 90); do
    if docker exec ecosync_getsentry_relay_sentry_redpanda \
        rpk cluster info -X brokers=127.0.0.1:9092 >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  docker logs --tail 100 ecosync_getsentry_relay_sentry_redpanda >&2 || true
  return 1
}

stop_relay_services() {
  if [[ "${ECOSYNC_STARTED_RELAY_REDIS:-0}" == "1" ]]; then
    docker rm -f ecosync_getsentry_c32abe_relay_redis >/dev/null 2>&1 || true
  fi
  if [[ "${ECOSYNC_STARTED_REDPANDA:-0}" == "1" ]]; then
    docker rm -f ecosync_getsentry_relay_sentry_redpanda >/dev/null 2>&1 || true
  fi
}

run_sentry() {
  ensure_sentry_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$sentry_image" bash -lc 'python --version && uv --version'
    return 0
  fi
  start_sentry_services
  trap stop_sentry_services EXIT

  timeout "${ECOSYNC_PROFILE_TIMEOUT:-1800}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e UV_CACHE_DIR=/uv-cache \
    -e UV_PYTHON_INSTALL_DIR=/uv-cache/python \
    -e UV_LINK_MODE=copy \
    -e SENTRY_SKIP_SERVICE_VALIDATION=1 \
    --network host \
    -v "$workspace:/workspace" \
    -v "$cache_root/uv:/uv-cache" \
    -v "$sentry_venv:/workspace/repos/getsentry/sentry/.venv" \
    -w /workspace/repos/getsentry/sentry \
    "$sentry_image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      uv sync --group dev --frozen
      uv run python tools/fast_editable.py
      uv run python -m pytest --override-ini=addopts= tests/sentry/relay/test_config.py \
        --junitxml=/workspace/.ecosyncbench/test-reports/sentry-relay-config.xml
    '
}

run_relay() {
  ensure_relay_image
  local relay_repo="$workspace/repos/getsentry/relay"
  local git_args=()
  mapfile -d '' -t git_args < <(relay_submodule_git_args)
  git -C "$relay_repo" "${git_args[@]}" submodule update --init --recursive
  if grep -q 'SCORE__TOTAL' "$relay_repo/relay-event-normalization/src/event.rs"; then
    git -C "$relay_repo/relay-conventions/sentry-conventions" "${git_args[@]}" fetch origin 108b899c2ec3f78726ade12837a067783df0c551
    git -C "$relay_repo/relay-conventions/sentry-conventions" checkout 108b899c2ec3f78726ade12837a067783df0c551
  fi
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$relay_image" bash -lc 'python --version && uv --version && rustc --version && cargo --version'
    return 0
  fi
  start_relay_services
  trap stop_relay_services EXIT

  timeout "${ECOSYNC_PROFILE_TIMEOUT:-2400}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e UV_CACHE_DIR=/uv-cache \
    -e UV_PYTHON_INSTALL_DIR=/uv-cache/python \
    -e CARGO_HOME=/cargo-cache \
    -e CARGO_TARGET_DIR=/cargo-target \
    -e PATH=/usr/local/cargo/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin \
    -e RELAY_RELEASE=relay@ecosyncbench \
    -e KAFKA_BOOTSTRAP_SERVER=127.0.0.1:9092 \
    -e UV_LINK_MODE=copy \
    --network host \
    -v "$workspace:/workspace" \
    -v "$cache_root/uv:/uv-cache" \
    -v "$cache_root/cargo:/cargo-cache" \
    -v "$cache_root/target:/cargo-target" \
    -v "$relay_venv:/workspace/repos/getsentry/relay/.venv" \
    -w /workspace/repos/getsentry/relay \
    "$relay_image" \
    bash -lc '
      set -euo pipefail
      export PATH=/usr/local/cargo/bin:$PATH
      export RELAY_RELEASE="${RELAY_RELEASE:-relay@ecosyncbench}"
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      rm -rf target
      ln -s /cargo-target target
      uv sync --frozen --only-dev
      RELAY_DEBUG=1 uv pip install -v -e py
      cargo build --all-features
      RELAY_BIN="$PWD/target/debug/relay" PYTEST_N=0 uv run pytest tests/integration/test_spans_standalone.py tests/integration/test_spansv2.py \
        --junitxml=/workspace/.ecosyncbench/test-reports/relay-spans.xml
    '
}

case "$profile" in
  sentry-hidden)
    run_sentry
    ;;
  relay-hidden)
    run_relay
    ;;
  *)
    echo "Unknown profile for getsentry_relay_sentry_c32abe62a9e3: $profile" >&2
    exit 2
    ;;
esac
