#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/node-cache/getsentry-701fa84fe330}"
mkdir -p "$cache_root/yarn1" "$cache_root/yarn3" "$cache_root/npm" "$cache_root/corepack" "$workspace/.ecosyncbench/test-reports"

run_in_node() {
  local image="$1"
  local workdir="$2"
  local script="$3"
  docker image inspect "$image" >/dev/null 2>&1 || docker pull "$image"
  docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e YARN_ENABLE_GLOBAL_CACHE=1 \
    -e YARN_GLOBAL_FOLDER=/node-cache/yarn/global \
    -e YARN_CACHE_FOLDER=/node-cache/yarn/cache \
    -e COREPACK_HOME=/node-cache/corepack \
    -e npm_config_cache=/node-cache/npm \
    -e ECOSYNC_DOCKER_PREBUILD_ONLY="${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" \
    -e ECOSYNC_DOCKER_WARMUP_ONLY="${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/node-cache" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -lc "$script"
}

case "$profile" in
  sentry_javascript_bundler_plugins-hidden)
    run_in_node "ecosyncbench/base/node:22-bookworm" "repos/getsentry/sentry-javascript-bundler-plugins" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home /node-cache/yarn/global /node-cache/yarn/cache /node-cache/corepack
      corepack prepare yarn@1.22.22 --activate
      corepack yarn install --frozen-lockfile --ignore-scripts --non-interactive
      if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
        exit 0
      fi
      set +e
      corepack yarn workspace @sentry/babel-plugin-component-annotate vitest run test/sentry-label.test.ts \
        --reporter=default --reporter=junit \
        --outputFile=/workspace/.ecosyncbench/test-reports/sentry-javascript-bundler-plugins-hidden.xml
      code=$?
      set -e
      exit "$code"
    '
    ;;
  sentry_react_native-hidden)
    run_in_node "ecosyncbench/base/node:18-bookworm-slim" "repos/getsentry/sentry-react-native" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home /node-cache/yarn/global /node-cache/yarn/cache /node-cache/corepack
      corepack prepare yarn@3.6.4 --activate
      YARN_ENABLE_SCRIPTS=false corepack yarn install --immutable
      if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
        exit 0
      fi
      set +e
      corepack yarn workspace @sentry/react-native jest --config jest.config.tools.js test/tools/sentryBabelTransformer.test.ts \
        --runInBand --ci --json \
        --outputFile=/workspace/.ecosyncbench/test-reports/sentry-react-native-hidden.json
      code=$?
      set -e
      exit "$code"
    '
    ;;
  *)
    echo "Unknown profile for getsentry_sentry_javascript_bundler_plugins_sentry_react_native_701fa84fe330: $profile" >&2
    exit 2
    ;;
esac
