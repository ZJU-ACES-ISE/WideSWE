#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
repo_root="${ECOSYNC_REPO_ROOT:-/opt/ecosyncbench}"

python_image="ecosyncbench/base/python:3.12-uv"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/uv-cache/langgenius_dify_graphon_98bb3070fc3a}"
venv_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/python-env/langgenius_dify_graphon_98bb3070fc3a}"
mkdir -p "$cache_root" "$venv_root" "$workspace/.ecosyncbench/test-reports"

ensure_python_uv_base() {
  docker image inspect "$python_image" >/dev/null 2>&1 || \
    docker build -t "$python_image" \
      -f "$repo_root/benchmark/images/base/python-3.12-uv/Dockerfile" \
      "$repo_root/benchmark/images/base/python-3.12-uv"
}

venv_fingerprint() {
  local project_dir="$1"
  shift
  local image_id
  image_id="$(docker image inspect --format '{{.Id}}' "$python_image")"
  {
    printf 'image=%s\n' "$image_id"
    (
      cd "$project_dir"
      printf '%s\n' "$@" | sort -u | while IFS= read -r dependency_file; do
        [[ -f "$dependency_file" ]] || continue
        printf '%s\0' "$dependency_file"
        sha256sum "$dependency_file"
      done
    )
  } | sha256sum | awk '{print $1}'
}

dify_venv_name() {
  local project_dir="$workspace/repos/langgenius/dify/api"
  local dependency_files=()
  while IFS= read -r dependency_file; do
    dependency_files+=("$dependency_file")
  done < <(
    cd "$project_dir"
    {
      printf '%s\n' pyproject.toml uv.lock
      find providers/vdb providers/trace -mindepth 2 -maxdepth 2 -type f -name pyproject.toml -printf '%p\n'
    } | sort -u
  )
  printf 'dify-%s\n' "$(venv_fingerprint "$project_dir" "${dependency_files[@]}")"
}

graphon_venv_name() {
  local project_dir="$workspace/repos/langgenius/graphon"
  printf 'graphon-%s\n' "$(venv_fingerprint "$project_dir" pyproject.toml uv.lock)"
}

run_dify() {
  ensure_python_uv_base
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$python_image" bash -lc 'python --version && uv --version'
    return 0
  fi

  local venv_name
  venv_name="$(dify_venv_name)"

  timeout "${ECOSYNC_PROFILE_TIMEOUT:-1800}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e PYTHONPATH=/workspace/repos/langgenius/dify/api:/workspace/repos/langgenius/dify/api/providers/trace/trace-arize-phoenix:/workspace/repos/langgenius/graphon/src \
    -e UV_CACHE_DIR=/uv-cache \
    -e UV_PROJECT_ENVIRONMENT="/python-env/$venv_name" \
    -e ECOSYNC_VENV_NAME="$venv_name" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/uv-cache" \
    -v "$venv_root:/python-env" \
    -w /workspace/repos/langgenius/dify \
    "$python_image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home /python-env/.locks
      ready="/python-env/${ECOSYNC_VENV_NAME}/.ecosync-ready"
      if [[ ! -f "$ready" ]]; then
        (
          flock 9
          if [[ ! -f "$ready" ]]; then
            uv run --project api --with pytest-mock python -c "import pytest, pytest_mock"
            touch "$ready"
          fi
        ) 9>"/python-env/.locks/${ECOSYNC_VENV_NAME}.lock"
      fi
      uv run --no-sync --project api --with pytest-mock pytest --override-ini=addopts= \
        api/providers/trace/trace-arize-phoenix/tests/unit_tests/arize_phoenix_trace/test_arize_phoenix_trace.py \
        api/tests/unit_tests/core/app/apps/test_workflow_app_generator.py \
        api/tests/unit_tests/core/app/workflow/test_persistence_layer.py \
        api/tests/unit_tests/core/helper/test_trace_id_helper.py \
        api/tests/unit_tests/core/tools/workflow_as_tool/test_tool.py \
        api/tests/unit_tests/core/workflow/nodes/tool/test_tool_node.py \
        api/tests/unit_tests/core/workflow/nodes/tool/test_tool_node_runtime.py \
        api/tests/unit_tests/core/workflow/test_node_runtime.py \
        api/tests/unit_tests/tasks/test_ops_trace_task.py \
        --junitxml=/workspace/.ecosyncbench/test-reports/dify-hidden.xml
    '
}

run_graphon() {
  ensure_python_uv_base
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$python_image" bash -lc 'python --version && uv --version'
    return 0
  fi

  local venv_name
  venv_name="$(graphon_venv_name)"

  timeout "${ECOSYNC_PROFILE_TIMEOUT:-900}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e PYTHONPATH=/workspace/repos/langgenius/graphon/src \
    -e UV_CACHE_DIR=/uv-cache \
    -e UV_PROJECT_ENVIRONMENT="/python-env/$venv_name" \
    -e ECOSYNC_VENV_NAME="$venv_name" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/uv-cache" \
    -v "$venv_root:/python-env" \
    -w /workspace/repos/langgenius/graphon \
    "$python_image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home /python-env/.locks
      ready="/python-env/${ECOSYNC_VENV_NAME}/.ecosync-ready"
      if [[ ! -f "$ready" ]]; then
        (
          flock 9
          if [[ ! -f "$ready" ]]; then
            uv run python -c "import pytest"
            touch "$ready"
          fi
        ) 9>"/python-env/.locks/${ECOSYNC_VENV_NAME}.lock"
      fi
      uv run --no-sync pytest tests/nodes/tool/test_tool_node.py \
        --junitxml=/workspace/.ecosyncbench/test-reports/graphon-hidden.xml
    '
}

case "$profile" in
  dify-hidden)
    run_dify
    ;;
  graphon-hidden)
    run_graphon
    ;;
  *)
    echo "Unknown profile for langgenius_dify_graphon_98bb3070fc3a: $profile" >&2
    exit 2
    ;;
esac
