#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/prisma-prisma-engines-a2fd903b2a40}"
node_image="ecosyncbench/base/node:22-bookworm"
rust_image="rust:1.91-bookworm"

mkdir -p \
  "$workspace/.ecosyncbench/test-reports" \
  "$cache_root/node/corepack" \
  "$cache_root/node/npm" \
  "$cache_root/node/pnpm-home" \
  "$cache_root/node/pnpm-store" \
  "$cache_root/node/home" \
  "$cache_root/node/node_modules" \
  "$cache_root/node/locks" \
  "$cache_root/rust/cargo" \
  "$cache_root/rust/rustup" \
  "$cache_root/rust/target"

ensure_node_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$node_image" >/dev/null 2>&1; then
    docker build -t "$node_image" \
      -f /opt/ecosyncbench/benchmark/images/base/node-22-bookworm/Dockerfile \
      /opt/ecosyncbench/benchmark/images/base/node-22-bookworm
  fi
}

ensure_rust_image() {
  if ! docker image inspect "$rust_image" >/dev/null 2>&1; then
    docker pull "$rust_image"
  fi
}

dependency_fingerprint() {
  local workdir="$1"
  local repo_dir="$workspace/$workdir"
  local image_id
  image_id="$(docker image inspect --format '{{.Id}}' "$node_image")"
  {
    printf '%s\0%s\0' "$image_id" "$workdir"
    find "$repo_dir" \
      \( -path '*/node_modules' -o -path '*/.git' \) -prune -o \
      \( -name package.json -o -name package-lock.json -o -name pnpm-lock.yaml -o -name pnpm-workspace.yaml \) \
      -type f -print0 \
      | sort -z \
      | while IFS= read -r -d '' manifest; do
          printf '%s\0' "${manifest#"$repo_dir/"}"
          sha256sum "$manifest" | awk '{print $1}'
        done
  } | sha256sum | awk '{print $1}'
}

run_node() {
  local script="$1"
  local workdir="repos/prisma/prisma"
  local dependency_id node_modules_dir
  local pg_name="ecosync_${profile}_pg_$$_${RANDOM}"
  local net_name="ecosync_${profile}_net_$$_${RANDOM}"
  ensure_node_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$node_image" bash -lc 'node --version >/dev/null && corepack --version >/dev/null'
    return 0
  fi
  dependency_id="$(dependency_fingerprint "$workdir")"
  node_modules_dir="$cache_root/node/node_modules/$dependency_id"
  mkdir -p "$node_modules_dir"
  docker network create "$net_name" >/dev/null
  docker run -d --rm \
    --name "$pg_name" \
    --network "$net_name" \
    --tmpfs /var/lib/postgresql/data:rw,size=2g \
    -e POSTGRES_USER=prisma \
    -e POSTGRES_PASSWORD=prisma \
    -e POSTGRES_DB=tests \
    postgres:16-alpine >/dev/null
  cleanup_node_services() {
    docker rm -f "$pg_name" >/dev/null 2>&1 || true
    docker network rm "$net_name" >/dev/null 2>&1 || true
  }
  trap cleanup_node_services RETURN
  for _ in $(seq 1 90); do
    if docker exec "$pg_name" pg_isready -U prisma >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  set +e
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-5400}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    --network "$net_name" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/node-cache/home \
    -e CI=1 \
    -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    -e COREPACK_HOME=/node-cache/corepack \
    -e npm_config_cache=/node-cache/npm \
    -e PNPM_HOME=/node-cache/pnpm-home \
    -e PNPM_STORE_DIR=/node-cache/pnpm-store \
    -e ECOSYNC_NODE_MODULES_LOCK="/node-cache/locks/$dependency_id.lock" \
    -e PRISMA_SKIP_POSTINSTALL_GENERATE=1 \
    -e PRISMA_ENGINES_SKIP_DOWNLOAD=1 \
    -e MONGOMS_DISABLE_POSTINSTALL=1 \
    -e CYPRESS_INSTALL_BINARY=0 \
    -e PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    -e PUPPETEER_SKIP_DOWNLOAD=1 \
    -e TEST_POSTGRES_URI=postgres://prisma:prisma@"$pg_name":5432/tests \
    -e TEST_POSTGRES_URI_MIGRATE=postgres://prisma:prisma@"$pg_name":5432/tests-migrate \
    -e TEST_POSTGRES_SHADOWDB_URI_MIGRATE=postgres://prisma:prisma@"$pg_name":5432/tests-migrate-shadowdb \
    -e TEST_FUNCTIONAL_POSTGRES_URI=postgres://prisma:prisma@"$pg_name":5432/PRISMA_DB_NAME \
    -e PRISMA_HIDE_UPDATE_MESSAGE=true \
    -e JEST_JUNIT_OUTPUT_DIR=/workspace/.ecosyncbench/test-reports \
    -e JEST_JUNIT_OUTPUT_NAME=prisma-client-functional.xml \
    -e PATH=/node-cache/pnpm-home:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -v "$workspace:/workspace" \
    -v "$cache_root/node:/node-cache" \
    -v "$node_modules_dir:/workspace/$workdir/node_modules" \
    -w "/workspace/$workdir" \
    "$node_image" \
    bash -lc "$script"
  local code=$?
  set -e
  cleanup_node_services
  trap - RETURN
  return "$code"
}

