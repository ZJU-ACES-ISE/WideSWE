#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
cache_root="${ECOSYNC_ELASTIC_CACHE_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy}}"
case_cache="elastic-elser-defaults-d64c23936ecd"
shared_corepack_root="${ECOSYNC_SHARED_COREPACK_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/evaluator-dependency-cache/corepack}}"

elasticsearch_image="ecosyncbench/base/elastic-java:21"
kibana_image="ecosyncbench/base/elastic-node:22.16.0"

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

common_docker_args=(
  --rm
  -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}"
  -e HOME=/tmp/ecosync-home
  -e CI=1
  -e FORCE_COLOR=0
  -v "$workspace:/workspace"
)

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

ensure_elasticsearch_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$elasticsearch_image" >/dev/null 2>&1; then
    docker build \
      --build-arg "ECOSYNC_JAVA_BASE=eclipse-temurin:21-jdk" \
      -t "$elasticsearch_image" \
      -f "$task_dir/environment/elastic-java.Dockerfile" \
      "$task_dir/environment"
  fi
}

ensure_kibana_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$kibana_image" >/dev/null 2>&1; then
    docker build \
      --build-arg "ECOSYNC_NODE_BASE=ecosyncbench/base/node:22-bookworm" \
      --build-arg "ECOSYNC_NODE_VERSION=22.16.0" \
      -t "$kibana_image" \
      -f "$task_dir/environment/elastic-node.Dockerfile" \
      "$task_dir/environment"
  fi
}

gradle_mirror_script() {
  cat <<'GRADLE'
def ecosyncMirrorRepos = { handler ->
  handler.maven { url = uri("https://maven.aliyun.com/repository/gradle-plugin") }
  handler.maven { url = uri("https://maven.aliyun.com/repository/public") }
  handler.maven { url = uri("https://mirrors.cloud.tencent.com/nexus/repository/maven-public/") }
  handler.mavenCentral()
  handler.gradlePluginPortal()
}

beforeSettings { settings ->
  try {
    settings.pluginManagement.repositories.clear()
    settings.pluginManagement.repositories { ecosyncMirrorRepos(delegate) }
  } catch (Throwable ignored) {
  }
}

settingsEvaluated { settings ->
  try {
    settings.dependencyResolutionManagement.repositories.clear()
    settings.dependencyResolutionManagement.repositories { ecosyncMirrorRepos(delegate) }
  } catch (Throwable ignored) {
  }
}

allprojects {
  buildscript {
    repositories { ecosyncMirrorRepos(delegate) }
  }
  repositories { ecosyncMirrorRepos(delegate) }
}
GRADLE
}

run_elasticsearch() {
  ensure_elasticsearch_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$elasticsearch_image" bash -lc 'java -version >/dev/null'
    return 0
  fi

  local -a mounts=()
  local mount_spec
  while IFS= read -r -d '' mount_spec; do
    mounts+=(-v "$mount_spec")
  done < <(git_mounts)

  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-5400}" docker run \
    "${common_docker_args[@]}" \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -e GRADLE_USER_HOME=/gradle-cache \
    -e RUNTIME_JAVA_HOME=/opt/java/openjdk \
    -v "$cache_root/gradle-cache/$case_cache:/gradle-cache" \
    "${mounts[@]}" \
    -w /workspace/repos/elastic/elasticsearch \
    "$elasticsearch_image" \
    bash -lc "$(declare -f gradle_mirror_script); $(cat <<'BASH'
      set -euo pipefail
      mkdir -p /tmp/ecosync-home /workspace/.ecosyncbench/test-reports/elasticsearch-hidden
      rm -rf x-pack/plugin/inference/build/test-results
      if ! grep -q "^networkTimeout=" gradle/wrapper/gradle-wrapper.properties; then
        printf "\nnetworkTimeout=120000\n" >> gradle/wrapper/gradle-wrapper.properties
      else
        sed -i "s/^networkTimeout=.*/networkTimeout=120000/" gradle/wrapper/gradle-wrapper.properties
      fi
      gradle_mirror_script > /tmp/ecosync-gradle-mirrors.gradle
      status=0
      ./gradlew --build-cache --init-script /tmp/ecosync-gradle-mirrors.gradle --no-daemon --continue \
        :x-pack:plugin:inference:test \
        --tests "org.elasticsearch.xpack.inference.services.elastic.ElasticInferenceServiceTests" \
        --tests "org.elasticsearch.xpack.inference.services.elastic.authorization.ElasticInferenceServiceAuthorizationHandlerTests" \
        --tests "org.elasticsearch.xpack.inference.services.elasticsearch.ElserModelsTests" || status=1
      report_dir=/workspace/.ecosyncbench/test-reports/elasticsearch-hidden
      find x-pack/plugin/inference/build/test-results -name "TEST-*.xml" -type f -exec cp {} "$report_dir/" \; 2>/dev/null || true
      test "$(find "$report_dir" -name "TEST-*.xml" -type f | wc -l)" -gt 0
      rm -rf x-pack/plugin/inference/build/test-results
      exit "$status"
BASH
    )"
}

run_kibana() {
  ensure_kibana_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$kibana_image" bash -lc 'node --version >/dev/null && corepack --version >/dev/null'
    return 0
  fi

  local -a mounts=()
  local mount_spec
  while IFS= read -r -d '' mount_spec; do
    mounts+=(-v "$mount_spec")
  done < <(git_mounts)

  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-5400}" docker run \
    "${common_docker_args[@]}" \
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
    "${mounts[@]}" \
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
      node scripts/jest.js \
        x-pack/solutions/search/plugins/search_inference_endpoints/public/components/all_inference_endpoints/tabular_page.test.tsx \
        --runInBand \
        --forceExit \
        --json \
        --outputFile=/workspace/.ecosyncbench/test-reports/kibana-hidden-jest.json
      test -s /workspace/.ecosyncbench/test-reports/kibana-hidden-jest.json
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
    echo "Unknown profile for elastic_elasticsearch_kibana_d64c23936ecd: $profile" >&2
    exit 2
    ;;
esac
