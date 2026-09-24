#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
image="ecosyncbench/base/ansible-django-py:3.12"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/python-cache/ansible-awx-dispatcherd-6056e1231c04}"
mkdir -p "$cache_root/venvs" "$cache_root/pip" "$cache_root/uv" "$workspace/.ecosyncbench/test-reports"

ensure_runner_script() {
  mkdir -p "$workspace/.ecosyncbench"
  cat > "$workspace/.ecosyncbench/run-ansible-awx-dispatcherd-profile.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

profile="${ECOSYNC_PROFILE:?ECOSYNC_PROFILE required}"
workspace=/workspace
reports="$workspace/.ecosyncbench/test-reports"
cache_root=/python-cache
mkdir -p "$reports" "$cache_root/venvs" "$cache_root/pip" "$cache_root/uv" "$cache_root/locks" "$cache_root/markers"
export PIP_CACHE_DIR="$cache_root/pip"
export UV_CACHE_DIR="$cache_root/uv"
export UV_LINK_MODE=copy
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
export SETUPTOOLS_SCM_PRETEND_VERSION="${SETUPTOOLS_SCM_PRETEND_VERSION:-25.0.0}"

make_venv() {
  local name="$1"
  local venv="$cache_root/venvs/$name"
  if [[ ! -x "$venv/bin/python" ]]; then
    python -m venv "$venv"
    "$venv/bin/python" -m pip install --upgrade pip setuptools wheel uv >&2
  fi
  echo "$venv"
}

install_dispatcherd() {
  local venv
  venv="$(make_venv dispatcherd)"
  (
    flock 9
    if [[ ! -f "$cache_root/markers/dispatcherd-deps.v1" ]]; then
      "$venv/bin/python" -m pip install --upgrade pytest pytest-mock pytest-asyncio pyyaml
      touch "$cache_root/markers/dispatcherd-deps.v1"
    fi
  ) 9>"$cache_root/locks/dispatcherd.lock"
  cd "$workspace/repos/ansible/dispatcherd"
  "$venv/bin/python" -m pip install --no-build-isolation -e ".[pg_notify]"
}

install_awx() {
  local venv
  venv="$(make_venv awx)"
  (
    flock 9
    if [[ ! -f "$cache_root/markers/awx-deps.v1" ]]; then
      "$venv/bin/python" -m pip install --upgrade pytest pytest-django pytest-mock pytest-timeout
      "$venv/bin/python" -m pip install \
        "certifi @ git+https://github.com/ansible/system-certifi.git@devel" \
        "ansible-runner @ git+https://github.com/ansible/ansible-runner.git@devel" \
        "django-ansible-base[feature-flags,jwt-consumer,rbac,resource-registry,rest-filters] @ git+https://github.com/ansible/django-ansible-base@devel" \
        "awx_plugins.interfaces @ git+https://github.com/ansible/awx_plugins.interfaces.git"
      cd "$workspace/repos/ansible/awx"
      grep -v -E '^(pip|setuptools|wheel)==' requirements/requirements.txt > /tmp/awx_requirements_no_build_tools.txt
      "$venv/bin/python" -m pip install -r /tmp/awx_requirements_no_build_tools.txt
      "$venv/bin/python" -m pip install logutils colorama
      touch "$cache_root/markers/awx-deps.v1"
    fi
  ) 9>"$cache_root/locks/awx.lock"

  cd "$workspace/repos/ansible/dispatcherd"
  "$venv/bin/python" -m pip install --no-build-isolation -e ".[pg_notify]"

  cd "$workspace/repos/ansible/awx"
  "$venv/bin/python" -m pip uninstall -y awx-plugins-core ecosync-awx-plugin-shim >/dev/null 2>&1 || true
  mkdir -p "$workspace/.ecosyncbench/awx-plugin-shim"
  cat > "$workspace/.ecosyncbench/awx-plugin-shim/pyproject.toml" <<'PY'
[build-system]
requires = ["setuptools >= 64"]
build-backend = "setuptools.build_meta"

[project]
name = "ecosync-awx-plugin-shim"
version = "0.0.0"

[tool.setuptools]
py-modules = ["ecosync_awx_plugin_shim"]

[project.entry-points."awx_plugins.managed_credentials"]
ssh = "ecosync_awx_plugin_shim:ssh"
scm = "ecosync_awx_plugin_shim:scm"
vault = "ecosync_awx_plugin_shim:vault"
controller = "ecosync_awx_plugin_shim:controller"
kubernetes_bearer_token = "ecosync_awx_plugin_shim:kubernetes_bearer_token"
registry = "ecosync_awx_plugin_shim:registry"
galaxy_api_token = "ecosync_awx_plugin_shim:galaxy_api_token"
net = "ecosync_awx_plugin_shim:net"

[project.entry-points."awx_plugins.managed_credentials.supported"]
aws = "ecosync_awx_plugin_shim:aws"
openstack = "ecosync_awx_plugin_shim:openstack"
vmware = "ecosync_awx_plugin_shim:vmware"
satellite6 = "ecosync_awx_plugin_shim:satellite6"
bitbucket_dc_token = "ecosync_awx_plugin_shim:bitbucket_dc_token"
gce = "ecosync_awx_plugin_shim:gce"
azure_rm = "ecosync_awx_plugin_shim:azure_rm"
github_token = "ecosync_awx_plugin_shim:github_token"
gitlab_token = "ecosync_awx_plugin_shim:gitlab_token"
insights = "ecosync_awx_plugin_shim:insights"
rhv = "ecosync_awx_plugin_shim:rhv"
gpg_public_key = "ecosync_awx_plugin_shim:gpg_public_key"
terraform = "ecosync_awx_plugin_shim:terraform"
hcp_terraform = "ecosync_awx_plugin_shim:hcp_terraform"

[project.entry-points."awx_plugins.inventory"]
azure_rm = "ecosync_awx_plugin_shim:azure_rm"
ec2 = "ecosync_awx_plugin_shim:ec2"
gce = "ecosync_awx_plugin_shim:gce"
vmware = "ecosync_awx_plugin_shim:vmware"
openstack = "ecosync_awx_plugin_shim:openstack"
rhv = "ecosync_awx_plugin_shim:rhv"
satellite6 = "ecosync_awx_plugin_shim:satellite6"
terraform = "ecosync_awx_plugin_shim:terraform"
controller = "ecosync_awx_plugin_shim:controller"
insights = "ecosync_awx_plugin_shim:insights"
openshift_virtualization = "ecosync_awx_plugin_shim:openshift_virtualization"
constructed = "ecosync_awx_plugin_shim:constructed"

[project.entry-points."awx_plugins.inventory.supported"]
rhv = "ecosync_awx_plugin_shim:rhv_supported"
satellite6 = "ecosync_awx_plugin_shim:satellite6_supported"
controller = "ecosync_awx_plugin_shim:controller_supported"
insights = "ecosync_awx_plugin_shim:insights_supported"
openshift_virtualization = "ecosync_awx_plugin_shim:openshift_virtualization_supported"
vmware_esxi = "ecosync_awx_plugin_shim:vmware_esxi_supported"
PY
  cat > "$workspace/.ecosyncbench/awx-plugin-shim/ecosync_awx_plugin_shim.py" <<'PY'
_names = (
    "ssh",
    "scm",
    "vault",
    "controller",
    "kubernetes_bearer_token",
    "registry",
    "galaxy_api_token",
    "net",
    "aws",
    "openstack",
    "vmware",
    "satellite6",
    "bitbucket_dc_token",
    "gce",
    "azure_rm",
    "github_token",
    "gitlab_token",
    "insights",
    "rhv",
    "gpg_public_key",
    "terraform",
    "hcp_terraform",
    "ec2",
    "openshift_virtualization",
    "constructed",
    "rhv_supported",
    "satellite6_supported",
    "controller_supported",
    "insights_supported",
    "openshift_virtualization_supported",
    "vmware_esxi_supported",
)

class _CredentialType:
    inputs = {}
    injectors = {}

def __getattr__(name):
    if name not in _names:
        raise AttributeError(name)
    try:
        from awx.main.models.credential import ManagedCredentialType

        ManagedCredentialType.registry.pop(name, None)
    except Exception:
        pass
    return _CredentialType()
PY
  "$venv/bin/python" -m pip install --no-build-isolation --no-deps -e "$workspace/.ecosyncbench/awx-plugin-shim"
  "$venv/bin/python" -m pip install --no-build-isolation --no-deps -e .
}

run_dispatcherd() {
  install_dispatcherd
  local venv="$cache_root/venvs/dispatcherd"
  cd "$workspace/repos/ansible/dispatcherd"
  "$venv/bin/python" -m pytest -q -o addopts="" \
    tests/unit/test_cli_control_args.py \
    tests/unit/test_control_tasks.py \
    --junitxml="$reports/dispatcherd-hidden.xml"
}

run_awx() {
  install_awx
  local venv="$cache_root/venvs/awx"
  cd "$workspace/repos/ansible/awx"
  mkdir -p /var/log/tower /var/lib/awx /etc/tower
  cat > "$workspace/.ecosyncbench/ecosync_awx_test_settings.py" <<'PY'
from awx.main.tests.settings_for_test import *  # noqa: F401,F403

INSTALLED_APPS = [app for app in INSTALLED_APPS if app != 'debug_toolbar']  # noqa: F405
MIDDLEWARE = [mw for mw in MIDDLEWARE if 'debug_toolbar' not in mw]  # noqa: F405
DEBUG = True
PY
  export PYTHONPATH="$workspace/.ecosyncbench:${PYTHONPATH:-}"
  export DJANGO_SETTINGS_MODULE=ecosync_awx_test_settings
  export AWX_MODE=development
  export SECRET_KEY=ecosyncbench-test-secret
  "$venv/bin/python" -c 'import sys, pytest; args = sys.argv[1:]; sys.argv.append("pytest"); raise SystemExit(pytest.main(args))' \
    -q -o addopts="" \
    -W ignore::ResourceWarning \
    -W "ignore:Accessing the database during app initialization is discouraged:RuntimeWarning" \
    --reuse-db --nomigrations \
    awx/main/tests/functional/tasks/test_tasks_system.py \
    --junitxml="$reports/awx-hidden.xml"
}

case "$profile" in
  awx-hidden)
    run_awx
    ;;
  dispatcherd-hidden)
    run_dispatcherd
    ;;
  *)
    echo "Unknown profile: $profile" >&2
    exit 2
    ;;
