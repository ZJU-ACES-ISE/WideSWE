#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
repo_root="${ECOSYNC_REPO_ROOT:-/opt/ecosyncbench}"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/django-asgiref-django-ce878fef1ed0}"
image="ecosyncbench/deps/django-asgiref-django:ce878fef1ed0"

mkdir -p "$workspace/.ecosyncbench/test-reports" "$cache_root/pip" "$cache_root/venvs"

ensure_base_image() {
  docker image inspect ecosyncbench/base/python:3.13-slim >/dev/null 2>&1 || \
    docker build -t ecosyncbench/base/python:3.13-slim \
      -f "$repo_root/benchmark/images/base/python-3.13-slim/Dockerfile" \
      "$repo_root/benchmark/images/base/python-3.13-slim"
}

ensure_image() {
  ensure_base_image
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$task_dir/environment/django-python.Dockerfile" "$task_dir/environment"
  fi
}

write_helpers() {
  mkdir -p "$workspace/.ecosyncbench"
  cat > "$workspace/.ecosyncbench/python-cache.sh" <<'SH'
prepare_cached_venv() {
  local name="$1"
  local schema="$2"
  shift 2
  local fingerprint
  fingerprint="$({
    printf '%s\n' "$schema"
    for path in "$@"; do
      if [[ -d "$path" ]]; then
        find "$path" -type f \( -name 'pyproject.toml' -o -name 'setup.cfg' -o -name 'setup.py' -o -name 'requirements*.txt' \) -print0 \
          | sort -z | xargs -0 -r sha256sum
      elif [[ -f "$path" ]]; then
        sha256sum "$path"
      fi
    done
  } | sha256sum | awk '{print $1}')"
  local venv="/cache/venvs/${name}-${fingerprint}"
  exec 9>"/cache/venvs/.${name}-${fingerprint}.lock"
  flock 9
  if [[ ! -f "$venv/.ready" ]]; then
    rm -rf "$venv"
    python -m venv "$venv"
    # shellcheck disable=SC1090
    source "$venv/bin/activate"
    export ECOSYNC_NEW_VENV=1
  else
    # shellcheck disable=SC1090
    source "$venv/bin/activate"
    export ECOSYNC_NEW_VENV=0
    flock -u 9
  fi
}

finish_cached_venv() {
  touch "$VIRTUAL_ENV/.ready"
  flock -u 9
}
SH
  cat > "$workspace/.ecosyncbench/unittest_log_to_junit.py" <<'PY'
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

log_path = Path(sys.argv[1])
xml_path = Path(sys.argv[2])
suite_name = sys.argv[3]
text = log_path.read_text(encoding="utf-8", errors="ignore")
suite = ET.Element("testsuite", name=suite_name)
case_by_key = {}
pattern = re.compile(r"^(?P<name>\w+) \((?P<classname>[^)]+)\) \.\.\. (?P<status>ok|FAIL|ERROR|skipped .*)$")
for line in text.splitlines():
    match = pattern.match(line.strip())
    if not match:
        continue
    key = (match.group("classname"), match.group("name"))
    case = ET.SubElement(
        suite,
        "testcase",
        classname=match.group("classname"),
        name=match.group("name"),
    )
    case_by_key[key] = case
    status = match.group("status")
    if status == "FAIL":
        ET.SubElement(case, "failure", message="unittest reported FAIL")
    elif status == "ERROR":
        ET.SubElement(case, "error", message="unittest reported ERROR")
    elif status.startswith("skipped"):
        ET.SubElement(case, "skipped", message=status)

cases = list(suite)
suite.set("tests", str(len(cases)))
suite.set("failures", str(sum(1 for case in cases if case.find("failure") is not None)))
suite.set("errors", str(sum(1 for case in cases if case.find("error") is not None)))
suite.set("skipped", str(sum(1 for case in cases if case.find("skipped") is not None)))
xml_path.parent.mkdir(parents=True, exist_ok=True)
ET.ElementTree(suite).write(xml_path, encoding="utf-8", xml_declaration=True)
if not cases:
    raise SystemExit("no unittest cases parsed from log")
PY
}

run_in_container() {
  local workdir="$1"
  local venv_name="$2"
  local command="$3"
  ensure_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image" bash -c 'python --version >/dev/null'
    return 0
  fi
  mkdir -p "$cache_root/venvs/$venv_name"
  timeout "${ECOSYNC_PROFILE_TIMEOUT:-1800}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e PIP_CACHE_DIR=/cache/pip \
    -e PYTHONPATH="/workspace/repos/django/asgiref:/workspace/repos/django/django:/workspace/repos/django/django/tests" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -c "$command"
}

case "$profile" in
  asgiref-hidden)
    write_helpers
    run_in_container "repos/django/asgiref" "asgiref" '
      set -euo pipefail
      source /workspace/.ecosyncbench/python-cache.sh
      prepare_cached_venv asgiref asgiref-v1 pyproject.toml setup.cfg setup.py requirements
      if [[ "$ECOSYNC_NEW_VENV" == "1" ]]; then
        python -m pip install -U pip setuptools wheel >/dev/null
        python -m pip install -e . pytest pytest-asyncio >/dev/null
        finish_cached_venv
      fi
      pytest tests/test_sync_contextvars.py --junitxml=/workspace/.ecosyncbench/test-reports/asgiref-hidden.xml
    '
    ;;
  django-hidden)
    write_helpers
    run_in_container "repos/django/django" "django" '
      set -euo pipefail
      source /workspace/.ecosyncbench/python-cache.sh
      prepare_cached_venv django django-v1 pyproject.toml setup.cfg setup.py requirements ../asgiref/pyproject.toml ../asgiref/setup.cfg ../asgiref/setup.py ../asgiref/requirements
      if [[ "$ECOSYNC_NEW_VENV" == "1" ]]; then
        python -m pip install -U pip setuptools wheel >/dev/null
        python -m pip install -e /workspace/repos/django/asgiref -e . tblib >/dev/null
        finish_cached_venv
      fi
      log=/workspace/.ecosyncbench/test-reports/django-hidden.log
      xml=/workspace/.ecosyncbench/test-reports/django-hidden.xml
      set +e
      python tests/runtests.py signals --verbosity 2 2>&1 | tee "$log"
      status=${PIPESTATUS[0]}
      set -e
      python /workspace/.ecosyncbench/unittest_log_to_junit.py "$log" "$xml" django-signals-hidden
      test -s "$xml"
      exit "$status"
    '
    ;;
  *)
    echo "Unknown profile for django_asgiref_django_ce878fef1ed0: $profile" >&2
    exit 2
    ;;
esac
