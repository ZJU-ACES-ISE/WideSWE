#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
workspace="${ECOSYNC_WORKSPACE:?ECOSYNC_WORKSPACE is required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="ecosyncbench/deps/nodejs-node-undici:d2a368f889d6"
cache_root="${ECOSYNC_NODEJS_CACHE:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/node-cache/nodejs_node_undici_d2a368f889d6}}"
uid_gid="$(id -u):$(id -g)"

mkdir -p "$cache_root/npm" "$cache_root/ccache"

build_image() {
  docker build \
    -t "$image" \
    -f "$task_dir/environment/nodejs-node-undici.Dockerfile" \
    "$task_dir/environment"
}

docker_run() {
  local repo_path="$1"
  local script="$2"
  docker run --rm \
    --user "$uid_gid" \
    -e HOME=/tmp \
    -e npm_config_cache=/node-cache/npm \
    -e npm_config_audit=false \
    -e npm_config_fund=false \
    -e CCACHE_DIR=/node-cache/ccache \
    -e PATH="/usr/lib/ccache:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/node-cache" \
    -w "/workspace/$repo_path" \
    "$image" \
    bash -lc "$script"
}

if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-}" == "1" || "${ECOSYNC_DOCKER_FORCE_WARMUP:-}" == "1" ]]; then
  build_image
  exit 0
fi

if ! docker image inspect "$image" >/dev/null 2>&1; then
  build_image
fi

case "$profile" in
  node-hidden)
    docker_run "repos/nodejs/node" '
      set -euo pipefail
      mkdir -p .ecosyncbench/test-reports
      export CCACHE_DIR=/node-cache/ccache
      export PATH="/usr/lib/ccache:$PATH"
      if [[ ! -x out/Release/node ]]; then
        ./configure --ninja --without-npm
        ninja -C out/Release -j"${ECOSYNC_NODE_BUILD_JOBS:-8}" node
      fi
      python3 tools/test.py \
        -p tap \
        --logfile .ecosyncbench/test-reports/node-hidden.tap \
        --shell ./out/Release/node \
        -j"${ECOSYNC_NODE_TEST_JOBS:-2}" \
        parallel/test-http-server-non-utf8-header
      python3 - .ecosyncbench/test-reports/node-hidden.tap .ecosyncbench/test-reports/node-hidden.xml <<'"'"'PY'"'"'
import html
import re
import sys
from pathlib import Path

tap_path = Path(sys.argv[1])
xml_path = Path(sys.argv[2])
lines = tap_path.read_text(encoding="utf-8", errors="replace").splitlines()
case_re = re.compile(r"^(not ok|ok)\s+(\d+)\s+(.+?)(?:\s+#\s*(.*))?$", re.IGNORECASE)
cases = []
current = None
diag = []

def finish_current():
    global current, diag
    if current is not None:
        current["diagnostic"] = "\n".join(diag).strip()
        cases.append(current)
    current = None
    diag = []

for line in lines:
    match = case_re.match(line.strip())
    if match:
        finish_current()
        status_word, _number, name, directive = match.groups()
        directive = directive or ""
        skipped = directive.lower().startswith("skip")
        failed = status_word.lower() == "not ok"
        current = {
            "name": name.strip(),
            "failed": failed and not skipped,
            "skipped": skipped,
            "directive": directive,
        }
    elif current is not None:
        diag.append(line)
finish_current()

failures = sum(1 for item in cases if item["failed"])
skipped = sum(1 for item in cases if item["skipped"])
xml_path.parent.mkdir(parents=True, exist_ok=True)
parts = [
    f'"'"'<testsuite name="node-hidden" tests="{len(cases)}" failures="{failures}" errors="0" skipped="{skipped}">'"'"'
]
for item in cases:
    name = html.escape(item["name"], quote=True)
    parts.append(f'"'"'  <testcase classname="node-hidden" name="{name}" file="{name}.js">'"'"')
    if item["skipped"]:
        parts.append(f'"'"'    <skipped message="{html.escape(item["directive"], quote=True)}" />'"'"')
    if item["failed"]:
        diagnostic = html.escape(item.get("diagnostic") or "test failed", quote=False)
        parts.append(f'"'"'    <failure message="TAP failure">{diagnostic}</failure>'"'"')
    parts.append("  </testcase>")
parts.append("</testsuite>")
xml_path.write_text("\n".join(parts) + "\n", encoding="utf-8")
if not cases:
    raise SystemExit("no TAP test cases were parsed from Node tools/test.py output")
PY
    '
    ;;

  undici-hidden)
    docker_run "repos/nodejs/undici" '
      set -euo pipefail
      mkdir -p .ecosyncbench/test-reports
      node --test \
        --test-reporter=junit \
        --test-reporter-destination=.ecosyncbench/test-reports/undici-hidden.xml \
        test/node-test/util.js
    '
    ;;

  *)
    echo "Unknown profile: $profile" >&2
    exit 2
    ;;
esac
