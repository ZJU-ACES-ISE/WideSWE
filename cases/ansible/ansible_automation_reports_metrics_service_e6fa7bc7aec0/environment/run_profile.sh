#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:?ECOSYNC_WORKSPACE required}" && pwd)"
export ECOSYNC_PROFILE="$profile"

ensure_runner_script() {
  mkdir -p "$workspace/.ecosyncbench"
  cat > "$workspace/.ecosyncbench/run-ansible-aap-cost-profile.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

profile="${ECOSYNC_PROFILE:?ECOSYNC_PROFILE required}"
workspace=/workspace
reports="$workspace/.ecosyncbench/test-reports"
mkdir -p "$reports" /python-cache/venvs /python-cache/uv /python-cache/pip /python-cache/locks
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/python-cache/uv}"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-/python-cache/pip}"
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1

start_postgres() {
  cat >>/etc/postgresql/17/main/postgresql.conf <<'PGCONF'
# WIDESWE databases are disposable; avoid slow durable writes on evaluator storage.
fsync = off
synchronous_commit = off
full_page_writes = off
autovacuum = off
PGCONF
  service postgresql start >/tmp/ecosync-postgres.log 2>&1 || {
    cat /tmp/ecosync-postgres.log >&2
    return 1
  }
  su postgres -c "psql -v ON_ERROR_STOP=1 -tc \"SELECT 1 FROM pg_roles WHERE rolname='aapdashboard'\" | grep -q 1 || createuser aapdashboard" >/dev/null
  su postgres -c "psql -v ON_ERROR_STOP=1 -c \"ALTER USER aapdashboard CREATEDB PASSWORD 'aapdashboard';\"" >/dev/null
  su postgres -c "psql -v ON_ERROR_STOP=1 -tc \"SELECT 1 FROM pg_database WHERE datname='aapdashboard'\" | grep -q 1 || createdb -O aapdashboard aapdashboard" >/dev/null
  su postgres -c "psql -v ON_ERROR_STOP=1 -tc \"SELECT 1 FROM pg_roles WHERE rolname='metrics_service'\" | grep -q 1 || createuser metrics_service" >/dev/null
  su postgres -c "psql -v ON_ERROR_STOP=1 -c \"ALTER USER metrics_service CREATEDB PASSWORD 'metrics_service';\"" >/dev/null
  su postgres -c "psql -v ON_ERROR_STOP=1 -tc \"SELECT 1 FROM pg_roles WHERE rolname='awx'\" | grep -q 1 || createuser awx" >/dev/null
  su postgres -c "psql -v ON_ERROR_STOP=1 -c \"ALTER USER awx CREATEDB PASSWORD 'awx';\"" >/dev/null
  su postgres -c "psql -v ON_ERROR_STOP=1 -tc \"SELECT 1 FROM pg_database WHERE datname='metrics_service'\" | grep -q 1 || createdb -O metrics_service metrics_service" >/dev/null
  su postgres -c "psql -v ON_ERROR_STOP=1 -tc \"SELECT 1 FROM pg_database WHERE datname='awx'\" | grep -q 1 || createdb -O awx awx" >/dev/null
}

run_automation_reports() {
  start_postgres
  cd "$workspace/repos/ansible/automation-reports"
  local fingerprint
  fingerprint="$({
    printf '%s\n' 'automation-reports-venv-v2-requirements-build'
    sha256sum requirements-build.txt
  } | sha256sum | cut -d' ' -f1)"
  local venv="/python-cache/venvs/automation-reports-$fingerprint"
  if [[ ! -x "$venv/bin/python" ]]; then
    uv venv --seed "$venv"
    uv pip install --python "$venv/bin/python" -r requirements-build.txt
  fi
  export DB_NAME=aapdashboard
  export DB_USER=aapdashboard
  export DB_PASSWORD=aapdashboard
  export DB_HOST=127.0.0.1
  export DB_PORT=5432
  export PYTHONPATH="$workspace/repos/ansible/automation-reports/src:${PYTHONPATH:-}"
  cat > src/backend/django_config/local_settings.py <<'PY'
import os

