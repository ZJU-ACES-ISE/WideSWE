#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
image="ecosyncbench/base/node:22-bookworm"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/docker-actions-yarn}"

mkdir -p "$cache_root/corepack"

run_vitest() {
  local workdir="$1"
  local timeout_seconds="$2"
  local report="$3"
  shift 3
  local tests=("$@")

  if ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$task_dir/../../../../../../benchmark/images/base/node-22-bookworm/Dockerfile" "$task_dir/../../../../../../benchmark/images/base/node-22-bookworm"
  fi

  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image" bash -c 'node --version && corepack --version >/dev/null'
    return 0
  fi

  local test_args
  printf -v test_args '%q ' "${tests[@]}"

  timeout "$timeout_seconds" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    --network host \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e COREPACK_HOME=/yarn-cache/corepack \
    -e YARN_ENABLE_GLOBAL_CACHE=true \
    -e YARN_GLOBAL_FOLDER=/yarn-cache/global \
    -e YARN_CACHE_FOLDER=/yarn-cache/cache \
    -v "$workspace:/workspace" \
    -v "$cache_root:/yarn-cache" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -c "
      set -euo pipefail
      mkdir -p /tmp/ecosync-home /workspace/.ecosyncbench/test-reports
      corepack enable
      corepack prepare yarn@4.15.0 --activate
      yarn install --immutable
      yarn vitest run $test_args --reporter=default --reporter=junit --outputFile=/workspace/.ecosyncbench/test-reports/$report
    "
}

case "$profile" in
  actions_toolkit-hidden)
    run_vitest "repos/docker/actions-toolkit" "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-900}" \
      "actions-toolkit-hidden.xml" \
      "__tests__/buildx/build.test.ts" "__tests__/context.test.ts" "__tests__/util.test.ts"
    ;;
  bake_action-hidden)
    run_vitest "repos/docker/bake-action" "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-900}" \
      "bake-action-hidden.xml" \
      "__tests__/context.test.ts"
    ;;
  build_push_action-hidden)
    run_vitest "repos/docker/build-push-action" "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-900}" \
      "build-push-action-hidden.xml" \
      "__tests__/context.test.ts"
    ;;
  *)
    echo "Unknown profile for docker_actions_toolkit_bake_action_build_push_action_b58918c90001: $profile" >&2
    exit 2
    ;;
esac
