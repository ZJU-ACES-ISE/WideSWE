#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
node_image="ecosyncbench/base/node:22-bookworm"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy}/node-cache/elastic-js-dual-modules-b5a8200a9250"

mkdir -p "$workspace/.ecosyncbench/test-reports" "$cache_root/npm" "$cache_root/xdg"

ensure_node_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$node_image" >/dev/null 2>&1; then
    docker pull node:22-bookworm
    docker tag node:22-bookworm "$node_image"
  fi
}

run_node() {
  local repo_path="$1"
  local body="$2"

  ensure_node_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$node_image" bash -lc 'node --version >/dev/null && npm --version >/dev/null'
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
    -v "$task_dir/environment:/ecosync-env:ro" \
    -v "$cache_root:/node-cache" \
    -w "/workspace/${repo_path}" \
    "$node_image" \
    bash -lc "
      set -euo pipefail
      mkdir -p /tmp/ecosync-home /workspace/.ecosyncbench/test-reports /node-cache/npm /node-cache/xdg
      ${body}
    "
}

case "$profile" in
  elastic_transport_js-hidden)
    run_node "repos/elastic/elastic-transport-js" '
      npm install --prefer-offline --no-audit --no-fund
      npm run build
      /ecosync-env/run_tap_junit.sh /workspace/.ecosyncbench/test-reports/elastic-transport-js-hidden.xml \
        test/unit/commonjs-import.test.ts test/unit/esm-import.test.mjs
    '
    ;;
  elasticsearch_js-hidden)
    run_node "repos/elastic/elasticsearch-js" '
      npm install --prefer-offline --no-audit --no-fund
      npm run build
      /ecosync-env/run_tap_junit.sh /workspace/.ecosyncbench/test-reports/elasticsearch-js-cjs-hidden.xml \
        test/unit/cjs-import.test.ts
    '
    ;;
  elasticsearch_js-esm-hidden)
    run_node "repos/elastic/elasticsearch-js" '
      npm install --prefer-offline --no-audit --no-fund
      npm run build
      node node_modules/typescript/lib/tsc.js \
        --noEmit --strict --skipLibCheck \
        --target ES2022 --module NodeNext --moduleResolution NodeNext \
        test/types/dual-package-imports.mts
      (
        cd test/esm
        npm install --prefer-offline --no-audit --no-fund
        node test-import.mjs
      )
    '
    ;;
  *)
    echo "Unknown profile for elastic_elastic_transport_js_elasticsearch_js_b5a8200a9250: ${profile}" >&2
    exit 2
    ;;
esac
