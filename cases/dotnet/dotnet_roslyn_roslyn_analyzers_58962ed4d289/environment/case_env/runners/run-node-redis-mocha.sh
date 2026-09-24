#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
shift
mkdir -p /workspace/.ecosyncbench/test-reports
deps_root="${ECOSYNC_DEPS_ROOT:-/opt/ecosync}"
if [[ -d "$deps_root/deps/node_modules" && ! -d node_modules ]]; then cp -a "$deps_root/deps/node_modules" node_modules; fi
if [[ -d "$deps_root/deps/packages" && ! -d packages/node_modules ]]; then
  for pkg_modules in "$deps_root"/deps/packages/*/node_modules; do
    [[ -d "$pkg_modules" ]] || continue
    pkg_name="$(basename "$(dirname "$pkg_modules")")"
    mkdir -p "packages/$pkg_name"
    cp -a "$pkg_modules" "packages/$pkg_name/node_modules"
  done
fi
npm run build
./node_modules/.bin/mocha -r tsx --reporter mocha-junit-reporter --reporter-options "mochaFile=/workspace/.ecosyncbench/test-reports/${profile}.xml,toConsole=false" --exit "$@"
