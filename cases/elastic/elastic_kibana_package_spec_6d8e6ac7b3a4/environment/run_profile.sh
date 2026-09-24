#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
repo_root="${ECOSYNC_REPO_ROOT:-$(pwd -P)}"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/elastic-kibana-package-spec-6d8e6ac7b3a4}"
go_image="ecosyncbench/base/go:1.26-bookworm"
kibana_image="ecosyncbench/base/elastic-node:22.22.0"

mkdir -p \
  "$workspace/.ecosyncbench/test-reports" \
  "$cache_root/go-build" \
  "$cache_root/go-mod" \
  "$cache_root/kibana/corepack" \
  "$cache_root/kibana/yarn" \
  "$cache_root/kibana/npm" \
  "$cache_root/kibana/kbn-bootstrap" \
  "$cache_root/kibana/home"

ensure_go_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$go_image" >/dev/null 2>&1; then
    docker build -t "$go_image" \
      -f "$repo_root/benchmark/images/base/go-1.26-bookworm/Dockerfile" \
      "$repo_root/benchmark/images/base/go-1.26-bookworm"
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

  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-2400}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e PATH=/go/bin:/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -e GOCACHE=/go-cache/build \
    -e GOMODCACHE=/go-cache/mod \
    -v "$workspace:/workspace" \
    -v "$cache_root/go-build:/go-cache/build" \
    -v "$cache_root/go-mod:/go-cache/mod" \
    "${mounts[@]}" \
    -w /workspace/repos/elastic/package-spec/code/go \
    "$go_image" \
    bash -c '
      set -euo pipefail
      mkdir -p /tmp/ecosync-home /workspace/.ecosyncbench/test-reports
      go test -count=1 -json ./pkg/validator \
        | tee /workspace/.ecosyncbench/test-reports/package-spec-hidden.json
    '
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

  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-5400}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/node-cache/home \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    -e COREPACK_HOME=/node-cache/corepack \
    -e YARN_CACHE_FOLDER=/node-cache/yarn \
    -e YARN_GLOBAL_FOLDER=/node-cache/yarn-berry \
    -e npm_config_cache=/node-cache/npm \
    -e KBN_BOOTSTRAP_CACHE_DIR=/node-cache/kbn-bootstrap \
    -e CYPRESS_INSTALL_BINARY=0 \
    -e PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    -e PUPPETEER_SKIP_DOWNLOAD=1 \
    -v "$workspace:/workspace" \
    -v "$cache_root/kibana:/node-cache" \
    "${mounts[@]}" \
    -w /workspace/repos/elastic/kibana \
    "$kibana_image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /node-cache/home /node-cache/corepack /node-cache/yarn /node-cache/npm /node-cache/kbn-bootstrap /workspace/.ecosyncbench/test-reports
      corepack prepare yarn@1.22.22 --activate
      yarn install --frozen-lockfile --non-interactive
      node - <<'"'"'NODE'"'"'
const { updatePackageMap, getRepoRelsSync } = require("./src/platform/packages/private/kbn-repo-packages");
const repoRoot = process.cwd();
updatePackageMap(repoRoot, Array.from(getRepoRelsSync(repoRoot, ["**/kibana.jsonc"])));
NODE

      find target/junit -maxdepth 1 -type f -name "TEST-Jest Tests*.xml" -delete 2>/dev/null || true

      status=0
      node scripts/jest.js \
        --config x-pack/platform/plugins/shared/fleet/public/jest.config.js \
        x-pack/platform/plugins/shared/fleet/public/applications/fleet/sections/agent_policy/create_package_policy_page/components/steps/components/package_policy_input_stream.test.tsx \
        --runInBand --forceExit --json \
        --outputFile=/workspace/.ecosyncbench/test-reports/kibana-fleet-public-hidden.json \
        --colors=false || status=$?

      node scripts/jest.js \
        --config x-pack/platform/plugins/shared/fleet/server/jest.config.js \
        x-pack/platform/plugins/shared/fleet/server/services/agent_policies/full_agent_policy.test.ts \
        x-pack/platform/plugins/shared/fleet/server/services/agent_policies/otel_collector.test.ts \
        x-pack/platform/plugins/shared/fleet/server/services/epm/packages/input_type_packages.test.ts \
        --testNamePattern="^(?!.*should call generateOtelcolConfig with packageInfoCache).*$" \
        --runInBand --forceExit --json \
        --outputFile=/workspace/.ecosyncbench/test-reports/kibana-fleet-server-hidden.json \
        --colors=false || status=$?

      find target/junit -maxdepth 1 -type f -name "TEST-Jest Tests*.xml" -delete 2>/dev/null || true
      exit "$status"
    '
}

case "$profile" in
  kibana-hidden)
    run_kibana
    ;;
  package_spec-hidden)
    run_package_spec
    ;;
  *)
    echo "Unknown profile for elastic_kibana_package_spec_6d8e6ac7b3a4: $profile" >&2
    exit 2
    ;;
esac
