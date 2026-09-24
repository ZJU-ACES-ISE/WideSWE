#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"

task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
image="ecosyncbench/deps/paddle-paconvert-py312:d3f5712cbcf6"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/paddlepaddle-paconvert-paddle-d3f5712cbcf6}"
paddle_compat_source="$workspace/repos/PaddlePaddle/Paddle/python/paddle/compat.py"
paddle_compat_target="/usr/local/lib/python3.12/site-packages/paddle/compat.py"
paddle_tensor_compat_source="$workspace/repos/PaddlePaddle/Paddle/python/paddle/tensor/compat.py"
paddle_tensor_compat_target="/usr/local/lib/python3.12/site-packages/paddle/tensor/compat.py"
uid="${ECOSYNC_UID:-$(id -u)}"
gid="${ECOSYNC_GID:-$(id -g)}"

mkdir -p "$cache_root/pip" "$cache_root/home" "$workspace/.ecosyncbench/test-reports"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$task_dir/environment/paddle-paconvert-py312.Dockerfile" "$task_dir/environment"
  fi
}

run_python() {
  local workdir="$1"
  local script="$2"
  ensure_image
  for source in "$paddle_compat_source" "$paddle_tensor_compat_source"; do
    if [[ ! -f "$source" ]]; then
      echo "Missing Paddle overlay source: $source" >&2
      exit 2
    fi
  done
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-2400}" docker run --rm \
    --network host \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "$uid:$gid" \
    -e HOME=/cache/home \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e PIP_CACHE_DIR=/cache/pip \
    -e PYTHONNOUSERSITE=1 \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    -v "$paddle_compat_source:$paddle_compat_target:ro" \
    -v "$paddle_tensor_compat_source:$paddle_tensor_compat_target:ro" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -lc "$script"
}

run_paconvert() {
  run_python "repos/PaddlePaddle/PaConvert" "
    set -euo pipefail
    mkdir -p /cache/home /workspace/.ecosyncbench/test-reports
    export PYTHONPATH=\"\$PWD:\$PWD/tests:\${PYTHONPATH:-}\"
    python -m pytest -q \
      tests/test_nn_functional_pad.py \
      --junitxml=/workspace/.ecosyncbench/test-reports/paconvert-hidden.xml
  "
}

run_paddle() {
  local tests=(
    test_basic_pad
    test_constant_fast_pass
    test_dyn_graph_reflect
    test_error_handling
    test_no_pad
    test_single_dim
    test_special_cases
    test_static_graph_circular
  )
  local status=0
  local index=0
  local test_name report

  rm -f "$workspace/.ecosyncbench/test-reports"/paddle-hidden*.xml
  for test_name in "${tests[@]}"; do
    index=$((index + 1))
    report=$(printf '/workspace/.ecosyncbench/test-reports/paddle-hidden-%02d.xml' "$index")
    if ! run_python "repos/PaddlePaddle/Paddle" "
      set -euo pipefail
      mkdir -p /cache/home /workspace/.ecosyncbench/test-reports
      export PYTHONPATH=\"/workspace/repos/PaddlePaddle/Paddle/test/legacy_test:\${PYTHONPATH:-}\"
      python -m pytest -q \
        \"test/legacy_test/test_compat_pad.py::TestCompatPad::$test_name\" \
        --junitxml=\"$report\"
    "; then
      status=1
    fi
  done

  python3 - "$workspace/.ecosyncbench/test-reports" <<'PY'
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

report_dir = Path(sys.argv[1])
combined = ET.Element("testsuites")
totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time": 0.0}

for report in sorted(report_dir.glob("paddle-hidden-[0-9][0-9].xml")):
    root = ET.parse(report).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    for suite in suites:
        combined.append(suite)
        for key in ("tests", "failures", "errors", "skipped"):
            totals[key] += int(suite.get(key, "0"))
        totals["time"] += float(suite.get("time", "0"))

for key, value in totals.items():
    combined.set(key, str(value))
ET.ElementTree(combined).write(
    report_dir / "paddle-hidden.xml",
    encoding="utf-8",
    xml_declaration=True,
)
PY
  return "$status"
}

if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
  ensure_image
  docker run --rm "$image" bash -lc 'python -c "import paddle, pytest, torch, numpy" >/dev/null'
  exit 0
fi

case "$profile" in
  paconvert-hidden)
    run_paconvert
    ;;
  paddle-hidden)
    run_paddle
    ;;
  *)
    echo "Unknown profile for paddlepaddle_paconvert_paddle_d3f5712cbcf6: $profile" >&2
    exit 2
    ;;
esac
