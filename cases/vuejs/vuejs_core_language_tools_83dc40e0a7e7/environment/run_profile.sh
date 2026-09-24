#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
image="ecosyncbench/base/node-vue:22-pnpm11-all"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/node-cache/vuejs}"
mkdir -p "$cache_root/pnpm" "$cache_root/npm" "$cache_root/corepack" "$cache_root/locks" "$workspace/.ecosyncbench/test-reports"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$task_dir/environment/node-vue.Dockerfile" "$task_dir/environment"
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
  timeout 900 docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    -e COREPACK_HOME=/node-cache/corepack \
    -e PUPPETEER_SKIP_DOWNLOAD=1 \
    -e PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    -e PNPM_HOME=/node-cache/pnpm/home \
    -e PNPM_STORE_DIR=/node-cache/pnpm/store \
    -e ECOSYNC_PNPM_INSTALL_MODE="${ECOSYNC_PNPM_INSTALL_MODE:---offline}" \
    -e npm_config_cache=/node-cache/npm \
    -e PATH=/node-cache/pnpm/home:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -v "$workspace:/workspace" \
    -v "$cache_root:/node-cache" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -lc "$script"
}

case "$profile" in
  core-hidden)
    run_in_node "repos/vuejs/core" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home /node-cache/pnpm/home /node-cache/pnpm/store /node-cache/locks
      install_args=(--store-dir /node-cache/pnpm/store --frozen-lockfile)
      if [[ -n "${ECOSYNC_PNPM_INSTALL_MODE:-}" ]]; then
        install_args+=("${ECOSYNC_PNPM_INSTALL_MODE}")
      fi
      flock /node-cache/locks/core-install.lock corepack pnpm install "${install_args[@]}"
      corepack pnpm vitest run --project=unit \
        packages/compiler-sfc/__tests__/compileScript/definePropsDestructure.spec.ts \
        --reporter=default --reporter=junit \
        --outputFile=/workspace/.ecosyncbench/test-reports/core-definePropsDestructure.junit.xml
    '
    ;;
  language_tools-hidden)
    run_in_node "repos/vuejs/language-tools" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home /node-cache/pnpm/home /node-cache/pnpm/store /node-cache/locks
      install_args=(--store-dir /node-cache/pnpm/store --frozen-lockfile)
      if [[ -n "${ECOSYNC_PNPM_INSTALL_MODE:-}" ]]; then
        install_args+=("${ECOSYNC_PNPM_INSTALL_MODE}")
      fi
      flock /node-cache/locks/language-tools-install.lock corepack pnpm install "${install_args[@]}"
      corepack pnpm run build
      corepack pnpm vitest run \
        packages/language-server/tests/inlayHints.spec.ts \
        --reporter=default --reporter=junit \
        --outputFile=/workspace/.ecosyncbench/test-reports/language-tools-inlayHints.junit.xml
    '
    ;;
  *)
    echo "Unknown profile for vuejs_core_language_tools_83dc40e0a7e7: $profile" >&2
    exit 2
    ;;
esac
