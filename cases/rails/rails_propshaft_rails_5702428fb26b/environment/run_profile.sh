#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
image="ecosyncbench/deps/rails-propshaft-ruby:5702428fb26b"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/rails-propshaft-rails-5702428fb26b}"

if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
  docker build -t "$image" -f "$task_dir/environment/rails-ruby.Dockerfile" "$task_dir/environment"
fi

mkdir -p "$cache_root/bundle" "$cache_root/locks" "$workspace/.ecosyncbench/test-reports"

cat > "$workspace/.ecosyncbench/minitest_to_junit.py" <<'PY'
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

log_path = Path(sys.argv[1])
xml_path = Path(sys.argv[2])
suite_name = sys.argv[3]
exit_code = int(sys.argv[4])

suite = ET.Element("testsuite", name=suite_name)
pattern = re.compile(r"^(?P<class>[^#\s]+)#(?P<name>.+?) = [0-9.]+ s = (?P<status>[.FE S])$")

for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
    match = pattern.match(line.strip())
    if not match:
        continue
    case = ET.SubElement(
        suite,
        "testcase",
        classname=match.group("class"),
        name=match.group("name").strip(),
    )
    status = match.group("status")
    if status in {"F", "E"}:
        tag = "failure" if status == "F" else "error"
        ET.SubElement(case, tag, message=f"minitest reported {status}")
    elif status == "S":
        ET.SubElement(case, "skipped")

if not list(suite) and exit_code != 0:
    case = ET.SubElement(suite, "testcase", classname=suite_name, name="collection")
    ET.SubElement(case, "error", message="test command failed before reporting test cases")

cases = list(suite)
suite.set("tests", str(len(cases)))
suite.set("failures", str(sum(1 for case in cases if case.find("failure") is not None)))
suite.set("errors", str(sum(1 for case in cases if case.find("error") is not None)))
suite.set("skipped", str(sum(1 for case in cases if case.find("skipped") is not None)))
xml_path.parent.mkdir(parents=True, exist_ok=True)
ET.ElementTree(suite).write(xml_path, encoding="utf-8", xml_declaration=True)
PY

run_in_container() {
  local workdir="$1"
  local bundle_key="$2"
  local command="$3"
  mkdir -p "$cache_root/bundle/$bundle_key"
  docker run --rm \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    -w "/workspace/$workdir" \
    -e HOME=/tmp/ecosync-home \
    -e BUNDLE_PATH="/cache/bundle/$bundle_key" \
    -e BUNDLE_APP_CONFIG="/cache/bundle/$bundle_key/.bundle" \
    -e ECOSYNC_BUNDLE_LOCK="/cache/locks/${bundle_key}.lock" \
    -e BUNDLE_JOBS="${BUNDLE_JOBS:-4}" \
    -e BUNDLE_RETRY="${BUNDLE_RETRY:-3}" \
    "$image" \
    bash -c "$command"
}

case "$profile" in
  propshaft-hidden)
    if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
      run_in_container "repos/rails/propshaft" "propshaft" '
        set -euo pipefail
        mkdir -p /tmp/ecosync-home
        exec 9>"$ECOSYNC_BUNDLE_LOCK"
        flock 9
        bundle install
        flock -u 9
      '
      exit 0
    fi
    run_in_container "repos/rails/propshaft" "propshaft" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      exec 9>"$ECOSYNC_BUNDLE_LOCK"
      flock 9
      bundle install
      flock -u 9
      set +e
      bundle exec ruby -Itest test/propshaft/asset_test.rb test/propshaft/server_test.rb --verbose \
        2>&1 | tee /workspace/.ecosyncbench/test-reports/propshaft-hidden.log
      code=${PIPESTATUS[0]}
      set -e
      python3 /workspace/.ecosyncbench/minitest_to_junit.py \
        /workspace/.ecosyncbench/test-reports/propshaft-hidden.log \
        /workspace/.ecosyncbench/test-reports/propshaft-hidden.xml \
        propshaft-hidden \
        "$code"
      exit "$code"
    '
    ;;
  rails-hidden)
    if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
      run_in_container "repos/rails/rails" "rails" '
        set -euo pipefail
        mkdir -p /tmp/ecosync-home
        exec 9>"$ECOSYNC_BUNDLE_LOCK"
        flock 9
        bundle install
        flock -u 9
      '
      exit 0
    fi
    run_in_container "repos/rails/rails" "rails" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      exec 9>"$ECOSYNC_BUNDLE_LOCK"
      flock 9
      bundle install
      flock -u 9
      set +e
      bundle exec ruby -Iactionpack/test -Iactivesupport/test actionpack/test/dispatch/static_test.rb --verbose \
        2>&1 | tee /workspace/.ecosyncbench/test-reports/rails-hidden.log
      code=${PIPESTATUS[0]}
      set -e
      python3 /workspace/.ecosyncbench/minitest_to_junit.py \
        /workspace/.ecosyncbench/test-reports/rails-hidden.log \
        /workspace/.ecosyncbench/test-reports/rails-hidden.xml \
        rails-hidden \
        "$code"
      exit "$code"
    '
    ;;
  *)
    echo "Unknown profile for rails_propshaft_rails_5702428fb26b: $profile" >&2
    exit 2
    ;;
esac
