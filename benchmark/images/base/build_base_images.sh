#!/usr/bin/env bash
set -euo pipefail

base_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

docker_proxy_args() {
  local proxy="${ECOSYNC_BUILD_PROXY:-}"
  if [[ -n "$proxy" ]]; then
    proxy="${proxy//127.0.0.1/host.docker.internal}"
    proxy="${proxy//localhost/host.docker.internal}"
    printf '%s\n' --add-host=host.docker.internal:host-gateway
    printf '%s\n' --build-arg HTTP_PROXY="$proxy"
    printf '%s\n' --build-arg HTTPS_PROXY="$proxy"
    printf '%s\n' --build-arg ALL_PROXY="$proxy"
    printf '%s\n' --build-arg http_proxy="$proxy"
    printf '%s\n' --build-arg https_proxy="$proxy"
    printf '%s\n' --build-arg all_proxy="$proxy"
    printf '%s\n' --build-arg NO_PROXY="${NO_PROXY:-localhost,127.0.0.1}"
    printf '%s\n' --build-arg no_proxy="${no_proxy:-localhost,127.0.0.1}"
  fi
}

build_one() {
  local name="$1"
  local tag="$2"
  local proxy_args=()
  mapfile -t proxy_args < <(docker_proxy_args)
  docker build "${proxy_args[@]}" -f "$base_dir/$name/Dockerfile" -t "$tag" "$base_dir/$name"
}

services=("$@")
if [[ ${#services[@]} -eq 0 ]]; then
  services=(dotnet-sdk-8.0.100 node-22-bookworm android-sdk-36 python-3.14-slim)
fi

for service in "${services[@]}"; do
  case "$service" in
    dotnet-sdk-8.0.100)
      build_one "$service" ecosyncbench/base/dotnet-sdk:8.0.100
      ;;
    node-22-bookworm)
      build_one "$service" ecosyncbench/base/node:22-bookworm-slim
      ;;
    node-24-bookworm)
      build_one "$service" ecosyncbench/base/node:24-bookworm
      ;;
    android-sdk-36)
      build_one "$service" ecosyncbench/base/android-sdk:36
      ;;
    python-3.14-slim)
      build_one "$service" ecosyncbench/base/python:3.14-slim
      ;;
    *)
      echo "unknown base image: $service" >&2
      exit 2
      ;;
  esac
done
