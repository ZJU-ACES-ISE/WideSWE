#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
image="ecosyncbench/base/node:22-bookworm"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy}/node-cache/mapbox-mcp-ca69b6fe7525"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker pull node:22-bookworm
    docker tag node:22-bookworm "$image"
  fi
}

run_npm_vitest() {
  local repo_path="$1"
  local report_name="$2"
  shift 2
  local test_files=("$@")

  ensure_image
  mkdir -p "$cache_root" "$workspace/.ecosyncbench/test-reports"

  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-1800}" docker run --rm \
      --network host \
      -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
      -e HOME=/tmp/ecosync-home \
      -e CI=1 \
      -e npm_config_cache=/node-cache/npm \
      -v "$workspace:/workspace" \
      -v "$cache_root:/node-cache" \
      -w "/workspace/${repo_path}" \
      "$image" \
      bash -lc 'set -euo pipefail; mkdir -p /tmp/ecosync-home /node-cache/npm; npm ci --ignore-scripts --prefer-offline --no-audit --no-fund; npx patch-package || true'
    return 0
  fi

  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-1800}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    --network host \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e npm_config_cache=/node-cache/npm \
    -e XDG_CACHE_HOME=/node-cache/xdg \
    -v "$workspace:/workspace" \
    -v "$cache_root:/node-cache" \
    -w "/workspace/${repo_path}" \
    "$image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /tmp/ecosync-home /workspace/.ecosyncbench/test-reports /node-cache/npm /node-cache/xdg /node-cache/locks
      exec 9>"/node-cache/locks/'"$profile"'.node-modules.lock"
      flock 9
      if [[ ! -x node_modules/.bin/vitest || ! -f node_modules/.ecosync-ready ]]; then
        npm ci --ignore-scripts --prefer-offline --no-audit --no-fund
        touch node_modules/.ecosync-ready
      fi
      npx patch-package || true
      npx vitest run "$@" \
        --maxWorkers=2 \
        --minWorkers=1 \
        --reporter=default \
        --reporter=junit \
        --outputFile="/workspace/.ecosyncbench/test-reports/'"$report_name"'.xml"
    ' bash "${test_files[@]}"
}

case "$profile" in
  mcp_devkit_server-hidden)
    run_npm_vitest \
      "repos/mapbox/mcp-devkit-server" \
      "mcp-devkit-server-hidden" \
      "test/tools/tool-naming-convention.test.ts" \
      "test/tools/geojson-preview-tool/GeojsonPreviewTool.test.ts" \
      "test/tools/preview-style-tool/PreviewStyleTool.test.ts" \
      "test/tools/style-comparison-tool/StyleComparisonTool.test.ts"
    ;;
  mcp_server-hidden)
    run_npm_vitest \
      "repos/mapbox/mcp-server" \
      "mcp-server-hidden" \
      "test/tools/static-map-image-tool/StaticMapImageTool.test.ts"
    ;;
  *)
    echo "Unknown profile for mapbox_mcp_devkit_server_mcp_server_ca69b6fe7525: ${profile}" >&2
    exit 2
    ;;
esac
