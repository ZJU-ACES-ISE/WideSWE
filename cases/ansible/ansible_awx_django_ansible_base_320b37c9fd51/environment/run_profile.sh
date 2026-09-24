#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
image="ecosyncbench/deps/ansible-awx-dab:320b37c9fd51-py311"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/python-cache/ansible_awx_dab_320b37c9fd51}"
mkdir -p "$cache_root/venvs" "$cache_root/pip" "$workspace/.ecosyncbench/test-reports"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$task_dir/environment/ansible-awx-dab-py311.Dockerfile" "$task_dir/environment"
  fi
}

run_in_image() {
  local script="$1"
  ensure_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image" python --version
    return 0
  fi
  docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e PIP_CACHE_DIR=/python-cache/pip \
    -e SETUPTOOLS_SCM_PRETEND_VERSION_FOR_DJANGO_ANSIBLE_BASE=2025.1.0 \
    -e CFLAGS=-Wno-error=incompatible-pointer-types \
    -v "$workspace:/workspace" \
    -v "$cache_root:/python-cache" \
    -w /workspace \
    "$image" \
    bash -lc "$script"
}

case "$profile" in
  django_ansible_base-hidden)
    run_in_image '
      set -euo pipefail
      venv=/python-cache/venvs/dab
      if [[ ! -f "$venv/.ready" ]]; then
        rm -rf "$venv"
        python -m venv "$venv"
        "$venv/bin/python" -m pip install pip==25.3 setuptools wheel
        "$venv/bin/python" -m pip install lxml==5.3.0
        cd /workspace/repos/ansible/django-ansible-base
        "$venv/bin/python" -m pip install -r requirements/requirements_all.txt -r requirements/requirements_dev.txt -e .
        touch "$venv/.ready"
      fi
      cd /workspace/repos/ansible/django-ansible-base
      export DJANGO_SETTINGS_MODULE=test_app.sqlite3settings
      export TESTAPP_MODE=sqlite
      "$venv/bin/python" -m pytest -q -o addopts="" \
        --junitxml=/workspace/.ecosyncbench/test-reports/django_ansible_base-hidden.xml \
        test_app/tests/feature_flags/test_utils.py
    '
    ;;
  awx-hidden)
    run_in_image '
      set -euo pipefail
      venv=/python-cache/venvs/awx
      if [[ ! -f "$venv/.ready" ]]; then
        rm -rf "$venv"
        python -m venv "$venv"
        "$venv/bin/python" -m pip install pip==25.3 setuptools wheel
        "$venv/bin/python" -m pip install lxml==5.3.0
        cd /workspace/repos/ansible/django-ansible-base
        "$venv/bin/python" -m pip install -r requirements/requirements_all.txt -r requirements/requirements_dev.txt -e .
        cd /workspace/repos/ansible/awx
        grep -v -E "^pip[<>=]" requirements/requirements_dev.txt > /tmp/awx_requirements_dev_no_pip.txt
        grep -v "django-ansible-base" requirements/requirements_git.txt > /tmp/awx_requirements_git_no_dab.txt
        "$venv/bin/python" -m pip install \
          -r requirements/requirements.txt \
          -r /tmp/awx_requirements_dev_no_pip.txt \
          -r /tmp/awx_requirements_git_no_dab.txt
        SETUPTOOLS_SCM_PRETEND_VERSION=25.0.0 "$venv/bin/python" -m pip install -e .
        "$venv/bin/python" -m pip install "chardet<6"
        touch "$venv/.ready"
      fi
      mkdir -p /workspace/.ecosyncbench/test-reports /var/log/tower
      cd /workspace/repos/ansible/awx
      export SETUPTOOLS_SCM_PRETEND_VERSION=25.0.0
      export DJANGO_SETTINGS_MODULE=awx.main.tests.settings_for_test
      rm -rf .pytest_cache awx.sqlite3 awx_test.sqlite3 /tmp/awx.sqlite3 /tmp/awx_test.sqlite3
      "$venv/bin/python" -m pytest -q \
        --junitxml=/workspace/.ecosyncbench/test-reports/awx-hidden.xml \
        awx/main/tests/functional/dab_feature_flags/test_feature_flags_api.py \
        awx/main/tests/functional/models/test_ha.py \
        awx/main/tests/functional/test_dispatch.py \
        awx/main/tests/functional/test_jobs.py \
        awx/main/tests/unit/settings/test_defaults.py \
        awx/main/tests/unit/test_settings.py
    '
    ;;
  *)
    echo "Unknown profile for ansible_awx_django_ansible_base_320b37c9fd51: $profile" >&2
    exit 2
    ;;
esac
