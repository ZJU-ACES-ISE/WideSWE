#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_root="$task_dir"
while [[ "$repo_root" != "/" && ! -d "$repo_root/.git" ]]; do repo_root="$(dirname "$repo_root")"; done
if [[ ! -d "$repo_root/.git" ]]; then
  repo_root="$(git -C "$(pwd)" rev-parse --show-toplevel 2>/dev/null || true)"
fi
if [[ -z "$repo_root" || ! -d "$repo_root/.git" ]]; then
  echo "cannot locate repository root from $task_dir or current working directory" >&2
  exit 2
fi

workspace="$(cd "${ECOSYNC_WORKSPACE:?ECOSYNC_WORKSPACE required}" && pwd)"
export ECOSYNC_REPO_ROOT="$repo_root"
export ECOSYNC_BUILD_CONTEXT="$workspace"
export ECOSYNC_UID="${ECOSYNC_UID:-$(id -u)}"
export ECOSYNC_GID="${ECOSYNC_GID:-$(id -g)}"

mkdir -p "$workspace/.ecosyncbench/test-reports"

ensure_base_image() {
  local image="$1"
  local dockerfile="$2"
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$repo_root/$dockerfile" "$repo_root"
  fi
}

compose_run() {
  local service="$1"
  local image="$2"
  local as_user="$3"
  local container_name="ecosync_${profile}_$$_${RANDOM}"
  cd "$task_dir/environment"
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    ECOSYNC_WORKSPACE="$workspace" docker compose build "$service"
  fi
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image" bash -lc '/opt/ecosync/venv/bin/python --version'
    return 0
  fi
  ECOSYNC_WORKSPACE="$workspace" ECOSYNC_UID="${ECOSYNC_UID}" ECOSYNC_GID="${ECOSYNC_GID}" \
    docker compose run --rm --user "$as_user" --name "$container_name" "$service"
}

case "$profile" in
  core-hidden)
    ensure_base_image ecosyncbench/base/python:3.14-slim benchmark/images/base/python-3.14-slim/Dockerfile
    compose_run core-hidden ecosyncbench/deps/home-assistant-core-py:4bf3a5b4bd96-paho "${ECOSYNC_UID}:${ECOSYNC_GID}"
    ;;
  supervisor-hidden)
    ensure_base_image ecosyncbench/base/python:3.13-slim benchmark/images/base/python-3.13-slim/Dockerfile
    compose_run supervisor-hidden ecosyncbench/deps/home-assistant-supervisor-py:da800b888974 "0:0"
    ;;
  *)
    echo "Unknown profile for home_assistant_core_supervisor_5ec0c95de28d: $profile" >&2
    exit 2
    ;;
esac
