#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
image="ecosyncbench/base/sveltejs-node:24"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/node-cache/sveltejs-kit-hydratable}"

mkdir -p \
  "$cache_root/pnpm/home" \
  "$cache_root/pnpm/store" \
  "$cache_root/npm" \
  "$cache_root/corepack" \
  "$cache_root/ms-playwright" \
  "$cache_root/locks" \
  "$workspace/.ecosyncbench/test-reports"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$task_dir/environment/sveltejs-node.Dockerfile" "$task_dir/environment"
  fi
}

run_in_node() {
  local workdir="$1"
  local script="$2"

  ensure_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image" bash -lc 'node --version >/dev/null && corepack --version >/dev/null'
    return 0
  fi

  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-3000}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    -e COREPACK_HOME=/node-cache/corepack \
    -e PNPM_HOME=/node-cache/pnpm/home \
    -e PNPM_STORE_DIR=/node-cache/pnpm/store \
    -e npm_config_cache=/node-cache/npm \
    -e npm_config_registry=https://registry.npmmirror.com \
    -e npm_config_fetch_retries=5 \
    -e npm_config_fetch_retry_mintimeout=20000 \
    -e npm_config_fetch_retry_maxtimeout=120000 \
    -e PLAYWRIGHT_BROWSERS_PATH=/node-cache/ms-playwright \
    -e PATH=/node-cache/pnpm/home:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -v "$workspace:/workspace" \
    -v "$cache_root:/node-cache" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -lc "$script"
}

case "$profile" in
  kit-hidden)
    run_in_node "repos/sveltejs/kit" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      flock /node-cache/locks/sveltejs-kit-svelte-91ed3af473de-kit-install.lock \
        corepack pnpm install --store-dir /node-cache/pnpm/store --frozen-lockfile --offline
      corepack pnpm exec playwright install chromium

      cd packages/kit/test/apps/async
      PLAYWRIGHT_JUNIT_OUTPUT_NAME=/workspace/.ecosyncbench/test-reports/kit-async.xml \
        corepack pnpm exec playwright test test/test.js --reporter=junit
    '
    ;;
  svelte-hidden)
    run_in_node "repos/sveltejs/svelte" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      flock /node-cache/locks/sveltejs-kit-svelte-91ed3af473de-svelte-install.lock \
        corepack pnpm install --store-dir /node-cache/pnpm/store --frozen-lockfile --offline
      corepack pnpm --filter svelte build
      corepack pnpm vitest run packages/svelte/tests/runtime-runes/test.ts \
        --reporter=default --reporter=junit \
        --outputFile=/workspace/.ecosyncbench/test-reports/svelte-runtime-runes.xml
    '
    ;;
  *)
    echo "unknown profile: ${profile}" >&2
    exit 2
    ;;
esac
