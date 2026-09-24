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
cache_root="${ECOSYNC_IPFS_BOXO_HELIA_CACHE_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/ipfs-boxo-helia-7da5d4601ae4}}"
node_image="ecosyncbench/base/node:22-bookworm"

export ECOSYNC_REPO_ROOT="$repo_root"
export ECOSYNC_BUILD_CONTEXT="$workspace"
export ECOSYNC_UID="${ECOSYNC_UID:-$(id -u)}"
export ECOSYNC_GID="${ECOSYNC_GID:-$(id -g)}"

mkdir -p "$cache_root/npm" "$cache_root/home" "$cache_root/locks" "$cache_root/go-build" "$workspace/.ecosyncbench/test-reports"
chmod 0777 "$cache_root/go-build" 2>/dev/null || true

ensure_base_images() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect ecosyncbench/base/go:1.26-bookworm >/dev/null 2>&1; then
    docker build -t ecosyncbench/base/go:1.26-bookworm \
      -f "$repo_root/benchmark/images/base/go-1.26-bookworm/Dockerfile" \
      "$repo_root"
  fi
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$node_image" >/dev/null 2>&1; then
    docker build -t "$node_image" \
      -f "$repo_root/benchmark/images/base/node-22-bookworm/Dockerfile" \
      "$repo_root"
  fi
}

ensure_go_runner_script() {
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

run_boxo() {
  ensure_base_images
  ensure_go_runner_script
  cd "$task_dir/environment"
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect ecosyncbench/deps/ipfs-boxo-go:7da5d4601ae4 >/dev/null 2>&1; then
    ECOSYNC_WORKSPACE="$workspace" docker compose build boxo-hidden
  fi
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
    return 0
  fi
  ECOSYNC_WORKSPACE="$workspace" docker compose run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -v "$workspace/.ecosyncbench/run-go-test.sh:/opt/ecosync/run-go-test.sh:ro" \
    -v "$cache_root/go-build:/tmp/ecosync-gocache" \
    boxo-hidden
}

run_helia() {
  ensure_base_images
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-2400}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID}:${ECOSYNC_GID}" \
    -e HOME=/cache/home \
    -e CI=1 \
    -e npm_config_cache=/cache/npm \
    -e npm_config_audit=false \
    -e npm_config_fund=false \
    -e ECOSYNC_DOCKER_PREBUILD_ONLY="${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" \
    -e ECOSYNC_DOCKER_WARMUP_ONLY="${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    -w /workspace/repos/ipfs/helia \
    "$node_image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /cache/home /cache/npm
      rm -f package-lock.json packages/*/package-lock.json
      exec 9>/cache/locks/helia-node-modules.lock
      flock 9
      if [[ ! -f node_modules/.ecosyncbench-deps-ready || ! -x node_modules/.bin/mocha || ! -x node_modules/.bin/tsx ]]; then
        npm install --ignore-scripts --legacy-peer-deps
        npm install --no-save --ignore-scripts --legacy-peer-deps tsx
        touch node_modules/.ecosyncbench-deps-ready
      fi
      flock -u 9
      if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
        node_modules/.bin/mocha --version >/dev/null
        node_modules/.bin/tsx --version >/dev/null
        exit 0
      fi
      set +e
      (
        cd packages/bitswap
        NODE_OPTIONS="--import=/workspace/repos/ipfs/helia/node_modules/tsx/dist/loader.mjs" \
          ../../node_modules/.bin/mocha \
          test/peer-want-list.spec.ts \
          test/want-list.spec.ts \
          --ui bdd \
          --timeout 60000 \
          --reporter xunit \
          --reporter-option output=/workspace/.ecosyncbench/test-reports/helia-hidden.xml
      )
      status=$?
      set -e
      test -s /workspace/.ecosyncbench/test-reports/helia-hidden.xml
      exit "$status"
    '
}

case "$profile" in
  boxo-hidden)
    run_boxo
    ;;
  helia-hidden)
    run_helia
    ;;
  *)
    echo "Unknown profile for ipfs_boxo_helia_7da5d4601ae4: $profile" >&2
    exit 2
    ;;
esac
