#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
image_eui="ecosyncbench/base/elastic-node:22.22.0"
image_kibana="ecosyncbench/base/elastic-node:22.22.0"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/node-cache/elastic-eui-kibana}"
mkdir -p "$cache_root/yarn" "$cache_root/corepack" "$cache_root/npm" "$workspace/.ecosyncbench/test-reports"

ensure_image() {
  local image="$1"
  local base="$2"
  local node_version="$3"
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build \
      --build-arg "ECOSYNC_NODE_BASE=$base" \
      --build-arg "ECOSYNC_NODE_VERSION=$node_version" \
      -t "$image" \
      -f "$task_dir/environment/elastic-node.Dockerfile" \
      "$task_dir/environment"
  fi
}

git_mounts() {
  local git_file git_dir common_dir common_abs
  [[ -d "$workspace/repos" ]] || return 0
  while IFS= read -r git_file; do
    if [[ -d "$git_file" ]]; then
      printf '%s\0' "$git_file:$git_file:ro"
      continue
    fi
    git_dir="$(sed -n 's/^gitdir: //p' "$git_file" | head -1)"
    [[ "$git_dir" == /* && -d "$git_dir" ]] || continue
    printf '%s\0' "$git_dir:$git_dir:ro"
    if [[ -f "$git_dir/commondir" ]]; then
      common_dir="$(sed -n '1p' "$git_dir/commondir")"
      if [[ "$common_dir" == /* ]]; then
        common_abs="$common_dir"
      else
        common_abs="$(cd "$git_dir/$common_dir" && pwd)"
      fi
      [[ -d "$common_abs" ]] && printf '%s\0' "$common_abs:$common_abs:ro"
    fi
  done < <(find "$workspace/repos" -maxdepth 4 \( -type f -o -type d \) -name .git 2>/dev/null)
}

run_in_node() {
  local image="$1"
  local workdir="$2"
  local script="$3"
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image" bash -lc 'node --version >/dev/null && corepack --version >/dev/null'
    return 0
  fi
  local -a mounts=()
  local mount_spec
  while IFS= read -r -d '' mount_spec; do
    mounts+=(-v "$mount_spec")
  done < <(git_mounts)
  timeout "${ECOSYNC_PROFILE_TIMEOUT:-2400}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    -e COREPACK_HOME=/node-cache/corepack \
    -e CYPRESS_CACHE_FOLDER=/node-cache/cypress \
    -e CYPRESS_INSTALL_BINARY=0 \
    -e PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    -e PUPPETEER_SKIP_DOWNLOAD=1 \
    -e YARN_ENABLE_GLOBAL_CACHE=1 \
    -e YARN_CACHE_FOLDER=/node-cache/yarn \
    -e YARN_GLOBAL_FOLDER=/node-cache/yarn-berry \
    -e npm_config_cache=/node-cache/npm \
    -v "$workspace:/workspace" \
    -v "$cache_root:/node-cache" \
    "${mounts[@]}" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -lc "$script"
}

case "$profile" in
  eui-hidden)
    ensure_image "$image_eui" "ecosyncbench/base/node:22-bookworm" "22.22.0"
    run_in_node "$image_eui" "repos/elastic/eui" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      corepack yarn install --immutable
      corepack yarn --cwd packages/eui build:workspaces
      report=/workspace/.ecosyncbench/test-reports/eui-flyout-history-jest.json
      set +e
      corepack yarn --cwd packages/eui test-unit \
        src/components/flyout/manager/flyout_managed.test.tsx \
        src/components/flyout/manager/reducer.test.ts \
        src/components/flyout/manager/selectors.test.tsx \
        src/components/flyout/manager/store.test.ts \
        --runInBand --json --outputFile="$report"
      status=$?
      test -s "$report"
      exit "$status"
    '
    ;;
  kibana-hidden)
    ensure_image "$image_kibana" "ecosyncbench/base/node:22-bookworm" "22.22.0"
    run_in_node "$image_kibana" "repos/elastic/kibana" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      corepack prepare yarn@1.22.22 --activate
      yarn install --frozen-lockfile --non-interactive
      node - <<'"'"'NODE'"'"'
const { updatePackageMap, getRepoRelsSync } = require("./src/platform/packages/private/kbn-repo-packages");
const repoRoot = process.cwd();
updatePackageMap(repoRoot, Array.from(getRepoRelsSync(repoRoot, ["**/kibana.jsonc"])));
NODE
      report=/workspace/.ecosyncbench/test-reports/kibana-flyout-history-jest.json
      set +e
      node scripts/jest.js \
        src/core/packages/overlays/browser-internal/src/flyout/system_flyout_service.test.tsx \
        --runInBand --json --outputFile="$report"
      status=$?
      test -s "$report"
      rm -rf target/junit
      exit "$status"
    '
    ;;
  *)
    echo "Unknown profile for elastic EUI/Kibana flyout history case: $profile" >&2
    exit 2
    ;;
esac
