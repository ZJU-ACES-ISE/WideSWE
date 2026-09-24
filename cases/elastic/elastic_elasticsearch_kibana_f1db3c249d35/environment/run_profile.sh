#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/node-cache/elastic-esql-subquery}"
shared_corepack_root="${ECOSYNC_SHARED_COREPACK_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/evaluator-dependency-cache/corepack}}"
mkdir -p "$cache_root/gradle" "$cache_root/yarn" "$cache_root/corepack" "$cache_root/npm" "$workspace/.ecosyncbench/test-reports"

if [[ ! -s "$cache_root/corepack/v1/yarn/1.22.21/lib/cli.js" ]]; then
  if [[ ! -s "$shared_corepack_root/v1/yarn/1.22.21/lib/cli.js" ]]; then
    echo "missing offline Corepack yarn@1.22.21 cache: $shared_corepack_root" >&2
    exit 2
  fi
  mkdir -p "$cache_root/corepack/v1/yarn"
  cp -a "$shared_corepack_root/v1/yarn/1.22.21" "$cache_root/corepack/v1/yarn/"
fi

image_java="ecosyncbench/base/elastic-java:21"
image_kibana="ecosyncbench/base/elastic-node:24.14.1"

ensure_java_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image_java" >/dev/null 2>&1; then
    docker build \
      --build-arg "ECOSYNC_JAVA_BASE=eclipse-temurin:21-jdk" \
      -t "$image_java" \
      -f "$task_dir/environment/elastic-java.Dockerfile" \
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

run_in_docker() {
  local image="$1"
  local workdir="$2"
  local script="$3"
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image" bash -lc 'true'
    return 0
  fi
  local -a mounts=()
  local mount_spec
  while IFS= read -r -d '' mount_spec; do
    mounts+=(-v "$mount_spec")
  done < <(git_mounts)
  timeout "${ECOSYNC_PROFILE_TIMEOUT:-3600}" docker run --rm \
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
    -v "$workspace:/workspace" \
    -v "$cache_root/gradle:/gradle-cache" \
    -v "$cache_root:/node-cache" \
    "${mounts[@]}" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -lc "$script"
}

case "$profile" in
  elasticsearch-hidden)
    ensure_java_image
    run_in_docker "$image_java" "repos/elastic/elasticsearch" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      rm -rf x-pack/plugin/esql/build/test-results
      set +e
      ./gradlew --build-cache --no-daemon --continue :x-pack:plugin:esql:internalClusterTest \
        -Porg.elasticsearch.build.branches-file-location=/workspace/repos/elastic/elasticsearch/branches.json \
        --tests "org.elasticsearch.xpack.esql.action.InSubqueryContractIT"
      status=$?
      report_dir=/workspace/.ecosyncbench/test-reports/elasticsearch-esql
      mkdir -p "$report_dir"
      find x-pack/plugin/esql/build/test-results -name "TEST-*.xml" -type f -exec cp {} "$report_dir/" \; 2>/dev/null || true
      test "$(find "$report_dir" -name "TEST-*.xml" -type f | wc -l)" -gt 0
      rm -rf x-pack/plugin/esql/build/test-results
      exit "$status"
    '
    ;;
  kibana-hidden)
    run_in_docker "$image_kibana" "repos/elastic/kibana" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      rm -rf target/junit
      corepack prepare yarn@1.22.21 --activate
      yarn install --frozen-lockfile --non-interactive
      node - <<'"'"'NODE'"'"'
const { updatePackageMap, getRepoRelsSync } = require("./src/platform/packages/private/kbn-repo-packages");
const repoRoot = process.cwd();
updatePackageMap(repoRoot, Array.from(getRepoRelsSync(repoRoot, ["**/kibana.jsonc"])));
NODE
      report=/workspace/.ecosyncbench/test-reports/kibana-esql-language-jest.json
      set +e
      node scripts/jest.js \
        src/platform/packages/shared/kbn-esql-language/src/commands/definitions/utils/expressions.test.ts \
        src/platform/packages/shared/kbn-esql-language/src/commands/registry/eval/autocomplete.test.ts \
        src/platform/packages/shared/kbn-esql-language/src/commands/registry/where/autocomplete.test.ts \
        src/platform/packages/shared/kbn-esql-language/src/language/autocomplete/autocomplete.test.ts \
        --runInBand --json --outputFile="$report"
      status=$?
      test -s "$report"
      rm -rf target/junit
      exit "$status"
    '
    ;;
  *)
    echo "Unknown profile for Elastic ES|QL subquery case: $profile" >&2
    exit 2
    ;;
esac
