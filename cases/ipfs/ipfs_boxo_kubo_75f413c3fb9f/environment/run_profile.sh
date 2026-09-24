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
cache_root="${ECOSYNC_IPFS_BOXO_KUBO_75F413C3FB9F_CACHE_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/ipfs-boxo-kubo-75f413c3fb9f}}"
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

ensure_runner_script() {
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
  chmod +x "$workspace/.ecosyncbench/run-go-test.sh"
}

compose_run() {
  local service="$1"
  local image="$2"
  local go_build_cache="$cache_root/${service}-go-build"
  local container_name="ecosync_${profile}_$$_${RANDOM}"
  ensure_base_images
  ensure_runner_script
  cd "$task_dir/environment"
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    ECOSYNC_WORKSPACE="$workspace" docker compose build "$service"
  fi
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
    return 0
  fi
  ECOSYNC_WORKSPACE="$workspace" docker compose run --rm \
    --name "$container_name" \
    -v "$workspace/.ecosyncbench/run-go-test.sh:/opt/ecosync/run-go-test.sh:ro" \
    -v "$go_build_cache:/tmp/ecosync-gocache" \
    "$service"
}

case "$profile" in
  boxo-hidden)
    compose_run boxo-hidden ecosyncbench/deps/ipfs-boxo-go:75f413c3fb9f
    ;;
  kubo-hidden)
    compose_run kubo-hidden ecosyncbench/deps/ipfs-kubo-go:75f413c3fb9f
    ;;
  *)
    echo "Unknown profile for ipfs_boxo_kubo_75f413c3fb9f: $profile" >&2
    exit 2
    ;;
esac
