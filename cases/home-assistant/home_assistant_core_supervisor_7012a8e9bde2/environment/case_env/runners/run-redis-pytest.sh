#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
shift
mkdir -p /workspace/.ecosyncbench/test-reports
deps_root="${ECOSYNC_DEPS_ROOT:-/opt/ecosync}"
"$deps_root/venv/bin/python" -m pip install -e . --no-deps >/dev/null
redis_dir=/workspace/repos/redis/redis
if [[ ! -x "$redis_dir/src/redis-server" ]]; then
  make -C "$redis_dir" -j"${ECOSYNC_MAKE_JOBS:-2}" BUILD_TLS=no
fi
"$redis_dir/src/redis-server" --port 6379 --save "" --appendonly no --daemonize yes
trap '"$redis_dir/src/redis-cli" -p 6379 shutdown nosave >/dev/null 2>&1 || true' EXIT
for _ in $(seq 1 50); do "$redis_dir/src/redis-cli" -p 6379 ping >/dev/null 2>&1 && break; sleep 0.1; done
"$deps_root/venv/bin/python" -m pytest -q --redis-url redis://127.0.0.1:6379/0 --junitxml="/workspace/.ecosyncbench/test-reports/${profile}.xml" "$@"
