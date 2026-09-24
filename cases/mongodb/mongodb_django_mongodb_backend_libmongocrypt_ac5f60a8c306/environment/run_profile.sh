#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:?ECOSYNC_WORKSPACE required}" && pwd)"
image="ecosyncbench/deps/mongodb-django-libmongocrypt:ac5f60a8c306"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/mongodb-django-libmongocrypt-ac5f60a8c306}"
reports="${workspace}/.ecosyncbench/test-reports"

if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "${image}" >/dev/null 2>&1; then
  docker build -t "${image}" -f "${task_dir}/environment/mongodb-django-libmongocrypt.Dockerfile" "${task_dir}/environment"
fi
image_id="$(docker image inspect --format '{{.Id}}' "${image}")"

mkdir -p "${cache_root}" "${reports}"

free_port() {
  python - <<'PY'
import socket

sock = socket.socket()
sock.bind(("127.0.0.1", 0))
print(sock.getsockname()[1])
sock.close()
PY
}

start_atlas_local() {
  local port="$1"
  local name="$2"
  docker image inspect mongodb/mongodb-atlas-local:8.0 >/dev/null 2>&1 || docker pull mongodb/mongodb-atlas-local:8.0
  docker rm -f "${name}" >/dev/null 2>&1 || true
  docker run -d --name "${name}" -p "127.0.0.1:${port}:27017" mongodb/mongodb-atlas-local:8.0 >/dev/null
}

wait_atlas_local() {
  local name="$1"
  local deadline=$((SECONDS + 180))
  local state

  while (( SECONDS < deadline )); do
    state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${name}" 2>/dev/null || true)"
    case "${state}" in
      healthy)
        return 0
        ;;
      exited|dead)
        echo "MongoDB Atlas local exited before becoming healthy" >&2
        docker logs --tail 200 "${name}" >&2 || true
        return 1
        ;;
    esac
    sleep 2
  done

  echo "MongoDB Atlas local did not become healthy within 180 seconds (state=${state:-missing})" >&2
  docker logs --tail 200 "${name}" >&2 || true
  return 1
}

stop_atlas_local() {
  local name="$1"
  docker rm -f "${name}" >/dev/null 2>&1 || true
}

run_in_container() {
  local cache_subdir="$1"
  shift
  mkdir -p "${cache_root}/${cache_subdir}"
  docker run --rm \
    --network host \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -v "${workspace}:/workspace" \
    -v "${cache_root}/${cache_subdir}:/cache" \
    -e HOME=/tmp/ecosync-home \
    -e ECOSYNC_DOCKER_PREBUILD_ONLY="${ECOSYNC_DOCKER_PREBUILD_ONLY:-}" \
    -e ECOSYNC_DOCKER_WARMUP_ONLY="${ECOSYNC_DOCKER_WARMUP_ONLY:-}" \
    -e ECOSYNC_BUILD_JOBS="${ECOSYNC_BUILD_JOBS:-2}" \
    -e ECOSYNC_DEPS_IMAGE_ID="${image_id}" \
    -e MONGODB_URI="${MONGODB_URI:-}" \
    -e CRYPT_SHARED_LIB_PATH="${CRYPT_SHARED_LIB_PATH:-}" \
    -w /workspace \
    "${image}" \
    bash -lc "$*"
}

case "${profile}" in
  django_mongodb_backend-hidden)
    atlas_name="ecosync_atlas_ac5f60a8c306_$$_${RANDOM}"
    atlas_port="$(free_port)"
    export MONGODB_URI="mongodb://127.0.0.1:${atlas_port}/?directConnection=true"

    if [[ -z "${ECOSYNC_DOCKER_PREBUILD_ONLY:-}" ]]; then
      start_atlas_local "${atlas_port}" "${atlas_name}"
      trap 'stop_atlas_local "'"${atlas_name}"'"' EXIT
      wait_atlas_local "${atlas_name}"
    fi

    run_in_container django '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home /cache/downloads
      cd /workspace/repos/mongodb/django-mongodb-backend

      if [[ ! -d /cache/django_repo/.git ]]; then
        rm -rf /cache/django_repo
        git clone --depth 1 --branch mongodb-6.0.x https://github.com/mongodb-forks/django.git /cache/django_repo
      fi

      dependency_key="$({
        printf "%s\n" "${ECOSYNC_DEPS_IMAGE_ID}" "$(python --version 2>&1)"
        git -C /cache/django_repo rev-parse HEAD
        sha256sum /cache/django_repo/pyproject.toml /cache/django_repo/tests/requirements/py3.txt
        for file in pyproject.toml setup.py setup.cfg; do
          [[ ! -f "${file}" ]] || sha256sum "${file}"
        done
      } | sha256sum | cut -d" " -f1)"
      venv="/cache/venvs/${dependency_key}"
      ready="${venv}/.ecosync-dependencies-ready"
      mkdir -p /cache/venvs /cache/locks
      exec 9>"/cache/locks/${dependency_key}.lock"
      flock 9
      if [[ ! -f "${ready}" ]]; then
        rm -rf "${venv}"
        python -m venv "${venv}"
        . "${venv}/bin/activate"
        python -m pip install -U pip setuptools wheel
        python -m pip install -e ".[encryption]"
        python -m pip install -e /cache/django_repo
        python -m pip install -r /cache/django_repo/tests/requirements/py3.txt
        python -m pip install pytest pytest-django
        touch "${ready}"
      else
        . "${venv}/bin/activate"
      fi
      flock -u 9

      if [[ ! -f /cache/downloads/mongo_crypt_v1.so ]]; then
        tmp="$(mktemp -d)"
        wget -q -O "${tmp}/crypt_shared.tgz" https://downloads.mongodb.com/linux/mongo_crypt_shared_v1-linux-x86_64-enterprise-ubuntu2404-8.3.2.tgz
        tar -xzf "${tmp}/crypt_shared.tgz" -C "${tmp}" lib/mongo_crypt_v1.so
        mv "${tmp}/lib/mongo_crypt_v1.so" /cache/downloads/mongo_crypt_v1.so
        rm -rf "${tmp}"
      fi
      export CRYPT_SHARED_LIB_PATH=/cache/downloads/mongo_crypt_v1.so
      mkdir -p /tmp/ecosync-settings
      cat > /tmp/ecosync-settings/ecosync_encrypted_settings.py <<PYSETTINGS
