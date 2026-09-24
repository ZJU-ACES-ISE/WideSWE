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
node --expose-gc --max-old-space-size=4096 --experimental-vm-modules \
  ./node_modules/jest-cli/bin/jest \
  --ci --colors=false --json --outputFile="/workspace/.ecosyncbench/test-reports/${profile}.json" "$@"
