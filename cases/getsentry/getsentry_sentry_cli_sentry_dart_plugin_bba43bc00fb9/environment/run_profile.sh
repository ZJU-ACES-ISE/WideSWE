#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
image="ecosyncbench/deps/getsentry-rust-dart:bba43bc00fb9"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/getsentry-rust-dart-bba43bc00fb9}"

if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
  docker build -t "$image" -f "$task_dir/environment/getsentry-rust-dart.Dockerfile" "$task_dir/environment"
fi

mkdir -p "$cache_root" "$workspace/.ecosyncbench/test-reports"
if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
  exit 0
fi

run_in_container() {
  docker run --rm \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    -w /workspace \
    -e HOME=/tmp/ecosync-home \
    -e CARGO_HOME=/cache/cargo \
    -e CARGO_TARGET_DIR=/cache/target \
    -e RUSTUP_HOME=/usr/local/rustup \
    -e PUB_CACHE=/cache/pub-cache \
    -e PATH=/usr/local/cargo/bin:/usr/lib/dart/bin:/cache/pub-cache/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    "$image" \
    bash -c "$*"
}

case "$profile" in
  sentry_cli-hidden)
    run_in_container '
      set -euo pipefail
      export PATH=/usr/local/cargo/bin:/usr/lib/dart/bin:/cache/pub-cache/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      cd /workspace/repos/getsentry/sentry-cli
      set +e
      cargo test --locked --test mod upload_dart_symbol_map -- --nocapture \
        | tee /workspace/.ecosyncbench/test-reports/sentry_cli-hidden.log
      code=${PIPESTATUS[0]}
      set -e
      python3 - <<'"'"'PY'"'"'
import re
import xml.etree.ElementTree as ET
from pathlib import Path

report = Path("/workspace/.ecosyncbench/test-reports/sentry_cli-hidden.log")
suite = ET.Element("testsuite", name="cargo-test")
pattern = re.compile(r"^test (?P<name>\\S+) \\.\\.\\. (?P<status>ok|FAILED|ignored)$")
for line in report.read_text(encoding="utf-8", errors="ignore").splitlines():
    match = pattern.match(line.strip())
    if not match:
        continue
    name = match.group("name")
    status = match.group("status")
    case = ET.SubElement(suite, "testcase", classname="cargo-test", name=name)
    if status == "FAILED":
        ET.SubElement(case, "failure", message="cargo test reported FAILED")
    elif status == "ignored":
        ET.SubElement(case, "skipped")

cases = list(suite)
failures = sum(1 for c in cases if c.find("failure") is not None)
skipped = sum(1 for c in cases if c.find("skipped") is not None)
suite.set("tests", str(len(cases)))
suite.set("failures", str(failures))
suite.set("errors", "0")
suite.set("skipped", str(skipped))
ET.ElementTree(suite).write(
    "/workspace/.ecosyncbench/test-reports/sentry_cli-hidden.xml",
    encoding="utf-8",
    xml_declaration=True,
)
PY
      exit "$code"
    '
    ;;
  sentry_dart_plugin-hidden)
    run_in_container '
      set -euo pipefail
      export PATH=/usr/local/cargo/bin:/usr/lib/dart/bin:/cache/pub-cache/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      cd /workspace/repos/getsentry/sentry-dart-plugin
      dart pub get --offline
      set +e
      dart test --reporter json test/dart_symbol_map_uploader_test.dart test/plugin_test.dart \
        | tee /workspace/.ecosyncbench/test-reports/sentry_dart_plugin-hidden.json
      code=${PIPESTATUS[0]}
      set -e
      python3 - <<'"'"'PY'"'"'
import json
import xml.etree.ElementTree as ET
from pathlib import Path

report = Path("/workspace/.ecosyncbench/test-reports/sentry_dart_plugin-hidden.json")
tests = {}
results = {}
errors = {}
for line in report.read_text(encoding="utf-8", errors="ignore").splitlines():
    if not line.strip():
        continue
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        continue
    event_type = event.get("type")
    if event_type == "testStart":
        test = event.get("test") or {}
        test_id = str(test.get("id"))
        tests[test_id] = test
    elif event_type == "testDone":
        test_id = str(event.get("testID"))
        results[test_id] = event
    elif event_type == "error":
        test_id = str(event.get("testID"))
        errors.setdefault(test_id, []).append(event)

suite = ET.Element("testsuite", name="dart-test")
for test_id, test in tests.items():
    if test.get("hidden"):
        continue
    result = results.get(test_id, {})
    status = result.get("result", "failure")
    name = test.get("name") or test_id
    case = ET.SubElement(suite, "testcase", classname="dart-test", name=name)
    if status == "skipped":
        ET.SubElement(case, "skipped")
    elif status != "success":
        message = "\\n".join(str(e.get("error") or e.get("message") or "") for e in errors.get(test_id, []))
        failure = ET.SubElement(case, "failure", message=message[:1000])
        failure.text = message

cases = list(suite)
failures = sum(1 for c in cases if c.find("failure") is not None)
skipped = sum(1 for c in cases if c.find("skipped") is not None)
suite.set("tests", str(len(cases)))
suite.set("failures", str(failures))
suite.set("errors", "0")
suite.set("skipped", str(skipped))
ET.ElementTree(suite).write(
    "/workspace/.ecosyncbench/test-reports/sentry_dart_plugin-hidden.xml",
    encoding="utf-8",
    xml_declaration=True,
)
PY
      exit "$code"
    '
    ;;
  *)
    echo "Unknown profile: $profile" >&2
    exit 2
    ;;
esac