run_rust() {
  ensure_rust_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$rust_image" bash -lc 'rustc --version >/dev/null && cargo --version >/dev/null'
    return 0
  fi
  local pg_name="ecosync_${profile}_pg_$$_${RANDOM}"
  local net_name="ecosync_${profile}_net_$$_${RANDOM}"
  docker network create "$net_name" >/dev/null
  docker run -d --rm \
    --name "$pg_name" \
    --network "$net_name" \
    --tmpfs /var/lib/postgresql/data:rw,size=2g \
    -e POSTGRES_USER=prisma \
    -e POSTGRES_PASSWORD=prisma \
    -e POSTGRES_DB=tests \
    postgres:16-alpine >/dev/null
  cleanup_rust_services() {
    docker rm -f "$pg_name" >/dev/null 2>&1 || true
    docker network rm "$net_name" >/dev/null 2>&1 || true
  }
  trap cleanup_rust_services RETURN
  local pg_ready=0
  for _ in $(seq 1 300); do
    if docker exec "$pg_name" pg_isready -U prisma >/dev/null 2>&1; then
      pg_ready=1
      break
    fi
    sleep 1
  done
  if [[ "$pg_ready" != "1" ]]; then
    echo "PostgreSQL did not become ready within 300 seconds" >&2
    return 1
  fi
  set +e
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-7200}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    --network "$net_name" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e TEST_DATABASE_URL="postgresql://prisma:prisma@$pg_name:5432/tests" \
    -e CARGO_HOME=/rust-cache/cargo \
    -e CARGO_NET_OFFLINE=false \
    -e RUSTUP_HOME=/rust-cache/rustup \
    -e RUSTUP_TOOLCHAIN=1.92.0 \
    -e CARGO_TARGET_DIR=/rust-target \
    -e RUST_BACKTRACE=1 \
    -e PATH=/rust-cache/cargo/bin:/usr/local/cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -v "$workspace:/workspace" \
    -v "$cache_root/rust/cargo:/rust-cache/cargo" \
    -v "$cache_root/rust/rustup:/rust-cache/rustup" \
    -v "$cache_root/rust/target:/rust-target" \
    -w /workspace/repos/prisma/prisma-engines \
    "$rust_image" \
    bash -c '
      set -euo pipefail
      export PATH=/rust-cache/cargo/bin:/usr/local/cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
      mkdir -p /tmp/ecosync-home /workspace/.ecosyncbench/test-reports /rust-cache/cargo /rust-cache/rustup /rust-target
      if ! /usr/local/cargo/bin/rustup toolchain list | grep -q "^1.92.0"; then
        /usr/local/cargo/bin/rustup toolchain install 1.92.0 --profile minimal
      fi
      export RUSTUP_TOOLCHAIN=1.92.0
      cat > /tmp/cargo-to-junit.py <<'"'"'PY'"'"'
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

