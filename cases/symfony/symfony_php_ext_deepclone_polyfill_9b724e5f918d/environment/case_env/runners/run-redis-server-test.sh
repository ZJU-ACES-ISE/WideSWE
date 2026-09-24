#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
single="${2:?single redis test required}"
mkdir -p /workspace/.ecosyncbench/test-reports
make -j"${ECOSYNC_MAKE_JOBS:-2}" BUILD_TLS=no
./runtest --single "$single" --timeout "${ECOSYNC_REDIS_TEST_TIMEOUT:-120}"
