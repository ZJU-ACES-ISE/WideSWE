#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
repo_root="${ECOSYNC_REPO_ROOT:-/opt/ecosyncbench}"
image="ecosyncbench/base/go:1.26-bookworm"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/cases/elastic_elastic_agent_fleet_server_6b3b072a9e2f}"
mkdir -p "$cache_root/mod" "$cache_root/build" "$workspace/.ecosyncbench/test-reports"

ensure_image() {
  docker image inspect "$image" >/dev/null 2>&1 || \
    docker build -t "$image" -f "$repo_root/benchmark/images/base/go-1.26-bookworm/Dockerfile" "$repo_root/benchmark/images/base/go-1.26-bookworm"
}

run_go() {
  local workdir="$1"
  local report="$2"
  local script="$3"
  ensure_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image" bash -c '/usr/local/go/bin/go version'
    return 0
  fi
  timeout "${ECOSYNC_PROFILE_TIMEOUT:-2400}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e GOTOOLCHAIN=auto \
    -e GOMODCACHE=/go-cache/mod \
    -e GOCACHE=/go-cache/build \
    -v "$workspace:/workspace" \
    -v "$cache_root:/go-cache" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -c "set -euo pipefail; mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home; ( ${script} ) 2>&1 | tee /workspace/.ecosyncbench/test-reports/${report}"
}

case "$profile" in
  elastic_agent-hidden)
    run_go "repos/elastic/elastic-agent" "elastic-agent-hidden.json" \
      "cp go.mod /tmp/ecosync-elastic-agent.mod; cp go.sum /tmp/ecosync-elastic-agent.sum; sed -i '/^replace github.com\\/elastic\\/beats\\/v7 => \\.\\/beats$/d' /tmp/ecosync-elastic-agent.mod; /usr/local/go/bin/go test -json -count=1 -mod=mod -modfile=/tmp/ecosync-elastic-agent.mod ./internal/pkg/agent/configuration ./internal/pkg/agent/application/enroll ./internal/pkg/agent/application/gateway/fleet"
    ;;
  fleet_server-hidden)
    run_go "repos/elastic/fleet-server" "fleet-server-hidden.json" \
      "/usr/local/go/bin/go test -json -count=1 ./internal/pkg/api ./internal/pkg/server"
    ;;
  *)
    echo "Unknown profile for elastic_elastic_agent_fleet_server_6b3b072a9e2f: $profile" >&2
    exit 2
    ;;
esac
