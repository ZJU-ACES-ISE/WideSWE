#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -d "$PWD/.git" ]]; then
  repo_root="$PWD"
else
  repo_root="$task_dir"
  while [[ "$repo_root" != "/" && ! -d "$repo_root/.git" ]]; do repo_root="$(dirname "$repo_root")"; done
fi
if [[ ! -d "$repo_root/.git" ]]; then echo "cannot locate repository root from $task_dir" >&2; exit 2; fi

workspace="$(cd "${ECOSYNC_WORKSPACE:-$repo_root}" && pwd)"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/ansible-django-ansible-base-jewel-9b9d317bd70a}"
export ECOSYNC_REPO_ROOT="$repo_root"
export ECOSYNC_BUILD_CONTEXT="$workspace"
export ECOSYNC_PROFILE="$profile"
export ECOSYNC_TASK_DIR="$task_dir"
mkdir -p "$cache_root/pip" "$cache_root/xdg" "$cache_root/venvs"

docker_proxy="${ECOSYNC_BUILD_PROXY:-${ECOSYNC_DOCKER_PROXY:-}}"
if [[ -n "$docker_proxy" ]]; then
  docker_proxy="${docker_proxy//127.0.0.1/host.docker.internal}"
  docker_proxy="${docker_proxy//localhost/host.docker.internal}"
  export ECOSYNC_DOCKER_PROXY="$docker_proxy"
fi

ensure_runner_script() {
  mkdir -p "$workspace/.ecosyncbench"
  cat > "$workspace/.ecosyncbench/run-ansible-profile.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
mkdir -p /workspace/.ecosyncbench/test-reports /pip-cache/pip /pip-cache/xdg /pip-cache/venvs
chmod a+rwX /pip-cache /pip-cache/pip /pip-cache/xdg /pip-cache/venvs 2>/dev/null || true
export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_DJANGO_ANSIBLE_BASE="${SETUPTOOLS_SCM_PRETEND_VERSION_FOR_DJANGO_ANSIBLE_BASE:-2026.6.0}"
export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_AAP_GATEWAY="${SETUPTOOLS_SCM_PRETEND_VERSION_FOR_AAP_GATEWAY:-0.0.0}"

start_postgres() {
  service postgresql start >/tmp/ecosync-postgres.log 2>&1 || {
    cat /tmp/ecosync-postgres.log >&2
    return 1
  }
  su postgres -c "psql -v ON_ERROR_STOP=1 -c \"ALTER USER postgres PASSWORD 'password';\"" >/dev/null
  su postgres -c "psql -tc \"SELECT 1 FROM pg_roles WHERE rolname='gw'\" | grep -q 1 || createuser gw" >/dev/null
  su postgres -c "psql -v ON_ERROR_STOP=1 -c \"ALTER USER gw PASSWORD 'password';\"" >/dev/null
  su postgres -c "createdb -O gw gw_db" >/dev/null 2>&1 || true
}

dependency_fingerprint() {
  local schema="$1"
  shift
  {
    printf '%s\n' "$schema"
    for path in "$@"; do
      if [[ -d "$path" ]]; then
        find "$path" -type f \( -name '*.txt' -o -name 'pyproject.toml' -o -name 'setup.cfg' -o -name 'setup.py' \) -print0 \
          | sort -z | xargs -0 -r sha256sum
      elif [[ -f "$path" ]]; then
        sha256sum "$path"
      fi
    done
  } | sha256sum | awk '{print $1}'
}

prepare_venv() {
  local name="$1"
  local fingerprint="$2"
  local venv="/pip-cache/venvs/${name}-${fingerprint}"
  exec 9>"/pip-cache/venvs/.${name}-${fingerprint}.lock"
  flock 9
  if [[ ! -f "$venv/.ready" ]]; then
    rm -rf "$venv"
    python -m venv "$venv"
    # shellcheck disable=SC1090
    source "$venv/bin/activate"
    python -m pip install --upgrade pip setuptools wheel >/tmp/ecosync-pip-upgrade-"$name".log
    export ECOSYNC_NEW_VENV=1
  else
    # shellcheck disable=SC1090
    source "$venv/bin/activate"
    export ECOSYNC_NEW_VENV=0
    flock -u 9
  fi
}

finish_venv() {
  touch "$VIRTUAL_ENV/.ready"
  flock -u 9
}

run_django_ansible_base() {
  cd /workspace/repos/ansible/django-ansible-base
  fingerprint="$(dependency_fingerprint dab-v2 pyproject.toml setup.cfg setup.py requirements)"
  prepare_venv django_ansible_base-hidden "$fingerprint"
  export CFLAGS="-Wno-error=incompatible-pointer-types"
  if [[ "$ECOSYNC_NEW_VENV" == "1" ]]; then
    python -m pip install -r requirements/requirements_all.txt -r requirements/requirements_dev.txt >/tmp/ecosync-pip-dab.log
    finish_venv
  fi
  export PYTHONPATH="/workspace/repos/ansible/django-ansible-base${PYTHONPATH:+:$PYTHONPATH}"
  export DJANGO_SETTINGS_MODULE=test_app.sqlite3settings
  pytest \
    --junit-xml=/workspace/.ecosyncbench/test-reports/django-ansible-base-hidden.xml \
    test_app/tests/lib/abstract_models/test_common_query_optimization.py
}

run_jewel() {
  start_postgres
  cd /workspace/repos/ansible/jewel
  ln -sfn ../django-ansible-base django-ansible-base
  fingerprint="$(dependency_fingerprint jewel-v3-redis-7.4.1 pyproject.toml setup.cfg setup.py requirements django-ansible-base/pyproject.toml django-ansible-base/setup.cfg django-ansible-base/setup.py django-ansible-base/requirements)"
  prepare_venv jewel-hidden "$fingerprint"
  export CFLAGS="-Wno-error=incompatible-pointer-types"
  if [[ "$ECOSYNC_NEW_VENV" == "1" ]]; then
    python -m pip install ./django-ansible-base[activitystream,api-documentation,authentication,redis-client,rest-filters,rbac,oauth2-provider,feature-flags,observability] >/tmp/ecosync-pip-jewel-dab.log
    python -m pip install psycopg[binary] -r requirements/requirements.txt -r requirements/requirements_test.txt >/tmp/ecosync-pip-jewel-reqs.log
    python -m pip install 'redis==7.4.1' >/tmp/ecosync-pip-jewel-redis.log
    finish_venv
  fi
  export PYTHONPATH="/workspace/repos/ansible/jewel:/workspace/repos/ansible/django-ansible-base${PYTHONPATH:+:$PYTHONPATH}"
  jwt_file="$(python tools/scripts/generate_test_jwt_keypair.py)"
  trap 'rm -f "$jwt_file"' EXIT
  export DJANGO_SETTINGS_MODULE=aap_gateway_api.tests.settings_overrides
  export ANSIBLE_GW_TEST_DB_HOST=localhost
  export DB_PORT=5432
  export GATEWAY_SECRET_KEY_FILE=/workspace/repos/ansible/jewel/tools/configs/dev_secret_key
  pytest \
    --jwt-keypair-file="$jwt_file" \
    --junit-xml=/workspace/.ecosyncbench/test-reports/jewel-hidden.xml \
    aap_gateway_api/tests/views/api/v1/user/test_user_list_performance.py
}

case "$profile" in
  django_ansible_base-hidden)
    run_django_ansible_base
    ;;
  jewel-hidden)
    run_jewel
    ;;
  *)
    echo "Unknown Ansible profile: $profile" >&2
    exit 2
    ;;