esac
SH
  chmod +x "$workspace/.ecosyncbench/run-ansible-awx-dispatcherd-profile.sh"
}

run_profile() {
  ensure_runner_script
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker image inspect "$image" >/dev/null
    return 0
  fi
  local container_name="ecosync_${profile}_$$_${RANDOM}"
  local cid
  cid="$(docker create --name "$container_name" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/python-cache" \
    -w /workspace \
    -e ECOSYNC_PROFILE="$profile" \
    -e GITHUB_TOKEN="${GITHUB_TOKEN:-}" \
    -e GH_TOKEN="${GH_TOKEN:-}" \
    -e PIP_INDEX_URL="${PIP_INDEX_URL:-}" \
    -e PIP_EXTRA_INDEX_URL="${PIP_EXTRA_INDEX_URL:-}" \
    -e HTTP_PROXY="${ECOSYNC_DOCKER_PROXY:-${HTTP_PROXY:-}}" \
    -e HTTPS_PROXY="${ECOSYNC_DOCKER_PROXY:-${HTTPS_PROXY:-}}" \
    -e NO_PROXY="${NO_PROXY:-}" \
    "$image" \
    bash /workspace/.ecosyncbench/run-ansible-awx-dispatcherd-profile.sh)"
  cleanup_container() { docker rm -f "$cid" >/dev/null 2>&1 || true; }
  trap cleanup_container EXIT
  docker start "$cid" >/dev/null
  local code
  code="$(docker wait "$cid")"
  docker logs "$cid"
  cleanup_container
  trap - EXIT
  if command -v sudo >/dev/null 2>&1; then
    sudo chown -R "$(id -u):$(id -g)" "$cache_root" "$workspace/.ecosyncbench" >/dev/null 2>&1 || true
  fi
  return "$code"
}

case "$profile" in
  awx-hidden|dispatcherd-hidden)
    run_profile
    ;;
  *)
    echo "Unknown profile for ansible_awx_dispatcherd_6056e1231c04: $profile" >&2
    exit 2
    ;;
esac
