#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
image="ecosyncbench/base/node-getsentry:22"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/yarn-cache/getsentry-react-router-4b79b8b29874}"

ensure_image() {
  docker image inspect "$image" >/dev/null
}

run_node() {
  local workdir="$1"
  local report="$2"
  shift 2
  ensure_image
  mkdir -p "$cache_root" "$workspace/.ecosyncbench/test-reports"
  docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e YARN_CACHE_FOLDER=/yarn-cache \
    -e npm_config_audit=false \
    -e npm_config_fund=false \
    -e ECOSYNC_REPORT="$report" \
    -e ECOSYNC_PREBUILD_ONLY="${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" \
    -e ECOSYNC_PRETEST_BUILD="${ECOSYNC_PRETEST_BUILD:-}" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/yarn-cache" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -lc 'set -euo pipefail
      mkdir -p /tmp/ecosync-home /workspace/.ecosyncbench/test-reports
      yarn install --frozen-lockfile --ignore-scripts --prefer-offline --network-timeout 600000
      if [[ "${ECOSYNC_PREBUILD_ONLY:-0}" == "1" ]]; then
        exit 0
      fi
      if [[ "${ECOSYNC_PRETEST_BUILD:-}" == "sentry-javascript-react-router" ]]; then
        yarn build:dev:filter @sentry/core @sentry/browser @sentry/react
      fi
      "$@" --outputFile="/workspace/.ecosyncbench/test-reports/${ECOSYNC_REPORT}.json"' bash "$@"
}

case "$profile" in
  sentry_javascript-hidden)
    export ECOSYNC_PRETEST_BUILD=sentry-javascript-react-router
    run_node "repos/getsentry/sentry-javascript" "sentry_javascript-hidden" \
      yarn vitest run packages/react-router/test/client/sentryOnError.test.ts --coverage=false --reporter=json
    ;;
  sentry_wizard-hidden)
    run_node "repos/getsentry/sentry-wizard" "sentry_wizard-hidden" \
      yarn vitest run \
        test/react-router/codemods/client-entry.test.ts \
        test/react-router/codemods/root.test.ts \
        test/react-router/react-router-wizard-on-error.test.ts \
        test/react-router/sdk-setup.test.ts \
        test/react-router/templates.test.ts \
        --coverage=false \
        --reporter=json
    ;;
  *)
    echo "Unknown profile for getsentry_sentry_javascript_sentry_wizard_4b79b8b29874: $profile" >&2
    exit 2
    ;;
esac
