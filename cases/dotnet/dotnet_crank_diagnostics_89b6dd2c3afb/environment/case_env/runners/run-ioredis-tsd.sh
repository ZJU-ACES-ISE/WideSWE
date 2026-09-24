#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
shift
mkdir -p /workspace/.ecosyncbench/test-reports
deps_root="${ECOSYNC_DEPS_ROOT:-/opt/ecosync}"
if [[ ! -d node_modules && -d "$deps_root/deps/node_modules" ]]; then cp -a "$deps_root/deps/node_modules" node_modules; fi
npm run build
./node_modules/.bin/tsd --files "$@"
