#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
host_repo_root="$(pwd -P)"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/cases/elastic_elastic_package_kibana_90b51a54dcde}"
go_image="ecosyncbench/base/go:1.26-bookworm"
kibana_image="ecosyncbench/base/elastic-node:22.22.0"

mkdir -p "$workspace/.ecosyncbench/test-reports" \
  "$cache_root/go-cache/elastic-input-qualifier/go-build" \
  "$cache_root/go-cache/elastic-input-qualifier/go-mod" \
  "$cache_root/node-cache/elastic-input-qualifier/kibana/corepack" \
  "$cache_root/node-cache/elastic-input-qualifier/kibana/yarn" \
  "$cache_root/node-cache/elastic-input-qualifier/kibana/npm" \
  "$cache_root/node-cache/elastic-input-qualifier/kibana/kbn-bootstrap"

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

run_go_json() {
  local repo_dir="$1"
  local report="$2"
  shift 2
  ensure_go_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$go_image" bash -c 'go version >/dev/null'
    return 0
  fi
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-3600}" docker run \
    "${docker_common_args[@]}" \
    -e PATH=/go/bin:/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -e ECOSYNC_REPORT="$report" \
    -e GOCACHE=/go-build-cache \
    -e GOMODCACHE=/go-mod-cache \
    -v "$cache_root/go-cache/elastic-input-qualifier/go-build:/go-build-cache" \
    -v "$cache_root/go-cache/elastic-input-qualifier/go-mod:/go-mod-cache" \
    -v "$host_repo_root/$repo_dir/.git:$host_repo_root/$repo_dir/.git:ro" \
    -w "/workspace/$repo_dir" \
    "$go_image" \
    bash -c 'set -euo pipefail; export PATH=/go/bin:/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; mkdir -p /tmp/ecosync-home /workspace/.ecosyncbench/test-reports; set +e; go test -json "$@" > "/workspace/.ecosyncbench/test-reports/$ECOSYNC_REPORT"; status=$?; set -e; test -s "/workspace/.ecosyncbench/test-reports/$ECOSYNC_REPORT"; exit "$status"' \
    -- "$@"
}

run_package_spec() {
  run_go_json repos/elastic/package-spec package-spec-go-test.json \
    ./code/go/internal/validator/semantic ./code/go/pkg/validator
}

run_elastic_package() {
  run_go_json repos/elastic/elastic-package elastic-package-go-test.json \
    ./internal/qualifiedinputcontract ./internal/kibana
}

run_kibana() {
  ensure_kibana_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$kibana_image" bash -lc 'node --version >/dev/null && yarn --version >/dev/null'
    return 0
  fi
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-3600}" docker run \
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
    -v "$cache_root/node-cache/elastic-input-qualifier/kibana:/node-cache" \
    -v "$host_repo_root/repos/elastic/kibana/.git:$host_repo_root/repos/elastic/kibana/.git:ro" \
    -w /workspace/repos/elastic/kibana \
    "$kibana_image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /tmp/ecosync-home /node-cache/corepack /node-cache/yarn /node-cache/npm /node-cache/kbn-bootstrap /workspace/.ecosyncbench/test-reports
      corepack prepare yarn@1.22.22 --activate
      yarn install --frozen-lockfile --non-interactive
      node - <<'"'"'NODE'"'"'
const { updatePackageMap, getRepoRelsSync } = require("./src/platform/packages/private/kbn-repo-packages");
const repoRoot = process.cwd();
updatePackageMap(repoRoot, Array.from(getRepoRelsSync(repoRoot, ["**/kibana.jsonc"])));
NODE
      set +e
      node scripts/jest.js \
        --config x-pack/platform/plugins/shared/fleet/common/jest.config.js \
        x-pack/platform/plugins/shared/fleet/common/services/package_to_package_policy.test.ts \
        x-pack/platform/plugins/shared/fleet/common/services/simplified_package_policy_helper.test.ts \
        x-pack/platform/plugins/shared/fleet/common/services/validate_package_policy.test.ts \
        x-pack/platform/plugins/shared/fleet/common/services/qualified_input_contract.test.ts \
        --runInBand \
        --json \
        --outputFile=/workspace/.ecosyncbench/test-reports/kibana-qualified-input-common-jest.json
      common_status=$?
      node scripts/jest.js \
        --config x-pack/platform/plugins/shared/fleet/public/jest.config.js \
        x-pack/platform/plugins/shared/fleet/public/applications/integrations/sections/epm/screens/detail/documentation/qualified_input_contract.test.tsx \
        --runInBand \
        --forceExit \
        --json \
        --outputFile=/workspace/.ecosyncbench/test-reports/kibana-qualified-input-documentation-jest.json
      documentation_status=$?
      set -e
      if (( common_status != 0 || documentation_status != 0 )); then
        exit 1
      fi
    '
}

case "$profile" in
  package_spec-hidden)
    run_package_spec
    ;;
  elastic_package-hidden)
    run_elastic_package
    ;;
  kibana-hidden)
    run_kibana
    ;;
  *)
    echo "Unknown profile for elastic_elastic_package_kibana_90b51a54dcde: $profile" >&2
    exit 2
    ;;
esac
