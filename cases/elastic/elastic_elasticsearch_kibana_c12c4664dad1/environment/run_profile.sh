#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
host_repo_root="$(pwd -P)"
image_java="ecosyncbench/base/elastic-java:21"
image_kibana="ecosyncbench/base/elastic-node:24.14.1"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/node-cache/elastic-change-point-by}"
gradle_cache_root="${ECOSYNC_GRADLE_CACHE_ROOT:-$cache_root/gradle}"
shared_corepack_root="${ECOSYNC_SHARED_COREPACK_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/evaluator-dependency-cache/corepack}}"
mkdir -p "$gradle_cache_root" "$cache_root/yarn" "$cache_root/corepack" "$cache_root/npm" "$workspace/.ecosyncbench/test-reports"

if [[ ! -s "$shared_corepack_root/v1/yarn/1.22.22/lib/cli.js" ]]; then
  echo "missing offline Corepack yarn@1.22.22 cache: $shared_corepack_root" >&2
  exit 2
fi

ensure_java_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image_java" >/dev/null 2>&1; then
    docker build \
      --build-arg "ECOSYNC_JAVA_BASE=eclipse-temurin:21-jdk" \
      -t "$image_java" \
      -f "$task_dir/environment/elastic-java.Dockerfile" \
      "$task_dir/environment"
  fi
}

ensure_node_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image_kibana" >/dev/null 2>&1; then
    docker build \
      --build-arg "ECOSYNC_NODE_BASE=ecosyncbench/base/node:24-bookworm" \
      --build-arg "ECOSYNC_NODE_VERSION=24.14.1" \
      -t "$image_kibana" \
      -f "$task_dir/environment/elastic-node.Dockerfile" \
      "$task_dir/environment"
  fi
}

run_in_docker() {
  local image="$1"
  local workdir="$2"
  local timeout_seconds="$3"
  local script="$4"
  local gradle_wrapper_mount=()
  if [[ -n "${ECOSYNC_GRADLE_WRAPPER_CACHE_ROOT:-}" ]]; then
    gradle_wrapper_mount=(-v "$ECOSYNC_GRADLE_WRAPPER_CACHE_ROOT:/gradle-cache/wrapper")
  fi
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image" bash -lc 'true'
    return 0
  fi
  timeout "$timeout_seconds" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    -e COREPACK_HOME=/node-cache/corepack \
    -e YARN_CACHE_FOLDER=/node-cache/yarn \
    -e YARN_GLOBAL_FOLDER=/node-cache/yarn-berry \
    -e npm_config_cache=/node-cache/npm \
    -e GRADLE_USER_HOME=/gradle-cache \
    -e RUNTIME_JAVA_HOME=/opt/java/openjdk \
    -e CYPRESS_INSTALL_BINARY=0 \
    -e PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    -e PUPPETEER_SKIP_DOWNLOAD=1 \
    -v "$workspace:/workspace" \
    -v "$gradle_cache_root:/gradle-cache" \
    "${gradle_wrapper_mount[@]}" \
    -v "$cache_root:/node-cache" \
    -v "$shared_corepack_root:/node-cache/corepack:ro" \
    -v "$host_repo_root/repos/elastic/elasticsearch/.git:$host_repo_root/repos/elastic/elasticsearch/.git:ro" \
    -v "$host_repo_root/repos/elastic/kibana/.git:$host_repo_root/repos/elastic/kibana/.git:ro" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -lc "$script"
}

case "$profile" in
  elasticsearch-hidden)
    ensure_java_image
    run_in_docker "$image_java" "repos/elastic/elasticsearch" "${ECOSYNC_PROFILE_TIMEOUT:-3600}" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      result_dir=x-pack/plugin/esql/build/test-results/test
      rm -rf "$result_dir"
      set +e
      ./gradlew --build-cache --no-daemon \
        :x-pack:plugin:esql:test \
        --tests "org.elasticsearch.xpack.esql.planner.LocalExecutionPlannerTests.testEcoSyncChangePoint*"
      status=$?
      report_dir=/workspace/.ecosyncbench/test-reports/elasticsearch-change-point-by
      mkdir -p "$report_dir"
      find "$result_dir" -name "TEST-*.xml" -type f -exec cp {} "$report_dir/" \; 2>/dev/null || true
      test "$(find "$report_dir" -name "TEST-*.xml" -type f | wc -l)" -gt 0
      rm -rf "$result_dir"
      exit "$status"
    '
    ;;
  kibana-hidden)
    ensure_node_image
    run_in_docker "$image_kibana" "repos/elastic/kibana" "${ECOSYNC_PROFILE_TIMEOUT:-3000}" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      rm -rf target/junit
      corepack prepare yarn@1.22.22 --activate
      yarn install --frozen-lockfile --non-interactive
      node - <<'"'"'NODE'"'"'
const { updatePackageMap, getRepoRelsSync } = require("./src/platform/packages/private/kbn-repo-packages");
const repoRoot = process.cwd();
updatePackageMap(repoRoot, Array.from(getRepoRelsSync(repoRoot, ["**/kibana.jsonc"])));
NODE

      status=0
      report=/workspace/.ecosyncbench/test-reports/kibana-discover-change-point-jest.json
      set +e
      node scripts/jest.js \
        src/platform/plugins/shared/discover/public/context_awareness/profile_providers/register_change_point_profile.ecosync.test.ts \
        src/platform/plugins/shared/discover/public/context_awareness/profile_providers/register_profile_providers.test.ts \
        --runInBand --json --outputFile="$report"
      status=$?
      set -e
      test -s "$report" || status=1
      rm -rf target/junit
      exit "$status"
    '
    ;;
  *)
    echo "Unknown profile for Elastic CHANGE_POINT BY case: $profile" >&2
    exit 2
    ;;
esac
