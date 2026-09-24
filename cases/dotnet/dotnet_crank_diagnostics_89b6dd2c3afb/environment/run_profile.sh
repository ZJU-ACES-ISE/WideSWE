#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_root="${ECOSYNC_REPO_ROOT:-}"
if [[ -z "$repo_root" ]]; then
  if [[ -d "$(pwd)/.git" ]]; then
    repo_root="$(pwd)"
  else
    repo_root="$task_dir"
    while [[ "$repo_root" != "/" && ! -d "$repo_root/.git" ]]; do repo_root="$(dirname "$repo_root")"; done
  fi
fi
if [[ ! -d "$repo_root/.git" ]]; then
  echo "cannot locate repository root; set ECOSYNC_REPO_ROOT" >&2
  exit 2
fi

workspace="$(cd "${ECOSYNC_WORKSPACE:-$repo_root}" && pwd)"
export ECOSYNC_REPO_ROOT="$repo_root"
export ECOSYNC_BUILD_CONTEXT="$workspace"
export ECOSYNC_UID="${ECOSYNC_UID:-$(id -u)}"
export ECOSYNC_GID="${ECOSYNC_GID:-$(id -g)}"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/dotnet-cache/dotnet_crank_diagnostics_89b6dd2c3afb}"
mkdir -p "$cache_root/dotnet-test" "$cache_root/nuget" "$cache_root/nuget-http" "$cache_root/dotnet-home"
export ECOSYNC_DOTNET_CACHE_ROOT="$cache_root"
export ECOSYNC_TASK_DIR="$task_dir"

prepare_modern_linux_shim() {
  local source="$task_dir/environment/fake_modern_linux.c"
  local output="$cache_root/fake-modern-linux.so"
  if [[ ! -f "$output" || "$source" -nt "$output" ]]; then
    command -v gcc >/dev/null 2>&1 || {
      echo "gcc is required to build the modern-Linux test shim" >&2
      return 1
    }
    gcc -shared -fPIC -O2 -Wall -Wextra -o "$output" "$source" -ldl
  fi
  export ECOSYNC_MODERN_LINUX_SHIM="$output"
}

ensure_base_images() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect ecosyncbench/base/dotnet-sdk:8.0 >/dev/null 2>&1; then
    docker build -t ecosyncbench/base/dotnet-sdk:8.0 \
      --build-arg ECOSYNC_DOTNET_UPSTREAM_IMAGE=mcr.microsoft.com/dotnet/sdk:8.0 \
      -f "$task_dir/environment/dotnet-sdk-base.Dockerfile" \
      "$repo_root"
  fi
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect ecosyncbench/base/dotnet-sdk:10.0 >/dev/null 2>&1; then
    docker build -t ecosyncbench/base/dotnet-sdk:10.0 \
      --build-arg ECOSYNC_DOTNET_UPSTREAM_IMAGE=mcr.microsoft.com/dotnet/sdk:10.0 \
      -f "$task_dir/environment/dotnet-sdk-base.Dockerfile" \
      "$repo_root"
  fi
}

compose_run() {
  local service="$1"
  local image="$2"
  local container_name="ecosync_${profile}_$$_${RANDOM}"
  ensure_base_images
  cd "$task_dir/environment"
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    ECOSYNC_WORKSPACE="$workspace" docker compose build "$service"
  fi
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
    return 0
  fi
  ECOSYNC_WORKSPACE="$workspace" docker compose run --rm --name "$container_name" "$service"
}

prepare_modern_linux_shim

case "$profile" in
  crank-hidden)
    compose_run crank-hidden ecosyncbench/base/dotnet-sdk:8.0
    ;;
  diagnostics-hidden)
    compose_run diagnostics-hidden ecosyncbench/base/dotnet-sdk:10.0
    ;;
  *)
    echo "Unknown profile for dotnet_crank_diagnostics_89b6dd2c3afb: $profile" >&2
    exit 2
    ;;
esac
