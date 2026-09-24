#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
host_repo_root="$(pwd -P)"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy}"
case_cache="elastic-promql-buckets-c9411c7b50e8"
shared_corepack_root="${ECOSYNC_SHARED_COREPACK_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/evaluator-dependency-cache/corepack}}"
source_repos_root="${ECOSYNC_SOURCE_REPOS_ROOT:-/tmp/ecosyncbench-release120-runtime/source-repos}"

image_java="ecosyncbench/base/elastic-java:21"
image_kibana="ecosyncbench/base/elastic-node:22.22.0"

mkdir -p \
  "$workspace/.ecosyncbench/test-reports" \
  "$cache_root/gradle-cache/$case_cache" \
  "$cache_root/node-cache/$case_cache/kibana/corepack" \
  "$cache_root/node-cache/$case_cache/kibana/yarn" \
  "$cache_root/node-cache/$case_cache/kibana/npm" \
  "$cache_root/node-cache/$case_cache/kibana/kbn-bootstrap"

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
      --build-arg "ECOSYNC_NODE_BASE=ecosyncbench/base/node:22-bookworm" \
      --build-arg "ECOSYNC_NODE_VERSION=22.22.0" \
      -t "$image_kibana" \
      -f "$task_dir/environment/elastic-node.Dockerfile" \
      "$task_dir/environment"
  fi
}

docker_common_args=(
  --rm
  -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}"
  -e HOME=/tmp/ecosync-home
  -e CI=1
  -e FORCE_COLOR=0
  -v "$workspace:/workspace"
)

run_elasticsearch() {
  ensure_java_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image_java" bash -lc 'java -version >/dev/null'
    return 0
  fi

  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-3600}" docker run \
    "${docker_common_args[@]}" \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -e GRADLE_USER_HOME=/gradle-cache \
    -e RUNTIME_JAVA_HOME=/opt/java/openjdk \
    -v "$cache_root/gradle-cache/$case_cache:/gradle-cache" \
    -v "$host_repo_root/repos/elastic/elasticsearch/.git:$host_repo_root/repos/elastic/elasticsearch/.git:ro" \
    -w /workspace/repos/elastic/elasticsearch \
    "$image_java" \
    bash -lc '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports/elasticsearch-promql /tmp/ecosync-home
      rm -rf x-pack/plugin/esql/build/test-results
      status=1
      for attempt in 1 2 3; do
        set +e
        ./gradlew --build-cache --no-daemon --continue :x-pack:plugin:esql:test \
          -Porg.elasticsearch.build.branches-file-location=/workspace/repos/elastic/elasticsearch/branches.json \
          --tests "org.elasticsearch.xpack.esql.optimizer.promql.PromqlBucketsContractTests" \
          2>&1 | tee /tmp/ecosync-gradle.log
        status=${PIPESTATUS[0]}
        set -e
        if [[ "$status" -eq 0 ]]; then
          break
        fi
        if [[ "$(find x-pack/plugin/esql/build/test-results -name "TEST-*.xml" -type f 2>/dev/null | wc -l)" -gt 0 ]]; then
          break
        fi
        if ! grep -Eq "Could not download|Remote host terminated the handshake" /tmp/ecosync-gradle.log; then
          break
        fi
        echo "Gradle dependency download failed on attempt ${attempt}; retrying with cached partial downloads." >&2
        sleep 5
      done
      set -e
      find x-pack/plugin/esql/build/test-results -name "TEST-*.xml" -type f \
        -exec cp {} /workspace/.ecosyncbench/test-reports/elasticsearch-promql/ \; 2>/dev/null || true
      test "$(find /workspace/.ecosyncbench/test-reports/elasticsearch-promql -name "TEST-*.xml" -type f | wc -l)" -gt 0
      rm -rf x-pack/plugin/esql/build/test-results
      exit "$status"
    '
}

run_kibana() {
  ensure_node_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image_kibana" bash -lc 'node --version >/dev/null && yarn --version >/dev/null'
    return 0
  fi

  for test_file in \
    src/platform/packages/shared/kbn-esql-language/src/commands/registry/promql/autocomplete.test.ts \
    src/platform/packages/shared/kbn-esql-language/src/commands/registry/promql/columns_after.test.ts \
    src/platform/packages/shared/kbn-esql-language/src/commands/registry/promql/summary.test.ts \
    src/platform/packages/shared/kbn-esql-language/src/commands/registry/promql/validate.test.ts; do
    git -C "$source_repos_root/elastic/kibana" show 8f985c39886455fe40ab389e8d10bb9e16e06a99:"$test_file" \
      > "$workspace/repos/elastic/kibana/$test_file"
  done

  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-3600}" docker run \
    "${docker_common_args[@]}" \
    --name "ecosync_${profile}_$$_${RANDOM}" \
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
    -v "$shared_corepack_root:/node-cache/corepack:ro" \
    -v "$host_repo_root/repos/elastic/kibana/.git:$host_repo_root/repos/elastic/kibana/.git:ro" \
    -w /workspace/repos/elastic/kibana \
    "$image_kibana" \
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
      node scripts/jest.js \
        --config src/platform/packages/shared/kbn-esql-language/jest.config.js \
        src/platform/packages/shared/kbn-esql-language/src/commands/registry/promql/autocomplete.test.ts \
        src/platform/packages/shared/kbn-esql-language/src/commands/registry/promql/columns_after.test.ts \
        src/platform/packages/shared/kbn-esql-language/src/commands/registry/promql/summary.test.ts \
        src/platform/packages/shared/kbn-esql-language/src/commands/registry/promql/validate.test.ts \
        src/platform/packages/shared/kbn-esql-language/src/commands/registry/promql/buckets_contract.ecosync.test.ts \
        --testNamePattern="^(?!.*(suggests params but not column when required params missing|missing step param|suggests = after buckets keyword with space)).*$" \
        --runInBand \
        --forceExit \
        --json \
        --outputFile=/workspace/.ecosyncbench/test-reports/kibana-promql-jest.json
    '
}

case "$profile" in
  elasticsearch-hidden)
    run_elasticsearch
    ;;
  kibana-hidden)
    run_kibana
    ;;
  *)
    echo "Unknown profile for elastic_elasticsearch_kibana_c9411c7b50e8: $profile" >&2
    exit 2
    ;;
esac
