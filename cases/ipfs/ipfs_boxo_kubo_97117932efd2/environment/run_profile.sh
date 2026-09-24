#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"

image_go="ecosyncbench/base/go:1.25-bookworm"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/go-cache/ipfs-boxo-kubo-provider-clear}"
mkdir -p "$cache_root/gomod" "$cache_root/gopath" "$cache_root/gocache" "$workspace/.ecosyncbench/test-reports"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image_go" >/dev/null 2>&1; then
    docker pull golang:1.25-bookworm
    docker tag golang:1.25-bookworm "$image_go"
  fi
}

run_go() {
  local workdir="$1"
  local script="$2"
  ensure_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image_go" bash -lc 'export PATH="/usr/local/go/bin:$PATH"; go version >/dev/null'
    return 0
  fi
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-3600}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e GOMODCACHE=/go-cache/gomod \
    -e GOPATH=/go-cache/gopath \
    -e GOCACHE=/go-cache/gocache \
    -e GOFLAGS=-mod=mod \
    -e PATH=/usr/local/go/bin:/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -v "$workspace:/workspace" \
    -v "$cache_root:/go-cache" \
    -w "/workspace/$workdir" \
    "$image_go" \
    bash -lc "$script"
}

case "$profile" in
  boxo-hidden)
    run_go "repos/ipfs/boxo" '
      set -euo pipefail
      export PATH="/usr/local/go/bin:$PATH"
      export GOWORK=off
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      report=/workspace/.ecosyncbench/test-reports/boxo-hidden.json
      go test -mod=mod -buildvcs=false -count=1 -json ./provider/internal/queue | tee "$report"
    '
    ;;
  kubo-hidden)
    run_go "repos/ipfs/kubo" '
      set -euo pipefail
      export PATH="/usr/local/go/bin:$PATH"
      export GOWORK=off
      go mod edit -replace github.com/ipfs/boxo=/workspace/repos/ipfs/boxo
      go mod edit -exclude google.golang.org/genproto@v0.0.0-20230410155749-daa745c078e1
      if [[ -f test/dependencies/go.mod ]]; then
        (cd test/dependencies && go mod edit -replace github.com/ipfs/boxo=/workspace/repos/ipfs/boxo)
      fi
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      go build -mod=mod -buildvcs=false -o ./cmd/ipfs/ipfs ./cmd/ipfs
      status=0
      run_profile_test() {
        local report="$1"
        shift
        set +e
        go test -mod=mod -buildvcs=false -count=1 -json "$@" | tee "$report"
        local rc=${PIPESTATUS[0]}
        set -e
        test -s "$report" || rc=1
        if [[ "$rc" -ne 0 ]]; then
          status=1
        fi
      }
      run_profile_test /workspace/.ecosyncbench/test-reports/kubo-commands-hidden.json ./core/commands -run "^TestCommands$"
      run_profile_test /workspace/.ecosyncbench/test-reports/kubo-provider-hidden.json ./test/cli -run "^TestProvider$"
      exit "$status"
    '
    ;;
  *)
    echo "Unknown profile for ipfs_boxo_kubo_97117932efd2: $profile" >&2
    exit 2
    ;;
esac
