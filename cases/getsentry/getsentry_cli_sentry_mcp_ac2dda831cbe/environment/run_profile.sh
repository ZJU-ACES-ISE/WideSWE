#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
host_repo_root="$(pwd -P)"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/node-cache/getsentry-cli-sentry-mcp-ac2dda831cbe}"
mkdir -p "$workspace/.ecosyncbench/test-reports" "$cache_root/cli" "$cache_root/sentry-mcp"

cli_image="ecosyncbench/deps/getsentry-cli-pnpm:e7dd817-ac2dda831cbe"
mcp_image="ecosyncbench/deps/getsentry-sentry-mcp-pnpm:87ca551-ac2dda831cbe"

build_pnpm_image() {
  local image="$1"
  local repo="$2"
  local repo_path="$3"
  local base_commit="$4"
  local fingerprint="$5"
  local pnpm_version="$6"
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build \
      --build-arg "ECOSYNC_TASK_ID=getsentry_cli_sentry_mcp_ac2dda831cbe" \
      --build-arg "ECOSYNC_REPO=${repo}" \
      --build-arg "ECOSYNC_REPO_PATH=${repo_path}" \
      --build-arg "ECOSYNC_BASE_COMMIT=${base_commit}" \
      --build-arg "ECOSYNC_DEPENDENCY_FINGERPRINT=${fingerprint}" \
      --build-arg "ECOSYNC_PNPM_VERSION=${pnpm_version}" \
      --build-arg "HTTP_PROXY=${ECOSYNC_DOCKER_PROXY:-}" \
      --build-arg "HTTPS_PROXY=${ECOSYNC_DOCKER_PROXY:-}" \
      --build-arg "ALL_PROXY=${ECOSYNC_DOCKER_PROXY:-}" \
      --build-arg "http_proxy=${ECOSYNC_DOCKER_PROXY:-}" \
      --build-arg "https_proxy=${ECOSYNC_DOCKER_PROXY:-}" \
      --build-arg "all_proxy=${ECOSYNC_DOCKER_PROXY:-}" \
      --build-arg "NO_PROXY=${NO_PROXY:-localhost,127.0.0.1}" \
      --build-arg "no_proxy=${no_proxy:-localhost,127.0.0.1}" \
      -t "$image" \
      -f "$task_dir/environment/pnpm-fetch.Dockerfile" \
      "$workspace"
  fi
}

run_cli() {
  build_pnpm_image \
    "$cli_image" \
    "getsentry/cli" \
    "repos/getsentry/cli" \
    "e7dd8177a04ed572ab2407e9140f9310820ba88b" \
    "e7dd817-pnpm10.11.0-ac2dda831cbe" \
    "10.11.0"
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$cli_image" bash -lc 'pnpm --version >/dev/null'
    return 0
  fi
  timeout "${ECOSYNC_PROFILE_TIMEOUT:-1200}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "0:0" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e PNPM_HOME=/opt/ecosync/pnpm \
    -e PNPM_STORE_DIR=/opt/ecosync/pnpm-store \
    -e PATH=/opt/ecosync/pnpm:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -v "$workspace:/workspace" \
    -v "$cache_root/cli:/tmp/ecosync-home" \
    -w "/workspace/repos/getsentry/cli" \
    "$cli_image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports
      pnpm config set store-dir "${PNPM_STORE_DIR}"
      if [[ ! -f node_modules/.modules.yaml || ! -x node_modules/.bin/vitest ]]; then
        pnpm install --offline --frozen-lockfile --ignore-scripts
      fi
      pnpm exec vitest run \
        test/commands/local/behavior.test.ts \
        test/commands/local/run.test.ts \
        test/commands/local/server.test.ts \
        test/lib/formatters/local.test.ts \
        test/lib/formatters/semantic-behavior.test.ts \
        --reporter=default --reporter=junit \
        --outputFile=/workspace/.ecosyncbench/test-reports/cli-vitest.xml
    '
}

run_mcp() {
  build_pnpm_image \
    "$mcp_image" \
    "getsentry/sentry-mcp" \
    "repos/getsentry/sentry-mcp" \
    "87ca551ca1ada5f2761947fd2dee04dd62c7b850" \
    "87ca551-pnpm10.15.1-ac2dda831cbe" \
    "10.15.1"
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$mcp_image" bash -lc 'pnpm --version >/dev/null'
    return 0
  fi
  timeout "${ECOSYNC_PROFILE_TIMEOUT:-1200}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "0:0" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e PNPM_HOME=/opt/ecosync/pnpm \
    -e PNPM_STORE_DIR=/opt/ecosync/pnpm-store \
    -e PATH=/opt/ecosync/pnpm:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -v "$workspace:/workspace" \
    -v "$cache_root/sentry-mcp:/tmp/ecosync-home" \
    -w "/workspace/repos/getsentry/sentry-mcp" \
    "$mcp_image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports
      pnpm config set store-dir "${PNPM_STORE_DIR}"
      pnpm install --offline --frozen-lockfile --ignore-scripts
      pnpm --filter @sentry/mcp-server-mocks run build
      pnpm --filter ./packages/mcp-core run generate-definitions
      cd packages/mcp-core
      pnpm exec vitest run \
        --config vitest.config.ts \
        src/tools/get-trace-details.test.ts \
        --reporter=default --reporter=junit \
        --outputFile=/workspace/.ecosyncbench/test-reports/sentry-mcp-vitest.xml
    '
}

case "$profile" in
  cli-hidden)
    run_cli
    ;;
  sentry_mcp-hidden)
    run_mcp
    ;;
  *)
    echo "Unknown profile for getsentry cli/sentry-mcp case: $profile" >&2
    exit 2
    ;;
esac
