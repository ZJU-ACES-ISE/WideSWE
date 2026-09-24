#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
focus="${2:?ginkgo focus required}"
mkdir -p /workspace/.ecosyncbench/test-reports
deps_root="${ECOSYNC_DEPS_ROOT:-/opt/ecosync}"
export GOMODCACHE="${GOMODCACHE:-$deps_root/gomodcache}"
export GOPATH="${GOPATH:-$deps_root/gopath}"
export PATH="/usr/local/go/bin:$PATH"
redis_dir=/workspace/repos/redis/redis
if [[ ! -x "$redis_dir/src/redis-server" ]]; then
  make -C "$redis_dir" -j"${ECOSYNC_MAKE_JOBS:-2}" BUILD_TLS=no
fi
"$redis_dir/src/redis-server" --port 6379 --save "" --appendonly no --daemonize yes
trap '"$redis_dir/src/redis-cli" -p 6379 shutdown nosave >/dev/null 2>&1 || true' EXIT
for _ in $(seq 1 50); do "$redis_dir/src/redis-cli" -p 6379 ping >/dev/null 2>&1 && break; sleep 0.1; done
REDIS_VERSION=8.8 RE_CLUSTER=true REDIS_PORT=6379 go test -count=1 -run TestGinkgoSuite -ginkgo.focus "$focus" . | tee "/workspace/.ecosyncbench/test-reports/${profile}.log"
