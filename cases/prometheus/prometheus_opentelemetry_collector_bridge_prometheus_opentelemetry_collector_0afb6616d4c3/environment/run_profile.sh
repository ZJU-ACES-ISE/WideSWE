#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
host_repo_root="$(pwd -P)"
image_go="ecosyncbench/base/go:1.25-bookworm"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/go-cache/prometheus-stackdriver}"
mkdir -p "$cache_root/mod" "$cache_root/build" "$workspace/.ecosyncbench/test-reports"

ensure_go_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image_go" >/dev/null 2>&1; then
    docker build \
      -t "$image_go" \
      -f "$task_dir/environment/go-1.25.Dockerfile" \
      "$task_dir/environment"
  fi
}

run_in_go() {
  local workdir="$1"
  local timeout_seconds="$2"
  local script="$3"
  ensure_go_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image_go" bash -lc '/usr/local/go/bin/go version'
    return 0
  fi
  timeout "$timeout_seconds" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e GOMODCACHE=/go-cache/mod \
    -e GOPATH=/go-cache/gopath \
    -e GOCACHE=/go-cache/build \
    -e GOFLAGS=-mod=mod \
    -v "$workspace:/workspace" \
    -v "$cache_root:/go-cache" \
    -v "$host_repo_root/repos/prometheus/opentelemetry-collector-bridge/.git:$host_repo_root/repos/prometheus/opentelemetry-collector-bridge/.git:ro" \
    -v "$host_repo_root/repos/prometheus/prometheus-opentelemetry-collector/.git:$host_repo_root/repos/prometheus/prometheus-opentelemetry-collector/.git:ro" \
    -v "$host_repo_root/repos/prometheus-community/stackdriver_exporter/.git:$host_repo_root/repos/prometheus-community/stackdriver_exporter/.git:ro" \
    -w "/workspace/$workdir" \
    "$image_go" \
    bash -lc "$script"
}

go_test_json() {
  local report="$1"
  shift
  mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
  go test -count=1 -json "$@" | tee "/workspace/.ecosyncbench/test-reports/${report}.json"
}

case "$profile" in
  opentelemetry_collector_bridge-hidden)
    run_in_go "repos/prometheus/opentelemetry-collector-bridge" "${ECOSYNC_PROFILE_TIMEOUT:-1800}" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      hidden_tmp=$(mktemp -d)
      for f in *_test.go; do
        if [[ "$f" != "ecosync_contract_test.go" ]]; then
          mv "$f" "$hidden_tmp"/
        fi
      done
      trap "mv \"$hidden_tmp\"/*_test.go . 2>/dev/null || true; rm -rf \"$hidden_tmp\"" EXIT
      /usr/local/go/bin/go test -count=1 -json . | tee /workspace/.ecosyncbench/test-reports/opentelemetry_collector_bridge-hidden.json
    '
    ;;
  prometheus_opentelemetry_collector-hidden)
    run_in_go "repos/prometheus/prometheus-opentelemetry-collector/receivers/stackdriver" "${ECOSYNC_PROFILE_TIMEOUT:-2400}" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      /usr/local/go/bin/go mod edit -replace github.com/prometheus/opentelemetry-collector-bridge=/workspace/repos/prometheus/opentelemetry-collector-bridge
      /usr/local/go/bin/go mod edit -replace github.com/prometheus-community/stackdriver_exporter=/workspace/repos/prometheus-community/stackdriver_exporter
      hidden_tmp=$(mktemp -d)
      for f in *_test.go; do
        if [[ "$f" != "ecosync_contract_test.go" ]]; then
          mv "$f" "$hidden_tmp"/
        fi
      done
      trap "mv \"$hidden_tmp\"/*_test.go . 2>/dev/null || true; rm -rf \"$hidden_tmp\"" EXIT
      /usr/local/go/bin/go test -count=1 -json . | tee /workspace/.ecosyncbench/test-reports/prometheus_opentelemetry_collector-hidden.json
    '
    ;;
  stackdriver_exporter-hidden)
    run_in_go "repos/prometheus-community/stackdriver_exporter" "${ECOSYNC_PROFILE_TIMEOUT:-2400}" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      /usr/local/go/bin/go test -count=1 -json ./ecosync_contract | tee /workspace/.ecosyncbench/test-reports/stackdriver_exporter-hidden.json
    '
    ;;
  *)
    echo "Unknown profile for prometheus Stackdriver case: $profile" >&2
    exit 2
    ;;
esac