esac
SH
  chmod +x "$workspace/.ecosyncbench/run-ansible-profile.sh"
}

run_compose_profile() {
  local container_name="ecosync_${profile}_$$_${RANDOM}"
  ensure_runner_script
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect ecosyncbench/base/ansible-django-py:3.12 >/dev/null 2>&1; then
    docker build \
      --build-arg ECOSYNC_BASE_IMAGE=ecosyncbench/base/python:3.12-uv \
      --build-arg HTTP_PROXY="${ECOSYNC_DOCKER_PROXY:-}" \
      --build-arg HTTPS_PROXY="${ECOSYNC_DOCKER_PROXY:-}" \
      -t ecosyncbench/base/ansible-django-py:3.12 \
      -f "$task_dir/environment/ansible-django-py.Dockerfile" \
      "$workspace"
  fi
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
    return 0
  fi
  local cid
  cid="$(docker create --name "$container_name" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/pip-cache" \
    -w /workspace \
    -e PIP_CACHE_DIR=/pip-cache/pip \
    -e XDG_CACHE_HOME=/pip-cache/xdg \
    -e CI=1 \
    ecosyncbench/base/ansible-django-py:3.12 \
    bash /workspace/.ecosyncbench/run-ansible-profile.sh "$profile")"
  cleanup_container() { docker rm -f "$cid" >/dev/null 2>&1 || true; }
  trap cleanup_container EXIT
  docker start "$cid" >/dev/null
  local code
  code="$(docker wait "$cid")"
  docker logs "$cid"
  cleanup_container
  trap - EXIT
  return "$code"
}

case "$profile" in
  django_ansible_base-hidden|jewel-hidden)
    run_compose_profile
    ;;
  *)
    echo "Unknown profile for ansible_django_ansible_base_jewel_9b9d317bd70a: $profile" >&2
    exit 2
    ;;
esac
