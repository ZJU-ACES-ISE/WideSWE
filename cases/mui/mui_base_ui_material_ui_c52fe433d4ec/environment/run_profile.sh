#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
image="ecosyncbench/base/node:22-bookworm"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/mui-base-ui-material-ui-c52fe433d4ec}"

mkdir -p \
  "$workspace/.ecosyncbench/test-reports" \
  "$cache_root/corepack" \
  "$cache_root/npm" \
  "$cache_root/pnpm/home" \
  "$cache_root/pnpm/store" \
  "$cache_root/home"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" \
      -f /opt/ecosyncbench/benchmark/images/base/node-22-bookworm/Dockerfile \
      /opt/ecosyncbench/benchmark/images/base/node-22-bookworm
  fi
}

run_node() {
  local workdir="$1"
  local script="$2"

  ensure_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image" bash -lc 'node --version >/dev/null && corepack --version >/dev/null'
    return 0
  fi

  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-2400}" docker run --rm \
    --init \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/node-cache/home \
    -e CI=1 \
    -e TZ=UTC \
    -e FORCE_COLOR=0 \
    -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    -e COREPACK_HOME=/node-cache/corepack \
    -e npm_config_cache=/node-cache/npm \
    -e npm_config_fetch_retries=5 \
    -e npm_config_fetch_retry_mintimeout=20000 \
    -e npm_config_fetch_retry_maxtimeout=120000 \
    -e PNPM_HOME=/node-cache/pnpm/home \
    -e PNPM_STORE_DIR=/node-cache/pnpm/store \
    -e PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    -e PUPPETEER_SKIP_DOWNLOAD=1 \
    -e CYPRESS_INSTALL_BINARY=0 \
    -e PATH=/node-cache/pnpm/home:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -v "$workspace:/workspace" \
    -v "$cache_root:/node-cache" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -lc "$script"
}

case "$profile" in
  base_ui-hidden)
    run_node "repos/mui/base-ui" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /node-cache/home /node-cache/corepack /node-cache/npm /node-cache/pnpm/home /node-cache/pnpm/store
      corepack prepare pnpm@10.14.0 --activate
      pnpm --filter @base-ui-components/utils... install \
        --store-dir /node-cache/pnpm/store \
        --frozen-lockfile \
        --ignore-scripts \
        --fetch-timeout 600000 \
        --network-concurrency 16 \
        --child-concurrency=2
      pnpm rebuild esbuild
      pnpm --dir packages/utils exec vitest run \
        src/useControlled.test.tsx \
        --environment jsdom \
        --reporter=default --reporter=junit \
        --outputFile=/workspace/.ecosyncbench/test-reports/base-ui-useControlled-hidden.xml
    '
    ;;
  material_ui-hidden)
    run_node "repos/mui/material-ui" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /node-cache/home /node-cache/corepack /node-cache/npm /node-cache/pnpm/home /node-cache/pnpm/store
      corepack prepare pnpm@10.14.0 --activate
      pnpm --filter @mui/utils... install \
        --store-dir /node-cache/pnpm/store \
        --frozen-lockfile \
        --ignore-scripts \
        --fetch-timeout 600000 \
        --network-concurrency 16 \
        --child-concurrency=2
      pnpm rebuild esbuild
      pnpm --filter @mui/internal-test-utils build
      NODE_OPTIONS="--no-experimental-detect-module" NODE_ENV=test pnpm exec mocha \
        --reporter xunit \
        --reporter-option output=/workspace/.ecosyncbench/test-reports/material-ui-useControlled-hidden.xml \
        packages/mui-utils/src/useControlled/useControlled.test.tsx
    '
    ;;
  *)
    echo "Unknown profile for mui_base_ui_material_ui_c52fe433d4ec: $profile" >&2
    exit 2
    ;;
esac
