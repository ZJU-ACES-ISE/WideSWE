#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
workspace="${ECOSYNC_WORKSPACE:?ECOSYNC_WORKSPACE required}"
image="mcr.microsoft.com/playwright:v1.56.1-noble"

docker_common=(
  docker run --rm
  --network host
  -v "${workspace}:/workspace"
  -v storybookjs-corepack-cache:/cache/corepack
  -v storybookjs-mcp-pnpm-store:/cache/mcp-pnpm-store
  -v storybookjs-storybook-yarn-cache:/cache/storybook-yarn-cache
  -e CI=1
  -e FORCE_COLOR=0
  -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0
  -e COREPACK_HOME=/cache/corepack
  -e HOME=/tmp/ecosync-home
  -e ECOSYNC_DOCKER_PREBUILD_ONLY="${ECOSYNC_DOCKER_PREBUILD_ONLY:-}"
  -e ECOSYNC_DOCKER_WARMUP_ONLY="${ECOSYNC_DOCKER_WARMUP_ONLY:-}"
  "${image}"
)

run_mcp() {
  "${docker_common[@]}" bash -lc '
    set -euo pipefail
    cd /workspace/repos/storybookjs/mcp
    mkdir -p /tmp/ecosync-home /workspace/.ecosyncbench/test-reports
    corepack enable
    corepack prepare pnpm@10.29.2 --activate
    pnpm config set store-dir /cache/mcp-pnpm-store
    pnpm install --frozen-lockfile
    if [ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-}" = "1" ] || [ "${ECOSYNC_DOCKER_WARMUP_ONLY:-}" = "1" ]; then
      exit 0
    fi
    pnpm --filter @storybook/mcp build
    pnpm --filter @storybook/addon-mcp build
    status=0
    pnpm exec vitest run \
      apps/internal-storybook/tests/mcp-endpoint.e2e.test.ts \
      --no-file-parallelism \
      --maxWorkers=1 \
      --testTimeout=60000 \
      --hookTimeout=60000 \
      --reporter=default \
      --reporter=junit \
      --outputFile=/workspace/.ecosyncbench/test-reports/mcp-hidden.junit.xml || status=$?
    exit "$status"
  '
}

run_storybook() {
  "${docker_common[@]}" bash -lc '
    set -euo pipefail
    cd /workspace/repos/storybookjs/storybook
    mkdir -p /tmp/ecosync-home /workspace/.ecosyncbench/test-reports
    activate_yarn() {
      corepack enable
      for attempt in 1 2 3; do
        if corepack prepare yarn@4.10.3 --activate; then
          yarn --version
          return 0
        fi
        sleep "$((attempt * 5))"
      done
      corepack prepare yarn@4.10.3 --activate
      yarn --version
    }
    activate_yarn
    export YARN_ENABLE_GLOBAL_CACHE=1
    export YARN_ENABLE_SCRIPTS=0
    export YARN_GLOBAL_FOLDER=/cache/storybook-yarn-cache/global
    export YARN_CACHE_FOLDER=/cache/storybook-yarn-cache/cache
    yarn install --immutable
    if [ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-}" = "1" ] || [ "${ECOSYNC_DOCKER_WARMUP_ONLY:-}" = "1" ]; then
      exit 0
    fi
    VITEST=/workspace/repos/storybookjs/storybook/node_modules/.bin/vitest
    gen_vitest_config() {
      pkg_dir="$1"
      node - "$pkg_dir" <<'"'"'NODE'"'"'
const fs = require("fs");
const path = require("path");

const pkgDir = process.argv[2];
const codeRoot = "/workspace/repos/storybookjs/storybook/code";

function walk(dir, out = []) {
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    if (ent.name === "node_modules" || ent.name === ".yarn" || ent.name === "dist") continue;
    const p = path.join(dir, ent.name);
    if (ent.isDirectory()) walk(p, out);
    else if (ent.isFile() && ent.name === "package.json") out.push(p);
  }
  return out;
}

const entries = [];
for (const pkgPath of walk(codeRoot)) {
  const pkg = require(pkgPath);
  if (!pkg.name || !pkg.exports || typeof pkg.exports !== "object") continue;
  const root = path.dirname(pkgPath);
  for (const [key, value] of Object.entries(pkg.exports)) {
    if (!value || typeof value !== "object" || !value.code) continue;
    const spec = key === "." ? pkg.name : `${pkg.name}/${key.slice(2)}`;
    entries.push({ spec, target: path.resolve(root, value.code) });
  }
}

entries.sort((a, b) => b.spec.length - a.spec.length);
const alias = entries
  .map(({ spec, target }) => `      { find: ${JSON.stringify(spec)}, replacement: ${JSON.stringify(target)} }`)
  .join(",\n");

fs.writeFileSync(
  path.join(pkgDir, ".ecosync-vitest.config.mjs"),
  `import { mergeConfig, defineConfig } from "vitest/config";\n` +
    `import original from "./vitest.config.ts";\n` +
    `export default mergeConfig(original, defineConfig({\n` +
    `  resolve: {\n` +
    `    alias: [\n${alias}\n    ],\n` +
    `  },\n` +
    `}));\n`
);
NODE
    }
    run_storybook_test() {
      pkg="$1"
      test_file="$2"
      report="$3"
      cd "/workspace/repos/storybookjs/storybook/code/${pkg}"
      gen_vitest_config "$PWD"
      "$VITEST" run \
        --config .ecosync-vitest.config.mjs \
        "$test_file" \
        --reporter=default \
        --reporter=junit \
        --outputFile="/workspace/.ecosyncbench/test-reports/${report}.junit.xml"
    }
    status=0
    run_storybook_test addons/vitest src/node/test-manager.test.ts storybook-addon-vitest-hidden || status=$?
    run_storybook_test builders/builder-vite src/vite-config.test.ts storybook-builder-vite-hidden || status=$?
    run_storybook_test core src/core-server/utils/index-json.test.ts storybook-core-hidden || status=$?
    run_storybook_test frameworks/angular src/server/framework-preset-angular-cli.test.ts storybook-angular-hidden || status=$?
    exit "$status"
  '
}

case "${profile}" in
  mcp-hidden)
    run_mcp
    ;;
  storybook-hidden)
    run_storybook
    ;;
  *)
    echo "Unknown profile for storybookjs_mcp_storybook_365af0fa39df: ${profile}" >&2
    exit 2
    ;;
esac
