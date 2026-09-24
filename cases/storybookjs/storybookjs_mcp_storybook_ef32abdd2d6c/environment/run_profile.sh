#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
image="ecosyncbench/base/node-storybook:22"
cache_root="${ECOSYNC_STORYBOOKJS_CACHE_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/storybookjs-shared}}"

mkdir -p \
  "$workspace/.ecosyncbench/test-reports" \
  "$cache_root/corepack" \
  "$cache_root/npm" \
  "$cache_root/pnpm/home" \
  "$cache_root/pnpm/store" \
  "$cache_root/yarn" \
  "$cache_root/locks" \
  "$cache_root/home"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" \
      -f /opt/ecosyncbench/benchmark/images/base/node-22-bookworm/Dockerfile \
      /opt/ecosyncbench/benchmark/images/base/node-22-bookworm
  fi
}

run_node() {
  local workdir="$1"
  local script="$2"
  ensure_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image" bash -lc 'node --version >/dev/null && corepack --version >/dev/null'
    return 0
  fi
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-1800}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/node-cache/home \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    -e COREPACK_HOME=/node-cache/corepack \
    -e npm_config_cache=/node-cache/npm \
    -e PNPM_HOME=/node-cache/pnpm/home \
    -e PNPM_STORE_DIR=/node-cache/pnpm/store \
    -e YARN_ENABLE_GLOBAL_CACHE=1 \
    -e YARN_ENABLE_SCRIPTS=0 \
    -e YARN_GLOBAL_FOLDER=/node-cache/yarn/global \
    -e YARN_CACHE_FOLDER=/node-cache/yarn/cache \
    -e PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    -e PUPPETEER_SKIP_DOWNLOAD=1 \
    -e CYPRESS_INSTALL_BINARY=0 \
    -e PATH=/node-cache/pnpm/home:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -v "$workspace:/workspace" \
    -v "$cache_root:/node-cache" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -lc "$script"
}

case "$profile" in
  mcp-hidden)
    run_node "repos/storybookjs/mcp" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /node-cache/home /node-cache/corepack /node-cache/npm /node-cache/pnpm/home /node-cache/pnpm/store
      corepack prepare pnpm@10.19.0 --activate
      flock /node-cache/locks/mcp-install.lock \
        pnpm install --store-dir /node-cache/pnpm/store --frozen-lockfile
      pnpm vitest run \
        packages/mcp/src/utils/format-manifest.test.ts \
        packages/mcp/src/utils/get-manifest.test.ts \
        --reporter=default --reporter=junit \
        --outputFile=/workspace/.ecosyncbench/test-reports/mcp-hidden.xml
    '
    ;;
  storybook-hidden)
    run_node "repos/storybookjs/storybook" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /node-cache/home /node-cache/corepack /node-cache/yarn/global /node-cache/yarn/cache
      corepack prepare yarn@4.10.3 --activate
      cd code
      flock /node-cache/locks/storybook-install.lock yarn install --immutable
      cd renderers/react
      node <<\NODE
const fs = require("fs");
const path = require("path");

const codeRoot = "/workspace/repos/storybookjs/storybook/code";
const corePkg = JSON.parse(fs.readFileSync(path.join(codeRoot, "core/package.json"), "utf8"));
const aliases = [];

function existingSource(target) {
  let src = target.replace(/^\.\/dist\//, "core/src/");
  const candidates = [];
  if (src.endsWith("/index.js")) {
    const base = src.replace(/\/index\.js$/, "/index");
    candidates.push(`${base}.ts`, `${base}.tsx`);
  } else if (src.endsWith(".js")) {
    const base = src.replace(/\.js$/, "");
    candidates.push(`${base}.ts`, `${base}.tsx`, `${base}/index.ts`, `${base}/index.tsx`);
  }
  return candidates.find((candidate) => fs.existsSync(path.join(codeRoot, candidate)));
}

for (const [key, value] of Object.entries(corePkg.exports || {})) {
  if (key === "./package.json") continue;
  const target = typeof value === "string" ? value : value && value.default;
  if (typeof target !== "string" || !target.startsWith("./dist/")) continue;
  const src = existingSource(target);
  if (!src) continue;
  aliases.push({
    find: key === "." ? "storybook" : `storybook/${key.slice(2)}`,
    replacement: path.join(codeRoot, src),
  });
}

const aliasBlock = aliases
  .sort((a, b) => b.find.length - a.find.length)
  .map(({ find, replacement }) => `        { find: ${JSON.stringify(find)}, replacement: ${JSON.stringify(replacement)} }`)
  .join(",\n");

fs.writeFileSync(
  ".ecosync-vitest.setup.ts",
  `import { vi } from "vitest";\n\n` +
    `const upstreamWarn = console.warn;\n` +
    `vi.spyOn(console, "warn").mockImplementation((message, ...args) => {\n` +
    `  if (String(message).includes("[baseline-browser-mapping] The data in this module is over two months old")) return;\n` +
    `  return upstreamWarn(message, ...args);\n` +
    `});\n`
);

fs.writeFileSync(
  ".ecosync-vitest.config.mjs",
  `import { mergeConfig, defineConfig } from "vitest/config";\n` +
    `import original from "./vitest.config.ts";\n\n` +
    `const originalSetupFiles = Array.isArray(original.test?.setupFiles)\n` +
    `  ? original.test.setupFiles\n` +
    `  : original.test?.setupFiles\n` +
    `    ? [original.test.setupFiles]\n` +
    `    : [];\n\n` +
    `export default mergeConfig(\n` +
    `  original,\n` +
    `  defineConfig({\n` +
    `    test: {\n` +
    `      setupFiles: [...originalSetupFiles, "./.ecosync-vitest.setup.ts"],\n` +
    `    },\n` +
    `    resolve: {\n` +
    `      alias: [\n${aliasBlock}\n      ],\n` +
    `    },\n` +
    `  })\n` +
    `);\n`
);

fs.writeFileSync(
  path.join(codeRoot, "core/.ecosync-vitest.config.mjs"),
  `import { mergeConfig, defineConfig } from "vitest/config";\n` +
    `import original from "./vitest.config.ts";\n\n` +
    `export default mergeConfig(\n` +
    `  original,\n` +
    `  defineConfig({\n` +
    `    resolve: {\n` +
    `      alias: [\n${aliasBlock}\n      ],\n` +
    `    },\n` +
    `  })\n` +
    `);\n`
);
NODE
      status=0
      NODE_OPTIONS="--max_old_space_size=4096 --preserve-symlinks" ../../node_modules/.bin/vitest run \
        --config .ecosync-vitest.config.mjs \
        src/componentManifest/generator.test.ts \
        --testNamePattern="manifest contract" \
        --reporter=default --reporter=junit \
        --outputFile=/workspace/.ecosyncbench/test-reports/storybook-react-hidden.xml || status=$?

      cd ../../core
      NODE_OPTIONS="--max_old_space_size=4096 --preserve-symlinks" ../node_modules/.bin/vitest run \
        --config .ecosync-vitest.config.mjs \
        src/csf-tools/enrichCsf.test.ts \
        --reporter=default --reporter=junit \
        --outputFile=/workspace/.ecosyncbench/test-reports/storybook-core-hidden.xml || status=$?
      exit "$status"
    '
    ;;
  *)
    echo "Unknown profile for storybookjs_mcp_storybook_ef32abdd2d6c: $profile" >&2
    exit 2
    ;;
esac
