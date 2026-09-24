#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:?ECOSYNC_WORKSPACE required}" && pwd)"
export ECOSYNC_PROFILE="$profile"

ensure_runner_script() {
  mkdir -p "$workspace/.ecosyncbench"
  cat > "$workspace/.ecosyncbench/run-ansible-metrics-profile.sh" <<'SH'
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
  service postgresql start >/tmp/ecosync-postgres.log 2>&1 || {
    cat /tmp/ecosync-postgres.log >&2
    return 1
  }
  su postgres -c "psql -v ON_ERROR_STOP=1 -tc \"SELECT 1 FROM pg_roles WHERE rolname='metrics_service'\" | grep -q 1 || createuser metrics_service" >/dev/null
  su postgres -c "psql -v ON_ERROR_STOP=1 -c \"ALTER USER metrics_service CREATEDB SUPERUSER PASSWORD 'metrics_service';\"" >/dev/null
  su postgres -c "psql -v ON_ERROR_STOP=1 -tc \"SELECT 1 FROM pg_roles WHERE rolname='awx'\" | grep -q 1 || createuser awx" >/dev/null
  su postgres -c "psql -v ON_ERROR_STOP=1 -c \"ALTER USER awx CREATEDB SUPERUSER PASSWORD 'awx';\"" >/dev/null
  su postgres -c "psql -v ON_ERROR_STOP=1 -tc \"SELECT 1 FROM pg_roles WHERE rolname='myuser'\" | grep -q 1 || createuser myuser" >/dev/null
  su postgres -c "psql -v ON_ERROR_STOP=1 -c \"ALTER USER myuser CREATEDB SUPERUSER PASSWORD 'mypassword';\"" >/dev/null
  su postgres -c "psql -v ON_ERROR_STOP=1 -tc \"SELECT 1 FROM pg_database WHERE datname='metrics_service'\" | grep -q 1 || createdb -O metrics_service metrics_service" >/dev/null
  su postgres -c "psql -v ON_ERROR_STOP=1 -tc \"SELECT 1 FROM pg_database WHERE datname='awx'\" | grep -q 1 || createdb -O awx awx" >/dev/null
  su postgres -c "psql -v ON_ERROR_STOP=1 -d awx -c \"GRANT ALL PRIVILEGES ON DATABASE awx TO myuser; GRANT ALL ON SCHEMA public TO myuser;\"" >/dev/null
}

load_metrics_utility_schema() {
  local sql_dir="$workspace/repos/ansible/metrics-utility/tools/docker"
  local exists
  exists="$(su postgres -c "psql -At -d awx -c \"SELECT to_regclass('public.main_unifiedjob') IS NOT NULL;\"")"
  if [[ "$exists" == "t" ]]; then
    return 0
  fi
  su postgres -c "psql -v ON_ERROR_STOP=1 -d awx" <"$sql_dir/latest.sql" >/tmp/ecosync-awx-schema.log 2>&1 || {
    cat /tmp/ecosync-awx-schema.log >&2
    return 1
  }
  for file in functions.sql conf_setting.sql main_hostmetric.sql main_instance.sql main_jobhostsummary.sql dab_feature_flags.sql; do
    su postgres -c "psql -v ON_ERROR_STOP=1 -d awx" <"$sql_dir/$file" >>/tmp/ecosync-awx-schema.log 2>&1 || {
      cat /tmp/ecosync-awx-schema.log >&2
      return 1
    }
  done
}