log = Path(sys.argv[1])
xml = Path(sys.argv[2])
classname = sys.argv[3]
file_attr = sys.argv[4]
suite = ET.Element("testsuite", name=classname)
seen = set()
ansi = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
pattern = re.compile(r"^test (?P<name>.+?) \.\.\. (?P<status>ok|FAILED|fail|ignored)(?:\b|:)")
for line in log.read_text(encoding="utf-8", errors="ignore").splitlines():
    match = pattern.match(ansi.sub("", line).strip())
    if not match:
        continue
    name = match.group("name")
    if name in seen:
        continue
    seen.add(name)
    case = ET.SubElement(suite, "testcase", classname=classname, name=name, file=file_attr)
    status = match.group("status")
    if status in {"FAILED", "fail"}:
        ET.SubElement(case, "failure", message="cargo test reported FAILED")
    elif status == "ignored":
        ET.SubElement(case, "skipped")
cases = list(suite)
suite.set("tests", str(len(cases)))
suite.set("failures", str(sum(1 for case in cases if case.find("failure") is not None)))
suite.set("errors", "0")
suite.set("skipped", str(sum(1 for case in cases if case.find("skipped") is not None)))
xml.parent.mkdir(parents=True, exist_ok=True)
ET.ElementTree(suite).write(xml, encoding="utf-8", xml_declaration=True)
if not cases:
    print(f"no cargo test cases parsed from {log}", file=sys.stderr)
    sys.exit(88)
PY
      log=/workspace/.ecosyncbench/test-reports/prisma-engines-sql-migration-tests.log
      xml=/workspace/.ecosyncbench/test-reports/prisma-engines-sql-migration-tests.xml
      status=0
      (
        test_status=0
        for test_filter in \
          migrations::diff \
          migrations::migration_persistence_tests \
          migrations::schema_filter \
          migrations::soft_resets \
          schema_push
        do
          cargo test -p sql-migration-tests --test migration_tests "$test_filter" -- --test-threads=1 || test_status=$?
        done
        exit "$test_status"
      ) 2>&1 | tee "$log" || status=${PIPESTATUS[0]}
      python3 /tmp/cargo-to-junit.py "$log" "$xml" prisma-engines-sql-migration-tests schema-engine/sql-migration-tests || status=$?
      exit "$status"
    '
  local code=$?
  set -e
  cleanup_rust_services
  trap - RETURN
  return "$code"
}

case "$profile" in
  prisma-hidden)
    run_node '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /node-cache/home /node-cache/corepack /node-cache/npm /node-cache/pnpm-home /node-cache/pnpm-store
      corepack prepare pnpm@10.15.1 --activate
      exec 9>"$ECOSYNC_NODE_MODULES_LOCK"
      flock 9
      pnpm install --store-dir /node-cache/pnpm-store --frozen-lockfile
      flock -u 9
      pnpm --filter @prisma/config... run build
      status=0
      pnpm --filter @prisma/config exec vitest run \
        src/__tests__/loadConfigFromFile.test.ts \
        --reporter=junit \
        --outputFile=/workspace/.ecosyncbench/test-reports/prisma-config-loadConfigFromFile.xml || status=$?
      exit "$status"
    '
    ;;
  prisma_engines-hidden)
    run_rust
    ;;
  *)
    echo "Unknown profile for prisma_prisma_prisma_engines_a2fd903b2a40: $profile" >&2
    exit 2
    ;;
esac
