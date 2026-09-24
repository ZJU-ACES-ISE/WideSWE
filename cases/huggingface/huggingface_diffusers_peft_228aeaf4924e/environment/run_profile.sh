#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="${ECOSYNC_WORKSPACE:?ECOSYNC_WORKSPACE required}"
image="ecosyncbench/deps/huggingface-diffusers-peft:228aeaf4924e"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/huggingface-diffusers-peft-228aeaf4924e}"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$task_dir/environment/huggingface-diffusers-peft.Dockerfile" "$task_dir/environment"
  fi
}

run_python_profile() {
  local workdir="$1"
  local timeout_seconds="$2"
  local script="$3"
  ensure_image
  mkdir -p "$cache_root/pip" "$cache_root/hf" "$cache_root/tmp" "$workspace/.ecosyncbench/test-reports/$profile"
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image" python -V
    return 0
  fi
  timeout "$timeout_seconds" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    --network host \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e RUN_SLOW=1 \
    -e HF_HOME=/hf-cache/home \
    -e HUGGINGFACE_HUB_CACHE=/hf-cache/hub \
    -e TRANSFORMERS_CACHE=/hf-cache/transformers \
    -e HF_HUB_DISABLE_TELEMETRY=1 \
    -e HF_HUB_DOWNLOAD_TIMEOUT=120 \
    -e TOKENIZERS_PARALLELISM=false \
    -e PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    -e PIP_CACHE_DIR=/pip-cache \
    -e TMPDIR=/tmp/ecosync-tmp \
    -v "$workspace:/workspace" \
    -v "$cache_root/pip:/pip-cache" \
    -v "$cache_root/hf:/hf-cache" \
    -v "$cache_root/tmp:/tmp/ecosync-tmp" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -lc "$script"
}

peft_version_metadata_fix='
python - <<'"'"'PY'"'"'
from pathlib import Path
import site

paths = []
workspace = Path("/workspace/repos/huggingface/peft")
paths.extend(workspace.glob("*.egg-info/PKG-INFO"))
paths.extend((workspace / "src").glob("*.egg-info/PKG-INFO"))
for site_dir in site.getsitepackages():
    base = Path(site_dir)
    paths.extend(base.glob("peft-*.dist-info/METADATA"))
    paths.extend(base.glob("peft.egg-info/PKG-INFO"))

for path in paths:
    if not path.exists():
        continue
    text = path.read_text(encoding="utf-8")
    lines = ["Version: 0.17.0" if line.startswith("Version: ") else line for line in text.splitlines()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
'

case "$profile" in
  diffusers-hidden)
    run_python_profile "repos/huggingface/diffusers" "${ECOSYNC_PROFILE_TIMEOUT:-7200}" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports/diffusers-hidden /tmp/ecosync-home /tmp/ecosync-tmp
      python -m pip install -e /workspace/repos/huggingface/peft --no-deps --no-build-isolation >/tmp/ecosync-peft-install.log
      '"$peft_version_metadata_fix"'
      python -m pip install -e . --no-deps --no-build-isolation >/tmp/ecosync-diffusers-install.log
      python -m pytest -q -o addopts="" \
        tests/lora/utils.py \
        tests/models/transformers/test_models_transformer_flux.py \
        --junitxml=/workspace/.ecosyncbench/test-reports/diffusers-hidden/diffusers-hidden.xml
      test -s /workspace/.ecosyncbench/test-reports/diffusers-hidden/diffusers-hidden.xml
    '
    ;;
  peft-hidden)
    run_python_profile "repos/huggingface/peft" "${ECOSYNC_PROFILE_TIMEOUT:-7200}" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports/peft-hidden /tmp/ecosync-home /tmp/ecosync-tmp
      python -m pip install -e /workspace/repos/huggingface/diffusers --no-deps --no-build-isolation >/tmp/ecosync-diffusers-install.log
      python -m pip install -e . --no-deps --no-build-isolation >/tmp/ecosync-peft-install.log
      '"$peft_version_metadata_fix"'
      python -m pytest -q -o addopts="" \
        tests/test_low_level_api.py \
        --junitxml=/workspace/.ecosyncbench/test-reports/peft-hidden/peft-hidden.xml
      test -s /workspace/.ecosyncbench/test-reports/peft-hidden/peft-hidden.xml
    '
    ;;
  *)
    echo "Unknown profile for huggingface_diffusers_peft_228aeaf4924e: $profile" >&2
    exit 2
    ;;
esac
