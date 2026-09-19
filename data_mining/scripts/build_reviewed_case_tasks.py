#!/usr/bin/env python3
"""Build matrix-ready task artifacts from manually reviewed case drafts.

This script is intentionally placed under data_mining. It converts reviewed
drafts into a benchmark-like task layout for construction-time matrix checks.
Promotion to benchmark/tasks still requires a passing matrix and a separate
release audit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required: python3 -m pip install pyyaml") from exc


REPO_ROOT = Path(__file__).resolve().parents[2]

HOME_ASSISTANT_TOP10: list[dict[str, str]] = [
    {
        "full_name": "home-assistant/core",
        "name": "core",
        "url": "https://github.com/home-assistant/core.git",
        "local_path": "repos/home-assistant/core",
        "role": "core_backend",
    },
    {
        "full_name": "home-assistant/home-assistant.io",
        "name": "home-assistant.io",
        "url": "https://github.com/home-assistant/home-assistant.io.git",
        "local_path": "repos/home-assistant/docs",
        "role": "documentation_site",
    },
    {
        "full_name": "home-assistant/operating-system",
        "name": "operating-system",
        "url": "https://github.com/home-assistant/operating-system.git",
        "local_path": "repos/home-assistant/operating-system",
        "role": "os_image",
    },
    {
        "full_name": "home-assistant/frontend",
        "name": "frontend",
        "url": "https://github.com/home-assistant/frontend.git",
        "local_path": "repos/home-assistant/frontend",
        "role": "web_frontend",
    },
    {
        "full_name": "home-assistant/android",
        "name": "android",
        "url": "https://github.com/home-assistant/android.git",
        "local_path": "repos/home-assistant/android",
        "role": "android_client",
    },
    {
        "full_name": "home-assistant/iOS",
        "name": "ios",
        "url": "https://github.com/home-assistant/iOS.git",
        "local_path": "repos/home-assistant/ios",
        "role": "ios_client",
    },
    {
        "full_name": "home-assistant/addons",
        "name": "addons",
        "url": "https://github.com/home-assistant/addons.git",
        "local_path": "repos/home-assistant/addons",
        "role": "addons",
    },
    {
        "full_name": "home-assistant/supervisor",
        "name": "supervisor",
        "url": "https://github.com/home-assistant/supervisor.git",
        "local_path": "repos/home-assistant/supervisor",
        "role": "supervisor_service",
    },
    {
        "full_name": "home-assistant/supervised-installer",
        "name": "supervised-installer",
        "url": "https://github.com/home-assistant/supervised-installer.git",
        "local_path": "repos/home-assistant/supervised-installer",
        "role": "supervised_installer",
    },
    {
        "full_name": "home-assistant/Iconic",
        "name": "iconic",
        "url": "https://github.com/home-assistant/Iconic.git",
        "local_path": "repos/home-assistant/Iconic",
        "role": "iconic_app_clip",
    },
]

SNAPSHOT_CACHE: dict[tuple[str, str], str] = {}

PYTHON_DEPENDENCY_FILES = {
    "core": [
        "pyproject.toml",
        "requirements.txt",
        "requirements_test.txt",
        "requirements_test_pre_commit.txt",
        "homeassistant/package_constraints.txt",
    ],
    "supervisor": [
        "pyproject.toml",
        "requirements.txt",
        "requirements_tests.txt",
        "tox.ini",
    ],
}

ANDROID_DEPENDENCY_FILES = [
    "settings.gradle.kts",
    "build.gradle.kts",
    "gradle/libs.versions.toml",
    "gradle/wrapper/gradle-wrapper.properties",
    "build-logic/settings.gradle.kts",
    "build-logic/convention/build.gradle.kts",
    "app/build.gradle.kts",
    "common/build.gradle.kts",
    "testing-unit/build.gradle.kts",
    "gradle.lockfile",
    "app/gradle.lockfile",
    "common/gradle.lockfile",
]


def run(cmd: list[str], *, cwd: Path = REPO_ROOT, stdout: int | None = None) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(cmd, cwd=cwd, stdout=stdout, stderr=subprocess.PIPE, check=True)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        rendered = " ".join(cmd)
        raise SystemExit(f"command failed: {rendered}\n{stderr}") from exc


def text(cmd: list[str], *, cwd: Path = REPO_ROOT) -> str:
    proc = run(cmd, cwd=cwd, stdout=subprocess.PIPE)
    return proc.stdout.decode("utf-8", errors="replace").strip()


def git(repo: Path, *args: str, stdout: int | None = None) -> subprocess.CompletedProcess[bytes]:
    return run(["git", "-C", str(repo), *args], stdout=stdout)


def git_retry(repo: Path, *args: str, attempts: int = 3) -> subprocess.CompletedProcess[bytes]:
    last_error = ""
    for attempt in range(1, attempts + 1):
        proc = subprocess.run(
            ["git", "-c", "http.version=HTTP/1.1", "-C", str(repo), *args],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if proc.returncode == 0:
            return proc
        last_error = proc.stderr.decode("utf-8", errors="replace").strip()
        if attempt < attempts:
            time.sleep(2 * attempt)
    rendered = " ".join(["git", "-c", "http.version=HTTP/1.1", "-C", str(repo), *args])
    raise SystemExit(f"command failed after {attempts} attempts: {rendered}\n{last_error}")


def git_text(repo: Path, *args: str) -> str:
    return text(["git", "-C", str(repo), *args])


def commit_exists(repo: Path, sha: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc.returncode == 0


def ensure_commit(repo: Path, sha: str) -> None:
    if commit_exists(repo, sha):
        return
    print(f"[git-fetch-commit] {repo} {sha[:12]}", flush=True)
    git_retry(repo, "fetch", "--depth=1", "origin", sha)
    if not commit_exists(repo, sha):
        raise SystemExit(f"commit is still missing after fetch: {repo} {sha}")


def commit_date(repo: Path, sha: str) -> datetime:
    value = git_text(repo, "show", "-s", "--format=%cI", sha)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def commit_date_text(repo: Path, sha: str) -> str:
    return git_text(repo, "show", "-s", "--format=%cI", sha)


def default_remote_ref(repo: Path) -> str:
    ref = subprocess.run(
        ["git", "-C", str(repo), "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if ref.returncode == 0:
        value = ref.stdout.decode().strip()
        if value.startswith("origin/"):
            return value

    symref = text(["git", "-C", str(repo), "ls-remote", "--symref", "origin", "HEAD"])
    for line in symref.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            branch = line.split()[1].removeprefix("refs/heads/")
            return f"origin/{branch}"
    raise SystemExit(f"cannot determine default branch for {repo}")


def github_api_json(path: str) -> Any:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "ecosyncbench-case-builder",
        },
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    last_error = ""
    for attempt in range(1, 4):
        try:
            print(f"[github-api] attempt={attempt} {path}", flush=True)
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # urllib raises several network-specific exception classes.
            last_error = repr(exc)
            if attempt < 3:
                time.sleep(2 * attempt)
    raise SystemExit(f"GitHub API request failed after 3 attempts: {path}\n{last_error}")


def github_default_branch(full_name: str) -> str:
    data = github_api_json(f"/repos/{full_name}")
    branch = data.get("default_branch")
    if not branch:
        raise SystemExit(f"GitHub API did not return default_branch for {full_name}")
    return str(branch)


def github_commit_before(full_name: str, before: datetime) -> str:
    default_branch = github_default_branch(full_name)
    params = urllib.parse.urlencode(
        {
            "sha": default_branch,
            "until": before.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "per_page": 1,
        }
    )
    commits = github_api_json(f"/repos/{full_name}/commits?{params}")
    if not commits:
        raise SystemExit(f"GitHub API found no commit before {before.isoformat()} for {full_name}")
    sha = commits[0].get("sha")
    if not sha:
        raise SystemExit(f"GitHub API commit response missing sha for {full_name}")
    return str(sha)


def fetch_history_for_date(repo: Path, remote_ref: str, before: datetime, days: int) -> None:
    branch = remote_ref.removeprefix("origin/")
    since = (before - timedelta(days=days)).date().isoformat()
    git_retry(repo, "fetch", "--filter=blob:none", f"--shallow-since={since}", "origin", f"{branch}:refs/remotes/origin/{branch}")


def snapshot_before(repo: Path, before: datetime) -> str:
    head = git_text(repo, "rev-parse", "HEAD")
    head_date = commit_date(repo, head)
    if head_date <= before:
        return head

    remote_ref = default_remote_ref(repo)
    for days in (120, 365, 1095):
        fetch_history_for_date(repo, remote_ref, before, days)
        proc = subprocess.run(
            ["git", "-C", str(repo), "rev-list", "-n", "1", f"--before={before.isoformat()}", remote_ref],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        sha = proc.stdout.decode().strip()
        if proc.returncode == 0 and sha:
            return sha
    raise SystemExit(f"cannot find snapshot before {before.isoformat()} for {repo} {remote_ref}")


def snapshot_before_api(cfg: dict[str, str], before: datetime) -> str:
    repo = ensure_local_repo(cfg)
    cache_key = (cfg["full_name"], before.astimezone(timezone.utc).isoformat())
    if cache_key in SNAPSHOT_CACHE:
        sha = SNAPSHOT_CACHE[cache_key]
        print(f"[snapshot-cache] {cfg['full_name']} {sha[:12]}", flush=True)
        ensure_commit(repo, sha)
        return sha

    head = git_text(repo, "rev-parse", "HEAD")
    head_date = commit_date(repo, head)
    if head_date <= before:
        print(f"[snapshot-local-head] {cfg['full_name']} {head[:12]} date={head_date.isoformat()}", flush=True)
        SNAPSHOT_CACHE[cache_key] = head
        return head

    print(f"[snapshot-api] {cfg['full_name']} before={before.isoformat()}", flush=True)
    sha = github_commit_before(cfg["full_name"], before)
    ensure_commit(repo, sha)
    SNAPSHOT_CACHE[cache_key] = sha
    return sha


def write_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def docker_chown_tree(path: Path) -> bool:
    uid = os.getuid()
    gid = os.getgid()
    for image in (
        "ecosyncbench/base/android-sdk:36",
        "ecosyncbench/base/python:3.14-slim",
        "alpine:latest",
        "busybox:latest",
    ):
        present = subprocess.run(["docker", "image", "inspect", image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if present.returncode != 0:
            continue
        proc = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{path.resolve()}:/target",
                "--entrypoint",
                "sh",
                image,
                "-c",
                f"chown -R {uid}:{gid} /target",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if proc.returncode == 0:
            return True
    return False


def remove_tree(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except PermissionError:
        if not docker_chown_tree(path):
            raise
        shutil.rmtree(path)


def repo_config_by_full_name() -> dict[str, dict[str, str]]:
    return {item["full_name"]: item for item in HOME_ASSISTANT_TOP10}


def repo_config_by_name() -> dict[str, dict[str, str]]:
    return {item["name"]: item for item in HOME_ASSISTANT_TOP10}


def ensure_local_repo(cfg: dict[str, str]) -> Path:
    repo_path = REPO_ROOT / cfg["local_path"]
    if (repo_path / ".git").exists():
        return repo_path
    repo_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "git",
            "clone",
            "--filter=blob:none",
            "--depth=1",
            cfg["url"],
            str(repo_path),
        ],
        cwd=REPO_ROOT,
        check=True,
    )
    return repo_path


def load_draft(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def task_type_from_categories(categories: list[str]) -> str:
    if "bug_fix" in categories:
        return "bug_fix"
    if "feature_addition" in categories:
        return "feature_addition"
    return "feature_modification"


def profile_for_repo(repo_name: str) -> str:
    return {
        "core": "core-pytest",
        "android": "android-gradle-unit",
        "ios": "ios-xcode-unit",
        "supervisor": "supervisor-pytest",
    }.get(repo_name, f"{repo_name}-tests")


def generate_patch(repo: Path, base: str, gold: str, patch_path: Path, paths: list[str] | None = None) -> None:
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "-C", str(repo), "diff", "--binary", base, gold]
    if paths:
        cmd.extend(["--", *paths])
    with patch_path.open("wb") as handle:
        subprocess.run(cmd, cwd=REPO_ROOT, stdout=handle, stderr=subprocess.PIPE, check=True)


def git_show_bytes(repo: Path, commit: str, rel_path: str) -> bytes | None:
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{rel_path}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def dependency_files_for_repo(repo_name: str) -> list[str]:
    if repo_name in PYTHON_DEPENDENCY_FILES:
        return PYTHON_DEPENDENCY_FILES[repo_name]
    if repo_name == "android":
        return ANDROID_DEPENDENCY_FILES
    return []


def dependency_fingerprint(repo: Path, commits: str | list[str], repo_name: str) -> tuple[str, list[str]]:
    paths = dependency_files_for_repo(repo_name)
    commit_list = [commits] if isinstance(commits, str) else list(commits)
    digest = hashlib.sha256()
    present: list[str] = []
    for rel_path in paths:
        digest.update(rel_path.encode("utf-8"))
        digest.update(b"\0")
        for commit in commit_list:
            digest.update(commit.encode("utf-8"))
            digest.update(b"\0")
            content = git_show_bytes(repo, commit, rel_path)
            if content is None:
                digest.update(b"MISSING")
                digest.update(b"\0")
                continue
            if rel_path not in present:
                present.append(rel_path)
            digest.update(content)
            digest.update(b"\0")
    if not paths:
        digest.update(f"{repo_name}:{','.join(commit_list)}".encode("utf-8"))
    return digest.hexdigest()[:12], present


def dependency_image_for_repo(repo_name: str, fingerprint: str) -> str | None:
    if repo_name in {"core", "supervisor"}:
        return f"ecosyncbench/deps/home-assistant-{repo_name}-py:{fingerprint}"
    if repo_name == "android":
        return f"ecosyncbench/deps/home-assistant-android-gradle:{fingerprint}"
    return None


def requirements_files_for_repo(repo_name: str) -> str:
    if repo_name == "supervisor":
        return "requirements.txt requirements_tests.txt"
    return "requirements.txt requirements_test.txt"


def android_test_filters(test_files: list[str]) -> list[str]:
    filters = []
    for test_file in test_files:
        if not test_file.endswith(".kt"):
            continue
        if "/src/test/" not in test_file:
            continue
        filters.append(Path(test_file).stem)
    return sorted(set(filters))


def android_gradle_test_task(test_files: list[str]) -> str:
    normalized = [path for path in test_files if path.endswith(".kt") and "/src/test/" in path]
    if normalized and all(path.startswith("common/src/test/") for path in normalized):
        return ":common:testDebugUnitTest"
    if normalized and all(path.startswith("app/src/test/") for path in normalized):
        return ":app:testFullDebugUnitTest"
    return "testFullDebugUnitTest"


def render_android_warmup_tasks(test_files: list[str]) -> str:
    gradle_task = android_gradle_test_task(test_files)
    if gradle_task == ":common:testDebugUnitTest":
        return ":common:compileDebugUnitTestKotlin :common:compileDebugUnitTestJavaWithJavac"
    if gradle_task == ":app:testFullDebugUnitTest":
        return ":app:compileFullDebugUnitTestKotlin :app:compileFullDebugUnitTestJavaWithJavac"
    return "compileFullDebugUnitTestKotlin compileFullDebugUnitTestJavaWithJavac"


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def compose_command_quote(command: str) -> str:
    # Docker Compose treats "$" as its own interpolation syntax before the
    # command reaches bash in the container. Escape it so bash parameter
    # expansion such as ${VAR//:/\\:} remains intact at runtime.
    return shell_quote(command).replace("$", "$$")


def render_python_command(test_files: list[str], profile: str) -> str:
    joined = " ".join(shell_quote(item) for item in test_files)
    report = f"/workspace/.ecosyncbench/test-reports/{profile}.xml"
    tests = f" {joined}" if joined else ""
    return (
        "mkdir -p /workspace/.ecosyncbench/test-reports && "
        f"/opt/ecosync/venv/bin/python -m pytest -q{tests} --junitxml={shell_quote(report)}"
    )


def render_android_command(test_files: list[str]) -> str:
    filters = " ".join(f"--tests {shell_quote('*' + item + '*')}" for item in android_test_filters(test_files))
    filter_part = f" {filters}" if filters else ""
    gradle_task = android_gradle_test_task(test_files)
    gradle_network_flags = (
        "-Dorg.gradle.internal.http.connectionTimeout=60000 "
        "-Dorg.gradle.internal.http.socketTimeout=60000"
    )
    return (
        "rm -f gradle/gradle-daemon-jvm.properties "
        "&& sed -i \"s#^distributionUrl=.*#distributionUrl=${ECOSYNC_GRADLE_DISTRIBUTION_URL//:/\\\\:}#; "
        "s#^networkTimeout=.*#networkTimeout=120000#\" gradle/wrapper/gradle-wrapper.properties "
        "&& if [ -f .github/mock-google-services.json ]; then for module in app automotive wear; do "
        "if [ -d \"$module\" ]; then cp .github/mock-google-services.json \"$module/google-services.json\"; fi; done; fi "
        f"&& ./gradlew --init-script /opt/ecosync/gradle-init.d/mirrors.gradle {gradle_task}{filter_part} "
        f"${{ECOSYNC_GRADLE_FLAGS:---offline}} --no-daemon --stacktrace --max-workers=2 {gradle_network_flags}"
    )


def render_run_profile(task_id: str, profiles: dict[str, dict[str, str]], image_specs: dict[str, dict[str, Any]]) -> str:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        'profile="${1:?profile required}"',
        'task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"',
        'repo_root="$task_dir"',
        'while [[ "$repo_root" != "/" && ! -d "$repo_root/.git" ]]; do repo_root="$(dirname "$repo_root")"; done',
        'if [[ ! -d "$repo_root/.git" ]]; then echo "cannot locate repository root from $task_dir" >&2; exit 2; fi',
        'workspace="$(cd "${ECOSYNC_WORKSPACE:-$repo_root}" && pwd)"',
        'export ECOSYNC_REPO_ROOT="$repo_root"',
        'export ECOSYNC_BUILD_CONTEXT="$workspace"',
        'export ECOSYNC_UID="${ECOSYNC_UID:-$(id -u)}"',
        'export ECOSYNC_GID="${ECOSYNC_GID:-$(id -g)}"',
        'docker_proxy="${ECOSYNC_BUILD_PROXY:-${ECOSYNC_DOCKER_PROXY:-}}"',
        'if [[ -n "$docker_proxy" ]]; then',
        '  docker_proxy="${docker_proxy//127.0.0.1/host.docker.internal}"',
        '  docker_proxy="${docker_proxy//localhost/host.docker.internal}"',
        '  export ECOSYNC_DOCKER_PROXY="$docker_proxy"',
        "fi",
        "",
        "image_needs_build() {",
        '  local image="$1"',
        '  local expected_warmup="$2"',
        '  if ! docker image inspect "$image" >/dev/null 2>&1; then',
        "    return 0",
        "  fi",
        '  if [[ -n "$expected_warmup" ]]; then',
        '    local actual_warmup',
        '    actual_warmup="$(docker image inspect --format \'{{ index .Config.Labels "org.ecosyncbench.gradle_warmup_tasks" }}\' "$image" 2>/dev/null || true)"',
        '    if [[ "$actual_warmup" != "$expected_warmup" ]]; then',
        '      echo "dependency image $image has warmup label ${actual_warmup:-<missing>}, expected $expected_warmup; rebuilding" >&2',
        "      return 0",
        "    fi",
        "  fi",
        "  return 1",
        "}",
        "",
        "warmup_android_image() {",
        '  local service="$1"',
        '  local image="$2"',
        '  local warmup_tasks="$3"',
        '  local container_name="ecosync_warmup_${service}_$$_${RANDOM}"',
        '  local label_key="org.ecosyncbench.gradle_warmup_tasks"',
        '  cd "$task_dir/environment"',
        '  echo "warming dependency image $image with Gradle tasks: $warmup_tasks" >&2',
        '  set +e',
        '  ECOSYNC_WORKSPACE="$workspace" ECOSYNC_GRADLE_FLAGS="" docker compose run --no-deps --name "$container_name" "$service" bash -lc \'',
        '    cd /workspace/repos/home-assistant/android',
        '    rm -f gradle/gradle-daemon-jvm.properties',
        '    sed -i "s#^distributionUrl=.*#distributionUrl=${ECOSYNC_GRADLE_DISTRIBUTION_URL//:/\\\\:}#; s#^networkTimeout=.*#networkTimeout=120000#" gradle/wrapper/gradle-wrapper.properties',
        '    if [ -f .github/mock-google-services.json ]; then for module in app automotive wear; do if [ -d "$module" ]; then cp .github/mock-google-services.json "$module/google-services.json"; fi; done; fi',
        '    ./gradlew $ECOSYNC_ANDROID_WARMUP_TASKS --no-daemon --no-configuration-cache --stacktrace --info --max-workers=2 -Dorg.gradle.daemon=false -Dorg.gradle.parallel=false -Dorg.gradle.vfs.watch=false -Dorg.gradle.internal.http.connectionTimeout=60000 -Dorg.gradle.internal.http.socketTimeout=60000 -Dorg.gradle.jvmargs="-Xmx2g -XX:MaxMetaspaceSize=768m -Dfile.encoding=UTF-8"',
        "  '",
        "  local code=$?",
        "  set -e",
        '  if [[ "$code" -ne 0 ]]; then',
        '    docker rm -f "$container_name" >/dev/null 2>&1 || true',
        '    return "$code"',
        "  fi",
        '  docker commit --change "LABEL ${label_key}=\\"${warmup_tasks}\\"" "$container_name" "$image" >/dev/null',
        '  docker rm -f "$container_name" >/dev/null',
        "}",
        "",
        "compose_run() {",
        '  local service="$1"',
        '  local image="$2"',
        '  local expected_warmup="${3:-}"',
        '  local container_name="ecosync_${profile}_$$_${RANDOM}"',
        '  cd "$task_dir/environment"',
        '  if [[ "${ECOSYNC_DOCKER_FORCE_WARMUP:-0}" == "1" ]] && docker image inspect "$image" >/dev/null 2>&1; then',
        '    if [[ -n "$expected_warmup" ]]; then',
        '      warmup_android_image "$service" "$image" "$expected_warmup"',
        "    fi",
        "    return 0",
        "  fi",
        '  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || image_needs_build "$image" "$expected_warmup"; then',
        '    if [[ -n "$expected_warmup" ]]; then',
        '      ECOSYNC_ANDROID_WARMUP_MODE=skip ECOSYNC_ANDROID_BUILD_WARMUP_TASKS= ECOSYNC_WORKSPACE="$workspace" docker compose build "$service"',
        '      warmup_android_image "$service" "$image" "$expected_warmup"',
        '    else',
        '      ECOSYNC_WORKSPACE="$workspace" docker compose build "$service"',
        "    fi",
        "  fi",
        '  if [[ "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then',
        '    if [[ -n "$expected_warmup" ]]; then',
        '      warmup_android_image "$service" "$image" "$expected_warmup"',
        "    fi",
        "    return 0",
        "  fi",
        '  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then',
        "    return 0",
        "  fi",
        '  ECOSYNC_WORKSPACE="$workspace" docker compose run --rm --name "$container_name" "$service"',
        "}",
        "",
        'case "$profile" in',
    ]
    for name, spec in profiles.items():
        repo_name = spec["repo"]
        image_spec = image_specs.get(repo_name) or {}
        image = image_spec.get("image")
        warmup_tasks = image_spec.get("warmup_tasks") or ""
        command = spec["command"].replace('"', '\\"')
        workdir = spec["workdir"].replace('"', '\\"')
        if not image:
            lines.extend(
                [
                    f"  {name})",
                    f'    cd "$workspace/{workdir}"',
                    f'    {command}',
                    "    ;;",
                ]
            )
            continue
        lines.extend(
            [
                f"  {name})",
                f"    compose_run {shell_quote(name)} {shell_quote(image or '')} {shell_quote(warmup_tasks)}",
                "    ;;",
            ]
        )
    lines.extend(
        [
            "  *)",
            f'    echo "Unknown profile for {task_id}: $profile" >&2',
            "    exit 2",
            "    ;;",
            "esac",
            "",
        ]
    )
    return "\n".join(lines)


def command_for_profile(repo_name: str, test_files: list[str]) -> str:
    if repo_name == "core":
        return render_python_command(test_files, profile_for_repo(repo_name))
    if repo_name == "supervisor":
        return render_python_command(test_files, profile_for_repo(repo_name))
    if repo_name == "android":
        return render_android_command(test_files)
    if repo_name == "ios":
        return "xcodebuild test -scheme HomeAssistant -destination 'platform=macOS'"
    return "echo profile-not-implemented && exit 2"


def docker_build_args_for_repo(task_id: str, spec: dict[str, Any]) -> dict[str, str]:
    common = {
        "ECOSYNC_TASK_ID": task_id,
        "ECOSYNC_REPO": spec["full_name"],
        "ECOSYNC_REPO_PATH": spec["local_path"],
        "ECOSYNC_BASE_COMMIT": spec["base_commit"],
        "ECOSYNC_DEPENDENCY_FINGERPRINT": spec["dependency_fingerprint"],
        "HTTP_PROXY": "${ECOSYNC_DOCKER_PROXY:-}",
        "HTTPS_PROXY": "${ECOSYNC_DOCKER_PROXY:-}",
        "ALL_PROXY": "${ECOSYNC_DOCKER_PROXY:-}",
        "http_proxy": "${ECOSYNC_DOCKER_PROXY:-}",
        "https_proxy": "${ECOSYNC_DOCKER_PROXY:-}",
        "all_proxy": "${ECOSYNC_DOCKER_PROXY:-}",
        "NO_PROXY": "${NO_PROXY:-localhost,127.0.0.1}",
        "no_proxy": "${no_proxy:-localhost,127.0.0.1}",
    }
    if spec["repo"] in {"core", "supervisor"}:
        common["ECOSYNC_REQUIREMENTS_FILES"] = requirements_files_for_repo(spec["repo"])
    elif spec["repo"] == "android":
        warmup_tasks = spec.get("warmup_tasks", "")
        common["ECOSYNC_ANDROID_WARMUP_TASKS"] = "${ECOSYNC_ANDROID_BUILD_WARMUP_TASKS-" + warmup_tasks + "}"
        common["ECOSYNC_ANDROID_WARMUP_MODE"] = "${ECOSYNC_ANDROID_WARMUP_MODE:-build}"
    return common


def dockerfile_for_repo(repo_name: str) -> str:
    if repo_name in {"core", "supervisor"}:
        return "${ECOSYNC_REPO_ROOT}/benchmark/images/deps/home-assistant-python/Dockerfile"
    if repo_name == "android":
        return "${ECOSYNC_REPO_ROOT}/benchmark/images/deps/home-assistant-android/Dockerfile"
    raise ValueError(f"no dockerfile for repo: {repo_name}")


def compose_service(
    *,
    task_id: str,
    profile: str,
    profile_spec: dict[str, str],
    image_spec: dict[str, Any],
) -> dict[str, Any]:
    repo_name = image_spec["repo"]
    service: dict[str, Any] = {
        "image": image_spec["image"],
        "build": {
            "context": "${ECOSYNC_BUILD_CONTEXT:-.}",
            "dockerfile": dockerfile_for_repo(repo_name),
            "args": docker_build_args_for_repo(task_id, image_spec),
            "extra_hosts": ["host.docker.internal:host-gateway"],
        },
        "working_dir": f"/workspace/{profile_spec['workdir']}",
        "volumes": ["${ECOSYNC_WORKSPACE:-../../../../}:/workspace"],
        "command": f"bash -lc {compose_command_quote(profile_spec['command'])}",
    }
    if repo_name in {"core", "supervisor"}:
        service["user"] = "${ECOSYNC_UID:-1000}:${ECOSYNC_GID:-1000}"
        service["environment"] = {
            "HOME": "/tmp/ecosync-home",
            "VIRTUAL_ENV": "/opt/ecosync/venv",
            "PATH": "/opt/ecosync/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        }
    elif repo_name == "android":
        service["volumes"].append(
            "${ECOSYNC_REPO_ROOT}/repos/home-assistant/android/.git:${ECOSYNC_REPO_ROOT}/repos/home-assistant/android/.git:ro"
        )
        service["environment"] = {
            "GITHUB_TOKEN": "${GITHUB_TOKEN:-}",
            "JAVA_TOOL_OPTIONS": "${JAVA_TOOL_OPTIONS:-}",
            "HTTP_PROXY": "${ECOSYNC_DOCKER_PROXY:-}",
            "HTTPS_PROXY": "${ECOSYNC_DOCKER_PROXY:-}",
            "ALL_PROXY": "${ECOSYNC_DOCKER_PROXY:-}",
            "ECOSYNC_GRADLE_FLAGS": "${ECOSYNC_GRADLE_FLAGS:-}",
            "ECOSYNC_GRADLE_DISTRIBUTION_URL": "${ECOSYNC_GRADLE_DISTRIBUTION_URL:-https://mirrors.cloud.tencent.com/gradle/gradle-9.4.1-bin.zip}",
            "ECOSYNC_ANDROID_WARMUP_TASKS": image_spec.get("warmup_tasks", ""),
        }
        service["extra_hosts"] = ["host.docker.internal:host-gateway"]
    return service


def render_docker_compose(task_id: str, profiles: dict[str, dict[str, str]], image_specs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    services = {}
    for profile, profile_spec in profiles.items():
        repo_name = profile_spec["repo"]
        image_spec = image_specs.get(repo_name)
        if not image_spec:
            continue
        services[profile] = compose_service(
            task_id=task_id,
            profile=profile,
            profile_spec=profile_spec,
            image_spec=image_spec,
        )
    return {"services": services}


def render_docker_layers(task_id: str, image_specs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    base_images: dict[str, dict[str, str]] = {}
    dependency_images: dict[str, dict[str, Any]] = {}
    for repo_name, spec in sorted(image_specs.items()):
        if repo_name in {"core", "supervisor"}:
            base_images["python-3.14-slim"] = {
                "image": "ecosyncbench/base/python:3.14-slim",
                "dockerfile": "benchmark/images/base/python-3.14-slim/Dockerfile",
                "source": "python:3.14-slim",
                "reuse_scope": "all Python 3.14 Home Assistant Core/Supervisor tasks",
            }
            dockerfile = "benchmark/images/deps/home-assistant-python/Dockerfile"
            base_image = "ecosyncbench/base/python:3.14-slim"
        elif repo_name == "android":
            base_images["android-sdk-36"] = {
                "image": "ecosyncbench/base/android-sdk:36",
                "dockerfile": "benchmark/images/base/android-sdk-36/Dockerfile",
                "source": "ghcr.io/cirruslabs/android-sdk:36",
                "reuse_scope": "all Android SDK 36 Gradle tasks",
            }
            dockerfile = "benchmark/images/deps/home-assistant-android/Dockerfile"
            base_image = "ecosyncbench/base/android-sdk:36"
        else:
            continue
        dependency_images[repo_name] = {
            "image": spec["image"],
            "base_image": base_image,
            "dockerfile": dockerfile,
            "dependency_fingerprint": spec["dependency_fingerprint"],
            "fingerprint_policy": "SHA256 over repo dependency files at the task base and gold commits; unchanged dependency states reuse the same image across cases.",
            "dependency_files": spec["dependency_files"],
            "base_commit": spec["base_commit"],
            "gold_commit": spec.get("gold_commit"),
            "repo": spec["full_name"],
            "reuse_scope": "same ecosystem repo with identical dependency fingerprint",
        }
        if spec.get("warmup_tasks"):
            dependency_images[repo_name]["warmup_tasks"] = spec["warmup_tasks"]
            dependency_images[repo_name]["warmup_policy"] = (
                "Gradle dependencies are resolved during dependency-image setup for both base and gold dependency states; "
                "matrix test containers run offline against the same image tag."
            )
    return {
        "task_id": task_id,
        "status": "layered_dependency_images_defined",
        "layering_policy": [
            "Base images contain language or SDK runtimes shared across ecosystems/tasks.",
            "Dependency images cache package-manager artifacts for a repo dependency fingerprint.",
            "Case workspaces are mounted at runtime; source code under test is not baked into the final test image.",
            "Docker/registry layer digests deduplicate repeated base and dependency layers across cases.",
        ],
        "base_images": base_images,
        "dependency_images": dependency_images,
    }


def extract_repo_prompts(draft: dict[str, Any], involved: list[dict[str, Any]]) -> dict[str, str]:
    raw_prompts = draft.get("repo_prompt_drafts") or draft.get("repo_prompts")
    if not isinstance(raw_prompts, dict):
        raise SystemExit(
            f"{draft['id']}: missing repo-level prompts. Add a repo_prompt_drafts object "
            "keyed by involved repo short name or full repo name before building the task."
        )

    prompts: dict[str, str] = {}
    missing: list[str] = []
    for item in involved:
        short_name = item["short_name"]
        full_name = item["repo"]
        value = raw_prompts.get(short_name, raw_prompts.get(full_name))
        if isinstance(value, dict):
            value = value.get("prompt") or value.get("text")
        if not isinstance(value, str) or not value.strip():
            missing.append(f"{short_name} ({full_name})")
            continue
        prompts[short_name] = value.strip()

    if missing:
        raise SystemExit(f"{draft['id']}: missing repo-level prompts for: {', '.join(missing)}")
    return prompts


def render_prompt_sources(draft: dict[str, Any], involved: list[dict[str, Any]]) -> dict[str, Any]:
    source_by_repo: dict[str, dict[str, Any]] = {}
    for item in draft.get("prompt_source_by_pr") or []:
        repo = item.get("repo")
        if repo:
            source_by_repo[str(repo)] = item

    repo_entries = []
    needs_audit = False
    for item in involved:
        source = source_by_repo.get(item["repo"], {})
        if not source:
            needs_audit = True
        evidence = []
        if source.get("source_section"):
            evidence.append(f"source section: {source['source_section']}")
        if source.get("excerpt"):
            evidence.append(str(source["excerpt"]))
        if not evidence:
            evidence.append("No explicit prompt_source_by_pr excerpt in draft; verify PR body before release.")
        repo_entries.append(
            {
                "repo": item["short_name"],
                "file": f"prompts/{item['short_name']}.md",
                "source_pr": {
                    "repo": item["repo"],
                    "number": item["pr"],
                    "url": item["url"],
                    "title": item["title"],
                },
                "source_evidence": evidence,
                "changed_files_sample": item.get("changed_files", [])[:20],
                "prompt_derivation": "Derived from reviewed draft repo_prompt_drafts and upstream PR evidence.",
            }
        )

    return {
        "schema_version": "ecosyncbench.prompt_sources.v1",
        "verified_at": datetime.now(timezone.utc).date().isoformat(),
        "verification_basis": [
            "reviewed_case_draft",
            "CASE_NOTES.zh.md",
            "github_pr_metadata",
            "local_gold_patch",
        ],
        "construction_rule": {
            "repo_prompts": draft.get(
                "agent_prompt_construction_rule",
                "Extract demand sentences from each involved PR body or linked issue, remove PR links/template prose/release-note noise, keep public behavior and contract constraints, then lightly polish without hidden-test details.",
            ),
            "ecosystem_prompt": "Combine repo-level prompts in task.yaml.repositories order, deduplicate repeated requirements, and lightly polish without adding requirements not present in the repo prompts.",
        },
        "ecosystem_prompt": {
            "file": "prompt.md",
            "composition": "union_of_repo_prompts",
            "audit_status": "needs_manual_prompt_source_audit" if needs_audit else "reviewed_draft_sources_available",
        },
        "repo_prompts": repo_entries,
    }


def build_case(draft_path: Path, output_root: Path, force: bool) -> dict[str, Any]:
    draft = load_draft(draft_path)
    task_id = draft["id"]
    print(f"[build-case] {task_id}", flush=True)
    out = output_root / task_id
    if out.exists():
        if not force:
            raise SystemExit(f"task dir exists: {out}; pass --force to replace")
        remove_tree(out)
    out.mkdir(parents=True)

    full_configs = repo_config_by_full_name()
    involved = []
    earliest_base: datetime | None = None
    for item in draft["involved_repositories"]:
        cfg = full_configs[item["repo"]]
        repo_path = ensure_local_repo(cfg)
        ensure_commit(repo_path, item["base_commit"])
        ensure_commit(repo_path, item["gold_commit"])
        base_dt = commit_date(repo_path, item["base_commit"])
        earliest_base = base_dt if earliest_base is None else min(earliest_base, base_dt)
        normalized = dict(item)
        normalized["short_name"] = cfg["name"]
        normalized["local_path"] = cfg["local_path"]
        normalized["role"] = cfg["role"]
        normalized["base_commit_date"] = base_dt.isoformat()
        normalized["gold_commit_date"] = commit_date(repo_path, item["gold_commit"]).isoformat()
        involved.append(normalized)
    if earliest_base is None:
        raise SystemExit(f"draft has no involved repositories: {draft_path}")

    repo_prompts = extract_repo_prompts(draft, involved)
    involved_by_name = {item["short_name"]: item for item in involved}
    repo_snapshots: dict[str, dict[str, Any]] = {}
    for cfg in HOME_ASSISTANT_TOP10:
        print(f"[repo-snapshot] {task_id} {cfg['full_name']}", flush=True)
        repo_path = ensure_local_repo(cfg)
        if cfg["name"] in involved_by_name:
            item = involved_by_name[cfg["name"]]
            snapshot = item["base_commit"]
            reason = "included upstream PR base commit"
        else:
            snapshot = snapshot_before_api(cfg, earliest_base)
            reason = f"context snapshot at or before earliest involved base commit {earliest_base.isoformat()}"
        ensure_commit(repo_path, snapshot)
        repo_snapshots[cfg["name"]] = {
            "url": cfg["url"],
            "local_path": cfg["local_path"],
            "role": cfg["role"],
            "included_in_task": cfg["name"] in involved_by_name,
            "agent_editable": True,
            "snapshot_commit": snapshot,
            "snapshot_commit_date": commit_date_text(repo_path, snapshot),
            "snapshot_commit_policy": reason,
        }
        if cfg["name"] in involved_by_name:
            item = involved_by_name[cfg["name"]]
            repo_snapshots[cfg["name"]].update(
                {
                    "base_commit": item["base_commit"],
                    "gold_commit": item["gold_commit"],
                    "upstream_pr": item["pr"],
                }
            )
        else:
            repo_snapshots[cfg["name"]].update(
                {
                    "base_commit": snapshot,
                    "reason": "Context-only repo. Manual CASE_NOTES.zh.md records impact-closure evidence.",
                }
            )

    task_repos = []
    hidden_items = []
    profile_defs: dict[str, dict[str, str]] = {}
    image_specs: dict[str, dict[str, Any]] = {}
    gold_meta = {"source": "upstream_pr_merge_commits", "patches": {}, "hidden_tests": {"policy": draft["hidden_test_policy"]}}
    pr_records = []
    for item in involved:
        repo_name = item["short_name"]
        cfg = repo_config_by_name()[repo_name]
        repo_path = REPO_ROOT / cfg["local_path"]
        fingerprint, dependency_files = dependency_fingerprint(
            repo_path,
            [item["base_commit"], item["gold_commit"]],
            repo_name,
        )
        dependency_image = dependency_image_for_repo(repo_name, fingerprint)
        if dependency_image:
            image_specs[repo_name] = {
                "repo": repo_name,
                "full_name": item["repo"],
                "local_path": cfg["local_path"],
                "base_commit": item["base_commit"],
                "gold_commit": item["gold_commit"],
                "dependency_fingerprint": fingerprint,
                "dependency_files": dependency_files,
                "image": dependency_image,
                "layer": "deps",
            }
            if repo_name == "android":
                image_specs[repo_name]["warmup_tasks"] = render_android_warmup_tasks(
                    item.get("upstream_test_files") or []
                )
                image_specs[repo_name]["warmup_policy"] = (
                    "Build the dependency image online once by resolving and compiling the Android unit-test "
                    "classpath that matches this case's selected upstream test files. Avoid using unrelated full-repo "
                    "test suites as image-build gates; all matrix and harness runs reuse this exact image tag."
                )
        gold_patch = f"gold/patches/{repo_name}.patch"
        hidden_patch = f"hidden/patches/{repo_name}-hidden-tests.patch"
        generate_patch(repo_path, item["base_commit"], item["gold_commit"], out / gold_patch)
        generate_patch(repo_path, item["base_commit"], item["gold_commit"], out / hidden_patch, item.get("upstream_test_files") or [])
        task_repo = {
            "name": repo_name,
            "path": cfg["local_path"],
            "role": cfg["role"],
            "base_commit": item["base_commit"],
            "gold_commit": item["gold_commit"],
            "gold_patch": gold_patch,
        }
        if dependency_image:
            task_repo["dependency_image"] = dependency_image
            task_repo["dependency_fingerprint"] = fingerprint
        task_repos.append(task_repo)
        upstream_test_files = item.get("upstream_test_files") or []
        hidden_items.append(
            {
                "repo": repo_name,
                "patch": hidden_patch,
                "test_profile": profile_for_repo(repo_name),
                "origin": "upstream_test_diff",
                "source_file": upstream_test_files,
                "hidden_file": upstream_test_files,
                "semantic_changes": "none",
                "source": (
                    f"upstream test changes from {item['repo']} PR {item['pr']} "
                    "kept as an evaluator-only diff against the original test files; the hidden "
                    "profile runs those original test files/modules so existing tests can "
                    "contribute PASS_TO_PASS"
                ),
            }
        )
        hidden_cmd = command_for_profile(repo_name, upstream_test_files)
        profile_defs[profile_for_repo(repo_name)] = {"repo": repo_name, "workdir": cfg["local_path"], "command": hidden_cmd}
        gold_meta["patches"][repo_name] = {
            "base_commit": item["base_commit"],
            "gold_commit": item["gold_commit"],
            "upstream_pr": item["pr"],
        }
        pr_records.append(
            {
                "repo": item["repo"],
                "pr": item["pr"],
                "html_url": item["url"],
                "title": item["title"],
                "merged": True,
                "merged_at": item["merged_at"],
                "merge_commit_sha": item["gold_commit"],
                "base_sha": item["base_commit"],
                "head_sha": item.get("head_commit"),
                "changed_files": item.get("changed_files", []),
                "test_file_paths": item.get("upstream_test_files", []),
            }
        )

    categories = draft.get("category_hint") or []
    task_yaml = {
        "id": task_id,
        "benchmark": "WIDESWE",
        "status": "candidate_pending_matrix",
        "ecosystem": "Home Assistant",
        "task_type": task_type_from_categories(categories),
        "subtype": categories,
        "repositories": task_repos,
        "source_prs": [
            {"repo": item["repo"], "pr": item["pr"], "title": item["title"], "url": item["url"]} for item in involved
        ],
        "source_unit": "pr_bundle",
        "source_provenance": {
            "construction_method": "upstream_pr_merge_commits",
            "data_collection_basis": [
                "explicit cross-repo PR links or issue references",
                "GitHub Pulls API merge status",
                "PR body evidence copied into reviewed CASE_NOTES.zh.md",
                "changed files",
                "upstream-added or upstream-modified tests",
            ],
            "source_bundle": draft.get("source_bundle"),
            "provenance_risk": {
                "level": "medium",
                "reasons": [
                    "Candidate was script-mined but retained only after manual review.",
                    "Release still requires matrix validation and environment lock.",
                ],
            },
        },
        "impact_closure": {
            "included_repos": [item["repo"] for item in involved],
            "excluded_repos": [cfg["full_name"] for cfg in HOME_ASSISTANT_TOP10 if cfg["name"] not in involved_by_name],
            "closure_basis": "See CASE_NOTES.zh.md for manual impact-closure evidence.",
        },
        "temporal_evidence": {
            "earliest_base_commit_date": earliest_base.isoformat(),
            "included_pr_merged_at": {item["repo"]: item["merged_at"] for item in involved},
        },
        "coupling_evidence": {
            "summary": draft.get("manual_audit", {}).get("reason", ""),
            "details": "See CASE_NOTES.zh.md.",
        },
        "evaluation": {
            "harness": "harness.yaml",
            "gold": "gold/metadata.yaml",
            "hidden_tests": "hidden/patches/",
            "environment": "environment/",
        },
    }

    repos_yaml = {
        "ecosystem": "Home Assistant",
        "snapshot_date": datetime.now(timezone.utc).date().isoformat(),
        "snapshot_policy": "Every Home Assistant Top10 repo is pinned. Included repos use exact upstream PR base commits; context repos use the latest default-branch commit at or before the earliest included base commit.",
        "repos": repo_snapshots,
    }

    linux_hidden = [profile_for_repo(item["short_name"]) for item in involved if item["short_name"] != "ios"]
    harness_yaml = {
        "task_id": task_id,
        "hidden_test_patches": hidden_items,
        "profile_sets": {
            "linux-docker": {
                "description": "Construction-stage Linux profile. iOS hidden tests are preserved for macos-full.",
                "hidden_tests": linux_hidden,
                "cross_repo_contract": [],
            },
            "macos-full": {
                "description": "Full profile including iOS tests on macOS/Xcode.",
                "hidden_tests": [profile_for_repo(item["short_name"]) for item in involved],
                "cross_repo_contract": [],
            },
        },
        "matrix_expectations": {
            "base_hidden": "fail",
            "gold_hidden": "pass",
        },
    }

    test_profiles_yaml = {
        "profiles": {
            name: {
                "runnable_on": ["linux", "macos"] if not name.startswith("ios-") else ["macos"],
                "required": not name.startswith("ios-"),
                "repo": spec["repo"],
                "workdir": spec["workdir"],
                "command": spec["command"],
                "docker_service": name if spec["repo"] in image_specs else None,
                "dependency_image": (image_specs.get(spec["repo"]) or {}).get("image"),
                "status": "candidate_profile_unverified",
            }
            for name, spec in profile_defs.items()
        }
    }
    docker_layers_yaml = render_docker_layers(task_id, image_specs)

    write_yaml(out / "task.yaml", task_yaml)
    write_yaml(out / "repos.yaml", repos_yaml)
    write_yaml(out / "harness.yaml", harness_yaml)
    write_yaml(out / "prompt_sources.yaml", render_prompt_sources(draft, involved))
    write_yaml(out / "gold" / "metadata.yaml", gold_meta)
    write_yaml(out / "environment" / "test_profiles.yaml", test_profiles_yaml)
    write_yaml(out / "environment" / "docker-compose.yml", render_docker_compose(task_id, profile_defs, image_specs))
    write_yaml(out / "environment" / "docker_layers.yaml", docker_layers_yaml)
    write_json(
        out / "provenance" / "pr_records.json",
        {
            "schema_version": "ecosyncbench.pr_records.v1",
            "source": "reviewed_home_assistant_pr_mining",
            "task_id": task_id,
            "record_count": len(pr_records),
            "records": pr_records,
        },
    )
    (out / "prompt.md").write_text(draft["agent_prompt_draft"].strip() + "\n", encoding="utf-8")
    prompts_dir = out / "prompts"
    prompts_dir.mkdir()
    for repo_name, prompt in repo_prompts.items():
        (prompts_dir / f"{repo_name}.md").write_text(prompt + "\n", encoding="utf-8")
    (out / "README.md").write_text(
        f"# {task_id}\n\n"
        "Status: candidate pending construction matrix.\n\n"
        "This task was generated from a manually reviewed Home Assistant PR bundle. "
        "See `CASE_NOTES.zh.md`, `task.yaml`, `repos.yaml`, and `provenance/pr_records.json`.\n",
        encoding="utf-8",
    )
    shutil.copy2(draft_path.parent / "CASE_NOTES.zh.md", out / "CASE_NOTES.zh.md")
    runner = out / "environment" / "run_profile.sh"
    runner.write_text(render_run_profile(task_id, profile_defs, image_specs), encoding="utf-8")
    runner.chmod(0o755)

    return {
        "task_id": task_id,
        "output_dir": str(out.relative_to(REPO_ROOT)),
        "included_repos": [item["repo"] for item in involved],
        "top10_repos_pinned": len(repo_snapshots),
        "dependency_images": {repo: spec["image"] for repo, spec in image_specs.items()},
        "earliest_base_commit_date": earliest_base.isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ecosystem", default="home-assistant", choices=["home-assistant"])
    parser.add_argument(
        "--reviewed-dir",
        type=Path,
        default=REPO_ROOT / "data_mining" / "data" / "cases" / "reviewed" / "home-assistant" / "drafts",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=REPO_ROOT / "data_mining" / "data" / "cases" / "test_matrix" / "home-assistant",
    )
    parser.add_argument("--case", action="append", help="Build only the given case id. May be repeated.")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    selected = set(args.case or [])
    summaries = []
    for draft_path in sorted(args.reviewed_dir.glob("*/case_draft.json")):
        case_id = draft_path.parent.name
        if selected and case_id not in selected:
            continue
        summaries.append(build_case(draft_path, args.output_root, args.force))
    if selected and len(summaries) != len(selected):
        built = {item["task_id"] for item in summaries}
        missing = sorted(selected - built)
        raise SystemExit(f"selected cases not found: {missing}")
    print(json.dumps({"built": summaries}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
