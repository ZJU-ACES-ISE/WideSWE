#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
shift
mkdir -p /workspace/.ecosyncbench/test-reports
deps_root="${ECOSYNC_DEPS_ROOT:-/opt/ecosync}"
export PYTHONDONTWRITEBYTECODE=1
"$deps_root/venv/bin/python" -m pytest -q --no-cov --junitxml="/workspace/.ecosyncbench/test-reports/${profile}.xml" "$@"
