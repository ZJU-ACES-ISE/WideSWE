#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
workspace="${ECOSYNC_WORKSPACE:-/workspace}"
reports="$workspace/.ecosyncbench/test-reports"
mkdir -p "$reports/$profile"

run_elasticsearch() {
  cd "$workspace/repos/elastic/elasticsearch"
  export GRADLE_USER_HOME="${GRADLE_USER_HOME:-/gradle-cache}"
  mkdir -p "$GRADLE_USER_HOME"
  ./gradlew :x-pack:plugin:inference:internalClusterTest \
    --build-cache \
    --no-daemon \
    --max-workers="${ECOSYNC_GRADLE_MAX_WORKERS:-2}" \
    --tests "org.elasticsearch.xpack.inference.integration.AuthorizationTaskExecutorIT.testRemovedEndpointsContract_*"
}

bootstrap_kibana() {
  cd "$workspace/repos/elastic/kibana"
  export YARN_CACHE_FOLDER="${YARN_CACHE_FOLDER:-/yarn-cache}"
  export CYPRESS_INSTALL_BINARY=0
  export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
  mkdir -p "$YARN_CACHE_FOLDER"
  mkdir -p .ecosyncbench
  cat > .ecosyncbench/regenerate_kibana_package_metadata.mjs <<'EOF'
import { discovery } from '../src/dev/kbn_pm/src/commands/bootstrap/discovery.mjs';
import { regeneratePackageMap } from '../src/dev/kbn_pm/src/commands/bootstrap/regenerate_package_map.mjs';
import { regenerateTsconfigPaths } from '../src/dev/kbn_pm/src/commands/bootstrap/regenerate_tsconfig_paths.mjs';
import { updatePackageJson } from '../src/dev/kbn_pm/src/commands/bootstrap/update_package_json.mjs';
import { regenerateBaseTsconfig } from '../src/dev/kbn_pm/src/commands/bootstrap/regenerate_base_tsconfig.mjs';

const log = {
  warning: (msg) => console.error(`[ecosyncbench:kibana-metadata] ${msg}`),
  info: (msg) => console.error(`[ecosyncbench:kibana-metadata] ${msg}`),
  success: (msg) => console.error(`[ecosyncbench:kibana-metadata] ${msg}`),
  debug: () => {},
};

const { packageManifestPaths, tsConfigRepoRels } = await discovery();
const packages = await regeneratePackageMap(log, packageManifestPaths);
await regenerateTsconfigPaths(tsConfigRepoRels, log);
await updatePackageJson(packages, log);
await regenerateBaseTsconfig(packages, log);
EOF
  node .ecosyncbench/regenerate_kibana_package_metadata.mjs
  yarn config set network-timeout 600000
  yarn install --non-interactive --ignore-scripts
}

run_kibana_jest() {
  cd "$workspace/repos/elastic/kibana"
  export YARN_CACHE_FOLDER="${YARN_CACHE_FOLDER:-/yarn-cache}"
  export CI=true
  export FORCE_COLOR=0
  mkdir -p "$reports/$profile"
  local status=0
  node scripts/jest \
    --config x-pack/platform/plugins/shared/actions/jest.config.js \
    --runInBand \
    --runTestsByPath x-pack/platform/plugins/shared/actions/server/plugin.test.ts \
    --json \
    --outputFile "$reports/$profile/actions.json" || status=$?
  node scripts/jest \
    --config x-pack/platform/plugins/shared/search_inference_endpoints/jest.config.js \
    --runInBand \
    --runTestsByPath \
      x-pack/platform/plugins/shared/search_inference_endpoints/server/lib/dynamic_connectors.test.ts \
      x-pack/platform/plugins/shared/search_inference_endpoints/server/utils/in_memory_connectors.test.ts \
    --json \
    --outputFile "$reports/$profile/search_inference_endpoints.json" || status=$?
  return "$status"
}

case "$profile" in
  elasticsearch-hidden)
    run_elasticsearch
    ;;
  kibana-hidden)
    bootstrap_kibana
    run_kibana_jest
    ;;
  *)
    echo "Unknown profile: $profile" >&2
    exit 2
    ;;
esac