DB_NAME = os.environ.get("DB_NAME", "aapdashboard")
DB_USER = os.environ.get("DB_USER", "aapdashboard")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "aapdashboard")
DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql_psycopg2",
        "NAME": DB_NAME,
        "USER": DB_USER,
        "PASSWORD": DB_PASSWORD,
        "HOST": DB_HOST,
        "PORT": DB_PORT,
    },
    "TEST": {
        "ENGINE": "django.db.backends.postgresql_psycopg2",
        "NAME": f"test_{DB_NAME}",
        "USER": DB_USER,
        "PASSWORD": DB_PASSWORD,
        "HOST": DB_HOST,
        "PORT": DB_PORT,
    },
}

SECRET_KEY = "ecosyncbench-test-secret"
DATABASE_KEY = "ecosyncbench-test-database-key"
DEBUG = False
PY
  "$venv/bin/python" -m pytest -q -o addopts="" --ds=backend.tests.settings_for_test \
    src/backend/tests/unit/test_cost_per_elapsed_second.py \
    src/backend/tests/unit/test_views.py \
    --junitxml="$reports/automation-reports-hidden.xml"
}

run_metrics_service() {
  start_postgres
  cd "$workspace/repos/ansible/metrics-service"
  local fingerprint
  fingerprint="$({
    printf '%s\n' 'metrics-service-venv-v1'
    sha256sum pyproject.toml uv.lock
  } | sha256sum | cut -d' ' -f1)"
  local venv="/python-cache/venvs/metrics-service-$fingerprint"
  export METRICS_SERVICE_MODE=test
  export METRICS_SERVICE_DATABASES__DEFAULT__HOST=127.0.0.1
  export METRICS_SERVICE_DATABASES__DEFAULT__USER=metrics_service
  export METRICS_SERVICE_DATABASES__DEFAULT__PASSWORD=metrics_service
  export METRICS_SERVICE_DATABASES__AWX__HOST=127.0.0.1
  export METRICS_SERVICE_DATABASES__AWX__USER=awx
  export METRICS_SERVICE_DATABASES__AWX__PASSWORD=awx
  export UV_PROJECT_ENVIRONMENT="$venv"
  (
    flock 9
    uv sync --frozen --group dev
  ) 9>"/python-cache/locks/metrics-service-$fingerprint.lock"
  uv run pytest -q -o addopts="" \
    tests/coverage/dashboard_reports/test_dashboard_models_extended.py \
    tests/coverage/general/test_final_push.py \
    tests/unit/dashboard_reports/test_daily_subscription_cost.py \
    tests/unit/dashboard_reports/test_export_urls.py \
    tests/unit/dashboard_reports/test_report_view_data.py \
    tests/unit/dashboard_reports/test_report_views.py \
    --junitxml="$reports/metrics-service-hidden.xml"
}

case "$profile" in
  automation_reports-hidden)
    run_automation_reports
    ;;
  metrics_service-hidden)
    run_metrics_service
    ;;
  *)
    echo "Unknown Ansible AAP cost profile: $profile" >&2
    exit 2
    ;;
esac
SH
  chmod +x "$workspace/.ecosyncbench/run-ansible-aap-cost-profile.sh"
}

run_compose_profile() {
  local container_name="ecosync_${profile}_$$_${RANDOM}"
  ensure_runner_script
  cd "$task_dir/environment"
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect ecosyncbench/deps/ansible-aap-cost-py:e6fa7bc7aec0 >/dev/null 2>&1; then
    ECOSYNC_WORKSPACE="$workspace" ECOSYNC_PROFILE="$profile" docker compose build ansible-aap-python
  fi
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker image inspect ecosyncbench/deps/ansible-aap-cost-py:e6fa7bc7aec0 >/dev/null
    return 0
  fi
  ECOSYNC_WORKSPACE="$workspace" ECOSYNC_PROFILE="$profile" docker compose run --rm --name "$container_name" ansible-aap-python
}

case "$profile" in
  automation_reports-hidden|metrics_service-hidden)
    run_compose_profile
    ;;
  *)
    echo "Unknown profile for ansible_automation_reports_metrics_service_e6fa7bc7aec0: $profile" >&2
    exit 2
    ;;
esac
