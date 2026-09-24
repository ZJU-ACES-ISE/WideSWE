#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="${ECOSYNC_WORKSPACE:-$(pwd)}"
reports="${workspace}/.ecosyncbench/test-reports"
image="ecosyncbench/deps/mui-base-ui-material-ui-node:87e16da977ce-chromium"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/mui-87e16da977ce}"

mkdir -p "${reports}" "${cache_root}/home" "${cache_root}/pnpm-store" "${cache_root}/tmp"

build_image() {
  if docker image inspect "${image}" >/dev/null 2>&1; then
    return 0
  fi
  docker build \
    --build-arg HTTP_PROXY="${HTTP_PROXY:-}" \
    --build-arg HTTPS_PROXY="${HTTPS_PROXY:-}" \
    --build-arg ALL_PROXY="${ALL_PROXY:-}" \
    --build-arg http_proxy="${http_proxy:-}" \
    --build-arg https_proxy="${https_proxy:-}" \
    --build-arg all_proxy="${all_proxy:-}" \
    --build-arg NO_PROXY="${NO_PROXY:-}" \
    --build-arg no_proxy="${no_proxy:-}" \
    -t "${image}" \
    -f "${task_dir}/environment/Dockerfile" \
    "${task_dir}"
}

run_in_image() {
  local workdir="$1"
  local script="$2"
  docker run --rm \
    -u "$(id -u):$(id -g)" \
    -v "${workspace}:/workspace" \
    -v "${cache_root}:/cache" \
    -e HOME=/cache/home \
    -e PNPM_HOME=/cache/home/.local/share/pnpm \
    -e PNPM_STORE_DIR=/cache/pnpm-store \
    -e TMPDIR=/cache/tmp \
    -e PLAYWRIGHT_SKIP_BROWSER_GC=1 \
    -e PATH=/cache/home/.local/share/pnpm:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -w "${workdir}" \
    "${image}" \
    bash -lc "${script}"
}

build_image

if [ -n "${ECOSYNC_DOCKER_PREBUILD_ONLY:-}" ]; then
  exit 0
fi

if [ -n "${ECOSYNC_DOCKER_WARMUP_ONLY:-}" ]; then
  case "${profile}" in
    base_ui-hidden)
      run_in_image /workspace/repos/mui/base-ui 'set -euo pipefail
        mkdir -p /workspace/.ecosyncbench/test-reports /cache/home /cache/pnpm-store /cache/tmp
        corepack prepare pnpm@10.28.1 --activate >/dev/null
        pnpm install --frozen-lockfile --ignore-scripts
        pnpm exec playwright install chromium
      '
      ;;
    material_ui-hidden)
      run_in_image /workspace/repos/mui/material-ui 'set -euo pipefail
        mkdir -p /workspace/.ecosyncbench/test-reports /cache/home /cache/pnpm-store /cache/tmp
        corepack prepare pnpm@10.33.0 --activate >/dev/null
        pnpm install --frozen-lockfile --ignore-scripts
        pnpm exec playwright install chromium
      '
      ;;
    *)
      echo "unknown profile: ${profile}" >&2
      exit 2
      ;;
  esac
  exit 0
fi

case "${profile}" in
  base_ui-hidden)
    run_in_image /workspace/repos/mui/base-ui 'set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /cache/home /cache/pnpm-store /cache/tmp
      corepack prepare pnpm@10.28.1 --activate >/dev/null
      pnpm install --frozen-lockfile --ignore-scripts
      pnpm exec playwright install chromium
      VITEST_ENV=chromium pnpm exec vitest run packages/react/src/field/control/FieldControl.test.tsx \
        --project @base-ui/react \
        --reporter=json \
        --outputFile=/workspace/.ecosyncbench/test-reports/base-ui-hidden.json
    '
    ;;
  material_ui-hidden)
    run_in_image /workspace/repos/mui/material-ui 'set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /cache/home /cache/pnpm-store /cache/tmp
      corepack prepare pnpm@10.33.0 --activate >/dev/null
      pnpm install --frozen-lockfile --ignore-scripts
      pnpm exec playwright install chromium
      TEST_SCOPE=browser VITEST_BROWSERS=chromium pnpm exec vitest run packages/mui-material/src/InputBase/InputBase.test.js \
        --reporter=json \
        --outputFile=/workspace/.ecosyncbench/test-reports/material-ui-hidden.json
    '
    ;;
  *)
    echo "unknown profile: ${profile}" >&2
    exit 2
    ;;
esac
