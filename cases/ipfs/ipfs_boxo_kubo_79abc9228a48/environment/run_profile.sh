#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_root="$task_dir"
while [[ "$repo_root" != "/" && ! -d "$repo_root/.git" ]]; do repo_root="$(dirname "$repo_root")"; done
if [[ ! -d "$repo_root/.git" ]]; then
  repo_root="$(pwd)"
fi
if [[ ! -d "$repo_root/.git" ]]; then echo "cannot locate repository root from $task_dir or cwd" >&2; exit 2; fi

workspace="$(cd "${ECOSYNC_WORKSPACE:-$repo_root}" && pwd)"
cache_root="${ECOSYNC_IPFS_BOXO_KUBO_79ABC9228A48_CACHE_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/ipfs-boxo-kubo-79abc9228a48}}"
export ECOSYNC_REPO_ROOT="$repo_root"
export ECOSYNC_BUILD_CONTEXT="$workspace"
export ECOSYNC_UID="${ECOSYNC_UID:-$(id -u)}"
export ECOSYNC_GID="${ECOSYNC_GID:-$(id -g)}"

mkdir -p "$cache_root/boxo-hidden-go-build" "$cache_root/kubo-hidden-go-build"
chmod 0777 "$cache_root/boxo-hidden-go-build" "$cache_root/kubo-hidden-go-build" 2>/dev/null || true

ensure_base_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect ecosyncbench/base/go:1.25-bookworm >/dev/null 2>&1; then
    docker build -t ecosyncbench/base/go:1.25-bookworm \
      -f "$repo_root/benchmark/images/base/go-1.25-bookworm/Dockerfile" \
      "$repo_root"
  fi
}

ensure_runner_script() {
  mkdir -p "$workspace/.ecosyncbench"
  cat > "$workspace/.ecosyncbench/run-go-profile.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
shift
mkdir -p /workspace/.ecosyncbench/test-reports
export GOMODCACHE="${GOMODCACHE:-/opt/ecosync/gomodcache}"
export GOPATH="${GOPATH:-/opt/ecosync/gopath}"
export GOCACHE="${GOCACHE:-/tmp/ecosync-gocache}"
export PATH="/usr/local/go/bin:$PATH"
report="/workspace/.ecosyncbench/test-reports/${profile}.json"
: > "$report"
run_go_test() {
  /usr/local/go/bin/go test -count=1 -json "$@" | tee -a "$report"
}
case "$profile" in
  boxo-hidden)
    run_go_test ./mfs ./pinning/pinner/dspinner
    ;;
  kubo-hidden)
    export GOWORK=off
    /usr/local/go/bin/go mod edit -replace github.com/ipfs/boxo=/workspace/repos/ipfs/boxo
    /usr/local/go/bin/go mod edit -exclude google.golang.org/genproto@v0.0.0-20230410155749-daa745c078e1
    if [[ -f test/dependencies/go.mod ]]; then
      (cd test/dependencies && /usr/local/go/bin/go mod edit -replace github.com/ipfs/boxo=/workspace/repos/ipfs/boxo)
    fi
    /usr/local/go/bin/go build -o ./cmd/ipfs/ipfs ./cmd/ipfs
    run_go_test ./client/rpc ./core/coreapi/test
    mv ./cmd/ipfs/ipfs ./cmd/ipfs/ipfs.real
    cat > ./cmd/ipfs/ipfs <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
real="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ipfs.real"
if [[ "${1:-}" == "swarm" && "${2:-}" == "connect" ]]; then
  exec /usr/bin/flock /tmp/ecosync-kubo-swarm-connect.lock "$real" "$@"
fi
exec "$real" "$@"
WRAPPER
    chmod +x ./cmd/ipfs/ipfs
    run_go_test -parallel=1 ./test/cli -run '^TestProvider$'
    ;;
  *)
    echo "Unknown Go profile: $profile" >&2
    exit 2
    ;;
esac
SH
  chmod +x "$workspace/.ecosyncbench/run-go-profile.sh"
}

compose_run() {
  local service="$1"
  local image="$2"
  local go_build_cache="$cache_root/${service}-go-build"
  local container_name="ecosync_${profile}_$$_${RANDOM}"
  ensure_base_image
  ensure_runner_script
  cd "$task_dir/environment"
  if [[ "${ECOSYNC_DOCKER_FORCE_WARMUP:-0}" == "1" ]] && docker image inspect "$image" >/dev/null 2>&1; then
    return 0
  fi
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    ECOSYNC_WORKSPACE="$workspace" docker compose build "$service"
  fi
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
    return 0
  fi
  ECOSYNC_WORKSPACE="$workspace" docker compose run --rm \
    --name "$container_name" \
    -v "$workspace/.ecosyncbench/run-go-profile.sh:/opt/ecosync/run-go-profile.sh:ro" \
    -v "$go_build_cache:/tmp/ecosync-gocache" \
    "$service"
}

case "$profile" in
  boxo-hidden)
    compose_run boxo-hidden ecosyncbench/deps/ipfs-boxo-go:c29662645c42-79abc9228a48-go125
    ;;
  kubo-hidden)
    compose_run kubo-hidden ecosyncbench/deps/ipfs-kubo-go:58ad11b57351-79abc9228a48-go125
    ;;
  *)
    echo "Unknown profile for ipfs_boxo_kubo_79abc9228a48: $profile" >&2
    exit 2
    ;;
esac
