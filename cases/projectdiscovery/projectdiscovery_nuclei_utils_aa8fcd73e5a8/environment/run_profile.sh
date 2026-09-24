#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
host_repo_root="/opt/ecosyncbench"
go_image="ecosyncbench/base/go:1.26-bookworm"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/go-cache/projectdiscovery-nuclei-utils-aa8fcd73e5a8}"

mkdir -p \
  "$workspace/.ecosyncbench/test-reports" \
  "$cache_root/go-build" \
  "$cache_root/go-mod" \
  "$cache_root/home"

ensure_go_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$go_image" >/dev/null 2>&1; then
    docker build -t "$go_image" \
      -f "$host_repo_root/benchmark/images/base/go-1.26-bookworm/Dockerfile" \
      "$host_repo_root/benchmark/images/base/go-1.26-bookworm"
  fi
}

run_go_test() {
  local repo_dir="$1"
  local report="$2"
  local script="$3"

  ensure_go_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$go_image" bash -lc '/usr/local/go/bin/go version >/dev/null'
    return 0
  fi

  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-1800}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/cache/home \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e GOCACHE=/cache/go-build \
    -e GOMODCACHE=/cache/go-mod \
    -e GOPATH=/cache/gopath \
    -e GOFLAGS=-mod=mod \
    -e PATH=/usr/local/go/bin:/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    -v "$host_repo_root/repos/projectdiscovery/nuclei/.git:$host_repo_root/repos/projectdiscovery/nuclei/.git:ro" \
    -v "$host_repo_root/repos/projectdiscovery/utils/.git:$host_repo_root/repos/projectdiscovery/utils/.git:ro" \
    -w "/workspace/$repo_dir" \
    "$go_image" \
    bash -lc "set -euo pipefail; export PATH=/usr/local/go/bin:\$PATH; mkdir -p /workspace/.ecosyncbench/test-reports /cache/home; go env -w GOPROXY=https://goproxy.cn,direct; ${script} 2>&1 | tee /workspace/.ecosyncbench/test-reports/${report}; test -s /workspace/.ecosyncbench/test-reports/${report}"
}

case "$profile" in
  nuclei-hidden)
    run_go_test \
      repos/projectdiscovery/nuclei \
      nuclei-hidden.json \
      'mv ./pkg/fuzz/component/path_test.go ./pkg/fuzz/component/path_test.go.ecosync-disabled; go mod edit -replace github.com/projectdiscovery/utils=/workspace/repos/projectdiscovery/utils; go mod download; go test -vet=off -count=1 -json ./pkg/fuzz/component'
    ;;
  utils-hidden)
    run_go_test \
      repos/projectdiscovery/utils \
      utils-hidden.json \
      'rm -f ./url/path_contract_candidates_test.go; go run ./url/path_contract_generator.go ./url ./url/path_contract_candidates_test.go; go mod download; go test -vet=off -count=1 -json ./url'
    ;;
  *)
    echo "Unknown profile for projectdiscovery_nuclei_utils_aa8fcd73e5a8: $profile" >&2
    exit 2
    ;;
esac
