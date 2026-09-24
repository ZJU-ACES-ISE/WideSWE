#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
shift
mkdir -p /workspace/.ecosyncbench/test-reports
deps_root="${ECOSYNC_DEPS_ROOT:-/opt/ecosync}"
export NODE_ENV=test
if [[ ! -d node_modules && -d "$deps_root/deps/node_modules" ]]; then
  cp -a "$deps_root/deps/node_modules" node_modules
fi
if [[ -f package.json ]] && node -e "const p=require('./package.json'); process.exit(p.scripts&&p.scripts.version?0:1)" >/dev/null 2>&1; then
  npm run version >/tmp/ecosync-version.log 2>&1 || true
fi
./node_modules/.bin/jest --ci --colors=false --json --outputFile="/workspace/.ecosyncbench/test-reports/${profile}.json" "$@"
