#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/node-cache/elastic-elasticsearch-js-kibana-2c333c534b80}"
image_node="ecosyncbench/base/elastic-node:22.22.0"

mkdir -p \
  "$workspace/.ecosyncbench/test-reports" \
  "$cache_root/elasticsearch-js/npm" \
  "$cache_root/elasticsearch-js/home" \
  "$cache_root/kibana/corepack" \
  "$cache_root/kibana/yarn" \
  "$cache_root/kibana/npm" \
  "$cache_root/kibana/kbn-bootstrap" \
  "$cache_root/kibana/home"

ensure_node_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image_node" >/dev/null 2>&1; then
    docker build \
      --build-arg "ECOSYNC_NODE_BASE=ecosyncbench/base/node:22-bookworm" \
      --build-arg "ECOSYNC_NODE_VERSION=22.22.0" \
      -t "$image_node" \
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

docker_node() {
  local workdir="$1"
  local cache_subdir="$2"
  local script="$3"
  local -a mounts=()
  local mount_spec
  ensure_node_image
  while IFS= read -r -d '' mount_spec; do
    mounts+=(-v "$mount_spec")
  done < <(git_mounts)
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-3600}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME="/node-cache/${cache_subdir}/home" \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    -e COREPACK_HOME="/node-cache/${cache_subdir}/corepack" \
    -e YARN_CACHE_FOLDER="/node-cache/${cache_subdir}/yarn" \
    -e YARN_GLOBAL_FOLDER="/node-cache/${cache_subdir}/yarn-berry" \
    -e npm_config_cache="/node-cache/${cache_subdir}/npm" \
    -e KBN_BOOTSTRAP_CACHE_DIR="/node-cache/${cache_subdir}/kbn-bootstrap" \
    -e CYPRESS_INSTALL_BINARY=0 \
    -e PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    -e PUPPETEER_SKIP_DOWNLOAD=1 \
    -v "$workspace:/workspace" \
    -v "$cache_root:/node-cache" \
    "${mounts[@]}" \
    -w "/workspace/${workdir}" \
    "$image_node" \
    bash -lc "set -euo pipefail; mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home; ${script}"
}

if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
  ensure_node_image
  docker run --rm "$image_node" bash -lc 'node --version >/dev/null && npm --version >/dev/null && yarn --version >/dev/null'
  exit 0
fi

case "$profile" in
  elasticsearch_js-hidden)
    docker_node "repos/elastic/elasticsearch-js" "elasticsearch-js" '
      npm install --ignore-scripts
      npm run build
      set +e
      ./node_modules/.bin/tap \
        --disable-coverage \
        --reporter=junit \
        --reporter-file=/workspace/.ecosyncbench/test-reports/elasticsearch-js-hidden.xml \
        test/unit/api.test.ts
      status=$?
      set -e
      test -s /workspace/.ecosyncbench/test-reports/elasticsearch-js-hidden.xml
      perl -0pi -e "s/\\s+(file|line|column)=\\\"[^\\\"]*\\\"//g" /workspace/.ecosyncbench/test-reports/elasticsearch-js-hidden.xml
      exit "$status"
    '
    ;;
  kibana-hidden)
    docker_node "repos/elastic/kibana" "kibana" '
      corepack prepare yarn@1.22.22 --activate
      yarn install --frozen-lockfile --non-interactive
      node - <<'"'"'NODE'"'"'
const { updatePackageMap, getRepoRelsSync } = require("./src/platform/packages/private/kbn-repo-packages");
const repoRoot = process.cwd();
updatePackageMap(repoRoot, Array.from(getRepoRelsSync(repoRoot, ["**/kibana.jsonc"])));
NODE
      set +e
      node scripts/jest.js \
        src/core/packages/elasticsearch/client-server-internal/src/cps_request_handler/cps_request_handler.test.ts \
        --runInBand \
        --forceExit \
        --json \
        --outputFile=/workspace/.ecosyncbench/test-reports/kibana-hidden.json \
        --colors=false
      status=$?
      set -e
      test -s /workspace/.ecosyncbench/test-reports/kibana-hidden.json
      exit "$status"
    '
    ;;
  *)
    echo "Unknown profile for elastic_elasticsearch_js_kibana_2c333c534b80: ${profile}" >&2
    exit 2
    ;;
esac
