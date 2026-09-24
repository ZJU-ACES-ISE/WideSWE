#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_root="/opt/ecosyncbench"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$repo_root}" && pwd)"
image="ecosyncbench/deps/redis-jedis-streams:87d94ea7e2d2"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/redis-jedis-streams-87d94ea7e2d2}"
uid="${ECOSYNC_UID:-$(id -u)}"
gid="${ECOSYNC_GID:-$(id -g)}"

ensure_base_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect ecosyncbench/base/java-maven:17-bookworm >/dev/null 2>&1; then
    docker build -t ecosyncbench/base/java-maven:17-bookworm \
      -f "$repo_root/benchmark/images/base/java-maven-17-bookworm/Dockerfile" \
      "$repo_root"
  fi
}

ensure_image() {
  ensure_base_image
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$task_dir/environment/redis-jedis-streams.Dockerfile" "$task_dir/environment"
  fi
}

docker_run() {
  local workdir="$1"
  shift
  mkdir -p "$workspace/.ecosyncbench/test-reports" "$cache_root/home" "$cache_root/m2"
  chmod 0777 "$cache_root" "$cache_root/home" "$cache_root/m2" >/dev/null 2>&1 || true
  docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    --user "$uid:$gid" \
    -e HOME=/cache/home \
    -e USER=ecosync \
    -e LOGNAME=ecosync \
    -e TMPDIR=/tmp/ecosync \
    -e MAVEN_OPTS="-Dmaven.repo.local=/cache/m2 -Xmx2g" \
    -e ECOSYNC_MAKE_JOBS="${ECOSYNC_MAKE_JOBS:-2}" \
    -e ECOSYNC_REDIS_TEST_TIMEOUT="${ECOSYNC_REDIS_TEST_TIMEOUT:-420}" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    -w "$workdir" \
    --entrypoint bash \
    "$image" \
    -lc "$*"
}

ensure_image
if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
  docker_run /workspace 'mvn -version >/dev/null && python3 --version >/dev/null && tclsh <<< "puts ok" >/dev/null'
  exit 0
fi

case "$profile" in
  redis-hidden)
    docker_run /workspace/repos/redis/redis \
      'set -euo pipefail
       mkdir -p /workspace/.ecosyncbench/test-reports "$TMPDIR"
       make -j"${ECOSYNC_MAKE_JOBS}" BUILD_TLS=no MALLOC=libc
       make -C tests/modules -j"${ECOSYNC_MAKE_JOBS}" cmdintrospection.so
       status=0
       ./runtest --single unit/type/stream --timeout "${ECOSYNC_REDIS_TEST_TIMEOUT}" \
         2>&1 | tee /workspace/.ecosyncbench/test-reports/redis-stream.log || status=$?
       ./runtest --single unit/type/stream-cgroups --timeout "${ECOSYNC_REDIS_TEST_TIMEOUT}" \
         2>&1 | tee /workspace/.ecosyncbench/test-reports/redis-stream-cgroups.log || status=$?
       ./runtest --single unit/moduleapi/cmdintrospection --timeout "${ECOSYNC_REDIS_TEST_TIMEOUT}" \
         2>&1 | tee /workspace/.ecosyncbench/test-reports/redis-cmdintrospection.log || status=$?
       python3 - <<'"'"'PY'"'"'
import pathlib
import re
import xml.etree.ElementTree as ET

report_dir = pathlib.Path("/workspace/.ecosyncbench/test-reports")
logs = [
    ("unit/type/stream", report_dir / "redis-stream.log"),
    ("unit/type/stream-cgroups", report_dir / "redis-stream-cgroups.log"),
    ("unit/moduleapi/cmdintrospection", report_dir / "redis-cmdintrospection.log"),
]

