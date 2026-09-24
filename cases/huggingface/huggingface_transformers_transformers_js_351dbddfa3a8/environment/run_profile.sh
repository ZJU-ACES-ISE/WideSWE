#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
workspace="${ECOSYNC_WORKSPACE:?ECOSYNC_WORKSPACE required}"
case_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
report_dir="$workspace/.ecosyncbench/test-reports"
mkdir -p "$report_dir"

cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/huggingface-transformers-js-351dbd}"
mkdir -p "$cache_root/pip" "$cache_root/hf" "$cache_root/pnpm-store"
chmod 0777 "$cache_root" "$cache_root/pip" "$cache_root/hf" "$cache_root/pnpm-store" 2>/dev/null || true

py_image="ecosyncbench/deps/huggingface-transformers-py:351dbddfa3a8"
js_image="ecosyncbench/deps/huggingface-transformers-js-node:351dbddfa3a8"

ensure_image() {
  local image="$1"
  local dockerfile="$2"
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$case_dir/environment/$dockerfile" "$case_dir/environment"
  fi
}

case "$profile" in
  transformers-hidden)
    ensure_image "$py_image" transformers-python.Dockerfile
    ;;
  transformers_js-hidden)
    ensure_image "$js_image" transformers-js-node.Dockerfile
    ;;
  *)
    echo "unknown profile: $profile" >&2
    exit 2
    ;;
esac

if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
  exit 0
fi

case "$profile" in
  transformers-hidden)
    docker run --rm \
      -e HF_HOME=/cache/hf \
      -e TRANSFORMERS_CACHE=/cache/hf \
      -e HF_HUB_DISABLE_TELEMETRY=1 \
      -e PYTHONPATH=/workspace/repos/huggingface/transformers/src \
      -v "$workspace:/workspace" \
      -v "$cache_root/pip:/cache/pip" \
      -v "$cache_root/hf:/cache/hf" \
      -w /workspace/repos/huggingface/transformers \
      "$py_image" \
      bash -lc 'set -o pipefail; export PIP_CACHE_DIR=/cache/pip; python -m pip install -e . --no-deps >/tmp/ecosync-pip-install.log; python -m pytest -q tests/models/cohere_asr/test_modeling_cohere_asr.py --junitxml=/workspace/.ecosyncbench/test-reports/transformers-hidden.xml'
    ;;
  transformers_js-hidden)
    docker run --rm \
      -e HF_HOME=/cache/hf \
      -e TRANSFORMERS_CACHE=/cache/hf \
      -e HF_HUB_DISABLE_TELEMETRY=1 \
      -v "$workspace:/workspace" \
      -v "$cache_root/hf:/cache/hf" \
      -v "$cache_root/pnpm-store:/pnpm-store" \
      -w /workspace/repos/huggingface/transformers.js/packages/transformers \
      "$js_image" \
      bash -lc 'set -o pipefail; pnpm config set store-dir /pnpm-store && pnpm --dir ../.. install --frozen-lockfile && pnpm typegen && node --experimental-vm-modules --expose-gc node_modules/jest/bin/jest.js --runInBand --verbose tests/feature_extractors.test.js --json --outputFile=/workspace/.ecosyncbench/test-reports/transformers_js-hidden.json'
    ;;
esac
