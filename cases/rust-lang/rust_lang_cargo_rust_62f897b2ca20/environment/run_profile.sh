#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/rust-lang-cargo-rust-62f897b2ca20}"
image="ecosyncbench/deps/rust-lang-cargo-rust:62f897b2ca20"

mkdir -p "$workspace/.ecosyncbench/test-reports" "$cache_root/cargo" "$cache_root/target" "$cache_root/locks"

write_helpers() {
  mkdir -p "$workspace/.ecosyncbench"
  cargo_to_junit > "$workspace/.ecosyncbench/cargo_to_junit.py"
  cat > "$workspace/.ecosyncbench/unittest_to_junit.py" <<'PY'
import sys
import time
import traceback
import importlib.util
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []
        self.starts = {}

    def startTest(self, test):
        self.starts[test.id()] = time.time()
        super().startTest(test)

    def addSuccess(self, test):
        self.records.append((test, "success", None, time.time() - self.starts.get(test.id(), time.time())))
        super().addSuccess(test)

    def addFailure(self, test, err):
        self.records.append((test, "failure", self._exc_info_to_string(err, test), time.time() - self.starts.get(test.id(), time.time())))
        super().addFailure(test, err)

    def addError(self, test, err):
        self.records.append((test, "error", self._exc_info_to_string(err, test), time.time() - self.starts.get(test.id(), time.time())))
        super().addError(test, err)

    def addSkip(self, test, reason):
        self.records.append((test, "skipped", reason, time.time() - self.starts.get(test.id(), time.time())))
        super().addSkip(test, reason)


module_or_name = sys.argv[1]
xml_path = Path(sys.argv[2])
suite_name = sys.argv[3]
loader = unittest.defaultTestLoader
if module_or_name.endswith(".py") or "/" in module_or_name:
    test_path = Path(module_or_name)
    module_name = test_path.stem
    spec = importlib.util.spec_from_file_location(module_name, test_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.path.insert(0, str(test_path.parent.resolve()))
    spec.loader.exec_module(module)
    suite = loader.loadTestsFromModule(module)
else:
    suite = loader.loadTestsFromName(module_or_name)
runner = unittest.TextTestRunner(resultclass=RecordingResult, verbosity=2)
result = runner.run(suite)

xml_suite = ET.Element("testsuite", name=suite_name)
for test, status, detail, duration in result.records:
    test_id = test.id()
    case = ET.SubElement(
        xml_suite,
        "testcase",
        classname=".".join(test_id.split(".")[:-1]),
        name=test_id.split(".")[-1],
        time=f"{duration:.6f}",
    )
    if status == "failure":
        ET.SubElement(case, "failure", message="unittest failure").text = detail
    elif status == "error":
        ET.SubElement(case, "error", message="unittest error").text = detail
    elif status == "skipped":
        ET.SubElement(case, "skipped", message=str(detail))

tests = list(xml_suite)
xml_suite.set("tests", str(len(tests)))
xml_suite.set("failures", str(sum(1 for case in tests if case.find("failure") is not None)))
xml_suite.set("errors", str(sum(1 for case in tests if case.find("error") is not None)))
xml_suite.set("skipped", str(sum(1 for case in tests if case.find("skipped") is not None)))
xml_path.parent.mkdir(parents=True, exist_ok=True)
ET.ElementTree(xml_suite).write(xml_path, encoding="utf-8", xml_declaration=True)
raise SystemExit(0 if result.wasSuccessful() else 1)
PY
}

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$task_dir/environment/rust-lang.Dockerfile" "$task_dir/environment"
  fi
}

run_in_container() {
  local workdir="$1"
  local target_subdir="$2"
  local command="$3"
  ensure_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image" bash -c 'export PATH=/usr/local/cargo/bin:$PATH; cargo --version >/dev/null && python3 --version >/dev/null'
    return 0
  fi
  mkdir -p "$cache_root/target/$target_subdir"
  timeout "${ECOSYNC_PROFILE_TIMEOUT:-3600}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e CARGO_HOME=/cache/cargo \
    -e CARGO_TARGET_DIR="/cache/target/$target_subdir" \
    -e ECOSYNC_CARGO_TARGET_LOCK="/cache/locks/${target_subdir}.lock" \
    -e RUSTC_BOOTSTRAP=1 \
    -e RUST_BACKTRACE=1 \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -c "set -euo pipefail; exec 9>\"\$ECOSYNC_CARGO_TARGET_LOCK\"; flock 9; mkdir -p /cache/cargo/bin; for tool in cargo rustc rustdoc rustfmt rustup; do ln -sf /usr/local/cargo/bin/\$tool /cache/cargo/bin/\$tool; done; export PATH=/cache/cargo/bin:/usr/local/cargo/bin:\$PATH; $command"
}

cargo_to_junit() {
  cat <<'PY'
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

log_path = Path(sys.argv[1])
xml_path = Path(sys.argv[2])
suite_name = sys.argv[3]
file_name = sys.argv[4]
suite = ET.Element("testsuite", name=suite_name)
pattern = re.compile(r"^test (?P<name>\S+) \.\.\. (?P<status>ok|FAILED|ignored)$")
for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
    match = pattern.match(line.strip())
    if not match:
        continue
    case = ET.SubElement(
        suite,
        "testcase",
        classname=file_name,
        name=match.group("name"),
        file=file_name,
    )
    status = match.group("status")
    if status == "FAILED":
        ET.SubElement(case, "failure", message="cargo test reported FAILED")
    elif status == "ignored":
        ET.SubElement(case, "skipped")

cases = list(suite)
failures = sum(1 for case in cases if case.find("failure") is not None)
skipped = sum(1 for case in cases if case.find("skipped") is not None)
suite.set("tests", str(len(cases)))
suite.set("failures", str(failures))
suite.set("errors", "0")
suite.set("skipped", str(skipped))
ET.ElementTree(suite).write(xml_path, encoding="utf-8", xml_declaration=True)
PY
}

case "$profile" in
  cargo-hidden)
    write_helpers
    run_in_container "repos/rust-lang/cargo" "cargo-${profile}" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      log=/workspace/.ecosyncbench/test-reports/cargo-hidden.log
      xml=/workspace/.ecosyncbench/test-reports/cargo-hidden.xml
      set +e
      cargo test --test testsuite warning_override -- --nocapture 2>&1 | tee "$log"
      status=${PIPESTATUS[0]}
      set -e
      python3 /workspace/.ecosyncbench/cargo_to_junit.py \
        "$log" "$xml" "cargo-warning-override" "tests/testsuite/warning_override.rs"
      test -s "$xml"
      exit "$status"
    '
    ;;
  rust-hidden)
    write_helpers
    run_in_container "repos/rust-lang/rust" "rust-${profile}" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      host="$(rustc -vV | awk "/^host:/ {print \$2}")"
      mkdir -p "build/${host}/stage0/bin"
      for tool in cargo rustc rustdoc rustfmt; do
        ln -sf "/cache/cargo/bin/${tool}" "build/${host}/stage0/bin/${tool}"
      done
      python3 /workspace/.ecosyncbench/unittest_to_junit.py \
        src/bootstrap/bootstrap_test.py \
        /workspace/.ecosyncbench/test-reports/rust-bootstrap-hidden.xml \
        rust-bootstrap-hidden \
        2>&1 | tee /workspace/.ecosyncbench/test-reports/rust-bootstrap-hidden.log
    '
    ;;
  *)
    echo "Unknown profile for rust_lang_cargo_rust_62f897b2ca20: $profile" >&2
    exit 2
    ;;
esac
