#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
host_repo_root="$(pwd -P)"
go_image="ecosyncbench/base/go:1.26-bookworm"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/projectdiscovery-dnsx-utils}"

mkdir -p "$workspace/.ecosyncbench/test-reports" "$cache_root/go-build" "$cache_root/go-mod" "$cache_root/home"

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
  shift 2
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
    -e GOCACHE=/cache/go-build \
    -e GOMODCACHE=/cache/go-mod \
    -e GOPATH=/cache/gopath \
    -e GOFLAGS=-mod=mod \
    -e PATH=/usr/local/go/bin:/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    -v "$host_repo_root/repos/projectdiscovery/dnsx/.git:$host_repo_root/repos/projectdiscovery/dnsx/.git:ro" \
    -v "$host_repo_root/repos/projectdiscovery/utils/.git:$host_repo_root/repos/projectdiscovery/utils/.git:ro" \
    -w "/workspace/$repo_dir" \
    "$go_image" \
    bash -c 'set -euo pipefail; mkdir -p /workspace/.ecosyncbench/test-reports /cache/home; set +e; /usr/local/go/bin/go test -count=1 -json "$@" > "/workspace/.ecosyncbench/test-reports/'"$report"'"; status=$?; set -e; test -s "/workspace/.ecosyncbench/test-reports/'"$report"'"; exit "$status"' \
    -- "$@"
}

case "$profile" in
  dnsx-hidden)
    run_go_test repos/projectdiscovery/dnsx dnsx-internal-runner-go-test.json ./internal/runner
    ;;
  utils-hidden)
    run_go_test repos/projectdiscovery/utils utils-wildcard-process-go-test.json ./dns/wildcard ./process
    ;;
  *)
    echo "Unknown profile for projectdiscovery_dnsx_utils_ae3ee347579a: $profile" >&2
    exit 2
    ;;
esac