from encrypted_settings import *  # noqa: F403
import os

DATABASES["encrypted"]["HOST"] = os.environ["MONGODB_URI"]  # noqa: F405
PYSETTINGS

      export DJANGO_SETTINGS_MODULE=ecosync_encrypted_settings
      export PYTHONPATH="/tmp/ecosync-settings:/workspace/repos/mongodb/django-mongodb-backend/.github/workflows:/workspace/repos/mongodb/django-mongodb-backend:/cache/django_repo/tests:${PYTHONPATH:-}"

      if [[ -n "${ECOSYNC_DOCKER_PREBUILD_ONLY:-}" || -n "${ECOSYNC_DOCKER_WARMUP_ONLY:-}" ]]; then
        exit 0
      fi

      python - <<PY
import os
import time
from pymongo import MongoClient

uri = os.environ["MONGODB_URI"]
deadline = time.time() + 180
last = None
while time.time() < deadline:
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.admin.command("ping")
        break
    except Exception as exc:
        last = exc
        time.sleep(2)
else:
    raise SystemExit(f"MongoDB Atlas local did not become ready: {last}")
PY

      python - /workspace/.ecosyncbench/test-reports/django-mongodb-backend-hidden.xml <<PY
import html
import ast
import re
import subprocess
import sys
from pathlib import Path

out = Path(sys.argv[1])
cases = []
line_re = re.compile(
    r"^(?P<name>\S+) \((?P<class>[^)\n]+)\)(?:\n[^\n]*)? \.\.\. "
    r"(?P<status>ok|FAIL|ERROR|skipped .*)$",
    re.M,
)
repo_tests = Path("/workspace/repos/mongodb/django-mongodb-backend/tests")


