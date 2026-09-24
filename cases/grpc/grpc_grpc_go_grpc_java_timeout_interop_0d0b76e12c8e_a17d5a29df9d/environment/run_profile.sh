#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"

task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_root="$task_dir"
while [[ "$repo_root" != "/" && ! -d "$repo_root/.git" ]]; do repo_root="$(dirname "$repo_root")"; done
if [[ ! -d "$repo_root/.git" ]]; then
  repo_root="/opt/ecosyncbench"
fi
if [[ ! -d "$repo_root/.git" ]]; then echo "cannot locate repository root from $task_dir" >&2; exit 2; fi

workspace="$(cd "${ECOSYNC_WORKSPACE:-$repo_root}" && pwd)"
uid="${ECOSYNC_UID:-$(id -u)}"
gid="${ECOSYNC_GID:-$(id -g)}"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/grpc_grpc_go_grpc_java_timeout_interop_0d0b76e12c8e_a17d5a29df9d}"

ensure_cache_dir() {
  local path="$1"
  mkdir -p "$path"
  if [[ ! -w "$path" ]] && command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    sudo chown "$uid:$gid" "$path" || true
  fi
}

run_go_transport() {
  local repo="$workspace/repos/grpc/grpc-go"
  local report_dir="$workspace/.ecosyncbench/test-reports"
  local mod_cache="$cache_root/grpc-go/gomod"
  local build_cache="$cache_root/grpc-go/gobuild"
  ensure_cache_dir "$mod_cache"
  ensure_cache_dir "$build_cache"
  mkdir -p "$report_dir"
  rm -f "$report_dir/${profile}.json"
  docker run --rm \
    -u "$uid:$gid" \
    -v "$repo:/workspace" \
    -v "$report_dir:/reports" \
    -v "$mod_cache:/go/pkg/mod" \
    -v "$build_cache:/home/ecosync/.cache/go-build" \
    -e GOMODCACHE=/go/pkg/mod \
    -e GOCACHE=/home/ecosync/.cache/go-build \
    -w /workspace \
    ecosyncbench/base/go:1.26-bookworm \
    bash -lc "export PATH=/usr/local/go/bin:\$PATH; go test -json ./internal/transport > /reports/${profile}.json"
}

run_java_core() {
  local repo="$workspace/repos/grpc/grpc-java"
  local gradle_cache="$cache_root/grpc-java-gradle"
  ensure_cache_dir "$gradle_cache"
  rm -rf "$repo/core/build/test-results/test" "$repo/core/build/reports/tests/test"
  docker run --rm --network host \
    -u "$uid:$gid" \
    -v "$repo:/workspace" \
    -v "$gradle_cache:/gradle-cache" \
    -e GRADLE_USER_HOME=/gradle-cache \
    -w /workspace \
    ecosyncbench/base/java-maven:21-ubuntu \
    bash -lc './gradlew --no-daemon --build-cache -PskipAndroid=true :grpc-core:test --tests io.grpc.internal.AbstractClientStreamTest --tests io.grpc.internal.GrpcUtilTest --tests io.grpc.internal.ServerImplTest --continue'
}

case "$profile" in
  grpc_go-hidden)
    if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
      docker image inspect ecosyncbench/base/go:1.26-bookworm >/dev/null
      exit 0
    fi
    run_go_transport
    ;;
  grpc_java-hidden)
    if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
      docker image inspect ecosyncbench/base/java-maven:21-ubuntu >/dev/null
      exit 0
    fi
    run_java_core
    ;;
  *)
    echo "Unknown profile for grpc_grpc_go_grpc_java_timeout_interop_0d0b76e12c8e_a17d5a29df9d: $profile" >&2
    exit 2
    ;;
esac
