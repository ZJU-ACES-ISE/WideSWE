#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
repo_root="${ECOSYNC_REPO_ROOT:-/opt/ecosyncbench}"
image="ecosyncbench/base/go:1.26-bookworm"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/go-cache/mongodb_atlas_providers}"
mkdir -p "$cache_root/go-build" "$cache_root/go-mod" "$workspace/.ecosyncbench/test-reports"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$repo_root/benchmark/images/base/go-1.26-bookworm/Dockerfile" "$repo_root/benchmark/images/base/go-1.26-bookworm"
  fi
}

run_go() {
  local workdir="$1"
  local script="$2"
  ensure_image
  timeout 1800 docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    --tmpfs /tmp:rw,exec,mode=1777,size=8g \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e GOCACHE=/go-cache/go-build \
    -e GOMODCACHE=/go-cache/go-mod \
    -e GOFLAGS=-mod=mod \
    -e ECOSYNC_DOCKER_PREBUILD_ONLY="${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" \
    -e ECOSYNC_DOCKER_WARMUP_ONLY="${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/go-cache" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -c "$script"
}

case "$profile" in
  mongodbatlas_cloudformation_resources-hidden)
    run_go "repos/mongodb/mongodbatlas-cloudformation-resources/cfn-resources" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
        go test -run '^$' ./alert-configuration/cmd/resource >/dev/null
        exit 0
      fi
      set +e
      go test -json ./alert-configuration/cmd/resource > /workspace/.ecosyncbench/test-reports/mongodbatlas-cloudformation-resources-alert-configuration.json
      code=$?
      set -e
      exit "$code"
    '
    ;;
  terraform_provider_mongodbatlas-hidden)
    run_go "repos/mongodb/terraform-provider-mongodbatlas" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
        go test -run '^$' ./internal/service/alertconfiguration >/dev/null
        exit 0
      fi
      set +e
      go test -json ./internal/service/alertconfiguration > /workspace/.ecosyncbench/test-reports/terraform-provider-mongodbatlas-alertconfiguration.json
      code=$?
      set -e
      exit "$code"
    '
    ;;
  *)
    echo "Unknown profile for mongodb_mongodbatlas_cloudformation_resources_terraform_provider_mongodbatlas_dc9616ecb793: $profile" >&2
    exit 2
    ;;
esac
