#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
TASKS_DIR = REPO_ROOT / "benchmark" / "tasks"
DEPS_DIR = REPO_ROOT / "benchmark" / "images" / "deps"


DEPENDENCY_FILES = {
    "server": [
        "*.sln",
        "global.json",
        "NuGet.config",
        "Directory.Build.props",
        "Directory.Build.targets",
        "Directory.Packages.props",
        "src/**/*.csproj",
        "test/**/*.csproj",
    ],
    "clients": [
        "package.json",
        "package-lock.json",
        "npm-shrinkwrap.json",
        "nx.json",
        "tsconfig*.json",
        "*.js",
        "*.json",
        "*.ts",
    ],
    "android": [
        "gradle/wrapper/gradle-wrapper.properties",
        "settings.gradle",
        "settings.gradle.kts",
        "build.gradle",
        "build.gradle.kts",
        "gradle.properties",
        "gradle/libs.versions.toml",
        "buildSrc/**/*",
        "gradle/**/*",
        "**/*.gradle",
        "**/*.gradle.kts",
    ],
}


IMAGE_NAMES = {
    "server": "bitwarden-server-dotnet",
    "clients": "bitwarden-clients-node",
    "android": "bitwarden-android-gradle",
}


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def iter_files(root: Path, patterns: Iterable[str]) -> list[Path]:
    files: set[Path] = set()
    for pattern in patterns:
        files.update(path for path in root.glob(pattern) if path.is_file())
    return sorted(files)


