#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
image="ecosyncbench/base/prettier-node:24"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/node-cache/prettier-yaml-v2}"
mkdir -p "$cache_root/yarn" "$cache_root/corepack" "$workspace/.ecosyncbench/test-reports"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$task_dir/environment/prettier-node.Dockerfile" "$task_dir/environment"
  fi
}

run_in_node() {
  local workdir="$1"
  local script="$2"
  ensure_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image" bash -lc 'node --version >/dev/null && corepack --version >/dev/null'
    return 0
  fi
  timeout "${ECOSYNC_PROFILE_TIMEOUT:-1800}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    -e COREPACK_HOME=/node-cache/corepack \
    -e YARN_CACHE_FOLDER=/node-cache/yarn \
    -e npm_config_cache=/node-cache/npm \
    -v "$workspace:/workspace" \
    -v "$cache_root:/node-cache" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -lc "$script"
}

case "$profile" in
  prettier-hidden)
    run_in_node "repos/prettier/prettier" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      corepack yarn install --immutable
      report=/workspace/.ecosyncbench/test-reports/prettier-yaml-v2-jest.json
      set +e
      corepack yarn jest \
        tests/format/yaml/_errors_/format.test.js \
        tests/format/yaml/block-value/format.test.js \
        tests/format/yaml/flow-mapping/comments/format.test.js \
        tests/format/yaml/mapping/format.test.js \
        tests/format/yaml/spec/format.test.js \
        tests/format/yaml/yaml-test-suite/format.test.js \
        tests/integration/__tests__/config-invalid.js \
        --runInBand --json --outputFile="$report"
      status=$?
      test -s "$report"
      exit "$status"
    '
    ;;
  yaml_unist_parser-hidden)
    run_in_node "repos/prettier/yaml-unist-parser" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      corepack yarn install --immutable
      report=/workspace/.ecosyncbench/test-reports/yaml-unist-parser-yaml-v2.xml
      set +e
      CI= corepack yarn vitest run \
        src/options.test.ts \
        src/yaml-test-suite.test.ts \
        src/transforms/alias.test.ts \
        src/transforms/block-folded.test.ts \
        src/transforms/block-literal.test.ts \
        src/transforms/document.test.ts \
        src/transforms/flow-collection.test.ts \
        src/transforms/transform.test.ts \
        --reporter=junit \
        --outputFile="$report"
      status=$?
      set -e
      node - "$report" <<'"'"'NODE'"'"'
const fs = require("fs");
const path = process.argv[2];
let xml = fs.readFileSync(path, "utf8");

const escapeRegExp = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

for (const classname of [
  "src/transforms/document.test.ts",
  "src/transforms/flow-collection.test.ts",
]) {
  const escaped = escapeRegExp(classname);
  xml = xml.replace(
    new RegExp(`\\n?\\s*<testcase classname="${escaped}" name="${escaped}"[^>]*>[\\s\\S]*?<\\/testcase>`, "g"),
    (match) => (match.includes("Obsolete snapshots found") ? "" : match),
  );
}

const normalizeGeneratedSyntaxNames = (classname, limit) => {
  const escaped = escapeRegExp(classname);
  const suitePattern = new RegExp(
    `(<testsuite[^>]*name="${escaped}"[\\s\\S]*?<\\/testsuite>)`,
    "g",
  );
  xml = xml.replace(suitePattern, (suite) => {
    let index = 0;
    return suite.replace(
      new RegExp(`(<testcase\\s+[^>]*classname="${escaped}"\\s+name=")([^"]*)("[^>]*>)`, "g"),
      (match, prefix, name, suffix) => {
        if (index >= limit || name === classname || name.startsWith("&quot;")) {
          return match;
        }
        index += 1;
        return `${prefix}syntax-error-${index}${suffix}`;
      },
    );
  });
};

normalizeGeneratedSyntaxNames("src/transforms/document.test.ts", 1);
normalizeGeneratedSyntaxNames("src/transforms/flow-collection.test.ts", 5);
fs.writeFileSync(path, xml);
NODE
      exit "$status"
    '
    ;;
  *)
    echo "Unknown profile for prettier yaml v2 case: $profile" >&2
    exit 2
    ;;
esac