def encryption_test_ids():
    for path in sorted((repo_tests / "encryption_").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for class_node in (node for node in tree.body if isinstance(node, ast.ClassDef)):
            for method in class_node.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith(
                    "test_"
                ):
                    yield (
                        f"encryption_.{path.stem}.{class_node.name}.{method.name}",
                        method.name,
                    )


runs = [
    ("backend_.test_features", []),
    ("encryption_", list(encryption_test_ids())),
]
return_code = 0
for label, expected in runs:
    cmd = [
        sys.executable,
        "/cache/django_repo/tests/runtests.py",
        "--settings",
        "ecosync_encrypted_settings",
        "--verbosity",
        "2",
        "--parallel",
        "1",
        "--noinput",
        label,
    ]
    proc = subprocess.run(
        cmd,
        cwd="/cache/django_repo/tests",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    print(proc.stdout, end="")
    return_code = max(return_code, proc.returncode)
    observed = set()
    for match in line_re.finditer(proc.stdout):
        status_text = match.group("status")
        if status_text == "ok":
            status = "passed"
        elif status_text == "FAIL":
            status = "failed"
        elif status_text == "ERROR":
            status = "error"
        else:
            status = "skipped"
        key = (match.group("class"), match.group("name"))
        observed.add(key)
        cases.append((*key, status, ""))

    # A missing public feature can stop Django while importing the test app.
    # Keep every affected behavior visible as a failure instead of turning
    # unrelated tests into missing-to-pass entries.
    if proc.returncode != 0:
        for key in expected:
            if key not in observed:
                cases.append(
                    (*key, "error", "Test collection stopped before this behavior was reported.")
                )

if return_code != 0 and not any(status in {"failed", "error"} for _cls, _name, status, _text in cases):
    cases.append(("django_mongodb_backend.runner", "unparsed_failure", "error", ""))

failures = sum(1 for _cls, _name, status, _text in cases if status == "failed")
errors = sum(1 for _cls, _name, status, _text in cases if status == "error")
skipped = sum(1 for _cls, _name, status, _text in cases if status == "skipped")
body = [f"<testsuite name=\"django-mongodb-backend-hidden\" tests=\"{len(cases)}\" failures=\"{failures}\" errors=\"{errors}\" skipped=\"{skipped}\">"]
for cls, name, status, text in cases:
    body.append(f"  <testcase classname=\"{html.escape(cls)}\" name=\"{html.escape(name)}\">")
    if status == "failed":
        body.append("    <failure><![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]></failure>")
    elif status == "error":
        body.append("    <error><![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]></error>")
    elif status == "skipped":
        body.append("    <skipped />")
    body.append("  </testcase>")
body.append("</testsuite>")
out.write_text("\n".join(body) + "\n", encoding="utf-8")
raise SystemExit(return_code)
PY
    '
    ;;

  libmongocrypt-hidden)
    run_in_container libmongocrypt '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      cd /workspace/repos/mongodb/libmongocrypt
      python - <<'PY'
import os
import subprocess


def git_paths(*args: str) -> set[bytes]:
    output = subprocess.check_output(["git", *args, "-z"])
    return {path for path in output.split(b"\0") if path}


changed = git_paths("diff", "--name-only", "HEAD")
changed.update(git_paths("ls-files", "--others", "--exclude-standard"))
for raw_path in git_paths("ls-files") - changed:
    path = os.fsdecode(raw_path)
    try:
        os.utime(path, ns=(1_000_000_000, 1_000_000_000), follow_symlinks=False)
    except FileNotFoundError:
        pass
PY
      config_key="$({
        printf "%s\n" "${ECOSYNC_DEPS_IMAGE_ID}" "$(cmake --version | head -n1)"
        find . -type f \( -name CMakeLists.txt -o -path "./cmake/*" \) -print0 \
          | sort -z \
          | xargs -0 sha256sum
      } | sha256sum | cut -d" " -f1)"
      build="/cache/build-${config_key}"
      mkdir -p /cache/locks
      exec 9>"/cache/locks/${config_key}.lock"
      flock 9
      # Base, Gold, and candidate workspaces share the configured build tree.
      # Recompile the task-owned source and test objects so one mode cannot
      # reuse an object file from another mode solely because of copied mtimes.
      touch src/mongocrypt-ctx-encrypt.c test/test-mongocrypt-ctx-encrypt.c
      cmake -S . -B "${build}" -G Ninja \
        -DCMAKE_BUILD_TYPE=RelWithDebInfo \
        -DBUILD_TESTING=ON \
        -DENABLE_MORE_WARNINGS_AS_ERRORS=OFF \
        -DBUILD_VERSION=1.14.0
      cmake --build "${build}" --target test-mongocrypt -j"${ECOSYNC_BUILD_JOBS}"

      if [[ -n "${ECOSYNC_DOCKER_PREBUILD_ONLY:-}" || -n "${ECOSYNC_DOCKER_WARMUP_ONLY:-}" ]]; then
        exit 0
      fi

      python - "${build}/test-mongocrypt" /workspace/.ecosyncbench/test-reports/libmongocrypt-hidden.xml <<PY
import html
import re
import subprocess
import sys
from pathlib import Path

binary = Path(sys.argv[1])
out = Path(sys.argv[2])
source = Path("test/test-mongocrypt-ctx-encrypt.c").read_text(encoding="utf-8")
install = source.split("void _mongocrypt_tester_install_ctx_encrypt", 1)[1]
end = install.find(chr(10) + "}")
if end != -1:
    install = install[:end]
tests = re.findall(r"INSTALL_TEST\(([^)]+)\);", install)
cases = []

for name in tests:
    proc = subprocess.run([str(binary), name], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    cases.append((name, proc.returncode, proc.stdout))

failures = sum(1 for _name, code, _text in cases if code != 0)
body = [f"<testsuite name=\"libmongocrypt-hidden\" tests=\"{len(cases)}\" failures=\"{failures}\" errors=\"0\" skipped=\"0\">"]
for name, code, text in cases:
    body.append(f"  <testcase classname=\"libmongocrypt.ctx_encrypt\" name=\"{html.escape(name)}\">")
    if code != 0:
        body.append("    <failure><![CDATA[" + text[-8000:].replace("]]>", "]]]]><![CDATA[>") + "]]></failure>")
    body.append("  </testcase>")
body.append("</testsuite>")
out.write_text("\\n".join(body) + "\\n", encoding="utf-8")
raise SystemExit(1 if failures else 0)
PY
      flock -u 9
    '
    ;;

  *)
    echo "unknown profile: ${profile}" >&2
    exit 2
    ;;
esac