def fingerprint(repo_root: Path, repo_type: str) -> str:
    digest = hashlib.sha256()
    files = iter_files(repo_root, DEPENDENCY_FILES[repo_type])
    if not files:
        raise SystemExit(f"no dependency files found for {repo_type}: {repo_root}")
    for path in files:
        rel = path.relative_to(repo_root).as_posix()
        digest.update(rel.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii") + b"\0")
    return digest.hexdigest()[:12]


def docker_proxy_args() -> list[str]:
    proxy = os.environ.get("ECOSYNC_BUILD_PROXY")
    if not proxy:
        return []
    proxy = proxy.replace("127.0.0.1", "host.docker.internal").replace("localhost", "host.docker.internal")
    no_proxy = os.environ.get("NO_PROXY", "localhost,127.0.0.1")
    return [
        "--add-host=host.docker.internal:host-gateway",
        "--build-arg", f"HTTP_PROXY={proxy}",
        "--build-arg", f"HTTPS_PROXY={proxy}",
        "--build-arg", f"ALL_PROXY={proxy}",
        "--build-arg", f"http_proxy={proxy}",
        "--build-arg", f"https_proxy={proxy}",
        "--build-arg", f"all_proxy={proxy}",
        "--build-arg", f"NO_PROXY={no_proxy}",
        "--build-arg", f"no_proxy={os.environ.get('no_proxy', no_proxy)}",
    ]


def repo_spec(task_id: str, repo_type: str) -> tuple[str, str]:
    repos = load_yaml(TASKS_DIR / task_id / "repos.yaml")["repos"]
    spec = repos[repo_type]
    return spec["local_path"], spec["base_commit"]


def image_spec(task_id: str, repo_type: str) -> tuple[dict, Path]:
    local_path, base_commit = repo_spec(task_id, repo_type)
    tmp = Path(tempfile.mkdtemp(prefix=f"ecosync-deps-{task_id}-{repo_type}-"))
    workspace = tmp / "workspace"
    try:
        subprocess.run(
            [
                "python3",
                "benchmark/harness/ecosync_harness.py",
                "prepare",
                "--task",
                task_id,
                "--workspace",
                str(workspace),
                "--mode",
                "base",
                "--force",
            ],
            cwd=REPO_ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        repo_path = workspace / local_path
        fp = fingerprint(repo_path, repo_type)
        build_context = workspace
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    image = f"ecosyncbench/deps/{IMAGE_NAMES[repo_type]}:{fp}"
    release = f"ghcr.io/code-tmp/wideswe/deps/{IMAGE_NAMES[repo_type]}:{fp}"
    spec = {
        "repo_type": repo_type,
        "local_tag": image,
        "release_tag": release,
        "release_digest": None,
        "dependency_fingerprint": fp,
        "base_commit": base_commit,
    }
    return spec, build_context


def build(task_id: str, repo_type: str, push: bool, dry_run: bool) -> dict:
    spec, build_context = image_spec(task_id, repo_type)
    tmp = build_context.parent
    image = spec["local_tag"]
    release = spec["release_tag"]

    dockerfile = DEPS_DIR / f"bitwarden-{repo_type}" / "Dockerfile"
    android_cache_image = os.environ.get("ECOSYNC_ANDROID_CACHE_IMAGE") if repo_type == "android" else None
    if android_cache_image:
        dockerfile = DEPS_DIR / "bitwarden-android" / "Dockerfile.from-cache"

    cmd = [
        "docker", "build",
        *docker_proxy_args(),
        "--build-arg", f"ECOSYNC_TASK_ID={task_id}",
        "--build-arg", f"ECOSYNC_BASE_COMMIT={spec['base_commit']}",
        "--build-arg", f"ECOSYNC_DEPENDENCY_FINGERPRINT={spec['dependency_fingerprint']}",
        "-f", str(dockerfile),
        "-t", image,
        str(build_context),
    ]
    if repo_type == "server":
        if "premium_status" in task_id:
            restore_project = "test/Notifications.Test/Notifications.Test.csproj"
        elif "kdf" in task_id:
            restore_project = "test/Core.Test/Core.Test.csproj"
        else:
            restore_project = "test/Api.Test/Api.Test.csproj"
        cmd[2:2] = ["--build-arg", f"ECOSYNC_RESTORE_PROJECT={restore_project}"]
    if repo_type == "android":
        if android_cache_image:
            cmd[2:2] = ["--build-arg", f"ECOSYNC_ANDROID_CACHE_IMAGE={android_cache_image}"]
            spec["cache_source"] = android_cache_image
        else:
            warmup_test = "*PushManagerTest*"
            if "bank_account" in task_id:
                warmup_test = "*VaultBankAccountTypeHiddenTest*"
            elif "kdf" in task_id or "logout" in task_id:
                warmup_test = "*PushManagerKdfLogoutHiddenTest*"
            cmd[2:2] = ["--build-arg", f"ECOSYNC_ANDROID_WARMUP_TEST={warmup_test}"]
            if os.environ.get("GITHUB_TOKEN"):
                cmd[2:2] = ["--secret", "id=github_token,env=GITHUB_TOKEN"]
            elif os.environ.get("ECOSYNC_GITHUB_TOKEN_FILE"):
                cmd[2:2] = ["--secret", f"id=github_token,src={os.environ['ECOSYNC_GITHUB_TOKEN_FILE']}"]

    env = os.environ.copy()
    env["DOCKER_BUILDKIT"] = "1"
    if dry_run:
        print(" ".join(cmd))
        shutil.rmtree(tmp, ignore_errors=True)
        return spec
    try:
        subprocess.run(cmd, cwd=REPO_ROOT, env=env, check=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    release_digest = None
    if push:
        subprocess.run(["docker", "tag", image, release], cwd=REPO_ROOT, check=True)
        with tempfile.NamedTemporaryFile(prefix="ecosync-deps-image-", suffix=".tar") as archive:
            subprocess.run(["docker", "save", "-o", archive.name, release], cwd=REPO_ROOT, check=True)
            output = subprocess.check_output(
                [
                    "python3",
                    "benchmark/harness/registry_tools/push_docker_archive.py",
                    archive.name,
                    release,
                ],
                cwd=REPO_ROOT,
                text=True,
            )
        for line in reversed([line.strip() for line in output.splitlines() if line.strip()]):
            if line.startswith(release.rsplit(":", 1)[0] + "@sha256:"):
                release_digest = line
                break
        spec["release_digest"] = release_digest

    return spec


def main() -> None:
    parser = argparse.ArgumentParser(description="Build reusable Bitwarden dependency images.")
    parser.add_argument("--task", required=True)
    parser.add_argument("--repo", choices=["server", "clients", "android"], action="append", required=True)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    results = [build(args.task, repo, args.push, args.dry_run) for repo in args.repo]
    output = {"task_id": args.task, "images": {item["repo_type"]: item for item in results}}
    print(json.dumps(output, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(yaml.safe_dump(output, sort_keys=False, width=120), encoding="utf-8")


if __name__ == "__main__":
    main()