run_metrics_utility() {
  start_postgres
  cd "$workspace/repos/ansible/metrics-utility"
  local fingerprint
  fingerprint="$({
    printf '%s\n' 'metrics-utility-venv-v1'
    sha256sum pyproject.toml uv.lock
  } | sha256sum | cut -d' ' -f1)"
  local venv="/python-cache/venvs/metrics-utility-$fingerprint"
  export UV_PROJECT_ENVIRONMENT="$venv"
  export METRICS_UTILITY_DB_HOST=127.0.0.1
  export METRICS_UTILITY_DB_NAME=awx
  export METRICS_UTILITY_DB_USER=awx
  export METRICS_UTILITY_DB_PASSWORD=awx
  export METRICS_SERVICE_DB_HOST=127.0.0.1
  export METRICS_SERVICE_DB_NAME=metrics_service
  export METRICS_SERVICE_DB_USER=metrics_service
  export METRICS_SERVICE_DB_PASSWORD=metrics_service
  (
    flock 9
    uv sync --frozen --group dev
  ) 9>"/python-cache/locks/metrics-utility-$fingerprint.lock"
  load_metrics_utility_schema
  "$venv/bin/python" -m pytest -q \
    --junitxml="$reports/metrics-utility-hidden.xml" \
    metrics_utility/test/gather/test_feature_flags_service_gather.py \
    metrics_utility/test/library/test_collectors_task_executions_service.py \
    metrics_utility/test/test_anonymized_rollups/big_test1/test_all.py \
    metrics_utility/test/test_anonymized_rollups/big_test1/test_all_no_events.py \
    metrics_utility/test/test_anonymized_rollups/big_test1/test_big_test_job1.py \
    metrics_utility/test/test_anonymized_rollups/big_test1/test_big_test_job2.py \
    metrics_utility/test/test_anonymized_rollups/big_test1/test_big_test_job3.py \
    metrics_utility/test/test_anonymized_rollups/big_test1/test_big_test_job4.py \
    metrics_utility/test/test_anonymized_rollups/big_test1/test_big_test_job5.py \
    metrics_utility/test/test_anonymized_rollups/big_test1/test_big_test_job6.py \
    metrics_utility/test/test_anonymized_rollups/big_test1/test_big_test_job7.py \
    metrics_utility/test/test_anonymized_rollups/big_test1/test_big_test_job8.py \
    metrics_utility/test/test_anonymized_rollups/test_anonymized_rollups_module.py \
    metrics_utility/test/test_anonymized_rollups/test_feature_flags_anonymized_rollup.py \
    metrics_utility/test/test_anonymized_rollups/test_from_gather_to_json.py \
    metrics_utility/test/test_anonymized_rollups/test_jobs_anonymized_rollups.py \
    metrics_utility/test/test_anonymized_rollups/test_json.py \
    metrics_utility/test/test_anonymized_rollups/test_multiple_files.py \
    metrics_utility/test/test_anonymized_rollups/test_task_executions_anonymized_rollup.py
}

run_metrics_service() {
  start_postgres
  export PYTHONPATH="$workspace/repos/ansible/metrics-utility${PYTHONPATH:+:$PYTHONPATH}"
  cd "$workspace/repos/ansible/metrics-service"
  local fingerprint
  fingerprint="$({
    printf '%s\n' 'metrics-service-utility-venv-v1'
    sha256sum pyproject.toml uv.lock ../metrics-utility/pyproject.toml ../metrics-utility/uv.lock
  } | sha256sum | cut -d' ' -f1)"
  local venv="/python-cache/venvs/metrics-service-$fingerprint"
  export UV_PROJECT_ENVIRONMENT="$venv"
  export METRICS_SERVICE_MODE=test
  export METRICS_SERVICE_DATABASES__DEFAULT__HOST=127.0.0.1
  export METRICS_SERVICE_DATABASES__DEFAULT__USER=metrics_service
  export METRICS_SERVICE_DATABASES__DEFAULT__PASSWORD=metrics_service
  export METRICS_SERVICE_DATABASES__AWX__HOST=127.0.0.1
  export METRICS_SERVICE_DATABASES__AWX__USER=awx
  export METRICS_SERVICE_DATABASES__AWX__PASSWORD=awx
  (
    flock 9
    uv sync --frozen --group dev
    uv pip install --python "$venv/bin/python" --no-deps --no-build-isolation -e ../metrics-utility
  ) 9>"/python-cache/locks/metrics-service-$fingerprint.lock"
  "$venv/bin/python" -m pytest -q \
    --junitxml="$reports/metrics-service-hidden.xml" \
    tests/unit/tasks/collectors/test_collect_daily_metrics.py \
    tests/unit/tasks/test_cron_scheduler.py \
    tests/unit/tasks/test_task_groups.py
}

case "$profile" in
  metrics_utility-hidden)
    run_metrics_utility
    ;;
  metrics_service-hidden)
    run_metrics_service
    ;;
  *)
    echo "Unknown Ansible metrics profile: $profile" >&2
    exit 2
    ;;
esac
SH
  chmod +x "$workspace/.ecosyncbench/run-ansible-metrics-profile.sh"
}

run_compose_profile() {
  local container_name="ecosync_${profile}_$$_${RANDOM}"
  ensure_runner_script
  cd "$task_dir/environment"
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
    docker image inspect ecosyncbench/base/ansible-django-py:3.12 >/dev/null
    return 0
  fi
  ECOSYNC_WORKSPACE="$workspace" ECOSYNC_PROFILE="$profile" docker compose run --rm --name "$container_name" ansible-metrics-python
}

case "$profile" in
  metrics_service-hidden|metrics_utility-hidden)
    run_compose_profile
    ;;
  *)
    echo "Unknown profile for ansible_metrics_service_metrics_utility_ad5f5516dca0: $profile" >&2
    exit 2
    ;;
esac
