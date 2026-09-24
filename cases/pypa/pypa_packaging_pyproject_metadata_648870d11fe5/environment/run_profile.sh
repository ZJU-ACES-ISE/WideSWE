#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
repo_root="/opt/ecosyncbench"
image="ecosyncbench/base/python:3.12-uv"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/pypa-packaging-pyproject-metadata-648870d11fe5}"

mkdir -p "$cache_root/locks"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$repo_root/benchmark/images/base/python-3.12-uv/Dockerfile" "$repo_root"
  fi
}

dependency_fingerprint() {
  local image_id
  image_id="$(docker image inspect --format '{{.Id}}' "$image")"
  {
    printf '%s\0' "$image_id"
    for repo_dir in \
      "$workspace/repos/pypa/packaging" \
      "$workspace/repos/pypa/pyproject-metadata"; do
      find "$repo_dir" \
        \( -path '*/.git' -o -path '*/.venv' -o -path '*/__pycache__' \) -prune -o \
        \( -name pyproject.toml -o -name setup.cfg -o -name setup.py -o -name uv.lock -o -name 'requirements*.txt' \) \
        -type f -print0 \
        | sort -z \
        | while IFS= read -r -d '' manifest; do
            printf '%s\0' "${manifest#"$workspace/"}"
            sha256sum "$manifest" | awk '{print $1}'
          done
    done
  } | sha256sum | awk '{print $1}'
}

run_pytest() {
  local repo_dir="$1"
  local report="$2"
  local venv_name="$3"
  local install_local_packaging="$4"
  local install_mode="$5"
  local dependency_id
  shift 5
  ensure_image
  dependency_id="$(dependency_fingerprint)"
  mkdir -p "$cache_root" "$workspace/.ecosyncbench/test-reports"
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image" bash -lc 'python --version >/dev/null && uv --version >/dev/null'
    return 0
  fi
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-1200}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e UV_CACHE_DIR=/cache/uv \
    -e PIP_CACHE_DIR=/cache/pip \
    -e VIRTUAL_ENV="/cache/venvs/${venv_name}/${dependency_id}" \
    -e PATH="/cache/venvs/${venv_name}/${dependency_id}/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin" \
    -e PYTHONPATH= \
    -e SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0 \
    -e ECOSYNC_REPORT="$report" \
    -e ECOSYNC_INSTALL_LOCAL_PACKAGING="$install_local_packaging" \
    -e ECOSYNC_INSTALL_MODE="$install_mode" \
    -e ECOSYNC_VENV_LOCK="/cache/locks/${venv_name}-${dependency_id}.lock" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    -w "/workspace/$repo_dir" \
    "$image" \
    bash -c 'set -euo pipefail
      mkdir -p /tmp/ecosync-home /workspace/.ecosyncbench/test-reports /cache/venvs
      exec 9>"$ECOSYNC_VENV_LOCK"
      flock 9
      if [[ ! -x "$VIRTUAL_ENV/bin/python" || ! -f "$VIRTUAL_ENV/.ecosync-ready" ]]; then
        rm -rf "$VIRTUAL_ENV"
        uv venv "$VIRTUAL_ENV"
        if [[ "$ECOSYNC_INSTALL_LOCAL_PACKAGING" == "1" ]]; then
          uv pip install --python "$VIRTUAL_ENV/bin/python" -e /workspace/repos/pypa/packaging
        fi
        if [[ "$ECOSYNC_INSTALL_MODE" == "packaging" ]]; then
          uv pip install --python "$VIRTUAL_ENV/bin/python" \
            -e . \
            "pytest<9" \
            "coverage[toml]>=7.2.0" \
            "hypothesis>=6.0.0" \
            pretend \
            tomli_w
        elif [[ "$ECOSYNC_INSTALL_MODE" == "pyproject-metadata" ]]; then
          uv pip install --python "$VIRTUAL_ENV/bin/python" -e . --group test "pytest<9"
        else
          echo "unknown install mode: $ECOSYNC_INSTALL_MODE" >&2
          exit 2
        fi
        touch "$VIRTUAL_ENV/.ecosync-ready"
      fi
      flock -u 9
      "$VIRTUAL_ENV/bin/python" -m pytest "$@" --junitxml "/workspace/.ecosyncbench/test-reports/${ECOSYNC_REPORT}.xml"' bash "$@"
}

case "$profile" in
  packaging-hidden)
    run_pytest "repos/pypa/packaging" "packaging-hidden" "packaging-hidden" "0" "packaging" \
      tests/test_metadata.py
    ;;
  pyproject_metadata-hidden)
    run_pytest "repos/pypa/pyproject-metadata" "pyproject-metadata-hidden" "pyproject-metadata-hidden" "1" "pyproject-metadata" \
      tests/test_standard_metadata.py
    ;;
  *)
    echo "Unknown profile for pypa_packaging_pyproject_metadata_648870d11fe5: $profile" >&2
    exit 2
    ;;
esac
