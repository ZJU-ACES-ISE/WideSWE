#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/node-cache/swagger-api-f6dabca1247a}"
mkdir -p "$workspace/.ecosyncbench/test-reports" "$cache_root/npm" "$cache_root/cypress"

swagger_js_image="ecosyncbench/deps/swagger-js-npm:5a4f9a3-f6dabca1247a"
swagger_ui_image="ecosyncbench/deps/swagger-ui-cypress-npm:f63d850-f6dabca1247a"

build_npm_image() {
  local image="$1"
  local repo="$2"
  local repo_path="$3"
  local base_commit="$4"
  local fingerprint="$5"
  local dockerfile="$6"
  shift 6
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build \
      --build-arg "ECOSYNC_TASK_ID=swagger_api_swagger_js_swagger_ui_f6dabca1247a" \
      --build-arg "ECOSYNC_REPO=${repo}" \
      --build-arg "ECOSYNC_REPO_PATH=${repo_path}" \
      --build-arg "ECOSYNC_BASE_COMMIT=${base_commit}" \
      --build-arg "ECOSYNC_DEPENDENCY_FINGERPRINT=${fingerprint}" \
      "$@" \
      -t "$image" \
      -f "$task_dir/environment/${dockerfile}" \
      "$workspace"
  fi
}

copy_node_modules() {
  rm -rf node_modules
  ln -s /opt/ecosync/deps/node_modules node_modules
}

run_swagger_js() {
  build_npm_image \
    "$swagger_js_image" \
    "swagger-api/swagger-js" \
    "repos/swagger-api/swagger-js" \
    "5a4f9a335b7fd56a58f00339d6f689fcfa78d700" \
    "5a4f9a3-npm-ci-f6dabca1247a" \
    "node-npm-ci.Dockerfile"
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-1800}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "0:0" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e npm_config_cache=/cache/npm \
    -e ECOSYNC_DOCKER_PREBUILD_ONLY="${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" \
    -e ECOSYNC_DOCKER_WARMUP_ONLY="${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    -w "/workspace/repos/swagger-api/swagger-js" \
    "$swagger_js_image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      copy_node_modules() { rm -rf node_modules && ln -s /opt/ecosync/deps/node_modules node_modules; }
      copy_node_modules
      if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
        npx jest --version >/dev/null
        exit 0
      fi
      npm run test:unit -- \
        test/execute/openapi-3-1.js \
        test/oas3/execute/main.js \
        test/oas3/execute/style-explode/query.js \
        --json --outputFile=/workspace/.ecosyncbench/test-reports/swagger-js-hidden.json
    '
}

run_swagger_ui() {
  build_npm_image \
    "$swagger_ui_image" \
    "swagger-api/swagger-ui" \
    "repos/swagger-api/swagger-ui" \
    "f63d8504997cca6172599022211c3e7315069afa" \
    "f63d850-npm-ci-cypress14.2.0-f6dabca1247a" \
    "node-cypress-npm-ci.Dockerfile"
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-2400}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "0:0" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e npm_config_cache=/cache/npm \
    -e CYPRESS_CACHE_FOLDER=/opt/ecosync/cypress-cache \
    -e ECOSYNC_DOCKER_PREBUILD_ONLY="${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" \
    -e ECOSYNC_DOCKER_WARMUP_ONLY="${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    -w "/workspace/repos/swagger-api/swagger-ui" \
    "$swagger_ui_image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home /cache/npm
      copy_node_modules() { rm -rf node_modules && ln -s /opt/ecosync/deps/node_modules node_modules; }
      copy_node_modules
      if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
        npx jest --version >/dev/null
        npx cypress verify >/dev/null
        exit 0
      fi
      set +e
      npm run test:unit -- \
        test/unit/core/utils.js \
        --json --outputFile=/workspace/.ecosyncbench/test-reports/swagger-ui-unit.json
      unit_status=$?
      npx start-server-and-test \
        "npm run cy:start" \
        http://localhost:3204 \
        "npm run cy:run -- --browser electron --spec test/e2e-cypress/e2e/features/try-it-out-schema-type-array-example-type-string.cy.js --reporter junit --reporter-options mochaFile=/workspace/.ecosyncbench/test-reports/swagger-ui-cypress.xml,toConsole=false"
      cypress_status=$?
      set -e
      if (( unit_status != 0 || cypress_status != 0 )); then
        exit 1
      fi
    '
}

case "$profile" in
  swagger_js-hidden)
    run_swagger_js
    ;;
  swagger_ui-hidden)
    run_swagger_ui
    ;;
  *)
    echo "Unknown profile for swagger_api_swagger_js_swagger_ui_f6dabca1247a: $profile" >&2
    exit 2
    ;;
esac
