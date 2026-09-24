#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -d "$PWD/.git" ]]; then
  repo_root="$PWD"
else
  repo_root="$task_dir"
  while [[ "$repo_root" != "/" && ! -d "$repo_root/.git" ]]; do repo_root="$(dirname "$repo_root")"; done
fi
if [[ ! -d "$repo_root/.git" ]]; then echo "cannot locate repository root from $task_dir" >&2; exit 2; fi

workspace="$(cd "${ECOSYNC_WORKSPACE:-$repo_root}" && pwd)"
cache_root="${ECOSYNC_IPFS_BOXO_KUBO_4508FC5F72A1_CACHE_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/ipfs-boxo-kubo-4508fc5f72a1}}"
export ECOSYNC_REPO_ROOT="$repo_root"
export ECOSYNC_BUILD_CONTEXT="$workspace"
export ECOSYNC_UID="${ECOSYNC_UID:-$(id -u)}"
export ECOSYNC_GID="${ECOSYNC_GID:-$(id -g)}"

mkdir -p "$cache_root/boxo-hidden-go-build" "$cache_root/kubo-hidden-go-build"
chmod 0777 "$cache_root/boxo-hidden-go-build" "$cache_root/kubo-hidden-go-build" 2>/dev/null || true

ensure_base_images() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect ecosyncbench/base/go:1.26-bookworm >/dev/null 2>&1; then
    docker build -t ecosyncbench/base/go:1.26-bookworm \
      -f "$repo_root/benchmark/images/base/go-1.26-bookworm/Dockerfile" \
      "$repo_root"
  fi
}

ensure_runner_scripts() {
  mkdir -p "$workspace/.ecosyncbench"
  cat > "$workspace/.ecosyncbench/run-go-test.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
shift
mkdir -p /workspace/.ecosyncbench/test-reports
export GOMODCACHE="${GOMODCACHE:-/opt/ecosync/gomodcache}"
export GOPATH="${GOPATH:-/opt/ecosync/gopath}"
export GOCACHE="${GOCACHE:-/tmp/ecosync-gocache}"
export PATH="/usr/local/go/bin:$PATH"
go test -count=1 -json "$@" | tee "/workspace/.ecosyncbench/test-reports/${profile}.json"
SH
  cat > "$workspace/.ecosyncbench/run-kubo-hidden.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
mkdir -p /workspace/.ecosyncbench/test-reports
export GOMODCACHE="${GOMODCACHE:-/opt/ecosync/gomodcache}"
export GOPATH="${GOPATH:-/opt/ecosync/gopath}"
export GOCACHE="${GOCACHE:-/tmp/ecosync-gocache}"
export PATH="/usr/local/go/bin:$PATH"
export GOWORK=off

go mod edit -replace github.com/ipfs/boxo=/workspace/repos/ipfs/boxo
go mod edit -exclude google.golang.org/genproto@v0.0.0-20230410155749-daa745c078e1
if [[ -f test/dependencies/go.mod ]]; then
  (cd test/dependencies && go mod edit -replace github.com/ipfs/boxo=/workspace/repos/ipfs/boxo)
fi

report=/workspace/.ecosyncbench/test-reports/kubo-hidden.json
rm -f "$report"
status=0
go build -o cmd/ipfs/ipfs ./cmd/ipfs || status=1
go test -count=1 -json ./core/node/libp2p -run '^(TestDetermineCapabilities|TestEndpointCapabilitiesReadWriteLogic|TestHttpRouterAddrFunc)$' | tee -a "$report" || status=1
go test -count=1 -json ./test/cli -run '^(TestHTTPDelegatedRouting|TestHTTPDelegatedRoutingProviderAddrs)$' | tee -a "$report" || status=1
exit "$status"
SH
  chmod +x "$workspace/.ecosyncbench/run-go-test.sh" "$workspace/.ecosyncbench/run-kubo-hidden.sh"
}

compose_run() {
  local service="$1"
  local image="$2"
  local go_build_cache="$cache_root/${service}-go-build"
  local container_name="ecosync_${profile}_$$_${RANDOM}"
  ensure_base_images
  ensure_runner_scripts
  cd "$task_dir/environment"
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    ECOSYNC_WORKSPACE="$workspace" docker compose build "$service"
  fi
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
    return 0
  fi
  ECOSYNC_WORKSPACE="$workspace" docker compose run -T --rm \
    --name "$container_name" \
    -v "$workspace/.ecosyncbench/run-go-test.sh:/opt/ecosync/run-go-test.sh:ro" \
    -v "$workspace/.ecosyncbench/run-kubo-hidden.sh:/opt/ecosync/run-kubo-hidden.sh:ro" \
    -v "$go_build_cache:/tmp/ecosync-gocache" \
    "$service"
}

case "$profile" in
  boxo-hidden)
    compose_run boxo-hidden ecosyncbench/deps/ipfs-boxo-go:4508fc5f72a1
    ;;
  kubo-hidden)
    compose_run kubo-hidden ecosyncbench/deps/ipfs-kubo-go:4508fc5f72a1
    ;;
  *)
    echo "Unknown profile for ipfs_boxo_kubo_4508fc5f72a1: $profile" >&2
    exit 2
    ;;
esac
