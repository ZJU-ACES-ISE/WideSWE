#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
host_repo_root="$(pwd -P)"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy}"
python_image="ecosyncbench/deps/pytorch-python:3.12"

mkdir -p "$workspace/.ecosyncbench/test-reports" \
  "$cache_root/pip-cache/pytorch/tensordict" \
  "$cache_root/pip-cache/pytorch/rl"

docker_common_args=(
  --rm
  -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}"
  -e HOME=/tmp/ecosync-home
  -e USER=ecosync
  -e LOGNAME=ecosync
  -e CI=1
  -e FORCE_COLOR=0
  -e TORCH_HOME=/tmp/ecosync-home/.cache/torch
  -e TORCHINDUCTOR_CACHE_DIR=/tmp/ecosync-home/.cache/torchinductor
  -e SETUPTOOLS_SCM_PRETEND_VERSION=0.12.0
  -e TORCHRL_BUILD_VERSION=0.12.0
  -v "$workspace:/workspace"
)

ensure_python_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$python_image" >/dev/null 2>&1; then
    docker build -t "$python_image" -f "$host_repo_root/benchmark/images/deps/pytorch-python/Dockerfile" "$host_repo_root/benchmark/images/deps/pytorch-python"
  fi
}

run_tensordict() {
  ensure_python_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$python_image" bash -lc 'python -c "import torch, pytest; print(torch.__version__)" >/dev/null'
    return 0
  fi
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-3600}" docker run \
    "${docker_common_args[@]}" \
    -e PIP_CACHE_DIR=/pip-cache \
    -v "$cache_root/pip-cache/pytorch/tensordict:/pip-cache" \
    -w /workspace/repos/pytorch/tensordict \
    "$python_image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /tmp/ecosync-home /workspace/.ecosyncbench/test-reports/tensordict
      python -m pip install --no-build-isolation -e .
      python -m pytest test/test_nn.py --junitxml=/workspace/.ecosyncbench/test-reports/tensordict/junit.xml
    '
}

run_rl() {
  ensure_python_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$python_image" bash -lc 'python -c "import torch, pytest; print(torch.__version__)" >/dev/null'
    return 0
  fi
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-3600}" docker run \
    "${docker_common_args[@]}" \
    -e PIP_CACHE_DIR=/pip-cache \
    -v "$cache_root/pip-cache/pytorch/rl:/pip-cache" \
    -w /workspace/repos/pytorch/rl \
    "$python_image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /tmp/ecosync-home /workspace/.ecosyncbench/test-reports/rl
      python -m pip install --no-build-isolation -e /workspace/repos/pytorch/tensordict
      python -m pip install --no-build-isolation -e .
      python -m pytest test/test_actors.py --junitxml=/workspace/.ecosyncbench/test-reports/rl/junit.xml
    '
}

case "$profile" in
  rl-hidden)
    run_rl
    ;;
  tensordict-hidden)
    run_tensordict
    ;;
  *)
    echo "Unknown profile for pytorch_rl_tensordict_12a98d4daef1: $profile" >&2
    exit 2
    ;;
esac
