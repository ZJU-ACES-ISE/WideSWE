#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
workspace="${ECOSYNC_WORKSPACE:?ECOSYNC_WORKSPACE is required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="ecosyncbench/deps/huggingface-xet:ca639219c2b5"
cache_root="${ECOSYNC_HUGGINGFACE_XET_CACHE:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/huggingface-xet-cache/ca639219c2b5}}"
uid_gid="$(id -u):$(id -g)"

mkdir -p "$cache_root/pip" "$cache_root/cargo" "$cache_root/rust-target" "$cache_root/locks" "$cache_root/venvs"

dependency_fingerprint() {
  local namespace="$1"
  local repo_root="$2"
  shift 2
  {
    printf '%s\0' "$namespace"
    local relative
    for relative in "$@"; do
      printf '%s\0' "$relative"
      if [[ -f "$repo_root/$relative" ]]; then
        sha256sum "$repo_root/$relative" | cut -d ' ' -f1
      else
        printf '<missing>\n'
      fi
    done
  } | sha256sum | cut -c1-20
}

build_image() {
  docker build \
    -t "$image" \
    -f "$task_dir/environment/huggingface-xet.Dockerfile" \
    "$task_dir/environment"
}

docker_run() {
  local repo_path="$1"
  local script="$2"
  docker run --rm \
    --user "$uid_gid" \
    -e HOME=/tmp/ecosync-home \
    -e PIP_CACHE_DIR=/cache/pip \
    -e CARGO_HOME=/cache/cargo \
    -e CARGO_TARGET_DIR=/cache/rust-target \
    -e HUB_VENV_FINGERPRINT="$hub_venv_fingerprint" \
    -e PATH="/workspace/$repo_path/.venv/bin:/usr/local/cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    -v "$hub_venv:/workspace/repos/huggingface/huggingface_hub/.venv" \
    -w "/workspace/$repo_path" \
    "$image" \
    bash -c "$script"
}

if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-}" == "1" || "${ECOSYNC_DOCKER_FORCE_WARMUP:-}" == "1" ]]; then
  build_image
  exit 0
fi

if ! docker image inspect "$image" >/dev/null 2>&1; then
  build_image
fi

image_id="$(docker image inspect "$image" --format '{{.Id}}')"
hub_venv_fingerprint="$(dependency_fingerprint "$image_id" "$workspace/repos/huggingface/huggingface_hub" pyproject.toml setup.py)"
hub_venv="$cache_root/venvs/huggingface-hub-$hub_venv_fingerprint"
mkdir -p "$hub_venv"

case "$profile" in
  huggingface_hub-hidden)
    docker_run "repos/huggingface/huggingface_hub" '
      set -euo pipefail
      mkdir -p .ecosyncbench/test-reports
      exec 9>"/cache/locks/huggingface-hub-${HUB_VENV_FINGERPRINT}.lock"
      flock 9
      if [[ ! -x .venv/bin/python || ! -f .venv/.ecosyncbench-deps-ready ]]; then
        python3 -m venv --clear .venv
        . .venv/bin/activate
        python -m pip install --upgrade pip wheel
        python -m pip install -e ".[testing,hf_xet]" pytest-timeout==2.4.0
        touch .venv/.ecosyncbench-deps-ready
      fi
      flock -u 9
      . .venv/bin/activate
      python -m pip install --no-deps --no-build-isolation -e .
      set +e
      timeout --signal=TERM --kill-after=15s 420s python - <<PY
import os
import sys

import pytest

selected_tests = [
    "tests/test_xet_download.py::TestXetFileDownload::test_xet_get_called_when_xet_metadata_present",
    "tests/test_xet_download.py::TestXetFileDownload::test_backward_compatibility_no_xet_metadata",
    "tests/test_xet_download.py::TestXetFileDownload::test_fallback_to_http_when_xet_not_available",
    "tests/test_xet_download.py::TestXetFileDownload::test_use_xet_when_available",
    "tests/test_xet_download.py::TestXetFileDownload::test_request_headers_passed_to_download_files",
    "tests/test_xet_upload.py::TestXetUpload::test_transfers_to_xet_when_server_returns_xet",
    "tests/test_xet_upload.py::TestXetUpload::test_transfers_bytesio_renegotiates_to_lfs_when_server_returns_xet",
    "tests/test_xet_upload.py::TestXetUpload::test_request_headers_passed_to_upload_files",
    "tests/test_xet_upload.py::TestXetUpload::test_request_headers_passed_to_upload_bytes",
]
code = pytest.main(
    [
        "-q",
        "-o",
        "addopts=",
        "--timeout=90",
        "--timeout-method=signal",
        *selected_tests,
        "--junitxml=.ecosyncbench/test-reports/huggingface_hub-hidden.xml",
    ]
)
sys.stdout.flush()
sys.stderr.flush()
os._exit(int(code))
PY
      status=$?
      set -e
      exit "$status"
    '
    ;;

  xet_core-hidden)
    docker_run "repos/huggingface/xet-core" '
      set -euo pipefail
      mkdir -p .ecosyncbench/test-reports
      log=.ecosyncbench/test-reports/xet_core-hidden.log
      xml=.ecosyncbench/test-reports/xet_core-hidden.xml
      tests=(
        download_with_existing_user_agent
        download_without_user_agent
        upload_apis_accept_request_headers
      )
      statuses=()
      : > "$log"
      build_status=0
      cargo build --manifest-path hf_xet/Cargo.toml --features pyo3/extension-module >> "$log" 2>&1 || build_status=$?
      module_dir=.ecosyncbench/hf_xet_contract_module
      mkdir -p "$module_dir"
      ln -sf "$CARGO_TARGET_DIR/debug/libhf_xet.so" "$module_dir/hf_xet.so"
      for test_name in "${tests[@]}"; do
        status="$build_status"
        if [[ "$status" == "0" ]]; then
          HF_XET_CLIENT_RETRY_MAX_ATTEMPTS=1 HF_XET_CLIENT_RETRY_BASE_DELAY=1ms PYTHONPATH="$PWD/$module_dir" timeout --signal=TERM --kill-after=5s 30s python3 hf_xet/tests/request_headers_contract.py "$test_name" >> "$log" 2>&1 || status=$?
        fi
        statuses+=("$status")
      done
      python3 - "$log" "$xml" "${tests[@]}" -- "${statuses[@]}" <<'"'"'PY'"'"'
import html
import sys
from pathlib import Path

separator = sys.argv.index("--")
log_path = Path(sys.argv[1])
xml_path = Path(sys.argv[2])
test_names = sys.argv[3:separator]
statuses = [int(value) for value in sys.argv[separator + 1 :]]
log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
xml_lines = [
    f"<testsuite name=\"xet_core-hidden\" tests=\"{len(test_names)}\" failures=\"{sum(status != 0 for status in statuses)}\" errors=\"0\" skipped=\"0\">"
]
for name, status in zip(test_names, statuses):
    xml_lines.append(f"  <testcase classname=\"xet_core-hidden\" name=\"{name}\">")
    if status != 0:
        xml_lines.append(f"    <failure message=\"test failed\">{html.escape(log_text[-12000:], quote=False)}</failure>")
    xml_lines.append("  </testcase>")
xml_lines.append("</testsuite>")
xml_path.write_text(
    "\n".join(xml_lines) + "\n",
    encoding="utf-8",
)
sys.exit(1 if any(statuses) else 0)
PY
    '
    ;;

  *)
    echo "Unknown profile: $profile" >&2
    exit 2
    ;;
esac
