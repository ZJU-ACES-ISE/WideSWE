#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
node_image="ecosyncbench/base/node:22-bookworm"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy}/node-cache/docker-actions-toolkit-metadata-action-8c65d8d37a2e"

mkdir -p "$workspace/.ecosyncbench/test-reports" "$cache_root"

ensure_node_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$node_image" >/dev/null 2>&1; then
    docker build -t "$node_image" -f "$task_dir/../../../../../../benchmark/images/base/node-22-bookworm/Dockerfile" "$task_dir/../../../../../../benchmark/images/base/node-22-bookworm"
  fi
}

run_node_profile() {
  local repo_path="$1"
  local test_file="$2"
  local report_name="$3"

  ensure_node_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$node_image" bash -lc 'node --version >/dev/null && corepack --version >/dev/null'
    return 0
  fi

  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-1200}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    --network host \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e YARN_ENABLE_GLOBAL_CACHE=true \
    -e YARN_GLOBAL_FOLDER=/yarn-cache/global \
    -e YARN_CACHE_FOLDER=/yarn-cache/cache \
    -v "$workspace:/workspace" \
    -v "$cache_root:/yarn-cache" \
    -w "/workspace/${repo_path}" \
    "$node_image" \
    bash -lc "
      set -euo pipefail
      mkdir -p /tmp/ecosync-home /workspace/.ecosyncbench/test-reports
      corepack enable
      corepack prepare yarn@4.9.2 --activate
      yarn install --immutable
      yarn vitest run ${test_file} \
        --reporter=default \
        --reporter=junit \
        --outputFile=/workspace/.ecosyncbench/test-reports/${report_name}
    "
}

case "$profile" in
  actions_toolkit-hidden)
    run_node_profile "repos/docker/actions-toolkit" "__tests__/util.test.ts" "actions-toolkit-util.xml"
    ;;
  metadata_action-hidden)
    run_node_profile "repos/docker/metadata-action" "__tests__/context.test.ts" "metadata-action-context.xml"
    ;;
  *)
    echo "Unknown profile for docker_actions_toolkit_metadata_action_8c65d8d37a2e: ${profile}" >&2
    exit 2
    ;;
esac
