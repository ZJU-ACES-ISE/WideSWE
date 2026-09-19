from __future__ import annotations

import base64
import shlex


POSTGRES_BOOTSTRAP_MARKER = "/opt/ecosync/postgres-bootstrap-ready"
POSTGRES_DEBUG_SETTINGS = "fsync = off\nsynchronous_commit = off\nfull_page_writes = off\n"


def postgres_bootstrap_sql() -> str:
    return """\
DO $ecosync$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gw') THEN
    CREATE ROLE gw LOGIN;
  END IF;
  ALTER ROLE gw PASSWORD 'password';
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'aapdashboard') THEN
    CREATE ROLE aapdashboard LOGIN;
  END IF;
  ALTER ROLE aapdashboard CREATEDB PASSWORD 'aapdashboard';
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'metrics_service') THEN
    CREATE ROLE metrics_service LOGIN;
  END IF;
  ALTER ROLE metrics_service CREATEDB PASSWORD 'metrics_service';
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'awx') THEN
    CREATE ROLE awx LOGIN;
  END IF;
  ALTER ROLE awx CREATEDB PASSWORD 'awx';
  ALTER ROLE postgres PASSWORD 'password';
END
$ecosync$;
SELECT 'CREATE DATABASE gw_db OWNER gw'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'gw_db')
\gexec
SELECT 'CREATE DATABASE aapdashboard OWNER aapdashboard'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'aapdashboard')
\gexec
SELECT 'CREATE DATABASE metrics_service OWNER metrics_service'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'metrics_service')
\gexec
"""


def postgres_bootstrap_sql_base64() -> str:
    return base64.b64encode(postgres_bootstrap_sql().encode("utf-8")).decode("ascii")


def automation_reports_local_settings() -> str:
    return """\
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
    }
}

SECRET_KEY = "ecosyncbench-test-secret"
DATABASE_KEY = "ecosyncbench-test-database-key"
DEBUG = False
"""


def automation_reports_local_settings_base64() -> str:
    return base64.b64encode(automation_reports_local_settings().encode("utf-8")).decode("ascii")


def postgres_image_bootstrap_dockerfile_block(task_id: str) -> list[str]:
    if task_id != "ansible_automation_reports_metrics_service_e6fa7bc7aec0":
        return []
    sql_base64 = shlex.quote(postgres_bootstrap_sql_base64())
    return [
        "RUN if [ -x /etc/init.d/postgresql ]; then \\",
        "      for conf in /etc/postgresql/*/main/postgresql.conf; do [ ! -f \"$conf\" ] || "
        "printf '%s\\n' 'fsync = off' 'synchronous_commit = off' 'full_page_writes = off' "
        ">>\"$conf\"; done; \\",
        "      service postgresql start; \\",
        f"      printf %s {sql_base64} | base64 -d | su postgres -c \"psql -v ON_ERROR_STOP=1\"; \\",
        "      service postgresql stop; \\",
        f"      touch {POSTGRES_BOOTSTRAP_MARKER}; \\",
        "    fi",
    ]
