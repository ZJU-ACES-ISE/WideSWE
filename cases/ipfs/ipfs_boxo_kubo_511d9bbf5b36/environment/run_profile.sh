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
cache_root="${ECOSYNC_IPFS_BOXO_KUBO_511D9BBF5B36_CACHE_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/ipfs-boxo-kubo-511d9bbf5b36}}"
export ECOSYNC_REPO_ROOT="$repo_root"
export ECOSYNC_BUILD_CONTEXT="$workspace"
export ECOSYNC_UID="${ECOSYNC_UID:-$(id -u)}"
export ECOSYNC_GID="${ECOSYNC_GID:-$(id -g)}"

mkdir -p "$cache_root/boxo-hidden-go-build" "$cache_root/kubo-hidden-go-build" "$cache_root/kubo-sharness"
chmod 0777 "$cache_root/boxo-hidden-go-build" "$cache_root/kubo-hidden-go-build" "$cache_root/kubo-sharness" 2>/dev/null || true

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
if [[ -n "${GOFLAGS:-}" ]]; then
  export GOFLAGS="${GOFLAGS} -tags=untested_go_version"
else
  export GOFLAGS="-tags=untested_go_version"
fi
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
if [[ -n "${GOFLAGS:-}" ]]; then
  export GOFLAGS="${GOFLAGS} -tags=untested_go_version -buildvcs=false"
else
  export GOFLAGS="-tags=untested_go_version -buildvcs=false"
fi
export PATH="/usr/local/go/bin:$PATH"
export GOWORK=off

go mod edit -replace github.com/ipfs/boxo=/workspace/repos/ipfs/boxo
go mod edit -exclude google.golang.org/genproto@v0.0.0-20230410155749-daa745c078e1
if [[ -f test/dependencies/go.mod ]]; then
  (cd test/dependencies && go mod edit -replace github.com/ipfs/boxo=/workspace/repos/ipfs/boxo)
fi

report=/workspace/.ecosyncbench/test-reports/kubo-hidden-go.json
rm -f "$report"
status=0
go build -o cmd/ipfs/ipfs ./cmd/ipfs || status=1
go test -count=1 -json -parallel=1 ./test/cli -run '^TestIdentityCIDOverflowProtection$' | tee "$report" || status=1

sharness_cache=/opt/ecosync/sharness-cache
sharness_repo="$sharness_cache/repo"
sharness_commit=803df39d3cba16bb7d493dd6cd8bc5e29826da61
exec 9>"$sharness_cache/.lock"
flock 9
if ! git -C "$sharness_repo" cat-file -e "${sharness_commit}^{commit}" >/dev/null 2>&1; then
  rm -rf "$sharness_repo"
  git clone https://github.com/ipfs/sharness.git "$sharness_repo" || status=1
fi
if [[ -d "$sharness_repo/.git" ]]; then
  git -C "$sharness_repo" remote set-url origin git@github.com:ipfs/sharness.git
  git -C "$sharness_repo" reset --hard "$sharness_commit" || status=1
  rm -rf test/sharness/lib/sharness
  ln -s "$sharness_repo" test/sharness/lib/sharness
  make test/bin/multihash test/bin/pollEndpoint test/bin/go-timeout test/bin/go-sleep || status=1
else
  status=1
fi
flock -u 9
rm -rf test/sharness/test-results
(
  cd test/sharness
  TEST_JUNIT=1 TEST_NO_COLOR=1 ./t0275-cid-security.sh
) || status=1
if compgen -G "test/sharness/test-results/*.xml.part" >/dev/null; then
  i=0
  for part in test/sharness/test-results/*.xml.part; do
    cp "$part" "/workspace/.ecosyncbench/test-reports/kubo-hidden-sharness-${i}.xml"
    i=$((i + 1))
  done
fi
exit "$status"
SH
  chmod +x "$workspace/.ecosyncbench/run-go-test.sh" "$workspace/.ecosyncbench/run-kubo-hidden.sh"
}

compose_run() {
  local service="$1"
  local image="$2"
  local go_build_cache="$cache_root/${service}-go-build"
  local container_name="ecosync_${profile}_$$_${RANDOM}"
  local -a cache_mounts=(-v "$go_build_cache:/tmp/ecosync-gocache")
  if [[ "$service" == "kubo-hidden" ]]; then
    cache_mounts+=(-v "$cache_root/kubo-sharness:/opt/ecosync/sharness-cache")
  fi
  ensure_base_images
  ensure_runner_scripts
  cd "$task_dir/environment"
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    ECOSYNC_WORKSPACE="$workspace" docker compose build "$service"
  fi
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    return 0
  fi
  ECOSYNC_WORKSPACE="$workspace" docker compose run -T --rm \
    --name "$container_name" \
    -v "$workspace/.ecosyncbench/run-go-test.sh:/opt/ecosync/run-go-test.sh:ro" \
    -v "$workspace/.ecosyncbench/run-kubo-hidden.sh:/opt/ecosync/run-kubo-hidden.sh:ro" \
    "${cache_mounts[@]}" \
    "$service"
}

case "$profile" in
  boxo-hidden)
    compose_run boxo-hidden ecosyncbench/deps/ipfs-boxo-go:511d9bbf5b36
    ;;
  kubo-hidden)
    compose_run kubo-hidden ecosyncbench/deps/ipfs-kubo-go:511d9bbf5b36
    ;;
  *)
    echo "Unknown profile for ipfs_boxo_kubo_511d9bbf5b36: $profile" >&2
    exit 2
    ;;
esac
