#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
host_repo_root="$(pwd -P)"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy}"
case_cache="elastic-var-groups-264fe9f9020d"
go_image="ecosyncbench/base/go:1.26-bookworm"
kibana_image="ecosyncbench/base/elastic-node:22.22.0"

mkdir -p "$workspace/.ecosyncbench/test-reports" \
  "$cache_root/go-cache/$case_cache/go-build" \
  "$cache_root/go-cache/$case_cache/go-mod" \
  "$cache_root/node-cache/$case_cache/kibana/corepack" \
  "$cache_root/node-cache/$case_cache/kibana/yarn" \
  "$cache_root/node-cache/$case_cache/kibana/npm" \
  "$cache_root/node-cache/$case_cache/kibana/kbn-bootstrap"

docker_common_args=(
  --rm
  -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}"
  -e HOME=/tmp/ecosync-home
  -e CI=1
  -e FORCE_COLOR=0
  -v "$workspace:/workspace"
)

ensure_go_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$go_image" >/dev/null 2>&1; then
    docker build -t "$go_image" -f "$host_repo_root/benchmark/images/base/go-1.26-bookworm/Dockerfile" "$host_repo_root/benchmark/images/base/go-1.26-bookworm"
  fi
}

ensure_kibana_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$kibana_image" >/dev/null 2>&1; then
    docker build \
      --build-arg "ECOSYNC_NODE_BASE=ecosyncbench/base/node:22-bookworm" \
      --build-arg "ECOSYNC_NODE_VERSION=22.22.0" \
      -t "$kibana_image" \
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

run_package_spec() {
  ensure_go_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$go_image" bash -c 'go version >/dev/null'
    return 0
  fi
  local -a mounts=()
  local mount_spec
  while IFS= read -r -d '' mount_spec; do
    mounts+=(-v "$mount_spec")
  done < <(git_mounts)
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-3600}" docker run \
    "${docker_common_args[@]}" \
    -e PATH=/go/bin:/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -e GOCACHE=/go-build-cache \
    -e GOMODCACHE=/go-mod-cache \
    -v "$cache_root/go-cache/$case_cache/go-build:/go-build-cache" \
    -v "$cache_root/go-cache/$case_cache/go-mod:/go-mod-cache" \
    "${mounts[@]}" \
    -w /workspace/repos/elastic/package-spec \
    "$go_image" \
    bash -c 'set -euo pipefail; mkdir -p /tmp/ecosync-home /workspace/.ecosyncbench/test-reports; set +e; go test -json ./code/go/pkg/validator > /workspace/.ecosyncbench/test-reports/package-spec-hidden.json; status=$?; set -e; test -s /workspace/.ecosyncbench/test-reports/package-spec-hidden.json; exit "$status"'
}

run_kibana() {
  ensure_kibana_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$kibana_image" bash -lc 'node --version >/dev/null && yarn --version >/dev/null'
    return 0
  fi
  local -a mounts=()
  local mount_spec
  while IFS= read -r -d '' mount_spec; do
    mounts+=(-v "$mount_spec")
  done < <(git_mounts)
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-5400}" docker run \
    "${docker_common_args[@]}" \
    -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    -e COREPACK_HOME=/node-cache/corepack \
    -e YARN_CACHE_FOLDER=/node-cache/yarn \
    -e YARN_GLOBAL_FOLDER=/node-cache/yarn-berry \
    -e npm_config_cache=/node-cache/npm \
    -e KBN_BOOTSTRAP_CACHE_DIR=/node-cache/kbn-bootstrap \
    -e CYPRESS_INSTALL_BINARY=0 \
    -e PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    -e PUPPETEER_SKIP_DOWNLOAD=1 \
    -v "$cache_root/node-cache/$case_cache/kibana:/node-cache" \
    "${mounts[@]}" \
    -w /workspace/repos/elastic/kibana \
    "$kibana_image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /tmp/ecosync-home /node-cache/corepack /node-cache/yarn /node-cache/npm /node-cache/kbn-bootstrap /workspace/.ecosyncbench/test-reports
      corepack prepare yarn@1.22.22 --activate
      yarn install --frozen-lockfile --non-interactive
      find target/junit -maxdepth 1 -type f -name "TEST-Jest Tests*.xml" -delete 2>/dev/null || true
      node - <<'"'"'NODE'"'"'
const { updatePackageMap, getRepoRelsSync } = require("./src/platform/packages/private/kbn-repo-packages");
const repoRoot = process.cwd();
updatePackageMap(repoRoot, Array.from(getRepoRelsSync(repoRoot, ["**/kibana.jsonc"])));
NODE
      status=0
      node scripts/jest.js \
        --config x-pack/platform/plugins/shared/fleet/common/jest.config.js \
        x-pack/platform/plugins/shared/fleet/common/services/simplified_package_policy_helper.test.ts \
        x-pack/platform/plugins/shared/fleet/common/services/validate_package_policy.test.ts \
        --runInBand \
        --json \
        --outputFile=/workspace/.ecosyncbench/test-reports/kibana-common-hidden.json || status=$?
      node scripts/jest.js \
        --config x-pack/platform/plugins/shared/fleet/public/jest.config.js \
        x-pack/platform/plugins/shared/fleet/public/applications/fleet/sections/agent_policy/create_package_policy_page/components/steps/components/package_policy_input_stream.test.tsx \
        x-pack/platform/plugins/shared/fleet/public/applications/fleet/sections/agent_policy/create_package_policy_page/components/steps/step_configure_package.test.tsx \
        x-pack/platform/plugins/shared/fleet/public/applications/fleet/sections/agent_policy/create_package_policy_page/components/steps/step_define_package_policy.test.tsx \
        x-pack/platform/plugins/shared/fleet/public/applications/fleet/sections/agent_policy/edit_package_policy_page/index.test.tsx \
        --testNamePattern="^(?!.*(isInputCompatibleWithVarGroupSelections|passes existing var_group selections to configure step|computes default var_group selections when missing)).*$" \
        --runInBand \
        --json \
        --outputFile=/workspace/.ecosyncbench/test-reports/kibana-public-hidden.json || status=$?
      node scripts/jest.js \
        --config x-pack/platform/plugins/shared/fleet/server/jest.config.js \
        x-pack/platform/plugins/shared/fleet/server/routes/package_policy/handlers.test.ts \
        x-pack/platform/plugins/shared/fleet/server/services/epm/archive/parse.test.ts \
        --runInBand \
        --json \
        --outputFile=/workspace/.ecosyncbench/test-reports/kibana-server-hidden.json || status=$?
      find target/junit -maxdepth 1 -type f -name "TEST-Jest Tests*.xml" -delete 2>/dev/null || true
      exit "$status"
    '
}

case "$profile" in
  package_spec-hidden)
    run_package_spec
    ;;
  kibana-hidden)
    run_kibana
    ;;
  *)
    echo "Unknown profile for elastic_kibana_package_spec_264fe9f9020d: $profile" >&2
    exit 2
    ;;
esac