cases = []
for suite, path in logs:
    text = path.read_text(errors="replace") if path.exists() else ""
    # Redis runtest emits lines like "[ok]: test name" and "[err]: test name".
    for match in re.finditer(r"^\[(ok|err|skip)\]:\s*(.+?)\s*$", text, re.MULTILINE):
        state, name = match.groups()
        status = {"ok": "passed", "err": "failed", "skip": "skipped"}[state]
        clean_name = re.sub(r"\s+\(\d+\s+ms\)$", "", name.strip())
        cases.append((suite, clean_name, status, ""))
    if not any(item[0] == suite for item in cases):
        # Preserve a deterministic collection-level row if this Redis version
        # fails before individual Tcl tests are reported.
        failed = (
            "!!! WARNING The following tests failed" in text
            or re.search(r"^\[(err|exception)\]:", text, re.MULTILINE) is not None
        )
        cases.append((suite, suite, "failed" if failed else "passed", text[-4000:]))

testsuite = ET.Element(
    "testsuite",
    name="redis-hidden",
    tests=str(len(cases)),
    failures=str(sum(1 for _, _, status, _ in cases if status == "failed")),
    errors="0",
    skipped=str(sum(1 for _, _, status, _ in cases if status == "skipped")),
)
for suite, name, status, detail in cases:
    node = ET.SubElement(
        testsuite,
        "testcase",
        classname=suite,
        name=name,
        time="0",
    )
    if status == "failed":
        failure = ET.SubElement(node, "failure", message="redis runtest failed")
        failure.text = detail
    elif status == "skipped":
        ET.SubElement(node, "skipped")

ET.ElementTree(testsuite).write(report_dir / "redis-hidden.xml", encoding="utf-8", xml_declaration=True)
PY
       exit "$status"'
    ;;
  jedis-hidden)
    docker_run /workspace \
      'set -euo pipefail
       mkdir -p /workspace/.ecosyncbench/test-reports "$TMPDIR/jedis-redis"
       cd /workspace/repos/redis/redis
       make -j"${ECOSYNC_MAKE_JOBS}" BUILD_TLS=no MALLOC=libc

       cleanup() {
         for port in 6379; do
           /workspace/repos/redis/redis/src/redis-cli -p "$port" -a foobared shutdown nosave >/dev/null 2>&1 || true
         done
       }
       trap cleanup EXIT
       cleanup

       rm -rf "$TMPDIR/jedis-redis"
       mkdir -p "$TMPDIR/jedis-redis"
       cat > "$TMPDIR/jedis-redis/redis-6379.conf" <<EOF
port 6379
bind 127.0.0.1
protected-mode no
requirepass foobared
user acljedis on allcommands allkeys >fizzbuzz
save ""
appendonly no
daemonize yes
dir $TMPDIR/jedis-redis
pidfile $TMPDIR/jedis-redis/redis-6379.pid
logfile $TMPDIR/jedis-redis/redis-6379.log
EOF
       /workspace/repos/redis/redis/src/redis-server "$TMPDIR/jedis-redis/redis-6379.conf"
       for i in $(seq 1 100); do
         if /workspace/repos/redis/redis/src/redis-cli -p 6379 -a foobared ping >/dev/null 2>&1; then
           break
         fi
         sleep 0.2
       done
       /workspace/repos/redis/redis/src/redis-cli -p 6379 -a foobared ping >/dev/null

       export PATH=/workspace/repos/redis/redis/src:$PATH
       cd /workspace/repos/redis/jedis
       mvn -B \
         -Dredis-hosts=localhost \
         -Dformatter.skip=true \
         -Dlicense.skip=true \
         -Dcheckstyle.skip=true \
         -Dtest=redis.clients.jedis.commands.jedis.StreamDeletionContractTest \
         test 2>&1 | tee /workspace/.ecosyncbench/test-reports/jedis-hidden.log'
    ;;
  *)
    echo "Unknown profile for redis_jedis_redis_87d94ea7e2d2: $profile" >&2
    exit 2
    ;;
esac
