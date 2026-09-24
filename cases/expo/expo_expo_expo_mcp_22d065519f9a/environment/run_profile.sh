#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
image="ecosyncbench/deps/expo-node-bun:22-bun1.3"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/node-cache/expo-expo-mcp-22d065519f9a}"
mkdir -p "$cache_root/corepack" "$cache_root/yarn" "$cache_root/pnpm" "$cache_root/npm" "$cache_root/bun" "$cache_root/xdg-cache" "$cache_root/node-gyp" "$workspace/.ecosyncbench/test-reports"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$task_dir/environment/node-bun-expo.Dockerfile" "$task_dir/environment"
  fi
}

git_mounts() {
  local git_file git_dir common_dir common_abs
  [[ -d "$workspace/repos" ]] || return 0
  while IFS= read -r git_file; do
    if [[ -d "$git_file" ]]; then
      printf '%s\0' "$git_file:$git_file:ro"
      continue
    fi
    git_dir="$(sed -n 's/^gitdir: //p' "$git_file" | head -1)"
    [[ "$git_dir" == /* && -d "$git_dir" ]] || continue
    printf '%s\0' "$git_dir:$git_dir:ro"
    if [[ -f "$git_dir/commondir" ]]; then
      common_dir="$(sed -n '1p' "$git_dir/commondir")"
      if [[ "$common_dir" == /* ]]; then common_abs="$common_dir"; else common_abs="$(cd "$git_dir/$common_dir" && pwd)"; fi
      [[ -d "$common_abs" ]] && printf '%s\0' "$common_abs:$common_abs:ro"
    fi
  done < <(find "$workspace/repos" -maxdepth 4 \( -type f -o -type d \) -name .git 2>/dev/null)
}

run_in_node() {
  local workdir="$1"
  local script="$2"
  ensure_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image" bash -lc 'node --version >/dev/null && corepack --version >/dev/null && bun --version >/dev/null'
    return 0
  fi
  local -a mounts=()
  local mount_spec
  while IFS= read -r -d '' mount_spec; do mounts+=(-v "$mount_spec"); done < <(git_mounts)
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-2400}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e COREPACK_HOME=/node-cache/corepack \
    -e XDG_CACHE_HOME=/node-cache/xdg-cache \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e YARN_ENABLE_GLOBAL_CACHE=1 \
    -e YARN_GLOBAL_FOLDER=/node-cache/yarn/global \
    -e YARN_CACHE_FOLDER=/node-cache/yarn/cache \
    -e PNPM_HOME=/node-cache/pnpm/home \
    -e PNPM_STORE_DIR=/node-cache/pnpm/store \
    -e BUN_INSTALL_CACHE_DIR=/node-cache/bun/install-cache \
    -e npm_config_cache=/node-cache/npm \
    -e npm_config_devdir=/node-cache/node-gyp \
    -e PATH=/node-cache/pnpm/home:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -v "$workspace:/workspace" \
    -v "$cache_root:/node-cache" \
    "${mounts[@]}" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -lc "$script"
}

case "$profile" in
  expo-hidden)
    run_in_node "repos/expo/expo" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home /node-cache/yarn/global /node-cache/yarn/cache
      if [[ ! -x node_modules/.bin/jest ]]; then
        corepack yarn@1.22.22 install --offline --frozen-lockfile || \
          corepack yarn@1.22.22 install --frozen-lockfile
      fi
      corepack yarn@1.22.22 --cwd packages/@expo/cli taskr
      corepack yarn@1.22.22 --cwd packages/@expo/cli jest \
        src/start/server/__tests__/MCP-test.ts \
        src/start/server/__tests__/MCP-lifecycle-test.ts \
        --runInBand --ci --json --outputFile=/workspace/.ecosyncbench/test-reports/expo-cli-mcp.json
    '
    ;;
  expo_mcp-hidden)
    run_in_node "repos/expo/expo-mcp" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home /node-cache/bun/install-cache
      bun install --offline --frozen-lockfile --ignore-scripts || \
        bun install --frozen-lockfile --ignore-scripts
      bun test packages/mcp-tunnel/src/__tests__/ReverseTunnelClientTransport.test.ts \
        --reporter=junit --reporter-outfile=/workspace/.ecosyncbench/test-reports/expo-mcp-tunnel.xml
    '
    ;;
  *)
    echo "Unknown profile for expo_expo_expo_mcp_22d065519f9a: $profile" >&2
    exit 2
    ;;
esac
