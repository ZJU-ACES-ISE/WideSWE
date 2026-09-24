#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
mkdir -p "$workspace/.ecosyncbench/test-reports"

run_python_profile() {
  local image="$1"
  local workdir="$2"
  local as_user="$3"
  local script="$4"

  docker image inspect "$image" >/dev/null 2>&1 || {
    echo "Missing dependency image: $image" >&2
    exit 2
  }
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image" bash -lc '/opt/ecosync/venv/bin/python --version >/dev/null && /opt/ecosync/venv/bin/python -m pytest --version >/dev/null'
    return 0
  fi

  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-3600}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "$as_user" \
    -e HOME=/tmp/ecosync-home \
    -e VIRTUAL_ENV=/opt/ecosync/venv \
    -e PATH=/opt/ecosync/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -v "$workspace:/workspace" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -lc "$script"
}

ensure_supervisor_overlay() {
  local image="ecosyncbench/deps/home-assistant-supervisor-py:7012a8e9bde2"
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build \
      -t "$image" \
      -f "$task_dir/environment/supervisor-runtime.Dockerfile" \
      "$task_dir/environment"
  fi
}

case "$profile" in
  core-hidden)
    run_python_profile \
      "ecosyncbench/deps/home-assistant-core-py:beaea2d99806" \
      "repos/home-assistant/core" \
      "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
      'mkdir -p /workspace/.ecosyncbench/test-reports
/opt/ecosync/venv/bin/python -m pytest -q -o addopts="" \
  tests/components/hassio/test_ingress.py \
  --junitxml=/workspace/.ecosyncbench/test-reports/core-hidden.xml'
    ;;
  supervisor-hidden)
    ensure_supervisor_overlay
    run_python_profile \
      "ecosyncbench/deps/home-assistant-supervisor-py:7012a8e9bde2" \
      "repos/home-assistant/supervisor" \
      "0:0" \
      'mkdir -p /workspace/.ecosyncbench/test-reports
dbus-run-session -- /opt/ecosync/venv/bin/python -m pytest -q -o addopts="" \
  tests/api/test_ingress.py \
  --junitxml=/workspace/.ecosyncbench/test-reports/supervisor-hidden.xml'
    ;;
  *)
    echo "Unknown profile for home_assistant_core_supervisor_7012a8e9bde2: $profile" >&2
    exit 2
    ;;
esac
