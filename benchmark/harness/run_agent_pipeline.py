#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any

try:
    from .run_validity import agent_execution_invalid_reason, evaluation_invalid_reason
except ImportError:  # Script execution from benchmark/harness.
    from run_validity import agent_execution_invalid_reason, evaluation_invalid_reason

try:
    from .task_selection import resolve_task_selection
except ImportError:  # Script execution from benchmark/harness.
    from task_selection import resolve_task_selection

try:
    from .ecosync_harness import evaluator_node_modules_mounts
except ImportError:  # Script execution from benchmark/harness.
    from ecosync_harness import evaluator_node_modules_mounts

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required: python3 -m pip install pyyaml") from exc


REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = REPO_ROOT / "benchmark" / "harness" / "ecosync_harness.py"
AGENT = REPO_ROOT / "benchmark" / "agents" / "run_agent.py"
SUPPORTED_AGENTS = {
    "noop",
    "gold",
    "shell",
    "claude-code",
    "codex",
}
AGENT_ENVIRONMENTS = {"agent-only", "case-env", "host"}
WORKSPACE_SCOPES = {"ecosystem", "involved", "single-repo"}
MAX_AGENT_TIMEOUT_SEC = 18000
MAX_API_REQUESTS = 200
DEFAULT_TASK_ID = None
TASK_DIR_OVERRIDE: Path | None = None


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_agent_profile(config_path: Path, profile_name: str) -> dict[str, Any]:
    config = load_yaml(config_path)
    profiles = config.get("profiles") or {}
    if profile_name not in profiles:
        raise SystemExit(f"missing agent profile '{profile_name}' in {config_path}")
    profile = profiles[profile_name] or {}
    if not isinstance(profile, dict):
        raise SystemExit(f"agent profile must be a mapping: {profile_name}")
    return profile


def formal_agent_resource_limit(env_name: str, configured: Any) -> int:
    raw = os.environ.get(env_name)
    return int(raw) if raw is not None and raw.strip() else int(configured)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def redact_snapshot_value(value: Any, key: str = "") -> Any:
    if re.search(r"(?:api[_-]?key|auth[_-]?token|secret|password|credential)", key, re.I):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(item_key): redact_snapshot_value(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact_snapshot_value(item) for item in value]
    if isinstance(value, str):
        return re.sub(
            r"(?i)\b([A-Z0-9_]*(?:API_KEY|AUTH_TOKEN|SECRET|PASSWORD))=([^\s'\"]+)",
            r"\1=<redacted>",
            value,
        )
    return value


def snapshot_run_inputs(
    *,
    run_dir: Path,
    task_id: str,
    workspace_scope: str,
    single_repo: str | None,
    agent_config: Path | None,
    agent_profile: str | None,
    profile: dict[str, Any],
    prompt_original: bytes | None = None,
    prompt_suffix_file: Path | None = None,
) -> dict[str, Any]:
    """Freeze the exact agent-visible prompt and non-secret run inputs."""
    artifacts_dir = run_dir / "artifacts" / "inputs"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    run_data = load_json_if_exists(run_dir / "run.json")
    workspace = Path(str(run_data.get("agent_workspace") or run_dir / "agent_workspace"))
    sources = {
        "prompt.md": workspace / ".ecosyncbench" / "prompt.md",
        "task.yaml": task_dir(task_id) / "task.yaml",
        "repos.yaml": task_dir(task_id) / "repos.yaml",
        "test_profiles.yaml": task_dir(task_id) / "environment" / "test_profiles.yaml",
        "deps_image_lock.yaml": task_dir(task_id) / "environment" / "deps_image_lock.yaml",
    }
    files: list[dict[str, Any]] = []
    for name, source in sources.items():
        if not source.exists():
            continue
        destination = artifacts_dir / name
        shutil.copy2(source, destination)
        files.append(
            {
                "name": name,
                "source": str(source),
                "snapshot": str(destination),
                "sha256": sha256_file(destination),
                "size_bytes": destination.stat().st_size,
            }
        )

    prompt_augmentation: dict[str, Any] | None = None
    if prompt_original is not None and prompt_suffix_file is not None:
        original_path = artifacts_dir / "prompt.original.md"
        suffix_path = artifacts_dir / "prompt_suffix.md"
        original_path.write_bytes(prompt_original)
        shutil.copy2(prompt_suffix_file, suffix_path)
        for name, source, destination in (
            ("prompt.original.md", None, original_path),
            ("prompt_suffix.md", str(prompt_suffix_file.resolve()), suffix_path),
        ):
            files.append(
                {
                    "name": name,
                    "source": source,
                    "snapshot": str(destination),
                    "sha256": sha256_file(destination),
                    "size_bytes": destination.stat().st_size,
                }
            )
        prompt_augmentation = {
            "mode": "append",
            "separator": "\\n\\n",
            "original_sha256": sha256_file(original_path),
            "suffix_sha256": sha256_file(suffix_path),
            "effective_sha256": sha256_file(artifacts_dir / "prompt.md"),
        }

    selected_profile = {
        "agent_config": str(agent_config.resolve()) if agent_config else None,
        "agent_profile": agent_profile,
        "profile": redact_snapshot_value(profile),
    }
    profile_path = artifacts_dir / "agent_profile.json"
    profile_path.write_text(json.dumps(selected_profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    files.append(
        {
            "name": "agent_profile.json",
            "source": str(agent_config.resolve()) if agent_config else None,
            "snapshot": str(profile_path),
            "sha256": sha256_file(profile_path),
            "size_bytes": profile_path.stat().st_size,
        }
    )

    evaluator_inputs: list[dict[str, Any]] = []
    for pattern in ("oracles/*.json", "patches/*.patch", "patches/**/*.patch"):
        for source in sorted(task_dir(task_id).glob(pattern)):
            if not source.is_file():
                continue
            evaluator_inputs.append(
                {
                    "path": str(source),
                    "sha256": sha256_file(source),
                    "size_bytes": source.stat().st_size,
                }
            )

    manifest = {
        "schema_version": "ecosyncbench.run_inputs.v1",
        "created_at_utc": utc_now(),
        "task_id": task_id,
        "workspace_scope": workspace_scope,
        "single_repo": single_repo,
        "files": files,
        "evaluator_input_hashes": evaluator_inputs,
    }
    if prompt_augmentation is not None:
        manifest["prompt_augmentation"] = prompt_augmentation
    manifest_path = artifacts_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest["manifest"] = str(manifest_path)
    manifest["manifest_sha256"] = sha256_file(manifest_path)
    return manifest


def append_prompt_suffix(
    workspace: Path,
    suffix_file: Path,
    run_dir: Path,
    *,
    reuse_workspace: bool = False,
) -> bytes:
    """Append one frozen suffix without changing the task's source prompt."""
    prompt_path = workspace / ".ecosyncbench" / "prompt.md"
    if not prompt_path.is_file():
        raise ValueError(f"missing agent-visible prompt: {prompt_path}")
    suffix = suffix_file.read_bytes()
    if not suffix.strip():
        raise ValueError(f"prompt suffix must not be empty: {suffix_file}")

    current = prompt_path.read_bytes()
    separator = b"\n\n"
    original_snapshot = run_dir / "artifacts" / "inputs" / "prompt.original.md"
    if reuse_workspace:
        if not original_snapshot.is_file():
            raise ValueError(
                "cannot reuse a suffixed workspace without prompt.original.md"
            )
        original = original_snapshot.read_bytes()
        expected = original + separator + suffix
        if current != expected:
            raise ValueError(
                "reused workspace prompt does not match the recorded prompt suffix"
            )
        return original

    prompt_path.write_bytes(current + separator + suffix)
    return current


def first_value(cli_value: Any, profile: dict[str, Any], key: str, default: Any = None) -> Any:
    if cli_value is not None:
        return cli_value
    return profile.get(key, default)


def default_agent_environment(sandbox: str) -> str:
    return "host" if sandbox == "none" else "case-env"


def task_dir(task_id: str) -> Path:
    if TASK_DIR_OVERRIDE is not None:
        return TASK_DIR_OVERRIDE
    return REPO_ROOT / "benchmark" / "tasks" / task_id


def case_base_lock_image(task_id: str) -> tuple[str | None, str]:
    lock_path = task_dir(task_id) / "environment" / "deps_image_lock.yaml"
    if lock_path.exists():
        lock = load_yaml(lock_path) or {}
        case_base = lock.get("case_base_image") or {}
        if isinstance(case_base, dict):
            image = case_base.get("local_tag") or case_base.get("release_tag")
            if image:
                return str(image), "task.deps_image_lock.case_base_image"
    dockerfile = task_dir(task_id) / "environment" / "case_env" / "Dockerfile.case-base"
    if dockerfile.exists():
        return f"ecosyncbench/cases/{task_id}/case-base:deps-env", "task.case_base_default_tag"
    return None, "unset"


def dependency_project_source_mask_mounts(task_id: str, run_dir: Path) -> list[str]:
    """Hide dependency-image source checkouts from the agent container.

    Publishable case images copy dependency artifacts below ``image-deps``.
    Workspace-oriented dependency images also contain the checkout used to
    install those artifacts at ``deps/project``. That checkout can be newer
    than the benchmark base commit, so exposing it leaks the target solution.
    """
    task_root = task_dir(task_id)
    dockerfile = task_root / "environment" / "case_env" / "Dockerfile.case-base"
    roots = (
        sorted(
            set(
                re.findall(
                    r"/opt/ecosync/image-deps/[A-Za-z0-9_.-]+",
                    dockerfile.read_text(encoding="utf-8"),
                )
            )
        )
        if dockerfile.exists()
        else []
    )
    workspace_masks: list[str] = []
    runtime_masks: list[str] = []
    runtime_file_masks: list[str] = []
    harness_path = task_root / "harness.yaml"
    if harness_path.exists():
        harness_config = load_yaml(harness_path) or {}
        configured_masks = harness_config.get("agent_dependency_source_masks") or []
        if not isinstance(configured_masks, list):
            raise SystemExit("agent_dependency_source_masks must be a list")
        visible_paths = visible_workspace_repo_paths(run_dir)
        for configured in configured_masks:
            relative = Path(str(configured).strip().strip("/"))
            if (
                not relative.parts
                or relative.parts[0] != "repos"
                or "node_modules" not in relative.parts
                or ".." in relative.parts
            ):
                raise SystemExit(
                    "agent_dependency_source_masks entries must be relative "
                    f"repo node_modules paths: {configured}"
                )
            relative_path = relative.as_posix()
            if visible_paths and not any(
                relative_path == visible or relative_path.startswith(f"{visible}/")
                for visible in visible_paths
            ):
                continue
            workspace_masks.append(f"/workspace/{relative_path}")
        configured_runtime_masks = harness_config.get("agent_runtime_source_masks") or []
        if not isinstance(configured_runtime_masks, list):
            raise SystemExit("agent_runtime_source_masks must be a list")
        allowed_runtime_roots = (
            "/cache/",
            "/opt/ecosync/",
            "/usr/local/lib/",
            "/usr/lib/",
        )
        for configured in configured_runtime_masks:
            destination = Path(str(configured).strip())
            normalized = destination.as_posix()
            if (
                not destination.is_absolute()
                or ".." in destination.parts
                or not normalized.startswith(allowed_runtime_roots)
            ):
                raise SystemExit(
                    "agent_runtime_source_masks entries must be absolute paths "
                    f"below an allowed runtime root: {configured}"
                )
            runtime_masks.append(normalized)
        configured_runtime_file_masks = (
            harness_config.get("agent_runtime_file_source_masks") or []
        )
        if not isinstance(configured_runtime_file_masks, list):
            raise SystemExit("agent_runtime_file_source_masks must be a list")
        for configured in configured_runtime_file_masks:
            destination = Path(str(configured).strip())
            normalized = destination.as_posix()
            if (
                not destination.is_absolute()
                or ".." in destination.parts
                or not normalized.startswith(allowed_runtime_roots)
            ):
                raise SystemExit(
                    "agent_runtime_file_source_masks entries must be absolute paths "
                    f"below an allowed runtime root: {configured}"
                )
            runtime_file_masks.append(normalized)
    if not roots and not workspace_masks and not runtime_masks and not runtime_file_masks:
        return []
    empty_dir = run_dir / "agent_runtime" / "empty-dependency-project"
    empty_dir.mkdir(parents=True, exist_ok=True)
    empty_dir.chmod(0o755)
    empty_file = run_dir / "agent_runtime" / "empty-dependency-file"
    if runtime_file_masks:
        empty_file.touch()
        empty_file.chmod(0o644)
    return [
        *[f"{empty_dir}:{root}/deps/project:ro" for root in roots],
        *[f"{empty_dir}:{destination}:ro" for destination in workspace_masks],
        *[f"{empty_dir}:{destination}:ro" for destination in runtime_masks],
        *[f"{empty_file}:{destination}:ro" for destination in runtime_file_masks],
    ]


def task_agent_python_source_paths(task_id: str) -> list[str]:
    """Return task-configured workspace Python sources exposed to the Agent."""
    harness_path = task_dir(task_id) / "harness.yaml"
    if not harness_path.exists():
        return []
    harness_config = load_yaml(harness_path) or {}
    configured_paths = harness_config.get("agent_python_source_paths") or []
    if not isinstance(configured_paths, list):
        raise SystemExit("agent_python_source_paths must be a list")
    source_paths: list[str] = []
    for configured in configured_paths:
        relative = Path(str(configured).strip().strip("/"))
        if not relative.parts or relative.parts[0] != "repos" or ".." in relative.parts:
            raise SystemExit(
                "agent_python_source_paths entries must be relative workspace repo paths: "
                f"{configured}"
            )
        source_paths.append(f"/workspace/{relative.as_posix()}")
    return list(dict.fromkeys(source_paths))


def materialize_agent_python_distribution_metadata(
    task_id: str, run_dir: Path
) -> str | None:
    """Expose task-configured local package versions to importlib.metadata."""
    harness_path = task_dir(task_id) / "harness.yaml"
    if not harness_path.exists():
        return None
    harness_config = load_yaml(harness_path) or {}
    configured_metadata = harness_config.get("agent_python_distribution_metadata") or {}
    if not isinstance(configured_metadata, dict):
        raise SystemExit("agent_python_distribution_metadata must be a mapping")
    workspace = agent_workspace_path(run_dir)
    if not configured_metadata or workspace is None or not workspace.is_dir():
        return None
    metadata_root = workspace / ".ecosyncbench" / "python-metadata"
    metadata_root.mkdir(parents=True, exist_ok=True)
    for distribution, configured_version in configured_metadata.items():
        name = str(distribution).strip()
        version = str(configured_version).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
            raise SystemExit(
                f"invalid distribution name in agent_python_distribution_metadata: {distribution}"
            )
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+!-]*", version):
            raise SystemExit(
                "invalid distribution version in agent_python_distribution_metadata "
                f"for {name}: {configured_version}"
            )
        normalized_name = re.sub(r"[-_.]+", "_", name)
        dist_info = metadata_root / f"{normalized_name}-{version}.dist-info"
        dist_info.mkdir(parents=True, exist_ok=True)
        (dist_info / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
            encoding="utf-8",
        )
    return "/workspace/.ecosyncbench/python-metadata"


def materialize_agent_cargo_workspace_patches(task_id: str, run_dir: Path) -> dict[str, str]:
    """Route selected crates.io packages to local ecosystem repositories for Agent runs."""
    harness_path = task_dir(task_id) / "harness.yaml"
    if not harness_path.exists():
        return {}
    harness_config = load_yaml(harness_path) or {}
    configured_patches = harness_config.get("agent_cargo_workspace_patches") or {}
    if not isinstance(configured_patches, dict):
        raise SystemExit("agent_cargo_workspace_patches must be a mapping")
    workspace = agent_workspace_path(run_dir)
    if not configured_patches or workspace is None or not workspace.is_dir():
        return {}
    patches: dict[str, str] = {}
    path_overrides: list[str] = []
    registry_patches: dict[str, str] = {}
    visible_paths = set(visible_workspace_repo_paths(run_dir))
    for crate, configured in configured_patches.items():
        crate_name = str(crate).strip()
        if isinstance(configured, dict):
            configured_path = configured.get("path")
            locked_version = str(configured.get("locked_version") or "").strip()
            locked_manifest_cache_path = str(
                configured.get("locked_manifest_cache_path") or ""
            ).strip()
        else:
            configured_path = configured
            locked_version = ""
            locked_manifest_cache_path = ""
        relative = Path(str(configured_path or "").strip().strip("/"))
        if not re.fullmatch(r"[A-Za-z0-9_-]+", crate_name):
            raise SystemExit(f"invalid crate name in agent_cargo_workspace_patches: {crate}")
        if not relative.parts or relative.parts[0] != "repos" or ".." in relative.parts:
            raise SystemExit(
                "agent_cargo_workspace_patches paths must be relative workspace repo paths: "
                f"{configured}"
            )
        if visible_paths and relative.as_posix() not in visible_paths:
            continue
        if not (workspace / relative / "Cargo.toml").is_file():
            raise SystemExit(
                f"agent Cargo workspace patch does not contain Cargo.toml: {configured_path}"
            )
        container_path = f"/workspace/{relative.as_posix()}"
        if locked_version or locked_manifest_cache_path:
            if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9_.-]+)?", locked_version):
                if not locked_manifest_cache_path:
                    raise SystemExit(
                        f"invalid locked_version for Agent Cargo workspace patch {crate_name}: "
                        f"{locked_version}"
                    )
            source_root = workspace / relative
            proxy_relative = Path(".ecosyncbench") / "cargo-patches" / safe_slug(crate_name)
            proxy_root = workspace / proxy_relative
            proxy_root.mkdir(parents=True, exist_ok=True)
            for child in source_root.iterdir():
                if child.name in {".git", "Cargo.lock", "Cargo.toml", "target"}:
                    continue
                destination = proxy_root / child.name
                if not destination.exists() and not destination.is_symlink():
                    destination.symlink_to(f"/workspace/{relative.as_posix()}/{child.name}")
            if locked_manifest_cache_path:
                manifest_relative = Path(locked_manifest_cache_path.strip("/"))
                cache_root = task_cache_root(task_id)
                if (
                    not manifest_relative.parts
                    or manifest_relative.is_absolute()
                    or ".." in manifest_relative.parts
                    or cache_root is None
                    or not (cache_root / manifest_relative).is_file()
                ):
                    raise SystemExit(
                        "invalid locked_manifest_cache_path for Agent Cargo workspace patch "
                        f"{crate_name}: {locked_manifest_cache_path}"
                    )
                cargo_lines = (cache_root / manifest_relative).read_text(
                    encoding="utf-8"
                ).splitlines()
            else:
                cargo_lines = (source_root / "Cargo.toml").read_text(
                    encoding="utf-8"
                ).splitlines()
                in_package = False
                version_replaced = False
                for index, line in enumerate(cargo_lines):
                    stripped = line.strip()
                    if stripped.startswith("[") and stripped.endswith("]"):
                        in_package = stripped == "[package]"
                        continue
                    if in_package and re.match(r"^\s*version\s*=", line):
                        prefix = line[: len(line) - len(line.lstrip())]
                        cargo_lines[index] = f'{prefix}version = "{locked_version}"'
                        version_replaced = True
                        break
                if not version_replaced:
                    raise SystemExit(
                        f"Agent Cargo workspace patch has no package version: {source_root}"
                    )
            (proxy_root / "Cargo.toml").write_text(
                "\n".join(cargo_lines) + "\n", encoding="utf-8"
            )
            container_path = f"/workspace/{proxy_relative.as_posix()}"
            if locked_manifest_cache_path:
                path_overrides.append(container_path)
            else:
                registry_patches[crate_name] = container_path
        else:
            registry_patches[crate_name] = container_path
        patches[crate_name] = container_path
    config_path = workspace / ".cargo" / "config.toml"
    config_text = ""
    if path_overrides:
        config_text += "paths = [\n" + "".join(
            f"  {json.dumps(path)},\n" for path in sorted(path_overrides)
        ) + "]\n"
    if registry_patches:
        if config_text:
            config_text += "\n"
        config_text += "[patch.crates-io]\n" + "".join(
            f"{json.dumps(crate)} = {{ path = {json.dumps(path)} }}\n"
            for crate, path in sorted(registry_patches.items())
        )
    if config_path.exists():
        existing_config = config_path.read_text(encoding="utf-8")
        if existing_config.strip() and existing_config != config_text:
            raise SystemExit(f"Agent workspace Cargo config already exists with different content: {config_path}")
        if existing_config == config_text:
            return patches
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config_text, encoding="utf-8")
    return patches


def resolve_sandbox_image(
    profile: dict[str, Any],
    task_id: str,
    cli_image: str | None,
    *,
    agent_id: str | None = None,
    agent_environment: str | None = None,
) -> tuple[str | None, str]:
    if cli_image is not None:
        return cli_image, "cli"
    image_by_task = profile.get("sandbox_image_by_task") or {}
    if isinstance(image_by_task, dict) and task_id in image_by_task:
        return str(image_by_task[task_id]), "profile.sandbox_image_by_task"
    image = profile.get("sandbox_image")
    if image:
        return str(image), "profile.sandbox_image"
    if agent_environment == "case-env":
        image, source = case_base_lock_image(task_id)
        if image:
            return image, source
    return None, "unset"


def safe_slug(value: str) -> str:
    cleaned = []
    for char in value.strip():
        if char.isalnum() or char in ("-", "_", "."):
            cleaned.append(char)
        else:
            cleaned.append("_")
    return "".join(cleaned).strip("._") or "unknown"


def task_cache_root(task_id: str) -> Path | None:
    configured = os.environ.get("ECOSYNC_CACHE_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    configured_base = os.environ.get("ECOSYNC_CACHE_BASE_ROOT")
    if configured_base:
        return (Path(configured_base).expanduser() / task_id).resolve()
    run_profile = task_dir(task_id) / "environment" / "run_profile.sh"
    if not run_profile.exists():
        return None
    text = run_profile.read_text(encoding="utf-8", errors="replace")
    match = re.search(r'cache_root=["\']([^"\']+)["\']', text)
    if not match:
        return None
    value = match.group(1)
    default_match = re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-(.+)\}", value)
    if default_match:
        value = default_match.group(1)
    value = re.sub(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-([^}]+)\}", r"\1", value)
    return Path(value)


def prepare_task_cache_root(task_id: str) -> Path | None:
    root = task_cache_root(task_id)
    if root is None:
        return None
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chown(root, -1, os.getgid())
        root.chmod(root.stat().st_mode | 0o2770)
    except PermissionError:
        pass
    return root


def prewarm_boxo_kubo_agent_go_cache(
    task_id: str,
    run_dir: Path,
    sandbox_image: str,
) -> dict[str, Any]:
    """Prepare a writable offline Go cache for Kubo against the local Boxo checkout."""
    workspace = agent_workspace_path(run_dir)
    if workspace is None or not workspace.exists():
        run_data = load_json_if_exists(run_dir / "run.json")
        evaluator_workspace = run_data.get("evaluator_workspace")
        if evaluator_workspace:
            workspace = Path(str(evaluator_workspace))
    cache_root = task_cache_root(task_id)
    if workspace is None or cache_root is None:
        return {"enabled": False}
    boxo_module = workspace / "repos/ipfs/boxo/go.mod"
    kubo_module = workspace / "repos/ipfs/kubo/go.mod"
    go_work = workspace / "go.work"
    if not (boxo_module.exists() and kubo_module.exists()):
        return {"enabled": False}
    if not go_work.exists():
        versions = []
        for module in (boxo_module, kubo_module):
            text = module.read_text(encoding="utf-8", errors="replace")
            match = re.search(r"^[ \t]*go[ \t]+(\d+(?:\.\d+){1,2})[ \t]*$", text, re.M)
            if match:
                value = match.group(1)
                parts = tuple(int(part) for part in value.split("."))
                versions.append((parts + (0,) * (3 - len(parts)), value))
        go_version = max(versions)[1] if versions else "1.25.0"
        go_work.write_text(
            f"go {go_version}\n\n"
            "use ./repos/ipfs/kubo\n\n"
            "replace github.com/ipfs/boxo => ./repos/ipfs/boxo\n",
            encoding="utf-8",
        )

    fingerprint = hashlib.sha256()
    fingerprint.update(b"boxo-kubo-agent-go-cache-v2\0")
    for path in (boxo_module, kubo_module, go_work):
        fingerprint.update(path.read_bytes())
    marker_name = f".ecosync-ready-{fingerprint.hexdigest()[:16]}"
    module_cache = cache_root / "agent-go-mod"
    build_cache = cache_root / "agent-go-build"
    module_cache.mkdir(parents=True, exist_ok=True)
    build_cache.mkdir(parents=True, exist_ok=True)
    module_cache.chmod(0o777)
    build_cache.chmod(0o777)
    marker = module_cache / marker_name
    if marker.exists():
        return {
            "enabled": True,
            "cache_hit": True,
            "module_cache": str(module_cache),
            "build_cache": str(build_cache),
        }

    script = r"""
set -euo pipefail
module_cache=/cache/agent-go-mod
for source in /opt/ecosync/gomodcache /opt/ecosync/image-deps/*/gomodcache; do
  [[ -d "$source" ]] || continue
  while IFS= read -r module; do
    rel=${module#"$source"/}
    target="$module_cache/$rel"
    if [[ ! -e "$target" && ! -L "$target" ]]; then
      mkdir -p "${target%/*}"
      ln -s "$module" "$target"
    fi
  done < <(find "$source" -mindepth 2 -type d -name '*@v*' -not -path '*/cache/download/*')
done
go_version=$(awk '$1 == "go" { print $2; exit }' /workspace/go.work)
cat > /tmp/ecosync-prewarm.work <<EOF
go ${go_version}

use /workspace/repos/ipfs/kubo

replace github.com/ipfs/boxo => /workspace/repos/ipfs/boxo
EOF
cd /workspace/repos/ipfs/kubo
GOWORK=/tmp/ecosync-prewarm.work \
GOMODCACHE="$module_cache" \
GOCACHE=/cache/agent-go-build \
GOPROXY=https://proxy.golang.org \
GOSUMDB=off \
/usr/local/go/bin/go mod download all
"""
    cmd = [
        "docker",
        "run",
        "--rm",
        "--network",
        "bridge",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--env",
        f"HOME=/tmp/ecosync-home-{os.getuid()}",
        "--env",
        "HTTP_PROXY=",
        "--env",
        "HTTPS_PROXY=",
        "--env",
        "ALL_PROXY=",
        "--volume",
        f"{workspace}:/workspace:ro",
        "--volume",
        f"{module_cache}:/cache/agent-go-mod:rw",
        "--volume",
        f"{build_cache}:/cache/agent-go-build:rw",
        "--entrypoint",
        "/bin/bash",
        sandbox_image,
        "-lc",
        script,
    ]
    code, output = run_capture(cmd)
    if code == 0:
        marker.write_text(utc_now() + "\n", encoding="utf-8")
    return {
        "enabled": True,
        "cache_hit": False,
        "ok": code == 0,
        "exit_code": code,
        "module_cache": str(module_cache),
        "build_cache": str(build_cache),
        "output_tail": output[-4000:],
    }


def set_env_assignment(env: list[str], name: str, value: str) -> None:
    assignment = f"{name}={value}"
    for index, item in enumerate(env):
        if item.split("=", 1)[0] == name:
            env[index] = assignment
            return
    env.append(assignment)


def env_assignment(env: list[str], name: str) -> str | None:
    for item in reversed(env):
        key, separator, value = item.partition("=")
        if separator and key == name:
            return value
    return None


def writable_mount_for_container_path(
    mounts: list[str],
    container_path: str,
) -> tuple[str, str] | None:
    target = Path(container_path)
    candidates: list[tuple[int, str, str]] = []
    for mount in mounts:
        host, container, mode = split_mount_spec(mount)
        if mode != "rw" or not host or not container:
            continue
        container_root = Path(container)
        if target == container_root or container_root in target.parents:
            candidates.append((len(container_root.parts), host, container))
    if not candidates:
        return None
    _depth, host, container = max(candidates)
    return host, container


def task_involved_go_module_dirs(task_id: str, workspace: Path) -> list[Path]:
    task_yaml = load_yaml(task_dir(task_id) / "task.yaml") or {}
    repositories = task_yaml.get("repositories") if isinstance(task_yaml, dict) else []
    repository_roots: dict[str, Path] = {}
    for repository in repositories if isinstance(repositories, list) else []:
        if not isinstance(repository, dict):
            continue
        name = str(repository.get("name") or "")
        relative = str(repository.get("path") or "").strip("/")
        if name and relative:
            repository_roots[name] = workspace / relative

    candidates: set[Path] = set()
    for root in repository_roots.values():
        if (root / "go.mod").is_file():
            candidates.add(root.resolve())

    profiles_path = task_dir(task_id) / "environment" / "test_profiles.yaml"
    profiles_yaml = load_yaml(profiles_path) if profiles_path.exists() else {}
    profiles = profiles_yaml.get("profiles") if isinstance(profiles_yaml, dict) else {}
    for profile in profiles.values() if isinstance(profiles, dict) else []:
        if not isinstance(profile, dict):
            continue
        repo_root = repository_roots.get(str(profile.get("repo") or ""))
        workdir = str(profile.get("workdir") or "").strip("/")
        if repo_root is None or not workdir:
            continue
        current = workspace / workdir
        while current == repo_root or repo_root in current.parents:
            if (current / "go.mod").is_file():
                candidates.add(current.resolve())
                break
            if current == repo_root:
                break
            current = current.parent

    for repository in repositories if isinstance(repositories, list) else []:
        if not isinstance(repository, dict):
            continue
        repo_root = repository_roots.get(str(repository.get("name") or ""))
        patch_relative = str(repository.get("gold_patch") or "").strip()
        patch_path = task_dir(task_id) / patch_relative
        if repo_root is None or not patch_relative or not patch_path.exists():
            continue
        patch_text = patch_path.read_text(encoding="utf-8", errors="replace")
        resolved_repo_root = repo_root.resolve()
        for changed_file in re.findall(r"^\+\+\+ b/(.+\.go)$", patch_text, re.M):
            current = (resolved_repo_root / changed_file).parent
            while current == resolved_repo_root or resolved_repo_root in current.parents:
                if (current / "go.mod").is_file():
                    candidates.add(current)
                    break
                if current == resolved_repo_root:
                    break
                current = current.parent

    pending = list(candidates)
    while pending:
        module_dir = pending.pop()
        module_text = (module_dir / "go.mod").read_text(encoding="utf-8", errors="replace")
        for relative in re.findall(r"=>\s+((?:\./|\.\./)[^\s]+)", module_text):
            replacement = (module_dir / relative).resolve()
            try:
                replacement.relative_to(workspace.resolve())
            except ValueError:
                continue
            if not (replacement / "go.mod").is_file() or replacement in candidates:
                continue
            candidates.add(replacement)
            pending.append(replacement)
    return sorted(candidates)


def task_go_prewarm_targets(
    task_id: str,
    workspace: Path,
    module_dirs: list[Path],
) -> dict[Path, list[str]]:
    profiles_path = task_dir(task_id) / "environment" / "test_profiles.yaml"
    profiles_yaml = load_yaml(profiles_path) if profiles_path.exists() else {}
    profiles = profiles_yaml.get("profiles") if isinstance(profiles_yaml, dict) else {}
    targets: dict[Path, set[str]] = {}

    task_yaml = load_yaml(task_dir(task_id) / "task.yaml") or {}
    repositories = task_yaml.get("repositories") if isinstance(task_yaml, dict) else []
    for repository in repositories if isinstance(repositories, list) else []:
        if not isinstance(repository, dict):
            continue
        repo_relative = str(repository.get("path") or "").strip("/")
        patch_relative = str(repository.get("gold_patch") or "").strip()
        patch_path = task_dir(task_id) / patch_relative
        if not repo_relative or not patch_relative or not patch_path.exists():
            continue
        repo_root = (workspace / repo_relative).resolve()
        patch_text = patch_path.read_text(encoding="utf-8", errors="replace")
        for changed_file in re.findall(r"^\+\+\+ b/(.+\.go)$", patch_text, re.M):
            package_dir = (repo_root / changed_file).parent.resolve()
            module_dir = next(
                (
                    candidate
                    for candidate in sorted(module_dirs, key=lambda path: len(path.parts), reverse=True)
                    if package_dir == candidate or candidate in package_dir.parents
                ),
                None,
            )
            if module_dir is None:
                continue
            relative = package_dir.relative_to(module_dir)
            targets.setdefault(module_dir, set()).add(
                "." if not relative.parts else f"./{relative.as_posix()}"
            )

    for profile in profiles.values() if isinstance(profiles, dict) else []:
        if not isinstance(profile, dict):
            continue
        workdir_value = str(profile.get("workdir") or "").strip("/")
        if not workdir_value:
            continue
        workdir = (workspace / workdir_value).resolve()
        module_dir = next(
            (
                candidate
                for candidate in sorted(module_dirs, key=lambda path: len(path.parts), reverse=True)
                if workdir == candidate or candidate in workdir.parents
            ),
            None,
        )
        if module_dir is None:
            continue
        profile_targets = targets.setdefault(module_dir, set())
        for package in profile.get("test_packages") or []:
            value = str(package).strip()
            if value:
                profile_targets.add(value)
        for test_file in profile.get("test_files") or []:
            value = str(test_file).strip()
            if not value.endswith(".go"):
                continue
            package_dir = (workdir / value).parent
            try:
                relative = package_dir.relative_to(module_dir)
            except ValueError:
                continue
            profile_targets.add("." if not relative.parts else f"./{relative.as_posix()}")
        for command_name in ("command", "public_command", "public_smoke_command"):
            command = str(profile.get(command_name) or "")
            for match in re.finditer(r"(?:^|[;&|]\s*|\s)go\s+test\s+([^;&|\n]+)", command):
                try:
                    tokens = shlex.split(match.group(1))
                except ValueError:
                    continue
                for token in tokens:
                    if token == "." or token.startswith("./"):
                        profile_targets.add(token)
    def local_target_exists(module_dir: Path, target: str) -> bool:
        if target == "." or not target.startswith("./"):
            return True
        static_prefix = target[2:].split("...", 1)[0].rstrip("/")
        return not static_prefix or (module_dir / static_prefix).exists()

    return {
        module_dir: sorted(
            target for target in values if local_target_exists(module_dir, target)
        )
        for module_dir, values in targets.items()
        if any(local_target_exists(module_dir, target) for target in values)
    }


def missing_local_go_replacements(module_dir: Path) -> list[str]:
    """Return local replacements whose module source is absent from the workspace."""
    go_mod = module_dir / "go.mod"
    if not go_mod.is_file():
        return []
    missing: set[str] = set()
    pattern = re.compile(
        r"^\s*(?:replace\s+)?([^\s]+)(?:\s+[^\s]+)?\s+=>\s+((?:\./|\.\./)[^\s]+)"
    )
    for raw_line in go_mod.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.split("//", 1)[0]
        match = pattern.match(line)
        if not match:
            continue
        replacement = (module_dir / match.group(2)).resolve()
        if not (replacement / "go.mod").is_file():
            missing.add(match.group(1))
    return sorted(missing)


def inherited_kubernetes_staging_replacements(
    module_dir: Path, module_dirs: list[Path]
) -> list[tuple[str, str]]:
    """Fill staging replacements omitted by a Kubernetes-consuming sibling module."""
    go_mod = module_dir / "go.mod"
    if not go_mod.is_file():
        return []
    content = go_mod.read_text(encoding="utf-8", errors="replace")
    if not re.search(
        r"^\s*(?:require\s+)?k8s\.io/kubernetes\s+v1\.\d+\.\d+",
        content,
        re.MULTILINE,
    ):
        return []

    replacement_pattern = re.compile(
        r"^\s*(?:replace\s+)?([^\s]+)(?:\s+[^\s]+)?\s+=>\s+([^\s]+)\s+(v[^\s]+)"
    )

    def replacements(path: Path) -> dict[str, tuple[str, str]]:
        parsed: dict[str, tuple[str, str]] = {}
        for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = replacement_pattern.match(raw_line.split("//", 1)[0])
            if match:
                parsed[match.group(1)] = (match.group(2), match.group(3))
        return parsed

    current = replacements(go_mod)
    candidates: dict[str, set[tuple[str, str]]] = {}
    for candidate_dir in module_dirs:
        candidate_mod = candidate_dir / "go.mod"
        if not candidate_mod.is_file():
            continue
        for source, target in replacements(candidate_mod).items():
            if (
                source.startswith("k8s.io/")
                and target[0] == source
                and re.fullmatch(r"v0\.\d+\.\d+(?:[-+].*)?", target[1])
            ):
                candidates.setdefault(source, set()).add(target)

    inherited: list[tuple[str, str]] = []
    for source, targets in sorted(candidates.items()):
        if source in current or len(targets) != 1:
            continue
        target_path, target_version = next(iter(targets))
        inherited.append((source, f"{target_path}@{target_version}"))
    return inherited


def prewarm_agent_go_modules(
    task_id: str,
    run_dir: Path,
    sandbox_image: str,
    sandbox_mounts: list[str],
    sandbox_env: list[str],
) -> dict[str, Any]:
    """Populate the persistent Go module cache before the offline agent starts."""
    workspace = agent_workspace_path(run_dir)
    cache_root = task_cache_root(task_id)
    if workspace is None or not workspace.exists() or cache_root is None:
        return {"enabled": False}
    workspace = workspace.resolve()
    module_dirs = task_involved_go_module_dirs(task_id, workspace)
    if not module_dirs:
        return {"enabled": False}
    prewarm_targets = task_go_prewarm_targets(task_id, workspace, module_dirs)

    cache_defaults = {
        "GOMODCACHE": "/ecosync-agent-go-cache/mod",
        "GOCACHE": "/ecosync-agent-go-cache/build",
        "GOPATH": "/ecosync-agent-go-cache/gopath",
        "GOBIN": "/ecosync-agent-go-cache/bin",
    }
    required_mounts: dict[str, str] = {}
    for name, fallback in cache_defaults.items():
        container_path = env_assignment(sandbox_env, name) or fallback
        matched = writable_mount_for_container_path(sandbox_mounts, container_path)
        if matched is not None:
            host, container = matched
            host_target = Path(host) / Path(container_path).relative_to(container)
            host_target.mkdir(parents=True, exist_ok=True)
            try:
                host_target.chmod(0o777)
            except PermissionError:
                pass
            existing_children = [
                child for child in host_target.iterdir() if child.is_dir()
            ]
            if not os.access(host_target, os.W_OK) or any(
                not os.access(child, os.W_OK) for child in existing_children
            ):
                matched = None
        if matched is None:
            cache_host = cache_root / "agent-go-cache"
            cache_host.mkdir(parents=True, exist_ok=True)
            cache_host.chmod(0o777)
            cache_mount = f"{cache_host}:/ecosync-agent-go-cache:rw"
            if cache_mount not in sandbox_mounts:
                sandbox_mounts.append(cache_mount)
            container_path = fallback
            matched = (str(cache_host), "/ecosync-agent-go-cache")
        set_env_assignment(sandbox_env, name, container_path)
        host, container = matched
        required_mounts[container] = f"{host}:{container}:rw"
        host_target = Path(host) / Path(container_path).relative_to(container)
        host_target.mkdir(parents=True, exist_ok=True)
        try:
            host_target.chmod(0o777)
        except PermissionError:
            pass

    fingerprint = hashlib.sha256()
    fingerprint.update(b"agent-go-modules-v10\0")
    fingerprint.update(sandbox_image.encode("utf-8"))
    for module_dir in module_dirs:
        fingerprint.update(str(module_dir.relative_to(workspace)).encode("utf-8"))
        for filename in ("go.mod", "go.sum"):
            path = module_dir / filename
            fingerprint.update(filename.encode("utf-8"))
            fingerprint.update(path.read_bytes() if path.exists() else b"<missing>")
        for target in prewarm_targets.get(module_dir, []):
            fingerprint.update(target.encode("utf-8"))
    marker_dir = cache_root / ".agent-go-prewarm"
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker = marker_dir / f"{fingerprint.hexdigest()}.json"
    if marker.exists():
        return {
            "enabled": True,
            "cache_hit": True,
            "module_dirs": [str(path.relative_to(workspace)) for path in module_dirs],
        }

    mutable_paths = {
        *(path / filename for path in module_dirs for filename in ("go.mod", "go.sum")),
        workspace / "go.work",
        workspace / "go.work.sum",
    }
    originals: dict[Path, tuple[bytes, int] | None] = {}
    for path in mutable_paths:
        originals[path] = (path.read_bytes(), path.stat().st_mode) if path.exists() else None

    modules_file = run_dir / "case_debug_deps" / "go-prewarm-modules.txt"
    modules_file.parent.mkdir(parents=True, exist_ok=True)
    modules_file.write_text(
        "\n".join(f"/workspace/{path.relative_to(workspace)}" for path in module_dirs) + "\n",
        encoding="utf-8",
    )
    targets_file = run_dir / "case_debug_deps" / "go-prewarm-targets.tsv"
    targets_file.write_text(
        "".join(
            f"/workspace/{module_dir.relative_to(workspace)}\t{' '.join(targets)}\n"
            for module_dir, targets in sorted(prewarm_targets.items())
        ),
        encoding="utf-8",
    )
    missing_replacements_file = run_dir / "case_debug_deps" / "go-prewarm-missing-replacements.tsv"
    missing_replacements_file.write_text(
        "".join(
            f"/workspace/{module_dir.relative_to(workspace)}\t{module_path}\n"
            for module_dir in module_dirs
            for module_path in missing_local_go_replacements(module_dir)
        ),
        encoding="utf-8",
    )
    inherited_replacements_file = (
        run_dir / "case_debug_deps" / "go-prewarm-inherited-replacements.tsv"
    )
    inherited_replacements_file.write_text(
        "".join(
            f"/workspace/{module_dir.relative_to(workspace)}\t{module_path}\t{replacement}\n"
            for module_dir in module_dirs
            for module_path, replacement in inherited_kubernetes_staging_replacements(
                module_dir, module_dirs
            )
        ),
        encoding="utf-8",
    )
    docker_env = []
    for name in cache_defaults:
        value = env_assignment(sandbox_env, name)
        if value:
            docker_env.extend(
                [
                    "--env",
                    f"{name}={value}",
                    "--env",
                    f"ECOSYNC_PREWARM_{name}={value}",
                ]
            )
    script = r"""
set -euo pipefail
if [[ -n "${GOFLAGS:-}" ]]; then
  sanitized_go_flags=()
  read -r -a existing_go_flags <<< "$GOFLAGS"
  for flag in "${existing_go_flags[@]}"; do
    [[ "$flag" == "-mod=mod" ]] || sanitized_go_flags+=("$flag")
  done
  GOFLAGS="${sanitized_go_flags[*]}"
  export GOFLAGS
fi
activate_prewarm_go_cache() {
  # Repo-aware login-shell hooks may replace these variables after cd.
  export GOMODCACHE="$ECOSYNC_PREWARM_GOMODCACHE"
  export GOCACHE="$ECOSYNC_PREWARM_GOCACHE"
  export GOPATH="$ECOSYNC_PREWARM_GOPATH"
  export GOBIN="$ECOSYNC_PREWARM_GOBIN"
}
activate_prewarm_go_cache
go_bin=/usr/local/go/bin/go
if [[ ! -x "$go_bin" ]]; then
  go_bin=$(type -P go)
fi
prewarm_go_work=/tmp/ecosync-prewarm.work
prepare_go_work() {
  local current_module_dir=$1
  local current_module_path module_dir module_path go_version
  current_module_path=$(sed -n 's/^module[[:space:]]\+//p' "$current_module_dir/go.mod" | head -1 | tr -d '"')
  go_version=$("$go_bin" env GOVERSION)
  go_version=${go_version#go}
  {
    printf 'go %s\n\nuse %s\n\nreplace (\n' "$go_version" "$current_module_dir"
    declare -A seen_module_paths=()
    while IFS= read -r module_dir; do
      [[ -n "$module_dir" && "$module_dir" != "$current_module_dir" ]] || continue
      module_path=$(sed -n 's/^module[[:space:]]\+//p' "$module_dir/go.mod" | head -1 | tr -d '"')
      [[ -n "$module_path" && "$module_path" != "$current_module_path" ]] || continue
      [[ -z "${seen_module_paths[$module_path]:-}" ]] || continue
      seen_module_paths[$module_path]=1
      printf '\t%s => %s\n' "$module_path" "$module_dir"
    done < /ecosync-prewarm/go-modules.txt
    printf ')\n'
  } > "$prewarm_go_work"
  export GOWORK="$prewarm_go_work"
}
drop_missing_local_replacements() {
  local current_module_dir=$1 module_dir module_path
  while IFS=$'\t' read -r module_dir module_path; do
    [[ "$module_dir" == "$current_module_dir" && -n "$module_path" ]] || continue
    "$go_bin" mod edit "-dropreplace=$module_path"
  done < /ecosync-prewarm/go-missing-replacements.tsv
}
apply_inherited_remote_replacements() {
  local current_module_dir=$1 module_dir module_path replacement
  while IFS=$'\t' read -r module_dir module_path replacement; do
    [[ "$module_dir" == "$current_module_dir" && -n "$module_path" && -n "$replacement" ]] || continue
    "$go_bin" mod edit "-replace=$module_path=$replacement"
  done < /ecosync-prewarm/go-inherited-replacements.tsv
}
while IFS= read -r module_dir; do
  [[ -n "$module_dir" ]] || continue
  cd "$module_dir"
  activate_prewarm_go_cache
  drop_missing_local_replacements "$module_dir"
  apply_inherited_remote_replacements "$module_dir"
  prepare_go_work "$module_dir"
  GOPROXY=https://proxy.golang.org,direct GOSUMDB=sum.golang.org timeout 900s "$go_bin" mod download all
done < /ecosync-prewarm/go-modules.txt
while IFS=$'\t' read -r module_dir packages; do
  [[ -n "$module_dir" && -n "$packages" ]] || continue
  cd "$module_dir"
  activate_prewarm_go_cache
  drop_missing_local_replacements "$module_dir"
  apply_inherited_remote_replacements "$module_dir"
  prepare_go_work "$module_dir"
  read -r -a package_args <<< "$packages"
  dependency_list=$(mktemp)
  GOPROXY=https://proxy.golang.org,direct GOSUMDB=sum.golang.org timeout 900s \
    "$go_bin" list -deps -test -e -f \
      '{{with .Module}}{{if .Replace}}{{with .Replace}}{{if .Version}}{{.Path}}@{{.Version}}{{end}}{{end}}{{else}}{{if .Version}}{{.Path}}@{{.Version}}{{end}}{{end}}{{end}}' \
      "${package_args[@]}" | sort -u > "$dependency_list"
  while IFS= read -r dependency; do
    [[ -n "$dependency" ]] || continue
    download_metadata=$(GOPROXY=https://proxy.golang.org,direct GOSUMDB=sum.golang.org timeout 900s \
      "$go_bin" mod download -json "$dependency")
    zip_path=$(printf '%s\n' "$download_metadata" | sed -n 's/^[[:space:]]*"Zip": "\([^"]*\)",/\1/p')
    if [[ -z "$zip_path" || ! -s "$zip_path" ]]; then
      printf 'Go module cache did not persist a zip for %s\n%s\n' \
        "$dependency" "$download_metadata" >&2
      exit 86
    fi
  done < "$dependency_list"
done < /ecosync-prewarm/go-targets.tsv
"""
    cmd = [
        "timeout",
        "--signal=TERM",
        "--kill-after=30s",
        "1200s",
        "docker",
        "run",
        "--rm",
        "--network",
        "bridge",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--env",
        f"HOME=/tmp/ecosync-home-{os.getuid()}",
        "--env",
        "HTTP_PROXY=",
        "--env",
        "HTTPS_PROXY=",
        "--env",
        "ALL_PROXY=",
        *docker_env,
        "--volume",
        f"{workspace}:/workspace:rw",
        "--volume",
        f"{modules_file}:/ecosync-prewarm/go-modules.txt:ro",
        "--volume",
        f"{targets_file}:/ecosync-prewarm/go-targets.tsv:ro",
        "--volume",
        f"{missing_replacements_file}:/ecosync-prewarm/go-missing-replacements.tsv:ro",
        "--volume",
        f"{inherited_replacements_file}:/ecosync-prewarm/go-inherited-replacements.tsv:ro",
    ]
    for mount in required_mounts.values():
        cmd.extend(["--volume", mount])
    cmd.extend(["--entrypoint", "/bin/bash", sandbox_image, "-lc", script])
    try:
        code, output = run_capture(cmd)
    finally:
        for path, original in originals.items():
            if original is None:
                if path.exists():
                    path.unlink()
                continue
            contents, mode = original
            path.write_bytes(contents)
            path.chmod(mode)
    if code == 0:
        marker.write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "created_at": utc_now(),
                    "module_dirs": [str(path.relative_to(workspace)) for path in module_dirs],
                    "targets": {
                        str(path.relative_to(workspace)): targets
                        for path, targets in prewarm_targets.items()
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return {
        "enabled": True,
        "cache_hit": False,
        "ok": code == 0,
        "exit_code": code,
        "module_dirs": [str(path.relative_to(workspace)) for path in module_dirs],
        "targets": {
            str(path.relative_to(workspace)): targets
            for path, targets in prewarm_targets.items()
        },
        "output_tail": output[-4000:],
    }


def task_dotnet_target_framework_majors(task_id: str, workspace: Path) -> list[int]:
    """Return the .NET target bands an agent is likely to exercise for this task."""
    task_yaml = load_yaml(task_dir(task_id) / "task.yaml") or {}
    repositories = task_yaml.get("repositories") if isinstance(task_yaml, dict) else []
    majors: set[int] = set()
    sdk_majors: set[int] = set()
    for repository in repositories if isinstance(repositories, list) else []:
        if not isinstance(repository, dict):
            continue
        repo_relative = str(repository.get("path") or "").strip("/")
        if not repo_relative:
            continue
        repo_root = workspace / repo_relative
        global_json = repo_root / "global.json"
        if global_json.is_file():
            try:
                sdk_version = str(json.loads(global_json.read_text(encoding="utf-8"))["sdk"]["version"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
                sdk_version = ""
            match = re.match(r"(\d+)\.", sdk_version)
            if match:
                sdk_major = int(match.group(1))
                sdk_majors.add(sdk_major)
                majors.add(sdk_major)
        for project in repo_root.rglob("*.*proj"):
            try:
                project_text = project.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            majors.update(int(value) for value in re.findall(r"\bnet(\d+)\.\d+\b", project_text))
    if sdk_majors:
        selected_sdk_major = max(sdk_majors)
        majors = {major for major in majors if major <= selected_sdk_major}
        majors.update(sdk_majors)
    if majors:
        highest = max(majors)
        if highest > 6:
            majors.add(highest - 1)
    return sorted(major for major in majors if 6 <= major <= 20)


def prewarm_agent_dotnet_frameworks(
    task_id: str,
    run_dir: Path,
    sandbox_image: str,
    sandbox_mounts: list[str],
    sandbox_env: list[str],
) -> dict[str, Any]:
    """Populate SDK-selected .NET targeting packs before the offline agent starts."""
    workspace = agent_workspace_path(run_dir)
    if workspace is None or not workspace.exists():
        return {"enabled": False}
    workspace = workspace.resolve()
    majors = task_dotnet_target_framework_majors(task_id, workspace)
    if not majors:
        return {"enabled": False}

    packages_container = env_assignment(sandbox_env, "NUGET_PACKAGES")
    if not packages_container:
        return {"enabled": False, "reason": "no-nuget-packages-cache"}
    matched = writable_mount_for_container_path(sandbox_mounts, packages_container.rstrip("/"))
    if matched is None:
        return {"enabled": False, "reason": "nuget-packages-cache-not-mounted"}
    packages_host_root, mount_container = matched
    packages_host = Path(packages_host_root) / Path(packages_container).relative_to(mount_container)
    packages_host.mkdir(parents=True, exist_ok=True)

    fingerprint = hashlib.sha256()
    fingerprint.update(b"agent-dotnet-frameworks-v2\0")
    fingerprint.update(sandbox_image.encode("utf-8"))
    fingerprint.update(",".join(map(str, majors)).encode("utf-8"))
    cache_root = task_cache_root(task_id)
    marker_dir = cache_root / ".agent-dotnet-prewarm" if cache_root is not None else None
    marker = marker_dir / f"{fingerprint.hexdigest()}.json" if marker_dir is not None else None
    if marker is not None and marker.exists():
        return {"enabled": True, "cache_hit": True, "target_framework_majors": majors}

    required_mount = f"{packages_host_root}:{mount_container}:rw"
    http_cache_container = env_assignment(sandbox_env, "NUGET_HTTP_CACHE_PATH")
    docker_env = [
        "--env",
        f"NUGET_PACKAGES={packages_container.rstrip('/')}",
        "--env",
        "NUGET_XMLDOC_MODE=skip",
        "--env",
        "NuGetAudit=false",
        "--env",
        "DOTNET_CLI_HOME=/tmp/ecosync-dotnet-home",
    ]
    if http_cache_container:
        docker_env.extend(["--env", f"NUGET_HTTP_CACHE_PATH={http_cache_container}"])

    requested_majors = " ".join(map(str, majors))
    script = f"""
set -euo pipefail
umask 0002
sdk_major=$(dotnet --version | sed -n 's/^\([0-9][0-9]*\)\..*/\\1/p')
[[ -n "$sdk_major" ]] || {{ echo 'Unable to determine installed .NET SDK major' >&2; exit 1; }}
requested_framework_majors=({requested_majors})
selected_tfms=()
for major in "${{requested_framework_majors[@]}}"; do
  if (( major <= sdk_major )); then
    selected_tfms+=("net${{major}}.0")
  fi
done
if (( ${{#selected_tfms[@]}} == 0 )); then
  exit 0
fi
tfms=$(IFS=';'; printf '%s' "${{selected_tfms[*]}}")
mkdir -p /tmp/ecosync-framework-probe
cat > /tmp/ecosync-framework-probe/framework-probe.csproj <<EOF
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFrameworks>${{tfms}}</TargetFrameworks>
    <RuntimeIdentifiers>linux-x64</RuntimeIdentifiers>
    <OutputType>Exe</OutputType>
  </PropertyGroup>
  <ItemGroup>
    <FrameworkReference Include="Microsoft.AspNetCore.App" />
  </ItemGroup>
</Project>
EOF
cd /tmp/ecosync-framework-probe
dotnet restore framework-probe.csproj --disable-parallel --verbosity minimal
for package in \
  microsoft.netcore.app.ref \
  microsoft.aspnetcore.app.ref \
  microsoft.netcore.app.host.linux-x64; do
  if [[ -d "$NUGET_PACKAGES/$package" ]]; then
    chmod -R g+rwX "$NUGET_PACKAGES/$package"
  fi
done
"""
    cmd = [
        "timeout",
        "--signal=TERM",
        "--kill-after=30s",
        "900s",
        "docker",
        "run",
        "--rm",
        "--network",
        "bridge",
        "--user",
        "0:0",
        "--env",
        "HOME=/tmp/ecosync-home",
        "--env",
        "HTTP_PROXY=",
        "--env",
        "HTTPS_PROXY=",
        "--env",
        "ALL_PROXY=",
        *docker_env,
        "--volume",
        required_mount,
    ]
    for mount in sandbox_mounts:
        if http_cache_container and writable_mount_for_container_path([mount], http_cache_container):
            if mount != required_mount:
                cmd.extend(["--volume", mount])
            break
    cmd.extend(["--entrypoint", "/bin/bash", sandbox_image, "-lc", script])
    code, output = run_capture(cmd)
    if code == 0 and marker is not None:
        marker_dir.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "created_at": utc_now(),
                    "target_framework_majors": majors,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return {
        "enabled": True,
        "cache_hit": False,
        "ok": code == 0,
        "exit_code": code,
        "target_framework_majors": majors,
        "output_tail": output[-4000:],
    }


def shared_node_tool_cache_root() -> Path:
    return Path(
        os.environ.get(
            "ECOSYNC_SHARED_NODE_TOOL_CACHE",
            str(Path.home() / ".cache" / "wideswe" / "shared-node-tool-cache"),
        )
    )


def shared_uv_python_cache_root() -> Path:
    return Path(
        os.environ.get(
            "ECOSYNC_SHARED_UV_PYTHON_CACHE",
            str(Path.home() / ".cache" / "wideswe" / "shared-uv-python"),
        )
    )


def shared_composer_cache_root() -> Path:
    return Path(
        os.environ.get(
            "ECOSYNC_SHARED_COMPOSER_CACHE",
            str(Path.home() / ".cache" / "wideswe" / "shared-composer-cache"),
        )
    )


def baked_rust_toolchain_env() -> list[str]:
    return [
        "RUSTUP_HOME=/usr/local/rustup",
        "RUSTUP_TOOLCHAIN=stable-x86_64-unknown-linux-gnu",
    ]


def add_shared_uv_python_cache(
    mounts: list[str],
    env: list[str],
    metadata: dict[str, Any],
) -> None:
    if not any(item.startswith("UV_CACHE_DIR=") for item in env):
        return
    root = shared_uv_python_cache_root()
    root.mkdir(parents=True, exist_ok=True)
    container_root = "/ecosync-shared-uv-python"
    mounts.append(f"{root}:{container_root}:rw")
    env[:] = [item for item in env if not item.startswith("UV_PYTHON_INSTALL_DIR=")]
    env.append(f"UV_PYTHON_INSTALL_DIR={container_root}")
    metadata.setdefault("cache_mounts", []).append(
        {
            "kind": "shared-uv-python-cache",
            "host": str(root),
            "container": container_root,
            "mode": "rw",
        }
    )


def task_runner_volume_mounts(task_id: str) -> list[tuple[Path | str, str]]:
    """Best-effort extraction of host cache mounts used by matrix runners."""
    run_profile = task_dir(task_id) / "environment" / "run_profile.sh"
    if not run_profile.exists():
        return []
    text = run_profile.read_text(encoding="utf-8", errors="replace")
    variables: dict[str, str] = {}
    active_cache_root = task_cache_root(task_id)
    assignment_pattern = re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)=["\']([^"\']+)["\']')
    for match in assignment_pattern.finditer(text):
        name = match.group(1)
        value = match.group(2)
        if "ECOSYNC_CACHE_ROOT" in value and active_cache_root is not None:
            active_root = str(active_cache_root)
            value = re.sub(
                r"\$\{ECOSYNC_CACHE_ROOT:-[^}]*\}",
                active_root,
                value,
            )
            value = value.replace("${ECOSYNC_CACHE_ROOT}", active_root)
            value = value.replace("$ECOSYNC_CACHE_ROOT", active_root)
        value = re.sub(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-([^}]+)\}", r"\1", value)
        if value.startswith("/"):
            variables[name] = value
            continue
        resolved = value
        for var_name, var_value in variables.items():
            resolved = resolved.replace(f"${var_name}", var_value)
            resolved = resolved.replace(f"${{{var_name}}}", var_value)
        variables[name] = resolved
    mounts: list[tuple[Path | str, str]] = []
    seen: set[tuple[Path | str, str]] = set()

    def docker_volume_exists(volume_name: str) -> bool:
        if not volume_name or volume_name.startswith(("/", "$")):
            return False
        if any(char in volume_name for char in (" ", "\t", "\n", "`", "\\")):
            return False
        proc = subprocess.run(
            ["docker", "volume", "inspect", volume_name, "--format", "{{.Name}}"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
        return proc.returncode == 0 and proc.stdout.strip() == volume_name

    def add_mount(host_raw: str, container: str) -> None:
        if not container.startswith("/"):
            return
        is_cargo_target = Path(host_raw).name == "cargo-target" and container.rstrip("/").endswith("/target")
        if not is_cargo_target and not any(
            token in container
            for token in (
                "cache",
                "python-cache",
                "python-env",
                "composer",
                "yarn",
                "pnpm",
                "npm",
                "gradle",
                "m2",
                "cargo",
                "go-cache",
                "pkg/mod",
                "uap-core",
                "sentry-conventions",
                "gomod",
                "node-cache",
                "vendor",
                "3rdparty",
            )
        ):
            return
        host = Path(host_raw)
        if not path_exists(host):
            if not docker_volume_exists(host_raw):
                return
            item = (host_raw, container)
            if item not in seen:
                mounts.append(item)
                seen.add(item)
            return
        item = (host, container)
        if item not in seen:
            mounts.append(item)
            seen.add(item)
    patterns = [
        r'-v\s+"([^"$`\\][^"]*):([^":\s]+)(?::[A-Za-z]+)?"',
        r"-v\s+'([^'$`\\][^']*):([^':\s]+)(?::[A-Za-z]+)?'",
        r"-v\s+(/[^:\s\\]+):([^:\s\\]+)(?::[A-Za-z]+)?",
        r"-v\s+([A-Za-z0-9][A-Za-z0-9_.-]*):([^:\s\\]+)(?::[A-Za-z]+)?",
    ]
    variable_patterns = [
        r'-v\s+"\$([A-Za-z_][A-Za-z0-9_]*)([^":\s]*):([^":\s]+)(?::[A-Za-z]+)?"',
        r"-v\s+'\$([A-Za-z_][A-Za-z0-9_]*)([^':\s]*):([^':\s]+)(?::[A-Za-z]+)?'",
        r"-v\s+\$([A-Za-z_][A-Za-z0-9_]*)([^:\s\\]*):([^:\s\\]+)(?::[A-Za-z]+)?",
    ]
    variable_container_patterns = [
        r'-v\s+"\$([A-Za-z_][A-Za-z0-9_]*)([^":\s]*):\$([A-Za-z_][A-Za-z0-9_]*)([^":\s]*)(?::[A-Za-z]+)?"',
        r"-v\s+'\$([A-Za-z_][A-Za-z0-9_]*)([^':\s]*):\$([A-Za-z_][A-Za-z0-9_]*)([^':\s]*)(?::[A-Za-z]+)?'",
        r"-v\s+\$([A-Za-z_][A-Za-z0-9_]*)([^:\s\\]*):\$([A-Za-z_][A-Za-z0-9_]*)([^:\s\\]*)(?::[A-Za-z]+)?",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            add_mount(match.group(1), match.group(2))
    for pattern in variable_patterns:
        for match in re.finditer(pattern, text):
            host_value = variables.get(match.group(1))
            host_suffix = match.group(2)
            container = match.group(3)
            if not host_value:
                continue
            for var_name, var_value in variables.items():
                host_suffix = host_suffix.replace(f"${var_name}", var_value)
                host_suffix = host_suffix.replace(f"${{{var_name}}}", var_value)
            add_mount(f"{host_value}{host_suffix}", container)
    for pattern in variable_container_patterns:
        for match in re.finditer(pattern, text):
            host_value = variables.get(match.group(1))
            container_value = variables.get(match.group(3))
            if not host_value or not container_value:
                continue
            add_mount(
                f"{host_value}{match.group(2)}",
                f"{container_value}{match.group(4)}",
            )

    # Release runners commonly derive cache_root from nested environment
    # defaults, while the active evaluation overrides it via
    # ECOSYNC_CACHE_BASE_ROOT. Resolve that mount through task_cache_root()
    # instead of retaining the stale literal default from the runner script.
    if re.search(
        r'-v\s+["\']?\$\{?cache_root\}?[^:\s"\']*:/cache(?=[:\s"\'])',
        text,
    ):
        resolved_cache_root = task_cache_root(task_id)
        if resolved_cache_root is not None:
            add_mount(str(resolved_cache_root), "/cache")

    # Nextcloud's shared runner passes repo-specific npm cache paths through a
    # shell function parameter, so the generic static parser cannot recover
    # them from `-v "$cache_dir:/npm-cache"`. Mount the common parent once and
    # let the synthesized public test command select the current repo cache.
    nextcloud_npm_cache = Path(
        os.environ.get(
            "ECOSYNC_NEXTCLOUD_NPM_CACHE",
            str(Path.home() / ".cache" / "wideswe" / "npm" / "nextcloud"),
        )
    )
    if "npm-cache/nextcloud" in text and path_exists(nextcloud_npm_cache):
        add_mount(str(nextcloud_npm_cache), "/nextcloud-npm-cache")

    if "repos/getsentry/relay" in text:
        relay_submodule_caches = (
            (
                "ECOSYNC_RELAY_UAP_CORE_CACHE",
                "/workspace/repos/getsentry/relay/relay-ua/uap-core",
            ),
            (
                "ECOSYNC_RELAY_SENTRY_CONVENTIONS_CACHE",
                "/workspace/repos/getsentry/relay/relay-conventions/sentry-conventions",
            ),
        )
        for env_name, container in relay_submodule_caches:
            host = os.environ.get(env_name, "").strip()
            if host and path_exists(Path(host)):
                add_mount(host, container)

    # Fresh agent workspaces do not contain server/3rdparty. Matrix runners
    # warm this cache on the host, and public debugging must restore it before
    # attempting any network-backed submodule operation.
    nextcloud_php_cache = Path(
        os.environ.get(
            "ECOSYNC_NEXTCLOUD_PHP_CACHE",
            str(Path.home() / ".cache" / "wideswe" / "php" / "nextcloud"),
        )
    )
    if "repos/nextcloud/server" in text and path_exists(nextcloud_php_cache):
        add_mount(str(nextcloud_php_cache), "/php-cache")
        nextcloud_3rdparty = nextcloud_php_cache / "3rdparty"
        if path_exists(nextcloud_3rdparty):
            add_mount(
                str(nextcloud_3rdparty),
                "/workspace/repos/nextcloud/server/3rdparty",
            )
    for app in ("mail", "calendar"):
        if f"repos/nextcloud/{app}" not in text:
            continue
        app_cache = nextcloud_php_cache / app
        for dependency_dir in ("vendor", "vendor-bin"):
            host = app_cache / dependency_dir
            if path_exists(host):
                add_mount(
                    str(host),
                    f"/workspace/repos/nextcloud/server/apps/{app}/{dependency_dir}",
                )
    return mounts


def prepare_agent_maven_cache(source: Path) -> Path:
    """Create an Agent-writable Maven cache without remote-origin lock metadata."""
    target = source.with_name(f"{source.name}-agent")
    marker = target / ".ecosync-agent-maven-cache-v1"
    if marker.exists():
        return target
    target.mkdir(parents=True, exist_ok=True)
    if source.exists():
        shutil.copytree(source, target, copy_function=os.link, symlinks=True, dirs_exist_ok=True)
    for pattern in ("_remote.repositories", "*.lastUpdated"):
        for metadata in target.rglob(pattern):
            if metadata.is_file() or metadata.is_symlink():
                metadata.unlink()
    marker.write_text(utc_now() + "\n", encoding="utf-8")
    target.chmod(0o777)
    return target


def prepare_agent_writable_cargo_target(target: Path) -> Path:
    """Keep an evaluator-warmed Cargo target writable by the non-root Agent."""
    target.mkdir(parents=True, exist_ok=True)
    children = list(target.iterdir())
    if children and all(os.access(child, os.W_OK) for child in children):
        return target

    def add_owner_write(path: Path) -> None:
        path.chmod(path.stat().st_mode | 0o700)

    try:
        add_owner_write(target)
        for root, dirs, files in os.walk(target):
            root_path = Path(root)
            add_owner_write(root_path)
            for name in dirs:
                add_owner_write(root_path / name)
            for name in files:
                add_owner_write(root_path / name)
    except PermissionError:
        owner = f"{os.getuid()}:{os.getgid()}"
        for command in (
            ["sudo", "-n", "chown", "-R", owner, str(target)],
            ["sudo", "-n", "chmod", "-R", "u+rwX", str(target)],
        ):
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                raise PermissionError(
                    f"failed to make Cargo target Agent-writable: {completed.stdout.strip()}"
                )
    return target


def agent_safe_runner_mounts(
    mounts: list[tuple[Path | str, str]],
) -> list[tuple[Path | str, str]]:
    """Keep evaluator-owned caches from becoming unwritable in the agent container."""
    safe_mounts: list[tuple[Path | str, str]] = []
    for host, container in mounts:
        host_path = Path(host)
        safe_host: Path | str = host
        normalized_host = host_path.name.lower().replace("_", "-")
        normalized_container = container.rstrip("/").lower().replace("_", "-")
        container_name = normalized_container.rsplit("/", 1)[-1]
        is_go_build_cache = (
            normalized_container == "/root/.cache/go-build"
            or "gocache" in normalized_host
            or "go-build" in normalized_host
            or "gocache" in container_name
            or "go-build" in container_name
            or (container_name == "build" and "go" in normalized_container)
        )
        is_cargo_target = (
            normalized_host == "cargo-target"
            and normalized_container.endswith("/target")
        )
        if is_cargo_target:
            safe_host = prepare_agent_writable_cargo_target(host_path)
        elif is_go_build_cache:
            # Hidden evaluators write their shared build cache as root. A
            # separate persistent agent cache stays writable and still becomes
            # hot across repeated model/agent configurations for this case.
            host_path = host_path.with_name(f"{host_path.name}-agent")
            host_path.mkdir(parents=True, exist_ok=True)
            host_path.chmod(0o777)
            safe_host = host_path
            if normalized_container == "/root/.cache/go-build":
                container = "/cache/go-build"
        elif normalized_container == "/m2":
            safe_host = prepare_agent_maven_cache(host_path)
        safe_mounts.append((safe_host, container))
    return safe_mounts


def split_mount_spec(mount: str) -> tuple[str, str, str]:
    parts = mount.rsplit(":", 2)
    if len(parts) == 3 and parts[2] in {"ro", "rw", "z", "Z"}:
        return parts[0], parts[1].rstrip("/") or "/", parts[2]
    parts = mount.rsplit(":", 1)
    if len(parts) == 2:
        return parts[0], parts[1].rstrip("/") or "/", "rw"
    return mount, "", "rw"


def dedupe_mount_specs(mounts: list[str]) -> list[str]:
    deduped: list[str] = []
    index_by_container: dict[str, int] = {}
    score_by_container: dict[str, int] = {}
    for mount in mounts:
        _host, container, mode = split_mount_spec(mount)
        if not container:
            if mount not in deduped:
                deduped.append(mount)
            continue
        score = 2 if mode == "rw" else 1
        existing = index_by_container.get(container)
        if existing is None:
            index_by_container[container] = len(deduped)
            score_by_container[container] = score
            deduped.append(mount)
            continue
        if score >= score_by_container.get(container, 0):
            deduped[existing] = mount
            score_by_container[container] = score
    return deduped


def extend_missing_env_assignments(target: list[str], additions: list[str]) -> list[str]:
    """Append derived environment assignments without overriding explicit values."""
    existing = {item.split("=", 1)[0] for item in target if "=" in item}
    for item in additions:
        if "=" not in item:
            continue
        key = item.split("=", 1)[0]
        if key in existing:
            continue
        target.append(item)
        existing.add(key)
    return target


def cache_env_for_explicit_mounts(mounts: list[str]) -> list[str]:
    env: list[str] = []
    for mount in mounts:
        host, container, mode = split_mount_spec(mount)
        if not host or not container:
            continue
        # Read-only mounts include agent runtimes, generated tool shims, and
        # evaluator metadata. Inspecting their filenames as cache layouts can
        # misclassify an executable such as the `uv` shim as a writable cache.
        if mode == "ro":
            continue
        extend_missing_env_assignments(
            env,
            cache_env_for_container_root(Path(host), container),
        )
    return env


def dedupe_cache_mount_metadata(cache_mounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    index_by_container: dict[str, int] = {}
    score_by_container: dict[str, int] = {}
    for item in cache_mounts:
        container = str(item.get("container") or "").rstrip("/") or "/"
        mode = str(item.get("mode") or "rw")
        score = 2 if mode == "rw" else 1
        existing = index_by_container.get(container)
        if existing is None:
            index_by_container[container] = len(deduped)
            score_by_container[container] = score
            deduped.append(item)
            continue
        if score >= score_by_container.get(container, 0):
            deduped[existing] = item
            score_by_container[container] = score
    return deduped


def task_compose_volume_mounts(task_id: str) -> list[tuple[Path, str]]:
    """Best-effort extraction of host cache mounts from task docker compose files."""
    compose_path = task_dir(task_id) / "environment" / "docker-compose.yml"
    if not compose_path.exists():
        return []
    try:
        compose = load_yaml(compose_path) or {}
    except Exception:
        return []
    services = compose.get("services") if isinstance(compose, dict) else None
    if not isinstance(services, dict):
        return []
    mounts: list[tuple[Path, str]] = []
    seen: set[tuple[Path, str]] = set()

    def add_mount(host_raw: Any, container_raw: Any) -> None:
        if not isinstance(host_raw, str) or not isinstance(container_raw, str):
            return
        if "$" in host_raw:
            return
        if not host_raw.startswith("/") or not container_raw.startswith("/"):
            return
        if not any(
            token in container_raw
            for token in (
                "cache",
                "python-cache",
                "composer",
                "yarn",
                "pnpm",
                "npm",
                "gradle",
                "m2",
                "cargo",
            )
        ):
            return
        host = Path(host_raw)
        if not host.exists():
            return
        item = (host, container_raw)
        if item not in seen:
            mounts.append(item)
            seen.add(item)

    for service in services.values():
        if not isinstance(service, dict):
            continue
        volumes = service.get("volumes") or []
        if not isinstance(volumes, list):
            continue
        for volume in volumes:
            if isinstance(volume, str):
                parts = volume.split(":")
                if len(parts) >= 2:
                    add_mount(parts[0], parts[1])
            elif isinstance(volume, dict):
                add_mount(volume.get("source"), volume.get("target"))
    return mounts


def cache_env_for_container_root(host_root: Path, container_root: str) -> list[str]:
    env: list[str] = []
    root = container_root.rstrip("/")
    normalized_host_name = re.sub(r"[-_]", "", host_root.name.lower())
    is_go_module_cache = (
        root.endswith(("/pkg/mod", "/go-cache/mod", "/go-cache/go-mod"))
        or normalized_host_name in {"gomod", "gomodcache"}
    )

    def exists(path: Path) -> bool:
        try:
            return path.exists()
        except PermissionError:
            return False

    # Some matrix runners mount a cache directory directly instead of mounting
    # a parent containing conventional cargo/uv subdirectories. Preserve those
    # runner paths when the same cache is reused by an agent environment.
    direct_cargo_home = (
        Path(root).name in {"cargo", "cargo-home"}
        and (exists(host_root / "git") or exists(host_root / "registry"))
    )
    if root == "/cargo-cache" or direct_cargo_home:
        env.extend([f"CARGO_HOME={root}", *baked_rust_toolchain_env()])
    if root == "/cargo-target":
        env.append(f"CARGO_TARGET_DIR={root}")
    if root == "/uv-cache":
        env.extend([f"UV_CACHE_DIR={root}", "UV_LINK_MODE=copy"])
    if root == "/ruby-cache" and exists(host_root / "bundle"):
        bundle_ruby_root = host_root / "bundle" / "ruby"
        ruby_api_versions: list[str] = []
        try:
            ruby_api_versions = sorted(
                child.name for child in bundle_ruby_root.iterdir() if child.is_dir()
            )
        except (FileNotFoundError, NotADirectoryError, OSError, PermissionError):
            pass
        bundle_root = f"{root}/bundle"
        gem_paths = [f"{root}/gems"] if exists(host_root / "gems") else []
        for ruby_api_version in ruby_api_versions:
            gem_paths.append(f"{bundle_root}/ruby/{ruby_api_version}")
        gem_paths.append("/usr/local/bundle")
        env.extend(
            [
                f"BUNDLE_PATH={bundle_root}",
                f"GEM_HOME={root}/gems",
                f"GEM_PATH={':'.join(gem_paths)}",
                "BUNDLE_DISABLE_LOCAL_BRANCH_CHECK=true",
                "BUNDLE_DISABLE_LOCAL_REVISION_CHECK=true",
            ]
        )

    if root == "/node-cache":
        yarn_cache = f"{root}/yarn/cache" if exists(host_root / "yarn" / "cache") else f"{root}/yarn"
        yarn_global = (
            f"{root}/yarn/global" if exists(host_root / "yarn" / "global") else f"{root}/yarn-berry"
        )
        env.extend(
            [
                f"NPM_CONFIG_CACHE={root}/npm",
                f"npm_config_cache={root}/npm",
                f"COREPACK_HOME={root}/corepack",
                f"YARN_CACHE_FOLDER={yarn_cache}",
                "YARN_ENABLE_GLOBAL_CACHE=true",
                f"YARN_GLOBAL_FOLDER={yarn_global}",
                f"XDG_CACHE_HOME={root}/xdg",
                f"XDG_DATA_HOME={root}/xdg",
            ]
        )
        if exists(host_root / "pnpm"):
            env.extend(
                [
                    f"PNPM_HOME={root}/pnpm/home",
                    f"PNPM_STORE_DIR={root}/pnpm/store",
                    f"pnpm_config_store_dir={root}/pnpm/store",
                    f"npm_config_store_dir={root}/pnpm/store",
                    f"NPM_CONFIG_STORE_DIR={root}/pnpm/store",
                ]
            )

    if "nuget" in root.lower() or "nuget" in host_root.name.lower():
        env.extend(
            [
                f"NUGET_PACKAGES={root}/",
                f"NUGET_HTTP_CACHE_PATH={root}/.http-cache",
            ]
        )
    if is_go_module_cache:
        if root.endswith("/pkg/mod"):
            gopath = str(Path(root).parent.parent)
        elif root.endswith(("/go-cache/mod", "/go-cache/go-mod")):
            gopath = str(Path(root).parent / "gopath")
        else:
            gopath = f"{root}/gopath"
        env.extend(
            [
                f"GOMODCACHE={root}",
                f"GOPATH={gopath}",
                f"ECOSYNC_CASE_GOMODCACHE={root}",
                f"ECOSYNC_CASE_GOPATH={gopath}",
            ]
        )
        if exists(host_root / "cache" / "download"):
            env.extend(
                [
                    f"GOPROXY=file://{root}/cache/download",
                    "GOSUMDB=off",
                ]
            )
    if "go-build" in root.lower() or normalized_host_name in {"gobuild", "gocache"}:
        env.extend([f"GOCACHE={root}", f"ECOSYNC_CASE_GOCACHE={root}"])
    if "gradle" in root.lower() or "gradle" in host_root.name.lower():
        env.append(f"GRADLE_USER_HOME={root}")
    has_generic_go_module_cache = (
        normalized_host_name == "gocache"
        or exists(host_root / "go-mod")
        or exists(host_root / "gomodcache")
        or (
            exists(host_root / "mod")
            and (
                exists(host_root / "build")
                or exists(host_root / "go-build")
                or exists(host_root / "gocache")
            )
        )
    )
    if not is_go_module_cache and has_generic_go_module_cache:
        if exists(host_root / "gomodcache"):
            gomod = "gomodcache"
        else:
            gomod = "go-mod" if exists(host_root / "go-mod") else "mod"
        if exists(host_root / "gocache"):
            gocache = "gocache"
        else:
            gocache = "go-build" if exists(host_root / "go-build") else "build"
        env.extend(
            [
                f"GOMODCACHE={root}/{gomod}",
                f"GOCACHE={root}/{gocache}",
                f"GOPATH={root}/gopath",
                f"GOBIN={root}/go-bin",
                f"ECOSYNC_CASE_GOMODCACHE={root}/{gomod}",
                f"ECOSYNC_CASE_GOCACHE={root}/{gocache}",
                f"ECOSYNC_CASE_GOPATH={root}/gopath",
            ]
        )
        if exists(host_root / gomod / "cache" / "download"):
            env.extend(
                [
                    f"GOPROXY=file://{root}/{gomod}/cache/download",
                    "GOSUMDB=off",
                ]
            )
    if exists(host_root / "pnpm-store"):
        env.extend(
            [
                f"PNPM_STORE_DIR={root}/pnpm-store",
                f"pnpm_config_store_dir={root}/pnpm-store",
                f"npm_config_store_dir={root}/pnpm-store",
                f"NPM_CONFIG_STORE_DIR={root}/pnpm-store",
            ]
        )
    if exists(host_root / "node" / "corepack"):
        env.append(f"COREPACK_HOME={root}/node/corepack")
    if exists(host_root / "node" / "yarn"):
        env.append(f"YARN_CACHE_FOLDER={root}/node/yarn")
    if exists(host_root / "node" / "yarn-berry"):
        env.extend(["YARN_ENABLE_GLOBAL_CACHE=true", f"YARN_GLOBAL_FOLDER={root}/node/yarn-berry"])
    if exists(host_root / "node" / "npm"):
        env.extend(
            [
                f"NPM_CONFIG_CACHE={root}/node/npm",
                f"npm_config_cache={root}/node/npm",
            ]
        )
    if root != "/node-cache" and (
        exists(host_root / "corepack")
        or "yarn" in root.lower()
        or "yarn" in host_root.name.lower()
    ):
        env.append(f"COREPACK_HOME={root}/corepack")
    if root != "/node-cache" and exists(host_root / "yarn"):
        yarn_root = f"{root}/yarn"
        if exists(host_root / "yarn" / "cache") or exists(host_root / "yarn" / "index"):
            env.extend(
                [
                    "YARN_ENABLE_GLOBAL_CACHE=true",
                    f"YARN_GLOBAL_FOLDER={yarn_root}",
                    f"YARN_CACHE_FOLDER={yarn_root}/cache",
                ]
            )
        else:
            env.append(f"YARN_CACHE_FOLDER={yarn_root}")
    if root != "/node-cache" and exists(host_root / "yarn-berry"):
        env.extend(["YARN_ENABLE_GLOBAL_CACHE=true", f"YARN_GLOBAL_FOLDER={root}/yarn-berry"])
    if root != "/node-cache" and exists(host_root / "npm"):
        env.extend(
            [
                f"NPM_CONFIG_CACHE={root}/npm",
                f"npm_config_cache={root}/npm",
            ]
        )
    if exists(host_root / "kbn-bootstrap"):
        env.append(f"KBN_BOOTSTRAP_CACHE_DIR={root}/kbn-bootstrap")
    if exists(host_root / "xdg"):
        env.extend(
            [
                f"XDG_CACHE_HOME={root}/xdg",
                f"XDG_DATA_HOME={root}/xdg",
            ]
        )
    if exists(host_root / "global"):
        env.extend(
            [
                "YARN_ENABLE_GLOBAL_CACHE=true",
                f"YARN_GLOBAL_FOLDER={root}/global",
            ]
        )
        if exists(host_root / "global" / "cache"):
            env.append(f"YARN_CACHE_FOLDER={root}/global/cache")
    if exists(host_root / "cache") and not is_go_module_cache:
        env.append(f"YARN_CACHE_FOLDER={root}/cache")
    if exists(host_root / "composer" / "home"):
        env.append(f"COMPOSER_HOME={root}/composer/home")
    if exists(host_root / "composer" / "cache"):
        env.append(f"COMPOSER_CACHE_DIR={root}/composer/cache")
    if exists(host_root / "composer"):
        env.extend(["COMPOSER_ALLOW_SUPERUSER=1", "COMPOSER_NO_INTERACTION=1"])
    if "composer-cache" in root.lower():
        composer_root = str(Path(root).parent) if root.endswith("/cache") else root
        env.extend(
            [
                f"COMPOSER_HOME={composer_root}/home",
                f"COMPOSER_CACHE_DIR={composer_root}/cache",
                "COMPOSER_ALLOW_SUPERUSER=1",
                "COMPOSER_NO_INTERACTION=1",
            ]
        )
    if exists(host_root / "dotnet-test"):
        env.append(f"ECOSYNC_DOTNET_TEST_ROOT={root}/dotnet-test")
    if exists(host_root / "nuget"):
        env.extend(
            [
                f"NUGET_PACKAGES={root}/nuget/",
                f"NUGET_HTTP_CACHE_PATH={root}/nuget-http",
            ]
        )
    if exists(host_root / "cargo"):
        env.append(f"CARGO_HOME={root}/cargo")
    elif exists(host_root / "cargo-home"):
        env.append(f"CARGO_HOME={root}/cargo-home")
    if exists(host_root / "rust-target"):
        env.append(f"CARGO_TARGET_DIR={root}/rust-target")
    elif exists(host_root / "cargo") and exists(host_root / "target"):
        env.append(f"CARGO_TARGET_DIR={root}/target")
    if exists(host_root / "pip"):
        env.append(f"PIP_CACHE_DIR={root}/pip")
    if exists(host_root / "home" / ".elastic-package"):
        env.append(f"ELASTIC_PACKAGE_DATA_HOME={root}/home/.elastic-package")
    if exists(host_root / "pub-cache"):
        env.append(f"PUB_CACHE={root}/pub-cache")
    if exists(host_root / "uv") or root.rstrip("/") == "/python-cache":
        env.extend(
            [
                f"UV_CACHE_DIR={root}/uv",
                f"UV_PYTHON_INSTALL_DIR={root}/uv-python",
                "UV_LINK_MODE=copy",
            ]
        )
    return env


def enforce_offline_package_manager_env(sandbox_network: str, sandbox_env: list[str]) -> list[str]:
    if sandbox_network not in {"none", "llm-api-only"}:
        return sandbox_env

    keys = {item.split("=", 1)[0] for item in sandbox_env if "=" in item}
    if "ECOSYNC_PACKAGE_NETWORK_DISABLED" not in keys:
        sandbox_env.append("ECOSYNC_PACKAGE_NETWORK_DISABLED=1")
    if "CARGO_HOME" in keys and "CARGO_NET_OFFLINE" not in keys:
        sandbox_env.append("CARGO_NET_OFFLINE=true")
    if "UV_CACHE_DIR" in keys and "UV_OFFLINE" not in keys:
        sandbox_env.append("UV_OFFLINE=1")
    if "COREPACK_HOME" in keys and "COREPACK_DEFAULT_TO_LATEST" not in keys:
        sandbox_env.append("COREPACK_DEFAULT_TO_LATEST=0")
    if {"YARN_CACHE_FOLDER", "YARN_GLOBAL_FOLDER"} & keys and "YARN_ENABLE_NETWORK" not in keys:
        sandbox_env.append("YARN_ENABLE_NETWORK=false")
    if {"NPM_CONFIG_CACHE", "npm_config_cache"} & keys and "npm_config_offline" not in keys:
        sandbox_env.append("npm_config_offline=true")
    if {"PNPM_HOME", "PNPM_STORE_DIR", "pnpm_config_store_dir", "NPM_CONFIG_STORE_DIR"} & keys:
        if "PNPM_CONFIG_OFFLINE" not in keys:
            sandbox_env.append("PNPM_CONFIG_OFFLINE=true")
        if "pnpm_config_offline" not in keys:
            sandbox_env.append("pnpm_config_offline=true")
    if "NUGET_PACKAGES" in keys and "NuGetAudit" not in keys:
        sandbox_env.append("NuGetAudit=false")
    if {"COMPOSER_HOME", "COMPOSER_CACHE_DIR"} & keys and "COMPOSER_DISABLE_NETWORK" not in keys:
        sandbox_env.append("COMPOSER_DISABLE_NETWORK=1")
    return sandbox_env


def validate_offline_agent_command(
    agent_id: str, sandbox_network: str, command: str | None
) -> None:
    """Prevent provider-side web tools from bypassing an offline sandbox."""
    if (
        agent_id != "claude-code"
        or sandbox_network not in {"none", "llm-api-only"}
        or not command
    ):
        return

    matches = re.findall(
        r"--disallowedTools(?:=|\s+)[\"']?([^\s\"';|]+)", command
    )
    disabled = {
        tool.strip()
        for match in matches
        for tool in match.split(",")
        if tool.strip()
    }
    missing = {"WebFetch", "WebSearch"} - disabled
    if missing:
        raise SystemExit(
            "offline Claude Code runs must disable provider-side web tools; "
            f"add {','.join(sorted(missing))} to --disallowedTools"
        )


def write_text_with_privilege(path: Path, contents: str) -> bool:
    try:
        path.write_text(contents, encoding="utf-8")
        return True
    except (OSError, PermissionError):
        completed = subprocess.run(
            ["sudo", "-n", "tee", str(path)],
            input=contents,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return completed.returncode == 0


def touch_with_privilege(path: Path) -> bool:
    try:
        path.touch()
        return True
    except (OSError, PermissionError):
        completed = subprocess.run(
            ["sudo", "-n", "touch", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return completed.returncode == 0


def refresh_offline_dotnet_cache_markers(task_id: str, sandbox_network: str) -> list[Path]:
    """Keep complete cached SDK installs from rerunning acquisition targets offline."""
    if sandbox_network not in {"none", "llm-api-only"}:
        return []

    roots: list[Path] = []
    cache_root = task_cache_root(task_id)
    if cache_root is not None:
        roots.append(cache_root)
    roots.extend(
        host
        for host, _container in [*task_runner_volume_mounts(task_id), *task_compose_volume_mounts(task_id)]
        if isinstance(host, Path)
    )

    refreshed: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        for http_cache in (root / "nuget-http", root / ".http-cache"):
            if not path_is_dir(http_cache):
                continue
            try:
                for marker in http_cache.rglob("*.dat"):
                    if touch_with_privilege(marker):
                        refreshed.append(marker)
            except (FileNotFoundError, NotADirectoryError, OSError, PermissionError):
                pass
        candidates = [root] if root.name == "dotnet-test" else [root / "dotnet-test"]
        for dotnet_root in candidates:
            try:
                dotnet_root = dotnet_root.resolve()
                if dotnet_root in seen:
                    continue
                seen.add(dotnet_root)
                install_script = dotnet_root / "dotnet-install.sh"
                timestamp = dotnet_root / ".dotnet-install.timestamp"
                sdk_root = dotnet_root / "sdk"
                if not (
                    (dotnet_root / "dotnet").is_file()
                    and install_script.is_file()
                    and sdk_root.is_dir()
                    and any(sdk_root.iterdir())
                ):
                    continue
                if write_text_with_privilege(timestamp, f"{int(time.time())}\n"):
                    refreshed.append(timestamp)
                runtime_manifest = dotnet_root / "Debugger.Tests.Versions.txt"
                if runtime_manifest.is_file() and touch_with_privilege(runtime_manifest):
                    refreshed.append(runtime_manifest)
            except (FileNotFoundError, NotADirectoryError, OSError, PermissionError):
                continue
    return refreshed


def path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except PermissionError:
        return False


def path_is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except PermissionError:
        return False


def corepack_cache_has_package_manager(root: Path | str, container: str = "") -> bool:
    if isinstance(root, str):
        try:
            result = subprocess.run(
                ["docker", "volume", "inspect", root, "--format", "{{.Mountpoint}}"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if result.returncode != 0 or not result.stdout.strip():
            return False
        root = Path(result.stdout.strip())

    candidates = [root / "corepack" / "v1"]
    if Path(container.rstrip("/")).name == "corepack":
        candidates.insert(0, root / "v1")
    for v1 in candidates:
        for manager in ("yarn", "pnpm", "npm"):
            manager_root = v1 / manager
            if not path_is_dir(manager_root):
                continue
            try:
                if any(
                    path_is_dir(version)
                    and path_exists(version / ".corepack")
                    and any(
                        path_exists(version / relative)
                        for relative in ("package.json", "yarn.js", "bin/yarn", "bin/pnpm", "bin/npm")
                    )
                    for version in manager_root.iterdir()
                ):
                    return True
            except (FileNotFoundError, NotADirectoryError, OSError, PermissionError):
                continue
    return False


def safe_iterdir(path: Path) -> list[Path]:
    try:
        return list(path.iterdir())
    except (FileNotFoundError, PermissionError, NotADirectoryError):
        return []


def safe_glob(path: Path, pattern: str) -> list[Path]:
    try:
        return list(path.glob(pattern))
    except PermissionError:
        return []


def profile_cache_venv(cache_root: Path, profile_name: str, profile: dict[str, Any]) -> Path:
    venvs_root = cache_root / "venvs"
    exact = venvs_root / profile_name
    if path_exists(exact):
        return exact
    candidates = [path for path in safe_iterdir(venvs_root) if path_is_dir(path)] if path_exists(venvs_root) else []
    repo = safe_slug(str(profile.get("repo") or "")).replace("_", "-")
    profile_slug = safe_slug(profile_name).replace("_", "-").removesuffix("-hidden")
    aliases = {
        "django-ansible-base": ["dab"],
    }
    needles = [profile_slug, repo, *aliases.get(profile_slug, []), *aliases.get(repo, [])]
    for needle in needles:
        if not needle:
            continue
        matches = [path for path in candidates if needle in path.name.replace("_", "-")]
        if len(matches) == 1:
            return matches[0]
        runnable = [path for path in matches if path_exists(path / "bin" / "pytest") or path_exists(path / "bin" / "python")]
        with_pytest = [path for path in runnable if path_exists(path / "bin" / "pytest")]
        if len(with_pytest) == 1:
            return with_pytest[0]
        if len(runnable) == 1:
            return runnable[0]
    return exact


def venv_has_runtime(path: Path) -> bool:
    """Return true for venvs that are runnable after mounting into the case image.

    Some venvs are created inside Docker and contain absolute symlinks such as
    bin/python -> /usr/local/bin/python. Those symlinks are broken on the host
    but valid inside the case image, so Path.exists() on bin/python is too
    strict here.
    """
    if not path_is_dir(path):
        return False
    return (
        path_exists(path / "pyvenv.cfg")
        or path_exists(path / "bin" / "activate")
        or (path / "bin" / "python").is_symlink()
        or path_exists(path / "bin" / "python")
        or path_exists(path / "bin" / "pytest")
    )


def venv_references_container_root(path: Path, container_root: str) -> bool:
    config = path / "pyvenv.cfg"
    if not path_exists(config) or not container_root.startswith("/"):
        return False
    try:
        return container_root.rstrip("/") + "/" in config.read_text(encoding="utf-8", errors="replace")
    except (OSError, PermissionError):
        return False


def find_profile_venv(cache_roots: list[Path], profile_name: str, profile: dict[str, Any]) -> Path | None:
    candidates: list[Path] = []

    def newest_venv(paths: list[Path]) -> Path | None:
        runnable = [path for path in paths if venv_has_runtime(path)]
        if not runnable:
            return None

        def cache_mtime(path: Path) -> float:
            marker = path / ".ecosync-dependencies-ready"
            try:
                return (marker if path_exists(marker) else path).stat().st_mtime
            except OSError:
                return 0.0

        return max(runnable, key=lambda path: (cache_mtime(path), str(path)))

    for cache_root in cache_roots:
        repo_slug = safe_slug(str(profile.get("repo") or "")).replace("_", "-")
        profile_slug = safe_slug(profile_name).replace("_", "-").removesuffix("-hidden")
        workdir_slug = safe_slug(Path(str(profile.get("workdir") or "")).name).replace("_", "-")
        needles = [value for value in (repo_slug, profile_slug, workdir_slug) if value]
        candidates.extend(
            [
                cache_root / "python" / "venv",
                cache_root / "py-cache" / "venv",
                cache_root / "venv",
                cache_root / ".venv",
            ]
        )
        for child in safe_iterdir(cache_root):
            if not path_is_dir(child):
                continue
            child_slug = safe_slug(child.name).replace("_", "-")
            if not any(child_slug in needle or needle in child_slug for needle in needles):
                continue
            candidates.extend([child / "venv", child / ".venv"])
            # Some runners key reusable environments by a dependency fingerprint,
            # for example django/venvs/<sha256>. Prefer the newest completed one.
            nested = newest_venv(safe_iterdir(child / "venvs"))
            if nested is not None:
                candidates.append(nested)
        for tox_root in [cache_root / "tox", *safe_glob(cache_root, "*/tox")]:
            if not path_exists(tox_root):
                continue
            for profile_root in safe_iterdir(tox_root):
                if not path_is_dir(profile_root):
                    continue
                profile_root_slug = safe_slug(profile_root.name).replace("_", "-").removesuffix("-hidden")
                if not any(profile_root_slug in needle or needle in profile_root_slug for needle in needles):
                    continue
                candidates.extend(path for path in safe_iterdir(profile_root) if path_is_dir(path))
        direct = profile_cache_venv(cache_root, profile_name, profile)
        if path_exists(direct):
            candidates.append(direct)
            candidates.extend(path for path in safe_iterdir(direct) if path_is_dir(path))
        for venvs_root in [cache_root / "venvs", *safe_glob(cache_root, "*/venvs")]:
            if not path_exists(venvs_root):
                continue
            exact = venvs_root / profile_name
            if path_exists(exact):
                candidates.append(exact)
                candidates.extend(path for path in safe_iterdir(exact) if path_is_dir(path))
            repo = safe_slug(str(profile.get("repo") or "")).replace("_", "-")
            profile_slug = safe_slug(profile_name).replace("_", "-").removesuffix("-hidden")
            aliases = {
                "django-ansible-base": ["dab"],
            }
            needles = [profile_slug, repo, *aliases.get(profile_slug, []), *aliases.get(repo, [])]
            for needle in needles:
                if not needle:
                    continue
                matches = [
                    path
                    for path in safe_iterdir(venvs_root)
                    if path_is_dir(path) and needle in path.name.replace("_", "-")
                ]
                candidates.extend(matches)
    runnable = [path for path in candidates if venv_has_runtime(path)]
    with_pytest = [path for path in runnable if path_exists(path / "bin" / "pytest")]
    if with_pytest:
        return sorted(set(with_pytest), key=lambda path: (len(str(path)), str(path)))[0]
    if runnable:
        return sorted(set(runnable), key=lambda path: (len(str(path)), str(path)))[0]
    return None


def task_debug_env_from_runner(task_id: str) -> list[str]:
    run_profile = task_dir(task_id) / "environment" / "run_profile.sh"
    if not run_profile.exists():
        return []
    text = run_profile.read_text(encoding="utf-8", errors="replace")
    env: list[str] = []
    safe_runner_env_names = {"RUSTUP_HOME", "RUSTUP_TOOLCHAIN"}

    def resolve_shell_value(value: str) -> str | None:
        value = value.strip().strip("'\"")
        default_match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-(.*)\}", value)
        if default_match:
            name, default = default_match.groups()
            return os.environ.get(name) or default
        unset_default_match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)-(.*)\}", value)
        if unset_default_match:
            name, default = unset_default_match.groups()
            return os.environ[name] if name in os.environ else default
        if "$" in value or "`" in value:
            return None
        return value

    def clean_env_assignment(item: str) -> str | None:
        cleaned = item.strip().strip("'\"")
        if "=" not in cleaned:
            return cleaned
        name, value = cleaned.split("=", 1)
        resolved = resolve_shell_value(value)
        return f"{name}={resolved}" if resolved is not None else None

    for match in re.finditer(r'-e\s+([A-Za-z_][A-Za-z0-9_]*=[^\s\\]+)', text):
        item = clean_env_assignment(match.group(1))
        if item is None:
            continue
        name = item.split("=", 1)[0]
        if name in safe_runner_env_names or name.startswith("SETUPTOOLS_SCM_PRETEND_VERSION"):
            env.append(item)
    assignment_names = (
        r"(?:RUSTUP_HOME|RUSTUP_TOOLCHAIN|"
        r"SETUPTOOLS_SCM_PRETEND_VERSION[A-Za-z0-9_]*)"
    )
    for match in re.finditer(
        rf'\b({assignment_names}=("[^"]*"|\'[^\']*\'|[^\s\\]+))',
        text,
    ):
        item = clean_env_assignment(match.group(1))
        if item is not None:
            env.append(item)
    if "-tags=untested_go_version" in text:
        env.append("GOFLAGS=-tags=untested_go_version")
    return sorted(set(env))


def task_profile_debug_envs(task_id: str, workspace_scope: str) -> dict[str, dict[str, str]]:
    """Extract safe, static per-profile environment from run_profile.sh.

    This is intentionally best-effort. It helps agent-visible debug commands
    inherit ordinary test settings such as DJANGO_SETTINGS_MODULE or DB_PORT
    without mounting hidden patches or oracle files into the agent workspace.
    Dynamic values such as command substitutions are skipped.
    """
    run_profile = task_dir(task_id) / "environment" / "run_profile.sh"
    if not run_profile.exists():
        return {}
    text = run_profile.read_text(encoding="utf-8", errors="replace")
    function_bodies: dict[str, str] = {}
    for match in re.finditer(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{\s*$", text, re.M):
        name = match.group(1)
        end = re.search(r"^}\s*$", text[match.end() :], re.M)
        if end:
            function_bodies[name] = text[match.end() : match.end() + end.start()]

    def profile_function(profile_name: str) -> str | None:
        pattern = re.compile(
            r"^\s*" + re.escape(profile_name) + r"\)\s*\n\s*([A-Za-z_][A-Za-z0-9_]*)\b",
            re.M,
        )
        match = pattern.search(text)
        return match.group(1) if match else None

    def profile_case_body(profile_name: str) -> str:
        pattern = re.compile(r"^\s*" + re.escape(profile_name) + r"\)\s*$", re.M)
        match = pattern.search(text)
        if not match:
            return ""
        end = re.search(r"^\s*;;\s*$", text[match.end() :], re.M)
        if not end:
            return ""
        return text[match.end() : match.end() + end.start()]

    allowed_names = {
        "ANSIBLE_GW_TEST_DB_HOST",
        "BUNDLE_ALLOW_OFFLINE_INSTALL",
        "BUNDLE_APP_CONFIG",
        "BUNDLE_PATH",
        "DB_HOST",
        "DB_PORT",
        "DATABASE_URL",
        "DJANGO_SETTINGS_MODULE",
        "ECOSYNC_CASE_GOCACHE",
        "ECOSYNC_CASE_GOMODCACHE",
        "ECOSYNC_CASE_GOPATH",
        "ECOSYNC_RUBY_OFFLINE",
        "ECOSYNC_YARN_VERSION",
        "GATEWAY_SECRET_KEY_FILE",
        "GEM_HOME",
        "GEM_PATH",
        "GOCACHE",
        "GOMODCACHE",
        "GOPATH",
        "GOPROXY",
        "GOSUMDB",
        "MONGODB_URI",
        "MYSQL_URL",
        "POSTGRES_URL",
        "PYTHONPATH",
        "REDIS_URL",
        "TEST_DATABASE_URL",
        "TESTAPP_MODE",
        "TEST_MYSQL_URI_MIGRATE",
        "TEST_MYSQL_SHADOWDB_URI_MIGRATE",
        "TEST_POSTGRES_URI_MIGRATE",
        "TEST_POSTGRES_SHADOWDB_URI_MIGRATE",
    }

    def resolve_shell_value(value: str) -> str | None:
        def replace_default(match: re.Match[str]) -> str:
            name, operator, default = match.groups()
            if operator == ":-":
                return os.environ.get(name) or default
            return os.environ[name] if name in os.environ else default

        resolved = re.sub(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-|-)([^{}]*)\}",
            replace_default,
            value,
        )
        if "$" in resolved or "`" in resolved:
            return None
        return resolved

    def safe_value(value: str) -> bool:
        if not value:
            return True
        return not any(token in value for token in ("`", "$(", "\n", "\r"))

    def collect_env(body: str) -> dict[str, str]:
        env: dict[str, str] = {}
        for match in re.finditer(
            r"(?:^|[ \t])(?:export[ \t]+)?"
            r"([A-Za-z_][A-Za-z0-9_]*)=(\"[^\"]*\"|'[^']*'|[^\s#;]+)",
            body,
            re.M,
        ):
            name = match.group(1)
            raw_value = match.group(2).strip()
            value = raw_value.strip("\"'")
            if name in allowed_names or name.startswith("SETUPTOOLS_SCM_PRETEND_VERSION"):
                resolved = resolve_shell_value(value)
                if resolved is not None and safe_value(resolved):
                    if name == "DJANGO_SETTINGS_MODULE" and resolved.startswith("ecosync_"):
                        continue
                    env[name] = resolved
        return env

    result: dict[str, dict[str, str]] = {}
    profiles_path = task_dir(task_id) / "environment" / "test_profiles.yaml"
    profiles_yaml = load_yaml(profiles_path) if profiles_path.exists() else {}
    profiles = profiles_yaml.get("profiles") if isinstance(profiles_yaml, dict) else {}
    if not isinstance(profiles, dict):
        return {}
    task_yaml = load_yaml(task_dir(task_id) / "task.yaml") or {}
    repositories = task_yaml.get("repositories") if isinstance(task_yaml, dict) else []
    repositories = repositories if isinstance(repositories, list) else []
    for profile_name, profile in profiles.items():
        fn_name = profile_function(str(profile_name))
        body = function_bodies.get(fn_name or "", "")
        profile_commands = ""
        if isinstance(profile, dict):
            profile_commands = "\n".join(
                str(profile.get(name) or "")
                for name in ("command", "public_command", "public_smoke_command")
            )
        profile_body = "\n".join(
            [body, profile_case_body(str(profile_name)), profile_commands]
        )
        env = collect_env(profile_body)
        editable_python_paths: list[str] = []
        for line in profile_body.splitlines():
            if re.search(r"\b(?:pip(?:3)?|python(?:3)?\s+-m\s+pip)\s+install\b", line) is None:
                continue
            editable_match = re.search(r"(?:^|\s)-e\s+['\"]?(/[^\s'\"]+)", line)
            if editable_match:
                editable_python_paths.append(editable_match.group(1))
        if editable_python_paths:
            existing_python_paths = env.get("PYTHONPATH", "").split(":")
            env["PYTHONPATH"] = ":".join(
                dict.fromkeys(
                    [
                        *editable_python_paths,
                        *(path for path in existing_python_paths if path),
                    ]
                )
            )
        if workspace_scope == "ecosystem" and isinstance(profile, dict):
            profile_text = "\n".join(
                [
                    profile_body,
                ]
            )
            python_paths: list[str] = []
            current_repo = str(profile.get("repo") or "")
            for repository in repositories:
                if not isinstance(repository, dict):
                    continue
                name = str(repository.get("name") or "")
                path = str(repository.get("path") or "").strip("/")
                if not name or not path or name == current_repo:
                    continue
                if re.search(r"(?<![A-Za-z0-9_.-])" + re.escape(name) + r"(?![A-Za-z0-9_.-])", profile_text):
                    python_paths.append(f"/workspace/{path}")
            if python_paths:
                env["PYTHONPATH"] = ":".join(
                    dict.fromkeys(
                        [
                            *python_paths,
                            *(path for path in env.get("PYTHONPATH", "").split(":") if path),
                        ]
                    )
                )
        if env:
            result[str(profile_name)] = env
    return result


def write_case_debug_shims(shim_dir: Path) -> None:
    shim_dir.mkdir(parents=True, exist_ok=True)
    script = shim_dir / "ecosync-tool-shim"
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'tool="$(basename "$0")"',
                'workspace="${ECOSYNC_WORKSPACE:-/workspace}"',
                'repo_env_files="${ECOSYNC_REPO_ENV_FILES:-/ecosync-debug-deps/repo-env-files.tsv}"',
                'repo_image_files="${ECOSYNC_REPO_IMAGE_FILES:-/opt/ecosync/repo-envs.tsv}"',
                'cwd="${PWD:-$(pwd -L)}"',
                'matched_root=""',
                'matched_rel=""',
                'matched_env_file=""',
                'matched_profile=""',
                'ambiguous_repo_args=0',
                'route_from_args=0',
                'cwd_matched=0',
                'cwd_in_workspace_repo=0',
                'case "$cwd" in "$workspace"/repos/*/*) cwd_in_workspace_repo=1 ;; esac',
                'if [[ -r "$repo_env_files" ]]; then',
                "  while IFS=$'\\t' read -r rel root env_file cache_route profile; do",
                '    [[ -n "${rel:-}" ]] || continue',
                '    [[ "${cache_route:-}" == "-" ]] && cache_route=""',
                '    [[ "${profile:-}" == "-" ]] && profile=""',
                '    repo_abs="$workspace/$rel"',
                '    case "$cwd" in',
                '      "$repo_abs"|"$repo_abs"/*)',
                '        matched_root="$root"',
                '        matched_rel="$rel"',
                '        matched_env_file="${env_file:-}"',
                '        matched_profile="${profile:-}"',
                '        cwd_matched=1',
                "        break",
                "        ;;",
                "    esac",
                '    if [[ -n "${cache_route:-}" ]]; then',
                '      case "$cwd" in',
                '        "$cache_route"|"$cache_route"/*)',
                '          matched_root="$root"',
                '          matched_rel="$rel"',
                '          matched_env_file="${env_file:-}"',
                '          matched_profile="${profile:-}"',
                '          cwd_matched=1',
                "          break",
                "          ;;",
                "      esac",
                "    fi",
                '  done < "$repo_env_files"',
                "fi",
                'if [[ -r "$repo_env_files" ]]; then',
                '  candidate_count=0',
                "  while IFS=$'\\t' read -r rel root env_file cache_route profile; do",
                '    [[ -n "${rel:-}" ]] || continue',
                '    [[ "${cache_route:-}" == "-" ]] && cache_route=""',
                '    [[ "${profile:-}" == "-" ]] && profile=""',
                '    repo_abs="$workspace/$rel"',
                '    argument_match=0',
                '    for arg in "$@"; do',
                '      arg_path="$arg"',
                '      [[ "$arg_path" == *=* ]] && arg_path="${arg_path#*=}"',
                '      arg_path="${arg_path#file://}"',
                '      case "$arg_path" in',
                '        "$repo_abs"|"$repo_abs"/*|"$rel"|"$rel"/*|"./$rel"|"./$rel"/*)',
                '          argument_match=1',
                '          break',
                '          ;;',
                '      esac',
                '      if [[ -n "${cache_route:-}" ]]; then',
                '        case "$arg_path" in',
                '          "$cache_route"|"$cache_route"/*)',
                '            argument_match=1',
                '            break',
                '            ;;',
                '        esac',
                '      fi',
                '      relative_path="${arg_path%%::*}"',
                '      if [[ "$cwd_matched" == "0" && "$cwd_in_workspace_repo" == "0" && -n "$relative_path" && "$relative_path" != /* && "$relative_path" != -* && -e "$repo_abs/${relative_path#./}" ]]; then',
                '        argument_match=1',
                '        break',
                '      fi',
                '    done',
                '    if [[ "$argument_match" == "1" ]]; then',
                '      candidate_count=$((candidate_count + 1))',
                '      matched_root="$root"',
                '      matched_rel="$rel"',
                '      matched_env_file="${env_file:-}"',
                '      matched_profile="${profile:-}"',
                '      route_from_args=1',
                '    fi',
                '  done < "$repo_env_files"',
                '  if (( candidate_count > 1 )); then',
                '    ambiguous_repo_args=1',
                '    matched_root=""',
                '    matched_rel=""',
                '    matched_env_file=""',
                '    matched_profile=""',
                '    route_from_args=0',
                '  fi',
                "fi",
                'if [[ -n "$matched_env_file" && -r "$matched_env_file" ]]; then',
                "  # shellcheck disable=SC1090",
                '  source "$matched_env_file"',
                "fi",
                'if [[ -n "$matched_rel" ]]; then',
                '  matched_repo_abs="$workspace/$matched_rel"',
                '  repo_python_path="$matched_repo_abs"',
                '  if [[ -d "$matched_repo_abs/src" ]]; then repo_python_path="$matched_repo_abs/src:$repo_python_path"; fi',
                '  export PYTHONPATH="$repo_python_path${PYTHONPATH:+:$PYTHONPATH}"',
                "fi",
                'if [[ -z "$matched_root" && -n "${ECOSYNC_DEPS_ROOT:-}" && -d "$ECOSYNC_DEPS_ROOT" ]]; then',
                '  matched_root="$ECOSYNC_DEPS_ROOT"',
                "fi",
                'image_root=""',
                'if [[ -n "$matched_rel" && -r "$repo_image_files" ]]; then',
                "  while IFS=$'\\t' read -r rel root; do",
                '    [[ -n "${rel:-}" && -n "${root:-}" ]] || continue',
                '    if [[ "$rel" == "$matched_rel" ]]; then image_root="$root"; break; fi',
                '  done < "$repo_image_files"',
                "fi",
                'if [[ -z "$image_root" && "$tool" =~ ^(cargo|rustc|rustdoc|rustfmt|rustup|cargo-fmt|cargo-clippy|clippy-driver)$ && -r "$repo_image_files" ]]; then',
                '  rust_candidate_root=""',
                '  rust_candidate_ambiguous=0',
                "  while IFS=$'\\t' read -r rel root; do",
                '    [[ -n "${root:-}" ]] || continue',
                '    candidate_has_tool=0',
                '    if [[ -n "${RUSTUP_TOOLCHAIN:-}" && -x "$root/usr-local-rustup/toolchains/$RUSTUP_TOOLCHAIN/bin/$tool" ]]; then candidate_has_tool=1; fi',
                '    if [[ "$candidate_has_tool" == "0" ]]; then for candidate in "$root"/usr-local-rustup/toolchains/*/bin/"$tool"; do if [[ -x "$candidate" ]]; then candidate_has_tool=1; break; fi; done; fi',
                '    if [[ "$candidate_has_tool" == "0" && -x "$root/usr-local-cargo/bin/$tool" ]]; then candidate_has_tool=1; fi',
                '    [[ "$candidate_has_tool" == "1" ]] || continue',
                '    if [[ -z "$rust_candidate_root" ]]; then rust_candidate_root="$root"; elif [[ "$rust_candidate_root" != "$root" ]]; then rust_candidate_ambiguous=1; fi',
                '  done < "$repo_image_files"',
                '  if [[ "$rust_candidate_ambiguous" == "0" ]]; then image_root="$rust_candidate_root"; fi',
                "fi",
                'if [[ "$matched_root" == /ecosync-debug-deps/runtime/* ]]; then',
                "  unset PYTHONHOME",
                "fi",
                'if [[ "$tool" == "uv" && "${1:-}" == "run" && "${2:-}" == "pytest" ]]; then',
                '  tool="pytest"',
                '  set -- "${@:3}"',
                'elif [[ "$tool" == "uv" && "${1:-}" == "run" && "${2:-}" =~ ^(python|python3)$ && "${3:-}" == "-m" && "${4:-}" == "pytest" ]]; then',
                '  tool="pytest"',
                '  set -- "${@:5}"',
                "fi",
                'pytest_route=0',
                'pytest_args=("$@")',
                'if [[ "$tool" == "pytest" ]]; then',
                '  pytest_route=1',
                'elif [[ "$tool" =~ ^(python|python3)$ && "${1:-}" == "-m" && "${2:-}" == "pytest" ]]; then',
                '  pytest_route=1',
                '  pytest_args=("${@:3}")',
                "fi",
                'if [[ "$pytest_route" == "1" && "$ambiguous_repo_args" == "1" ]]; then',
                '  printf "ecosync-tool-shim: test command targets multiple repositories; run the tests separately from each repository environment\\n" >&2',
                '  exit 64',
                "fi",
                'if [[ "$route_from_args" == "1" && -n "$matched_rel" ]]; then',
                '  cd "$workspace/$matched_rel"',
                '  cwd="$workspace/$matched_rel"',
                "fi",
                'if [[ "$pytest_route" == "1" && "${ECOSYNC_PYTEST_CLEAR_ADDOPTS:-0}" == "1" ]]; then',
                '  has_addopts_override=0',
                '  for arg in "${pytest_args[@]}"; do',
                '    case "$arg" in -o|--override-ini|addopts=*|--override-ini=addopts=*) has_addopts_override=1 ;; esac',
                '  done',
                '  if [[ "$has_addopts_override" == "0" ]]; then pytest_args+=("-o" "addopts="); fi',
                '  if [[ "$tool" == "pytest" ]]; then set -- "${pytest_args[@]}"; else set -- -m pytest "${pytest_args[@]}"; fi',
                "fi",
                'tox_cache_root="${ECOSYNC_TOX_CACHE_ROOT:-/cache/tox}"',
                'if [[ "$pytest_route" == "1" && -d "$tox_cache_root" ]]; then',
                '  best_python=""',
                '  best_score=0',
                '  shopt -s nullglob',
                '  for tox_python in "$tox_cache_root"/*/*/bin/python; do',
                '    tox_env="${tox_python%/bin/python}"',
                '    tox_env="${tox_env##*/}"',
                '    env_tokens="${tox_env//[-_.]/ }"',
                '    score=0',
                '    for arg in "${pytest_args[@]}"; do',
                '      arg_lower="${arg,,}"',
                '      for token in $env_tokens; do',
                '        token="${token,,}"',
                '        [[ ${#token} -ge 4 ]] || continue',
                '        [[ "$token" =~ ^(python|pytest|common|hidden|py[0-9]|v[0-9]|[0-9]+)$ ]] && continue',
                '        [[ "$arg_lower" == *"$token"* ]] && score=$((score + 1))',
                '      done',
                '    done',
                '    if (( score > best_score )); then',
                '      best_score=$score',
                '      best_python="$tox_python"',
                '    fi',
                '  done',
                '  shopt -u nullglob',
                '  if [[ -n "$best_python" && "$best_score" -gt 0 ]]; then',
                '    exec "$best_python" -m pytest "${pytest_args[@]}"',
                '  fi',
                "fi",
                'if [[ "$tool" =~ ^(python|python3)$ ',
                '  && "${1:-}" == "-m" ',
                '  && "${2:-}" == "tox" ',
                '  && -x "/opt/ecosync/shims/$tool" ]]; then',
                "  unset ECOSYNC_DEPS_ROOT",
                "  export ECOSYNC_REPO_ENVS=/opt/ecosync/repo-envs.tsv",
                '  exec "/opt/ecosync/shims/$tool" "$@"',
                "fi",
                'if [[ "$tool" =~ ^(python|python3|pytest|pip|pip3)$ ',
                '  && -d "$matched_root/venv" ',
                '  && -n "$image_root" ',
                '  && -d "$image_root/usr-local" ]]; then',
                '  python_version="$(awk -F\'[ =.]+\' \'/^version_info[[:space:]]*=/{print $2 "." $3; exit}\' "$matched_root/venv/pyvenv.cfg" 2>/dev/null || true)"',
                '  image_python="$image_root/usr-local-bin/python${python_version}"',
                '  [[ -x "$image_python" ]] || image_python="$image_root/usr-local-bin/python"',
                '  if [[ -x "$image_python" ]]; then',
                '    export PYTHONHOME="$image_root/usr-local"',
                '    export VIRTUAL_ENV="$matched_root/venv"',
                '    if [[ -d "$image_root/usr-local-lib" ]]; then',
                '      export LD_LIBRARY_PATH="$image_root/usr-local-lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"',
                "    fi",
                '    case "$tool" in',
                '      python|python3) exec -a "$matched_root/venv/bin/python" "$image_python" "$@" ;;',
                '      pytest) exec -a "$matched_root/venv/bin/python" "$image_python" -m pytest "$@" ;;',
                '      pip|pip3) exec -a "$matched_root/venv/bin/python" "$image_python" -m pip "$@" ;;',
                "    esac",
                "  fi",
                "fi",
                'if [[ "$tool" =~ ^(python|python3|pytest|pip|pip3)$ ',
                '  && -x "$matched_root/venv/bin/python" ]]; then',
                '  unset PYTHONHOME',
                '  export VIRTUAL_ENV="$matched_root/venv"',
                '  case "$tool" in',
                '    python|python3) exec "$matched_root/venv/bin/python" "$@" ;;',
                '    pytest) exec "$matched_root/venv/bin/python" -m pytest "$@" ;;',
                '    pip|pip3) exec "$matched_root/venv/bin/python" -m pip "$@" ;;',
                '  esac',
                "fi",
                'if [[ "$tool" == "pre-commit" && -x "$matched_root/venv/bin/python" ]]; then',
                '  exec "$matched_root/venv/bin/python" -m pre_commit "$@"',
                "fi",
                'if [[ "$tool" == "go" && -n "$matched_root" ]]; then',
                '  if [[ -z "${GOTMPDIR:-}" || "$GOTMPDIR" == /ecosync-scratch/* ]]; then go_tmp="/tmp/ecosync-go-build-$(id -u)"; mkdir -p "$go_tmp"; export TMPDIR=/tmp GOTMPDIR="$go_tmp"; fi',
                '  if [[ "$cwd" == "$workspace/repos/ipfs/boxo" || "$cwd" == "$workspace/repos/ipfs/boxo/"* ]]; then export GOWORK=off; fi',
                '  if [[ -n "${GOMODCACHE:-}" && -d "$GOMODCACHE" ]]; then :; elif [[ -n "${ECOSYNC_AGENT_GOMODCACHE:-}" && -d "$ECOSYNC_AGENT_GOMODCACHE" ]]; then export GOMODCACHE="$ECOSYNC_AGENT_GOMODCACHE"; elif [[ -n "${ECOSYNC_CASE_GOMODCACHE:-}" && -d "$ECOSYNC_CASE_GOMODCACHE" ]]; then export GOMODCACHE="$ECOSYNC_CASE_GOMODCACHE"; elif [[ -d "$matched_root/gomodcache" ]]; then export GOMODCACHE="$matched_root/gomodcache"; fi',
                '  if [[ -n "${GOCACHE:-}" && -d "$GOCACHE" ]]; then :; elif [[ -n "${ECOSYNC_CASE_GOCACHE:-}" && -d "$ECOSYNC_CASE_GOCACHE" ]]; then export GOCACHE="$ECOSYNC_CASE_GOCACHE"; fi',
                '  if [[ -n "${GOPATH:-}" && -d "$GOPATH" ]]; then :; elif [[ -n "${ECOSYNC_CASE_GOPATH:-}" && -d "$ECOSYNC_CASE_GOPATH" ]]; then export GOPATH="$ECOSYNC_CASE_GOPATH"; elif [[ -d "$matched_root/gopath" ]]; then export GOPATH="$matched_root/gopath"; fi',
                '  go_proxy_roots=()',
                '  if [[ -n "${ECOSYNC_AGENT_GOMODCACHE:-}" && -d "$ECOSYNC_AGENT_GOMODCACHE/cache/download" ]]; then go_proxy_roots+=("file://$ECOSYNC_AGENT_GOMODCACHE/cache/download"); fi',
                '  if [[ -n "${GOMODCACHE:-}" && -d "$GOMODCACHE/cache/download" ]]; then go_proxy_roots+=("file://$GOMODCACHE/cache/download"); fi',
                '  if [[ -d "$matched_root/gomodcache/cache/download" ]]; then go_proxy_roots+=("file://$matched_root/gomodcache/cache/download"); fi',
                '  if [[ "$matched_root" != "/opt/ecosync" && -d /opt/ecosync/gomodcache/cache/download ]]; then go_proxy_roots+=("file:///opt/ecosync/gomodcache/cache/download"); fi',
                '  if (( ${#go_proxy_roots[@]} > 0 )); then GOPROXY="$(IFS=,; printf \"%s\" \"${go_proxy_roots[*]}\")"; export GOPROXY GOSUMDB=off; elif [[ "${ECOSYNC_PACKAGE_NETWORK_DISABLED:-0}" == "1" ]]; then export GOPROXY=off GOSUMDB=off; fi',
                '  if [[ -x /usr/local/go/bin/go ]]; then exec /usr/local/go/bin/go "$@"; fi',
                "fi",
                'if [[ "$tool" == "yarn" && -n "${ECOSYNC_YARN_VERSION:-}" ]]; then',
                '  cached_yarn="${COREPACK_HOME:-}/v1/yarn/$ECOSYNC_YARN_VERSION"',
                '  yarn_node="$matched_root/node-runtime/bin/node"',
                '  [[ -x "$yarn_node" ]] || yarn_node="$(command -v node || true)"',
                '  if [[ -n "$yarn_node" && -f "$cached_yarn/bin/yarn.js" ]]; then exec "$yarn_node" "$cached_yarn/bin/yarn.js" "$@"; fi',
                '  if [[ -n "$yarn_node" && -f "$cached_yarn/yarn.js" ]]; then exec "$yarn_node" "$cached_yarn/yarn.js" "$@"; fi',
                '  if command -v corepack >/dev/null 2>&1; then exec corepack "yarn@$ECOSYNC_YARN_VERSION" "$@"; fi',
                "fi",
                'if [[ "$tool" == "bundle" && -n "${BUNDLE_PATH:-}" && -x /usr/local/bin/bundle ]]; then',
                '  exec /usr/local/bin/bundle "$@"',
                "fi",
                'if [[ "$tool" =~ ^(rake|rspec|rubocop)$ && -n "${BUNDLE_PATH:-}" ]]; then',
                '  shopt -s nullglob',
                '  ruby_bins=("$BUNDLE_PATH"/ruby/*/bin/"$tool")',
                '  shopt -u nullglob',
                '  if (( ${#ruby_bins[@]} > 0 )); then',
                '    exec "${ruby_bins[0]}" "$@"',
                '  fi',
                "fi",
                'if [[ "$tool" =~ ^(cargo|rustc|rustdoc|rustfmt|rustup|cargo-fmt|cargo-clippy|clippy-driver)$ && -n "$image_root" ]]; then',
                '  rust_toolchain_bin=""',
                '  rust_toolchain_name=""',
                '  if [[ -n "${RUSTUP_TOOLCHAIN:-}" && -d "$image_root/usr-local-rustup/toolchains/$RUSTUP_TOOLCHAIN/bin" ]]; then rust_toolchain_name="$RUSTUP_TOOLCHAIN"; rust_toolchain_bin="$image_root/usr-local-rustup/toolchains/$RUSTUP_TOOLCHAIN/bin"; else for candidate in "$image_root"/usr-local-rustup/toolchains/*/bin; do if [[ -d "$candidate" ]]; then rust_toolchain_bin="$candidate"; rust_toolchain_name="${candidate%/bin}"; rust_toolchain_name="${rust_toolchain_name##*/}"; break; fi; done; fi',
                '  if [[ -n "$rust_toolchain_bin" && -x "$rust_toolchain_bin/$tool" ]]; then export RUSTUP_HOME="$image_root/usr-local-rustup"; [[ -z "$rust_toolchain_name" ]] || export RUSTUP_TOOLCHAIN="$rust_toolchain_name"; exec "$rust_toolchain_bin/$tool" "$@"; fi',
                '  if [[ -x "$image_root/usr-local-cargo/bin/$tool" ]]; then export RUSTUP_HOME="$image_root/usr-local-rustup"; [[ -z "$rust_toolchain_name" ]] || export RUSTUP_TOOLCHAIN="$rust_toolchain_name"; exec "$image_root/usr-local-cargo/bin/$tool" "$@"; fi',
                "fi",
                'if [[ "$tool" == "uv" && -x /ecosync-host-tools/uv ]]; then',
                '  exec /ecosync-host-tools/uv "$@"',
                "fi",
                'if [[ "$tool" == "phpunit" && -n "$matched_profile" && -f "$matched_root/run-phpunit.sh" ]]; then',
                '  exec bash "$matched_root/run-phpunit.sh" "$matched_profile" "$@"',
                "fi",
                'flutter_root="$workspace/repos/flutter/flutter"',
                'flutter_dart="$flutter_root/bin/cache/dart-sdk/bin/dart"',
                'if [[ "$tool" == "dart" && -x "$flutter_dart" ]]; then',
                '  exec "$flutter_dart" "$@"',
                "fi",
                'if [[ "$tool" == "flutter" && -x "$flutter_dart" && -f "$flutter_root/bin/cache/flutter_tools.snapshot" ]]; then',
                '  export FLUTTER_ROOT="$flutter_root"',
                '  exec "$flutter_dart" "$flutter_root/bin/cache/flutter_tools.snapshot" "$@"',
                "fi",
                'if [[ "$tool" == "node" && -z "$matched_root" && -x "${ECOSYNC_AGENT_RUNTIME:-}/bin/node" ]]; then',
                '  exec "$ECOSYNC_AGENT_RUNTIME/bin/node" "$@"',
                "fi",
                'if [[ "$tool" =~ ^(npm|npx)$ && -x "${ECOSYNC_AGENT_RUNTIME:-}/bin/$tool" ]]; then',
                '  npm_cli="/usr/local/lib/node_modules/npm/bin/${tool}-cli.js"',
                '  npm_cli_header=""',
                '  [[ ! -f "$npm_cli" ]] || IFS= read -r npm_cli_header < "$npm_cli" || true',
                '  if [[ "$npm_cli_header" == "#!"*sh* ]]; then exec "$ECOSYNC_AGENT_RUNTIME/bin/$tool" "$@"; fi',
                "fi",
                'if [[ -x "/opt/ecosync/shims/$tool" ]]; then',
                '  exec "/opt/ecosync/shims/$tool" "$@"',
                "fi",
                'if [[ -n "${ECOSYNC_DEBUG_SHIMS:-}" ]]; then',
                '  PATH="$(printf "%s" "$PATH" | awk -v RS=: -v ORS=: -v skip="$ECOSYNC_DEBUG_SHIMS" \'$0 != skip { print }\')"',
                '  PATH="${PATH%:}"',
                "fi",
                'exec "$tool" "$@"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)
    for tool in (
        "composer",
        "bundle",
        "cargo",
        "cargo-clippy",
        "cargo-fmt",
        "clippy-driver",
        "dart",
        "flutter",
        "go",
        "godot",
        "gradle",
        "jest",
        "mocha",
        "mvn",
        "node",
        "npm",
        "npx",
        "php",
        "phpunit",
        "pip",
        "pip3",
        "pnpm",
        "pre-commit",
        "pytest",
        "python",
        "python3",
        "rake",
        "rspec",
        "rubocop",
        "rustc",
        "rustdoc",
        "rustfmt",
        "rustup",
        "scons",
        "tsd",
        "uv",
        "yarn",
    ):
        link = shim_dir / tool
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to("ecosync-tool-shim")


def visible_workspace_repo_paths(run_dir: Path) -> list[str]:
    run_path = run_dir / "run.json"
    if not run_path.exists():
        return []
    run_data = json.loads(run_path.read_text(encoding="utf-8"))
    workspace = Path(run_data.get("agent_workspace") or (run_dir / "agent_workspace"))
    manifest_path = workspace / ".ecosyncbench" / "manifest.json"
    if not manifest_path.exists():
        return []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return [str(repo["path"]).rstrip("/") for repo in manifest.get("repos", [])]


def agent_workspace_path(run_dir: Path) -> Path | None:
    run_path = run_dir / "run.json"
    if not run_path.exists():
        return None
    run_data = json.loads(run_path.read_text(encoding="utf-8"))
    workspace_value = run_data.get("agent_workspace")
    return Path(workspace_value) if workspace_value else run_dir / "agent_workspace"


def reusable_agent_workspace(run_dir: Path, task_id: str) -> Path:
    run_path = run_dir / "run.json"
    if not run_path.is_file():
        raise ValueError(f"cannot reuse agent workspace without run metadata: {run_path}")
    run_data = json.loads(run_path.read_text(encoding="utf-8"))
    recorded_task = str(run_data.get("task_id") or "")
    if recorded_task != task_id:
        raise ValueError(
            f"cannot reuse agent workspace for {task_id}: run metadata belongs to {recorded_task or 'unknown task'}"
        )
    workspace = agent_workspace_path(run_dir)
    if workspace is None or not workspace.is_dir():
        raise ValueError(f"cannot reuse missing agent workspace: {workspace}")
    manifest = workspace / ".ecosyncbench" / "manifest.json"
    if not manifest.is_file():
        raise ValueError(f"cannot reuse agent workspace without manifest: {manifest}")
    return workspace


def extract_workspace_heredoc(script_text: str, relative_path: str) -> str | None:
    """Extract a static file written below $workspace by a runner script."""
    lines = script_text.splitlines()
    target = f"$workspace/{relative_path}"
    for index, line in enumerate(lines):
        if target not in line or "cat" not in line or "<<" not in line:
            continue
        delimiter_match = re.search(
            r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1\s*$",
            line,
        )
        if not delimiter_match:
            continue
        delimiter = delimiter_match.group(2)
        body: list[str] = []
        for candidate in lines[index + 1 :]:
            if candidate.strip() == delimiter:
                return "\n".join(body) + "\n"
            body.append(candidate)
    return None


def materialize_agent_workspace_support(
    task_id: str,
    run_dir: Path,
    profiles: dict[str, Any],
) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Materialize public runner support files referenced by warmed venvs.

    Some matrix runners install editable helper packages below
    /workspace/.ecosyncbench. The warmed venv retains those paths, so the
    corresponding public helper files must also exist in the agent workspace.
    """
    workspace = agent_workspace_path(run_dir)
    run_profile = task_dir(task_id) / "environment" / "run_profile.sh"
    if workspace is None or not run_profile.exists():
        return {}, []
    script_text = run_profile.read_text(encoding="utf-8", errors="replace")
    relative_paths = (
        ".ecosyncbench/awx-plugin-shim/pyproject.toml",
        ".ecosyncbench/awx-plugin-shim/ecosync_awx_plugin_shim.py",
        ".ecosyncbench/ecosync_awx_test_settings.py",
    )
    written: list[str] = []
    for relative_path in relative_paths:
        body = extract_workspace_heredoc(script_text, relative_path)
        if body is None:
            continue
        if (
            relative_path == ".ecosyncbench/ecosync_awx_test_settings.py"
            and re.search(r"sys\.argv\.append\([\"']pytest[\"']\)", script_text)
        ):
            body += (
                "\n# Match the matrix runner's pytest.main() argv compatibility.\n"
                "import sys as _ecosync_sys\n"
                "if 'pytest' not in _ecosync_sys.argv and "
                "any('pytest' in arg for arg in _ecosync_sys.argv):\n"
                "    _ecosync_sys.argv.append('pytest')\n"
            )
        destination = workspace / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(body, encoding="utf-8")
        written.append(relative_path)

    boxo_module = workspace / "repos/ipfs/boxo/go.mod"
    kubo_module = workspace / "repos/ipfs/kubo/go.mod"
    if boxo_module.exists() and kubo_module.exists():
        boxo_go_mod = boxo_module.read_text(encoding="utf-8", errors="replace")
        kubo_go_mod = kubo_module.read_text(encoding="utf-8", errors="replace")
        go_versions = []
        for go_mod in (boxo_go_mod, kubo_go_mod):
            version_match = re.search(r"^[ \t]*go[ \t]+(\d+(?:\.\d+){1,2})[ \t]*$", go_mod, re.M)
            if version_match:
                version = version_match.group(1)
                parts = tuple(int(part) for part in version.split("."))
                go_versions.append((parts + (0,) * (3 - len(parts)), version))
        go_work_version = max(go_versions)[1] if go_versions else "1.25.0"
        go_work = workspace / "go.work"
        go_work_text = (
            f"go {go_work_version}\n\n"
            "use ./repos/ipfs/kubo\n\n"
            "replace github.com/ipfs/boxo => ./repos/ipfs/boxo\n"
        )
        go_work.write_text(go_work_text, encoding="utf-8")
        written.append("go.work")

    overrides: dict[str, dict[str, str]] = {}
    if "jenkins-hpi-lifecycle-mapping.jar" in script_text:
        mapping_jar = workspace / ".ecosyncbench" / "jenkins-hpi-lifecycle-mapping.jar"
        mapping_jar.parent.mkdir(parents=True, exist_ok=True)
        components = """<component-set>
  <components>
    <component>
      <role>org.apache.maven.lifecycle.mapping.LifecycleMapping</role>
      <role-hint>hpi</role-hint>
      <implementation>org.apache.maven.lifecycle.mapping.DefaultLifecycleMapping</implementation>
      <configuration>
        <lifecycles>
          <lifecycle>
            <id>default</id>
            <phases>
              <validate>org.jenkins-ci.tools:maven-hpi-plugin:validate,org.jenkins-ci.tools:maven-hpi-plugin:validate-hpi</validate>
              <process-resources>org.apache.maven.plugins:maven-resources-plugin:resources</process-resources>
              <compile>org.apache.maven.plugins:maven-compiler-plugin:compile</compile>
              <process-classes>org.kohsuke:access-modifier-checker:1.31:enforce</process-classes>
              <generate-test-sources>org.jenkins-ci.tools:maven-hpi-plugin:insert-test</generate-test-sources>
              <process-test-resources>org.apache.maven.plugins:maven-resources-plugin:testResources</process-test-resources>
              <test-compile>org.apache.maven.plugins:maven-compiler-plugin:testCompile,org.jenkins-ci.tools:maven-hpi-plugin:resolve-test-dependencies</test-compile>
              <process-test-classes>org.jenkins-ci.tools:maven-hpi-plugin:test-runtime</process-test-classes>
              <test>org.apache.maven.plugins:maven-surefire-plugin:test</test>
              <package>org.jenkins-ci.tools:maven-hpi-plugin:hpi</package>
            </phases>
          </lifecycle>
        </lifecycles>
      </configuration>
    </component>
    <component>
      <role>org.apache.maven.artifact.handler.ArtifactHandler</role>
      <role-hint>executable-war</role-hint>
      <implementation>org.apache.maven.artifact.handler.DefaultArtifactHandler</implementation>
      <configuration>
        <type>executable-war</type>
        <extension>war</extension>
        <packaging>war</packaging>
        <language>java</language>
        <addedToClasspath>true</addedToClasspath>
      </configuration>
    </component>
  </components>
</component-set>
"""
        with zipfile.ZipFile(mapping_jar, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n\n")
            archive.writestr("META-INF/plexus/components.xml", components)
        written.append(".ecosyncbench/jenkins-hpi-lifecycle-mapping.jar")
        for profile_name, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            workdir = str(profile.get("workdir") or "").rstrip("/")
            if not (workspace / workdir / "pom.xml").exists():
                continue
            overrides[str(profile_name)] = {
                "MAVEN_OPTS": (
                    "-Dmaven.repo.local=/m2 "
                    "-Denforcer.skip=true -Dmaven.gitcommitid.skip=true "
                    "-Dmaven.ext.class.path=/workspace/.ecosyncbench/jenkins-hpi-lifecycle-mapping.jar"
                )
            }
    if ".ecosyncbench/ecosync_awx_test_settings.py" in written:
        for profile_name, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            workdir = str(profile.get("workdir") or "").rstrip("/")
            if Path(workdir).name != "awx":
                continue
            python_paths = ["/workspace/.ecosyncbench"]
            if "repos/ansible/awx-plugins" in script_text:
                python_paths.append("/workspace/repos/ansible/awx-plugins/src")
            overrides[str(profile_name)] = {
                "AWX_LOGGING_MODE": "stdout",
                "AWX_MODE": "development",
                "DJANGO_SETTINGS_MODULE": "ecosync_awx_test_settings",
                "PYTHONPATH": ":".join(python_paths),
                "PYTHONWARNINGS": (
                    "ignore::ResourceWarning,"
                    "ignore:Accessing the database during app initialization is discouraged:RuntimeWarning"
                ),
                "PYTEST_ADDOPTS": (
                    "--ds=ecosync_awx_test_settings "
                    "-o filterwarnings=ignore::RuntimeWarning"
                ),
                "SECRET_KEY": "ecosyncbench-test-secret",
            }
    return overrides, written


def task_requires_postgres_prestart(task_id: str) -> bool:
    """Return whether the task runner explicitly starts PostgreSQL.

    RQ1 workspaces expose every repository in an ecosystem, so repository
    presence cannot be used to decide which services the current case needs.
    The matrix runner is the authoritative public environment definition.
    """
    run_profile = task_dir(task_id) / "environment" / "run_profile.sh"
    if not run_profile.exists():
        return False
    script_text = run_profile.read_text(encoding="utf-8", errors="replace")
    return re.search(r"\b(?:service\s+postgresql\s+start|start_postgres\b)", script_text) is not None


def task_postgres_sidecar(task_id: str) -> dict[str, Any] | None:
    """Return the PostgreSQL container settings declared by the task runner."""
    run_profile = task_dir(task_id) / "environment" / "run_profile.sh"
    if not run_profile.exists():
        return None
    script_text = run_profile.read_text(encoding="utf-8", errors="replace")
    if not re.search(r"\bdocker\s+run\s+-d\b", script_text):
        return None
    image_match = re.search(r"(?<![/\w-])postgres:[A-Za-z0-9_.-]+", script_text)
    if not image_match:
        return None

    def setting(name: str, default: str) -> str:
        match = re.search(rf"\b{name}=([^\s\\]+)", script_text)
        return match.group(1).strip('"\'') if match else default

    user = setting("POSTGRES_USER", "postgres")
    password = setting("POSTGRES_PASSWORD", "")
    database = setting("POSTGRES_DB", "postgres")
    trust = setting("POSTGRES_HOST_AUTH_METHOD", "") == "trust"
    prisma = user == "prisma" or "TEST_POSTGRES_URI=" in script_text
    return {
        "image": image_match.group(0),
        "user": user,
        "password": password,
        "database": database,
        "trust": trust,
        "prisma": prisma,
        "extra_databases": ["tests-migrate", "tests-migrate-shadowdb"] if prisma else [],
        "settle_seconds": 5 if prisma else 2,
    }


def task_mongodb_sidecar(task_id: str) -> tuple[str, int] | None:
    """Return the MongoDB image and post-port settle time used by the task."""
    run_profile = task_dir(task_id) / "environment" / "run_profile.sh"
    if not run_profile.exists():
        return None
    script_text = run_profile.read_text(encoding="utf-8", errors="replace")
    atlas = re.search(r"\bmongodb/mongodb-atlas-local:[A-Za-z0-9_.-]+", script_text)
    if atlas:
        return atlas.group(0), 15
    mongo = re.search(r"(?<![/\w-])mongo:[A-Za-z0-9_.-]+", script_text)
    if mongo:
        return mongo.group(0), 0
    return None


def task_requires_mongodb_sidecar(task_id: str) -> bool:
    return task_mongodb_sidecar(task_id) is not None


def persistent_uv_venv_path(task_id: str, profile_name: str, run_dir: Path) -> Path | None:
    configured = os.environ.get("ECOSYNC_DEBUG_VENV_ROOT")
    candidates = [
        Path(configured).expanduser()
        if configured
        else Path.home() / ".cache" / "wideswe" / "debug-venvs",
        run_dir.parents[2] / "_debug-venvs" if len(run_dir.parents) > 2 else run_dir.parent / "_debug-venvs",
    ]
    for root in candidates:
        try:
            root.mkdir(parents=True, exist_ok=True)
            if not os.access(root, os.W_OK):
                continue
            path = root / safe_slug(task_id) / safe_slug(profile_name)
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            continue
    return None


def persistent_node_modules_path(root: Path, task_id: str, profile_name: str) -> Path:
    return root / safe_slug(task_id) / safe_slug(profile_name) / "node_modules"


def persistent_vendor_path(root: Path, task_id: str, profile_name: str) -> Path:
    return root / safe_slug(task_id) / safe_slug(profile_name) / "vendor"


def persistent_nuget_test_packages_path(cache_root: Path) -> Path | None:
    warmed_test_packages = cache_root / "tmp" / "test-packages"
    if path_is_dir(warmed_test_packages):
        return warmed_test_packages
    if path_is_dir(cache_root / "nuget"):
        return cache_root / "nuget-test-packages"
    if "nuget" in cache_root.name.lower():
        return cache_root / ".test-packages"
    return None


def persistent_elastic_package_data_path(root: Path, task_id: str) -> Path:
    return root / "elastic-package-data" / safe_slug(task_id)


def persistent_elasticsearch_build_path(root: Path) -> Path:
    return root / "elasticsearch-build"


def persistent_flutter_bin_cache_path(cache_root: Path) -> Path | None:
    flutter_cache_root = cache_root / "flutter-cache"
    if not path_is_dir(flutter_cache_root):
        return None
    for candidate in sorted(flutter_cache_root.glob("*/bin-cache")):
        if path_exists(candidate / "dart-sdk" / "bin" / "dart"):
            return candidate
    return None


def profile_persists_node_modules(profile: dict[str, Any]) -> bool:
    return bool(profile.get("persist_node_modules")) or os.environ.get(
        "ECOSYNC_ENABLE_NODE_MODULES_CACHE", "0"
    ) == "1"


def profile_node_modules_paths(profile: dict[str, Any]) -> list[str]:
    configured = profile.get("persist_node_modules_paths")
    if isinstance(configured, list):
        paths = [str(item).strip().strip("/") or "." for item in configured]
        return list(dict.fromkeys(paths))
    return ["."] if profile_persists_node_modules(profile) else []


def profile_persists_vendor(profile: dict[str, Any]) -> bool:
    return bool(profile.get("persist_vendor"))


def profile_env_file_text(profile_env: dict[str, str]) -> str:
    forced_names = {
        "BUNDLE_ALLOW_OFFLINE_INSTALL",
        "BUNDLE_APP_CONFIG",
        "BUNDLE_PATH",
        "COREPACK_HOME",
        "ECOSYNC_CASE_GOCACHE",
        "ECOSYNC_CASE_GOMODCACHE",
        "ECOSYNC_CASE_GOPATH",
        "ECOSYNC_RUBY_OFFLINE",
        "GEM_HOME",
        "GEM_PATH",
        "GOCACHE",
        "GOMODCACHE",
        "GOPATH",
        "GOPROXY",
        "GOSUMDB",
        "KBN_BOOTSTRAP_CACHE_DIR",
        "MAVEN_OPTS",
        "NPM_CONFIG_CACHE",
        "PNPM_HOME",
        "PNPM_STORE_DIR",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "YARN_CACHE_FOLDER",
        "YARN_GLOBAL_FOLDER",
        "npm_config_cache",
        "npm_config_store_dir",
        "pnpm_config_store_dir",
    }
    lines = []
    for name, value in sorted(profile_env.items()):
        assignment = f"export {name}={shlex.quote(value)}"
        if name == "PYTHONPATH":
            lines.append(
                f"export PYTHONPATH={shlex.quote(value)}"
                "${PYTHONPATH:+:$PYTHONPATH}\n"
            )
        elif name in forced_names:
            lines.append(f"{assignment}\n")
        else:
            lines.append(f'if [ -z "${{{name}+x}}" ]; then {assignment}; fi\n')
    return "".join(lines)


def repo_yarn_version(agent_workspace: Path | None, workdir: str) -> str | None:
    """Select the repository's Yarn generation for agent-visible commands."""
    if agent_workspace is None:
        return None
    repo_root = agent_workspace / workdir.strip("/")
    package_json = repo_root / "package.json"
    package_manager = ""
    if package_json.exists():
        try:
            package_data = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            package_data = {}
        if isinstance(package_data, dict):
            package_manager = str(package_data.get("packageManager") or "").strip()
    if package_manager:
        match = re.fullmatch(r"yarn@([^+\s]+)(?:\+.*)?", package_manager)
        return match.group(1) if match else None

    yarn_lock = repo_root / "yarn.lock"
    if yarn_lock.exists():
        try:
            header = yarn_lock.read_text(encoding="utf-8", errors="replace")[:512]
        except OSError:
            return None
        if "# yarn lockfile v1" in header:
            return "1.22.22"
    return None


def profile_cache_env_from_runner_mounts(
    profile_name: str,
    profile: dict[str, Any],
    runner_mounts: list[tuple[Path | str, str]],
) -> dict[str, str]:
    """Resolve repo-specific cache subdirectories mounted by matrix runners."""
    workdir = str(profile.get("workdir") or "").rstrip("/")
    candidates = {
        safe_slug(str(profile.get("repo") or "")).replace("_", "-"),
        safe_slug(Path(workdir).name).replace("_", "-"),
        safe_slug(profile_name).replace("_", "-").removesuffix("-hidden"),
    }
    candidates.discard("")
    resolved: dict[str, str] = {}
    for host, container in runner_mounts:
        if not isinstance(host, Path):
            continue
        bundle_root = host / "bundle"
        if path_is_dir(bundle_root):
            preferred_bundle_slugs = list(
                dict.fromkeys(
                    [
                        safe_slug(profile_name).replace("_", "-").removesuffix("-hidden"),
                        safe_slug(str(profile.get("repo") or "")).replace("_", "-"),
                        safe_slug(Path(workdir).name).replace("_", "-"),
                        safe_slug(profile_name).replace("_", "-"),
                    ]
                )
            )
            bundle_children = {
                safe_slug(child.name).replace("_", "-"): child
                for child in safe_iterdir(bundle_root)
                if path_is_dir(child)
            }
            selected_bundle = next(
                (
                    bundle_children[slug]
                    for slug in preferred_bundle_slugs
                    if slug and slug in bundle_children
                ),
                None,
            )
            if selected_bundle is not None:
                bundle_container = (
                    f"{container.rstrip('/')}/bundle/{selected_bundle.name}"
                )
                config_host = host / "bundle-config" / selected_bundle.name
                config_container = (
                    f"{container.rstrip('/')}/bundle-config/{selected_bundle.name}"
                    if path_is_dir(config_host)
                    else f"{bundle_container}/.bundle"
                )
                resolved.update(
                    {
                        "BUNDLE_PATH": bundle_container,
                        "BUNDLE_APP_CONFIG": config_container,
                        "BUNDLE_DISABLE_LOCAL_BRANCH_CHECK": "true",
                        "BUNDLE_DISABLE_LOCAL_REVISION_CHECK": "true",
                    }
                )
        for child in safe_iterdir(host):
            if not path_is_dir(child):
                continue
            child_slug = safe_slug(child.name).replace("_", "-")
            if child_slug not in candidates:
                continue
            child_container = f"{container.rstrip('/')}/{child.name}"
            for item in cache_env_for_container_root(child, child_container):
                if "=" not in item:
                    continue
                name, value = item.split("=", 1)
                resolved[name] = value
            break
    return resolved


def isolated_agent_dependency_view(
    authoritative: Path,
    run_dir: Path,
    profile_name: str,
    fingerprint: str | None,
) -> tuple[Path, dict[str, Any]]:
    """Create a writable Agent view without exposing the evaluator cache to writes."""
    authoritative_id = hashlib.sha256(str(authoritative.resolve()).encode()).hexdigest()[:12]
    slug = safe_slug(
        f"{profile_name}-{fingerprint or 'unknown'}-{authoritative_id}"
    )
    root = run_dir / "case_debug_deps" / "isolated-node-modules" / slug
    upper = root / "upper"
    work = root / "work"
    merged = root / "merged"
    if root.exists():
        stale_unmount = unmount_dependency_view_layers(merged)
        if stale_unmount["mounted"]:
            raise RuntimeError(
                f"failed to unmount stale Agent dependency view {merged}: "
                f"{stale_unmount['output']}"
            )
        stale_cleanup = subprocess.run(
            ["sudo", "-n", "rm", "-rf", "--one-file-system", "--", str(root)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        if stale_cleanup.returncode != 0:
            raise RuntimeError(
                f"failed to remove stale Agent dependency view {root}: "
                f"{stale_cleanup.stdout.strip()}"
            )
    for path in (upper, work, merged):
        path.mkdir(parents=True, exist_ok=True)

    mounted = subprocess.run(
        [
            "sudo",
            "-n",
            "mount",
            "-t",
            "overlay",
            "overlay",
            "-o",
            (
                f"lowerdir={authoritative.resolve()},"
                f"upperdir={upper.resolve()},workdir={work.resolve()}"
            ),
            str(merged.resolve()),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if mounted.returncode == 0:
        runtime_dirs = reset_isolated_node_runtime_dirs(merged)
        return merged, {
            "strategy": "overlay",
            "authoritative_host": str(authoritative),
            "agent_host": str(merged),
            "mountpoint": str(merged),
            "root": str(root),
            "runtime_dirs": runtime_dirs,
        }

    # Correctness takes priority on hosts where overlay mounts are unavailable.
    # The copy fallback is slower but still isolates evaluator dependencies.
    shutil.rmtree(root)
    merged.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(authoritative, merged, symlinks=True)
    runtime_dirs = reset_isolated_node_runtime_dirs(merged)
    return merged, {
        "strategy": "copy",
        "authoritative_host": str(authoritative),
        "agent_host": str(merged),
        "mountpoint": None,
        "root": str(root),
        "overlay_error": mounted.stdout.strip(),
        "runtime_dirs": runtime_dirs,
    }


def reset_isolated_node_runtime_dirs(node_modules: Path) -> list[dict[str, Any]]:
    """Make tool-generated Node cache directories writable in the Agent view."""
    results: list[dict[str, Any]] = []
    for name in (".vite-temp", ".vite", ".cache"):
        path = node_modules / name
        removed_with_sudo = False
        if path.exists() or path.is_symlink():
            try:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except OSError:
                completed = subprocess.run(
                    ["sudo", "-n", "rm", "-rf", "--", str(path)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=False,
                )
                if completed.returncode != 0:
                    raise RuntimeError(
                        f"failed to reset Agent Node runtime directory {path}: "
                        f"{completed.stdout.strip()}"
                    )
                removed_with_sudo = True
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o777)
        results.append(
            {
                "path": str(path),
                "mode": "0777",
                "removed_with_sudo": removed_with_sudo,
            }
        )
    return results


NODE_RUNTIME_DIRECTORY_NAMES = frozenset({".vite-temp", ".vite", ".cache"})


def node_modules_has_dependency_entries(node_modules: Path) -> bool:
    """Return whether a cache contains packages rather than runtime scratch dirs."""
    try:
        return any(
            child.name not in NODE_RUNTIME_DIRECTORY_NAMES
            for child in node_modules.iterdir()
        )
    except OSError:
        return False


def agent_node_modules_authoritative_cache(requested: Path) -> tuple[Path, dict[str, Any] | None]:
    """Reuse a populated cache from the same immutable task/profile when needed."""
    if node_modules_has_dependency_entries(requested):
        return requested, None

    # Layout: <root>/<task>/<profile>/<fingerprint>/<relative>/node_modules.
    if len(requested.parents) < 3 or requested.name != "node_modules":
        return requested, None
    profile_root = requested.parents[2]
    relative_slug = requested.parent.name
    candidates: list[Path] = []
    for candidate in profile_root.glob(f"*/{relative_slug}/node_modules"):
        if candidate == requested:
            continue
        if node_modules_has_dependency_entries(candidate):
            candidates.append(candidate)
    if not candidates:
        return requested, None

    selected = max(candidates, key=lambda path: path.stat().st_mtime_ns)
    return selected, {
        "requested_host": str(requested),
        "selected_host": str(selected),
        "reason": "requested fingerprint cache is empty",
    }


def unmount_dependency_view_layers(mountpoint: Path, max_attempts: int = 32) -> dict[str, Any]:
    """Unmount every overlay stacked on a reused dependency-view mountpoint."""
    outputs: list[str] = []
    attempts = 0
    last_returncode = 0
    while attempts < max_attempts:
        try:
            mounted = mountpoint.is_mount()
        except OSError as exc:
            return {
                "attempts": attempts,
                "exit_code": last_returncode or 1,
                "output": "\n".join(outputs),
                "mounted": True,
                "error": f"{type(exc).__name__}: {exc}",
            }
        if not mounted:
            break
        completed = subprocess.run(
            ["sudo", "-n", "umount", str(mountpoint)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        attempts += 1
        last_returncode = completed.returncode
        if completed.stdout.strip():
            outputs.append(completed.stdout.strip())
        if completed.returncode != 0:
            break
    try:
        mounted = mountpoint.is_mount()
    except OSError:
        mounted = True
    return {
        "attempts": attempts,
        "exit_code": 0 if not mounted else (last_returncode or 1),
        "output": "\n".join(outputs),
        "mounted": mounted,
        "error": "" if not mounted else "mountpoint remains mounted",
    }


def cleanup_isolated_agent_dependency_views(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for view in reversed(metadata.get("isolated_dependency_views") or []):
        mountpoint = str(view.get("mountpoint") or "").strip()
        if not mountpoint:
            continue
        unmount = unmount_dependency_view_layers(Path(mountpoint))
        cleanup = None
        cleanup_error = str(unmount.get("error") or "")
        root = str(view.get("root") or "").strip()
        if not unmount["mounted"] and root:
            cleanup = subprocess.run(
                ["sudo", "-n", "rm", "-rf", "--one-file-system", "--", root],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            if cleanup.returncode != 0:
                cleanup_error = cleanup.stdout.strip() or f"rm exited {cleanup.returncode}"
        results.append(
            {
                "mountpoint": mountpoint,
                "exit_code": unmount["exit_code"],
                "output": unmount["output"],
                "unmount_attempts": unmount["attempts"],
                "cleanup_exit_code": cleanup.returncode if cleanup is not None else None,
                "cleanup_output": cleanup.stdout.strip() if cleanup is not None else "",
                "permission_cleanup_exit_code": None,
                "permission_cleanup_output": "",
                "cleanup_error": cleanup_error,
            }
        )
    return results


def case_debug_dependency_mounts(
    task_id: str,
    run_dir: Path,
    workspace_scope: str,
) -> tuple[list[str], list[str], dict[str, Any]]:
    """Mount matrix-warmed dependency envs for agent-visible public debugging.

    The case-base image contains shims that route python/pytest/node/etc. based
    on ECOSYNC_REPO_ENVS. Some matrix runners create venvs dynamically in a
    shared cache instead of baking them into case-base.
    Mounting those venvs here lets agents run visible repo tests without
    exposing evaluator-only hidden test patches or oracle metadata.
    """
    profiles_path = task_dir(task_id) / "environment" / "test_profiles.yaml"
    cache_root = task_cache_root(task_id)
    runner_mounts = []
    seen_runner_mounts: set[tuple[Path, str]] = set()
    for mount in [*task_runner_volume_mounts(task_id), *task_compose_volume_mounts(task_id)]:
        if mount in seen_runner_mounts:
            continue
        runner_mounts.append(mount)
        seen_runner_mounts.add(mount)
    exposed_runner_mounts = agent_safe_runner_mounts([
        mount
        for mount in runner_mounts
        if mount[1].rstrip("/") != "/yarn-cache"
    ])
    cache_roots = []
    if cache_root is not None and path_exists(cache_root):
        cache_roots.append(cache_root)
    cache_roots.extend(host for host, _container in runner_mounts if isinstance(host, Path))
    def writable_node_modules_cache_base() -> Path | None:
        candidates: list[Path] = []
        for root in cache_roots:
            candidates.append(root)
            candidates.append(root / "tmp")
        candidates.append(run_dir.parents[1] if len(run_dir.parents) > 1 else run_dir.parent)
        for root in candidates:
            try:
                root.mkdir(parents=True, exist_ok=True)
                if os.access(root, os.W_OK):
                    return root / "node_modules-cache"
            except OSError:
                continue
        return None

    node_modules_cache_base: Path | None = None
    elastic_package_cache_base: Path | None = None
    elasticsearch_build_cache: Path | None = None

    def writable_elastic_package_cache_base() -> Path | None:
        candidates: list[Path] = []
        for root in cache_roots:
            candidates.extend([root, root / "tmp"])
        candidates.append(run_dir.parents[1] if len(run_dir.parents) > 1 else run_dir.parent)
        for root in candidates:
            target = root / "elastic-package-data"
            try:
                target.mkdir(parents=True, exist_ok=True)
                if os.access(target, os.W_OK):
                    return root
            except OSError:
                continue
        return None

    def writable_elasticsearch_build_cache() -> Path | None:
        candidates: list[Path] = []
        for root in cache_roots:
            candidates.extend([root, root / "tmp"])
        candidates.append(run_dir.parents[1] if len(run_dir.parents) > 1 else run_dir.parent)
        for root in candidates:
            target = persistent_elasticsearch_build_path(root)
            try:
                target.mkdir(parents=True, exist_ok=True)
                if os.access(target, os.W_OK):
                    return target
            except OSError:
                continue
        return None

    metadata: dict[str, Any] = {
        "enabled": False,
        "cache_root": str(cache_root) if cache_root else None,
        "runner_cache_mounts": [
            {"host": str(host), "container": container} for host, container in runner_mounts
        ],
        "omitted_runner_cache_mounts": [
            {
                "host": str(host),
                "container": container,
                "reason": "package archive cache may contain evaluator-only dependency versions",
            }
            for host, container in runner_mounts
            if (host, container) not in exposed_runner_mounts
        ],
        "repo_envs": None,
        "mounts": [],
        "rows": [],
        "missing_profiles": [],
        "isolated_dependency_views": [],
    }
    if not profiles_path.exists():
        return [], [], metadata
    profiles = load_yaml(profiles_path) or {}
    profiles = profiles.get("profiles") if isinstance(profiles, dict) else None
    if not isinstance(profiles, dict):
        return [], [], metadata

    deps_dir = run_dir / "case_debug_deps"
    deps_dir.mkdir(parents=True, exist_ok=True)
    env_dir = deps_dir / "env"
    env_dir.mkdir(parents=True, exist_ok=True)
    shim_dir = deps_dir / "shims"
    repo_envs_path = deps_dir / "repo-envs.tsv"
    repo_env_files_path = deps_dir / "repo-env-files.tsv"
    mounts: list[str] = []
    env: list[str] = []
    rows: dict[str, tuple[str, str]] = {}
    cache_routes: dict[str, str] = {}
    profile_routes: dict[str, str] = {}
    mounted: list[dict[str, str]] = []
    missing: list[dict[str, str]] = []
    composer_profiles: list[tuple[str, str]] = []
    agent_workspace = agent_workspace_path(run_dir)
    visible_paths = visible_workspace_repo_paths(run_dir)
    if (
        cache_root is not None
        and agent_workspace is not None
        and (agent_workspace / "repos/ipfs/boxo/go.mod").exists()
        and (agent_workspace / "repos/ipfs/kubo/go.mod").exists()
    ):
        agent_go_mod_cache = cache_root / "agent-go-mod"
        agent_go_build_cache = cache_root / "agent-go-build"
        for cache_path in (agent_go_mod_cache, agent_go_build_cache):
            cache_path.mkdir(parents=True, exist_ok=True)
            cache_path.chmod(0o777)
        mounts.extend(
            [
                f"{agent_go_mod_cache}:/cache/agent-go-mod:rw",
                f"{agent_go_build_cache}:/cache/agent-go-build:rw",
            ]
        )
        env.extend(
            [
                "ECOSYNC_AGENT_GOMODCACHE=/cache/agent-go-mod",
                "ECOSYNC_CASE_GOCACHE=/cache/agent-go-build",
            ]
        )
        metadata["agent_go_cache"] = {
            "module_cache": str(agent_go_mod_cache),
            "build_cache": str(agent_go_build_cache),
        }
    profile_envs = task_profile_debug_envs(task_id, workspace_scope)
    postgres_prestart = task_requires_postgres_prestart(task_id)
    env.append(f"ECOSYNC_CASE_ENV_POSTGRES={'1' if postgres_prestart else '0'}")
    metadata["postgres_prestart"] = postgres_prestart
    postgres_sidecar = task_postgres_sidecar(task_id)
    env.append(f"ECOSYNC_CASE_ENV_POSTGRES_SIDECAR={'1' if postgres_sidecar else '0'}")
    if postgres_sidecar:
        env.extend(
            [
                f"ECOSYNC_CASE_ENV_POSTGRES_IMAGE={postgres_sidecar['image']}",
                f"ECOSYNC_CASE_ENV_POSTGRES_USER={postgres_sidecar['user']}",
                f"ECOSYNC_CASE_ENV_POSTGRES_PASSWORD={postgres_sidecar['password']}",
                f"ECOSYNC_CASE_ENV_POSTGRES_DB={postgres_sidecar['database']}",
                f"ECOSYNC_CASE_ENV_POSTGRES_TRUST={'1' if postgres_sidecar['trust'] else '0'}",
                f"ECOSYNC_CASE_ENV_POSTGRES_PRISMA={'1' if postgres_sidecar['prisma'] else '0'}",
                "ECOSYNC_CASE_ENV_POSTGRES_EXTRA_DATABASES="
                + ",".join(postgres_sidecar["extra_databases"]),
                f"ECOSYNC_CASE_ENV_POSTGRES_SETTLE_SECONDS={postgres_sidecar['settle_seconds']}",
            ]
        )
    metadata["postgres_sidecar"] = postgres_sidecar
    mongodb_sidecar = task_mongodb_sidecar(task_id)
    env.append(f"ECOSYNC_CASE_ENV_MONGODB={'1' if mongodb_sidecar else '0'}")
    if mongodb_sidecar:
        mongodb_image, mongodb_settle_seconds = mongodb_sidecar
        env.extend(
            [
                f"ECOSYNC_CASE_ENV_MONGODB_IMAGE={mongodb_image}",
                f"ECOSYNC_CASE_ENV_MONGODB_SETTLE_SECONDS={mongodb_settle_seconds}",
            ]
        )
        metadata["mongodb_sidecar"] = {
            "image": mongodb_image,
            "settle_seconds": mongodb_settle_seconds,
        }
    else:
        metadata["mongodb_sidecar"] = None
    support_envs, support_files = materialize_agent_workspace_support(
        task_id,
        run_dir,
        profiles,
    )
    for profile_name, support_env in support_envs.items():
        profile_env = profile_envs.setdefault(profile_name, {})
        for name, value in support_env.items():
            if name == "PYTHONPATH" and profile_env.get(name):
                value = ":".join(
                    dict.fromkeys(
                        [
                            *value.split(":"),
                            *profile_env[name].split(":"),
                        ]
                    )
                )
            profile_env[name] = value
    if support_files:
        metadata["workspace_support_files"] = support_files
    image_repo_envs: dict[str, str] = {}
    image_repo_envs_path = task_dir(task_id) / "environment" / "case_env" / "repo_envs.tsv"
    if image_repo_envs_path.exists():
        for line in image_repo_envs_path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or "\t" not in line:
                continue
            workdir, deps_root = line.split("\t", 1)
            if workdir.strip() and deps_root.strip().startswith("/"):
                image_repo_envs[workdir.strip().rstrip("/")] = deps_root.strip().rstrip("/")

    if cache_root is not None and (cache_root / "cargo-home").exists():
        mounts.append(f"{cache_root}:/cache:rw")
        env.extend(
            [
                "CARGO_HOME=/cache/cargo-home",
                *baked_rust_toolchain_env(),
            ]
        )
        metadata.setdefault("cache_mounts", []).append(
            {"kind": "rust-cache", "host": str(cache_root), "container": "/cache", "mode": "rw"}
        )

    if cache_root is not None:
        flutter_bin_cache = persistent_flutter_bin_cache_path(cache_root)
        if flutter_bin_cache is not None:
            flutter_container_cache = "/workspace/repos/flutter/flutter/bin/cache"
            mounts.append(f"{flutter_bin_cache}:{flutter_container_cache}:rw")
            metadata.setdefault("cache_mounts", []).append(
                {
                    "kind": "flutter-bin-cache",
                    "host": str(flutter_bin_cache),
                    "container": flutter_container_cache,
                    "mode": "rw",
                }
            )

        maven_cache = cache_root / "m2"
        runner_has_maven_cache = any(
            container.rstrip("/") == "/m2" for _host, container in runner_mounts
        )
        if maven_cache.is_dir() and not runner_has_maven_cache:
            agent_maven_cache = prepare_agent_maven_cache(maven_cache)
            mounts.append(f"{agent_maven_cache}:/m2:rw")
            metadata.setdefault("cache_mounts", []).append(
                {
                    "kind": "maven-cache",
                    "host": str(agent_maven_cache),
                    "container": "/m2",
                    "mode": "rw",
                }
            )

        if not runner_mounts:
            nuget_test_packages = persistent_nuget_test_packages_path(cache_root)
            if nuget_test_packages is not None:
                nuget_test_packages.mkdir(parents=True, exist_ok=True)
                test_packages_mount = (
                    f"{nuget_test_packages}:/ecosync-scratch/tmp/test-packages:rw"
                )
                if test_packages_mount not in mounts:
                    mounts.append(test_packages_mount)
                    metadata.setdefault("cache_mounts", []).append(
                        {
                            "kind": "nuget-test-packages-cache",
                            "host": str(nuget_test_packages),
                            "container": "/ecosync-scratch/tmp/test-packages",
                            "mode": "rw",
                        }
                    )

    for host, container in exposed_runner_mounts:
        mode = "rw"
        mount = f"{host}:{container}:{mode}"
        hide_named_python_venvs = (
            container.rstrip("/") == "/python-cache"
            and isinstance(host, Path)
            and (host / "venvs").exists()
        )
        if not hide_named_python_venvs and mount not in mounts:
            mounts.append(mount)
        container_root = container.rstrip("/")
        container_name = Path(container_root).name.lower()
        if container_root == "/cache":
            env.extend(
                [
                    "PNPM_STORE_DIR=/cache/pnpm-store",
                    "pnpm_config_store_dir=/cache/pnpm-store",
                    "npm_config_store_dir=/cache/pnpm-store",
                    "NPM_CONFIG_STORE_DIR=/cache/pnpm-store",
                    "COREPACK_HOME=/cache/corepack",
                    "XDG_CACHE_HOME=/cache/xdg",
                    "XDG_DATA_HOME=/cache/xdg",
                ]
            )
        if container_root == "/yarn-cache":
            env.extend(
                [
                    "YARN_ENABLE_GLOBAL_CACHE=true",
                    "YARN_GLOBAL_FOLDER=/yarn-cache/global",
                    "YARN_CACHE_FOLDER=/yarn-cache/cache",
                ]
            )
        if "corepack" in container_name:
            env.append(f"COREPACK_HOME={container_root}")
        if "pnpm" in container_name:
            env.extend(
                [
                    f"PNPM_STORE_DIR={container_root}",
                    f"pnpm_config_store_dir={container_root}",
                    f"npm_config_store_dir={container_root}",
                    f"NPM_CONFIG_STORE_DIR={container_root}",
                ]
            )
        if "yarn" in container_name:
            env.extend(
                [
                    "YARN_ENABLE_GLOBAL_CACHE=true",
                    f"YARN_GLOBAL_FOLDER={container_root}/global",
                    f"YARN_CACHE_FOLDER={container_root}/cache",
                ]
            )
        if container_root == "/composer-cache":
            env.extend(
                [
                    "COMPOSER_HOME=/composer-cache/home",
                    "COMPOSER_CACHE_DIR=/composer-cache/cache",
                    "COMPOSER_ALLOW_SUPERUSER=1",
                    "COMPOSER_NO_INTERACTION=1",
                ]
            )
        if isinstance(host, Path) and not hide_named_python_venvs:
            env.extend(cache_env_for_container_root(host, container))
            nuget_test_packages = persistent_nuget_test_packages_path(host)
            if nuget_test_packages is not None:
                nuget_test_packages.mkdir(parents=True, exist_ok=True)
                test_packages_mount = f"{nuget_test_packages}:/ecosync-scratch/tmp/test-packages:rw"
                if test_packages_mount not in mounts:
                    mounts.append(test_packages_mount)
                    metadata.setdefault("cache_mounts", []).append(
                        {
                            "kind": "nuget-test-packages-cache",
                            "host": str(nuget_test_packages),
                            "container": "/ecosync-scratch/tmp/test-packages",
                            "mode": "rw",
                        }
                    )
        if not hide_named_python_venvs:
            metadata.setdefault("cache_mounts", []).append(
                {"kind": "runner-cache", "host": str(host), "container": container, "mode": mode}
            )

    for profile_name, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        workdir = profile.get("workdir")
        if not isinstance(workdir, str) or not workdir:
            continue
        normalized_workdir = workdir.rstrip("/")
        if (
            workspace_scope == "single-repo"
            and visible_paths
            and not any(
                normalized_workdir == visible
                or normalized_workdir.startswith(f"{visible}/")
                for visible in visible_paths
            )
        ):
            metadata.setdefault("omitted_profiles", []).append(
                {
                    "profile": str(profile_name),
                    "workdir": normalized_workdir,
                    "reason": "repository is outside the single-repo workspace",
                }
            )
            continue
        profile_routes.setdefault(workdir.rstrip("/"), str(profile_name))
        runtime_slug = f"runtime-{len(mounted):03d}"
        test_files = [str(item) for item in (profile.get("test_files") or []) if str(item).strip()]
        command_text = "\n".join(
            str(profile.get(name) or "")
            for name in ("command", "public_command", "public_smoke_command")
        )
        uses_python = any(item.endswith(".py") for item in test_files) or re.search(
            r"\b(python(?:3)?|pytest|tox|nox)\b",
            command_text,
        ) is not None
        uses_node = any(
            item.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")) for item in test_files
        ) or re.search(r"\b(npm|npx|yarn|pnpm|jest|vitest|mocha|ava|tap)\b", command_text) is not None
        uses_composer = any(item.endswith(".php") for item in test_files) or re.search(
            r"\b(composer|phpunit)\b",
            command_text,
        ) is not None or workdir.rstrip("/") == "repos/nextcloud/spreed"
        if uses_composer:
            composer_profiles.append((str(profile_name), workdir.rstrip("/")))
        evaluator_node_mount_added = False
        if uses_node and agent_workspace is not None:
            for item in evaluator_node_modules_mounts(
                task_id,
                str(profile_name),
                agent_workspace,
            ):
                if item.get("kind") != "persistent":
                    continue
                host = str(item.get("host") or "").strip()
                container = str(item.get("container") or "").strip()
                if not host or not container:
                    continue
                requested_host = Path(host)
                authoritative_host, legacy_reuse = agent_node_modules_authoritative_cache(
                    requested_host
                )
                authoritative_fingerprint = str(item.get("fingerprint") or "") or None
                if legacy_reuse is not None:
                    authoritative_fingerprint = authoritative_host.parents[1].name
                if not node_modules_has_dependency_entries(authoritative_host):
                    metadata.setdefault("omitted_evaluator_node_modules_mounts", []).append(
                        {
                            "host": str(authoritative_host),
                            "requested_host": str(requested_host),
                            "container": container,
                            "reason": "cache contains runtime directories but no dependency packages",
                        }
                    )
                    continue
                agent_host, isolation = isolated_agent_dependency_view(
                    authoritative_host,
                    run_dir,
                    str(profile_name),
                    authoritative_fingerprint,
                )
                if legacy_reuse is not None:
                    isolation["legacy_cache_reuse"] = legacy_reuse
                metadata["isolated_dependency_views"].append(isolation)
                mount = f"{agent_host}:{container}:rw"
                if mount not in mounts:
                    mounts.append(mount)
                    metadata.setdefault("cache_mounts", []).append(
                        {
                            "kind": "isolated-evaluator-node-modules-cache",
                            "host": str(agent_host),
                            "authoritative_host": str(authoritative_host),
                            "requested_authoritative_host": str(requested_host),
                            "container": container,
                            "mode": "rw",
                            "fingerprint": item.get("fingerprint"),
                            "authoritative_fingerprint": authoritative_fingerprint,
                            "strategy": isolation["strategy"],
                            "legacy_cache_reuse": legacy_reuse,
                        }
                    )
                evaluator_node_mount_added = True
        # Keep node_modules cache mounts opt-in. Mounting node_modules itself can
        # put workspace packages and the root install on different filesystems,
        # which breaks Yarn/PNPM hardlink/link operations in monorepos.
        node_modules_paths = profile_node_modules_paths(profile)
        if uses_node and not evaluator_node_mount_added and cache_roots and node_modules_paths:
            if node_modules_cache_base is None:
                node_modules_cache_base = writable_node_modules_cache_base()
            if node_modules_cache_base is None:
                continue
            root_node_modules_host = persistent_node_modules_path(
                node_modules_cache_base,
                task_id,
                str(profile_name),
            )
            for relative_path in node_modules_paths:
                if relative_path == ".":
                    node_modules_host = root_node_modules_host
                    node_modules_container = f"/workspace/{workdir.rstrip('/')}/node_modules"
                else:
                    node_modules_host = (
                        root_node_modules_host.parent
                        / "workspace-node-modules"
                        / safe_slug(relative_path)
                        / "node_modules"
                    )
                    node_modules_container = (
                        f"/workspace/{workdir.rstrip('/')}/{relative_path}/node_modules"
                    )
                node_modules_host.mkdir(parents=True, exist_ok=True)
                mount = f"{node_modules_host}:{node_modules_container}:rw"
                if mount not in mounts:
                    mounts.append(mount)
                    metadata.setdefault("cache_mounts", []).append(
                        {
                            "kind": "node-modules-cache",
                            "host": str(node_modules_host),
                            "container": node_modules_container,
                            "mode": "rw",
                        }
                    )
        if uses_composer and cache_root is not None and profile_persists_vendor(profile):
            vendor_host = persistent_vendor_path(
                cache_root / "vendor-cache",
                task_id,
                str(profile_name),
            )
            vendor_host.mkdir(parents=True, exist_ok=True)
            vendor_container = f"/workspace/{workdir.rstrip('/')}/vendor"
            mount = f"{vendor_host}:{vendor_container}:rw"
            if mount not in mounts:
                mounts.append(mount)
                metadata.setdefault("cache_mounts", []).append(
                    {
                        "kind": "composer-vendor-cache",
                        "host": str(vendor_host),
                        "container": vendor_container,
                        "mode": "rw",
                    }
                )
        uses_uv_project = re.search(r"\buv\s+(run|sync)\b", command_text) is not None
        host_venv = find_profile_venv(cache_roots, str(profile_name), profile)
        persistent_uv_venv = False
        persistent_uv_venv_warmed = False
        if host_venv is None and uses_uv_project:
            host_venv = persistent_uv_venv_path(task_id, str(profile_name), run_dir)
            persistent_uv_venv = host_venv is not None
        python_entrypoint = host_venv / "bin" / "python" if host_venv else None
        persistent_uv_venv_warmed = bool(
            python_entrypoint and (python_entrypoint.exists() or python_entrypoint.is_symlink())
        )
        container_root = f"/ecosync-debug-deps/runtime/{runtime_slug}"
        image_container_root = image_repo_envs.get(workdir.rstrip("/"))
        if host_venv and image_container_root and venv_references_container_root(host_venv, image_container_root):
            container_root = image_container_root
        env_file_container = ""
        profile_env = dict(profile_envs.get(str(profile_name)) or {})
        profile_env.update(
            profile_cache_env_from_runner_mounts(
                str(profile_name),
                profile,
                exposed_runner_mounts,
            )
        )
        if uses_node:
            yarn_version = repo_yarn_version(agent_workspace, workdir)
            if yarn_version:
                profile_env["ECOSYNC_YARN_VERSION"] = yarn_version
        repo_name = Path(workdir.rstrip("/")).name
        if repo_name == "elasticsearch":
            if elasticsearch_build_cache is None:
                elasticsearch_build_cache = writable_elasticsearch_build_cache()
            if elasticsearch_build_cache is None:
                raise RuntimeError(
                    "no writable persistent Elasticsearch build cache for "
                    f"task={task_id} profile={profile_name}"
                )
            elasticsearch_build_container = "/ecosync-debug-deps/elasticsearch-build"
            elasticsearch_build_mount = (
                f"{elasticsearch_build_cache}:{elasticsearch_build_container}:rw"
            )
            if elasticsearch_build_mount not in mounts:
                mounts.append(elasticsearch_build_mount)
                metadata.setdefault("cache_mounts", []).append(
                    {
                        "kind": "elasticsearch-build-cache",
                        "host": str(elasticsearch_build_cache),
                        "container": elasticsearch_build_container,
                        "mode": "rw",
                    }
                )
            profile_env.setdefault(
                "ECOSYNC_ELASTICSEARCH_BUILD_CACHE",
                elasticsearch_build_container,
            )
        if repo_name == "elastic-package":
            if elastic_package_cache_base is None:
                elastic_package_cache_base = writable_elastic_package_cache_base()
            if elastic_package_cache_base is None:
                raise RuntimeError(
                    "no writable persistent elastic-package data cache for "
                    f"task={task_id} profile={profile_name}"
                )
            elastic_package_host = persistent_elastic_package_data_path(elastic_package_cache_base, task_id)
            elastic_package_host.mkdir(parents=True, exist_ok=True)
            elastic_package_container = "/ecosync-debug-deps/elastic-package-data"
            elastic_package_mount = f"{elastic_package_host}:{elastic_package_container}:rw"
            if elastic_package_mount not in mounts:
                mounts.append(elastic_package_mount)
                metadata.setdefault("cache_mounts", []).append(
                    {
                        "kind": "elastic-package-data-cache",
                        "host": str(elastic_package_host),
                        "container": elastic_package_container,
                        "mode": "rw",
                    }
                )
            profile_env.setdefault("ELASTIC_PACKAGE_DATA_HOME", elastic_package_container)
        if host_venv is not None and uses_uv_project:
            profile_env["UV_PROJECT_ENVIRONMENT"] = f"{container_root}/venv"
            if persistent_uv_venv_warmed:
                profile_env["UV_NO_SYNC"] = "1"
        if host_venv is not None and cache_root is not None:
            try:
                profile_cache_subdir = host_venv.parents[1].relative_to(cache_root)
            except (IndexError, ValueError):
                profile_cache_subdir = Path(".")
            if profile_cache_subdir != Path("."):
                cache_prefix = f"/cache/{profile_cache_subdir.as_posix().strip('/')}"
                cache_routes[workdir.rstrip("/")] = cache_prefix
                profile_env = {
                    name: value.replace("/cache/", f"{cache_prefix}/")
                    for name, value in profile_env.items()
                }
        if profile_env:
            env_file = env_dir / f"{runtime_slug}.env"
            env_file.write_text(profile_env_file_text(profile_env), encoding="utf-8")
            env_file_container = f"/ecosync-debug-deps/env/{runtime_slug}.env"
            if image_container_root:
                rows.setdefault(
                    workdir.rstrip("/"),
                    (image_container_root, env_file_container),
                )
        # Some profiles run from a subdirectory or a sibling repository that
        # shares the case image's single dependency environment. Give every
        # tested profile an explicit route so tool shims select that
        # environment from the profile's actual working directory.
        if not image_container_root:
            image_roots = set(image_repo_envs.values())
            if len(image_roots) == 1:
                image_container_root = next(iter(image_roots))
        if image_container_root:
            rows.setdefault(
                workdir.rstrip("/"),
                (image_container_root, env_file_container),
            )
            for cargo_registry_host, cargo_registry_container in exposed_runner_mounts:
                if cargo_registry_container.rstrip("/") != "/usr/local/cargo/registry":
                    continue
                cargo_registry_mount = (
                    f"{cargo_registry_host}:"
                    f"{image_container_root}/usr-local-cargo/registry:rw"
                )
                if cargo_registry_mount not in mounts:
                    mounts.append(cargo_registry_mount)
                    metadata.setdefault("cache_mounts", []).append(
                        {
                            "kind": "image-rust-registry-cache",
                            "host": str(cargo_registry_host),
                            "container": f"{image_container_root}/usr-local-cargo/registry",
                            "mode": "rw",
                        }
                    )
        if host_venv and host_venv.exists():
            venv_mode = "rw" if persistent_uv_venv else "ro"
            mounts.append(f"{host_venv}:{container_root}/venv:{venv_mode}")
            # A profile-specific matrix venv is more precise than the generic
            # dependency root baked into the case image. Route this repository
            # through the mounted venv even when an image row was added first.
            rows[workdir.rstrip("/")] = (container_root, env_file_container)
            mounted.append(
                {
                    "profile": str(profile_name),
                    "workdir": workdir.rstrip("/"),
                    "host_venv": str(host_venv),
                    "container_root": container_root,
                    "env_file": env_file_container,
                    "env": profile_env,
                    "mode": venv_mode,
                    "persistent_uv_venv": persistent_uv_venv,
                    "persistent_uv_venv_warmed": persistent_uv_venv_warmed,
                }
            )
        else:
            if cache_root is not None and cache_root.exists() and not runner_mounts:
                fallback_root = f"/ecosync-debug-cache/{safe_slug(task_id)}"
                mount = f"{cache_root}:{fallback_root}:rw"
                if mount not in mounts:
                    mounts.append(mount)
                env.extend(cache_env_for_container_root(cache_root, fallback_root))
                metadata.setdefault("cache_mounts", []).append(
                    {"kind": "generic-cache", "host": str(cache_root), "container": fallback_root, "mode": "rw"}
                )
            if uses_python:
                missing.append(
                    {
                        "profile": str(profile_name),
                        "workdir": workdir.rstrip("/"),
                        "expected_host_venv": str(profile_cache_venv(cache_root, str(profile_name), profile))
                        if cache_root is not None
                        else None,
                    }
                )
        if uses_node and not evaluator_node_mount_added:
            explicit_runner_caches = list(
                dict.fromkeys(
                    [
                        *(
                            cache_root / "node" / f"{repo_name}-node_modules"
                            for cache_root in cache_roots
                        ),
                        *(
                            cache_root / f"{repo_name}-node_modules"
                            for cache_root in cache_roots
                        ),
                    ]
                )
            )
            node_modules_candidates = explicit_runner_caches
            if os.environ.get("ECOSYNC_ENABLE_NODE_MODULES_CACHE", "0") == "1":
                node_modules_candidates.extend(
                    cache_root / "node_modules" for cache_root in cache_roots
                )
            for node_modules_cache in node_modules_candidates:
                if not path_exists(node_modules_cache):
                    continue
                mount = f"{node_modules_cache}:/workspace/{workdir.rstrip('/')}/node_modules:rw"
                if mount not in mounts:
                    mounts.append(mount)
                    metadata.setdefault("cache_mounts", []).append(
                        {
                            "kind": "node-modules-cache",
                            "host": str(node_modules_cache),
                            "container": f"/workspace/{workdir.rstrip('/')}/node_modules",
                            "mode": "rw",
                        }
                    )
                break

    add_shared_uv_python_cache(mounts, env, metadata)

    has_composer_cache_mount = any(
        split_mount_spec(mount)[1] == "/composer-cache" for mount in mounts
    )
    if composer_profiles and not has_composer_cache_mount:
        composer_root = shared_composer_cache_root()
        composer_home = composer_root / "home"
        composer_cache = composer_root / "cache"
        composer_vendor = composer_root / "vendor"
        for path in (composer_home, composer_cache, composer_vendor):
            path.mkdir(parents=True, exist_ok=True)
        mounts.append(f"{composer_root}:/composer-cache:rw")
        env[:] = [
            item
            for item in env
            if not item.startswith(("COMPOSER_HOME=", "COMPOSER_CACHE_DIR="))
        ]
        env.extend(
            [
                "COMPOSER_HOME=/composer-cache/home",
                "COMPOSER_CACHE_DIR=/composer-cache/cache",
                "COMPOSER_ALLOW_SUPERUSER=1",
                "COMPOSER_NO_INTERACTION=1",
            ]
        )
        metadata.setdefault("cache_mounts", []).append(
            {
                "kind": "shared-composer-cache",
                "host": str(composer_root),
                "container": "/composer-cache",
                "mode": "rw",
            }
        )
        mounted_containers = {spec.split(":", 2)[1] for spec in mounts if spec.count(":") >= 2}
        for profile_name, workdir in composer_profiles:
            vendor_container = f"/workspace/{workdir}/vendor"
            vendor_host = persistent_vendor_path(
                composer_vendor,
                task_id,
                profile_name,
            )
            vendor_host.mkdir(parents=True, exist_ok=True)
            if vendor_container not in mounted_containers:
                mounts.append(f"{vendor_host}:{vendor_container}:rw")
                mounted_containers.add(vendor_container)
                metadata.setdefault("cache_mounts", []).append(
                    {
                        "kind": "composer-vendor-cache",
                        "host": str(vendor_host),
                        "container": vendor_container,
                        "mode": "rw",
                    }
                )
            if workdir != "repos/nextcloud/spreed":
                continue
            extra_mounts: list[tuple[Path, str, str]] = [
                (
                    vendor_host,
                    "/workspace/repos/nextcloud/server/apps/spreed/vendor",
                    "nextcloud-app-vendor-cache",
                )
            ]
            generated_root = vendor_host.parent / "generated" / "lib"
            for generated_name in ("Vendor", "autoload"):
                generated_host = generated_root / generated_name
                generated_host.mkdir(parents=True, exist_ok=True)
                extra_mounts.extend(
                    [
                        (
                            generated_host,
                            f"/workspace/repos/nextcloud/spreed/lib/{generated_name}",
                            "nextcloud-spreed-generated-cache",
                        ),
                        (
                            generated_host,
                            f"/workspace/repos/nextcloud/server/apps/spreed/lib/{generated_name}",
                            "nextcloud-app-generated-cache",
                        ),
                    ]
                )
            for namespace in (
                "csfixer",
                "mozart",
                "openapi-extractor",
                "phpunit",
                "psalm",
                "rector",
            ):
                namespace_vendor = vendor_host.parent / "vendor-bin" / namespace / "vendor"
                namespace_vendor.mkdir(parents=True, exist_ok=True)
                extra_mounts.extend(
                    [
                        (
                            namespace_vendor,
                            f"/workspace/repos/nextcloud/spreed/vendor-bin/{namespace}/vendor",
                            "composer-vendor-bin-cache",
                        ),
                        (
                            namespace_vendor,
                            f"/workspace/repos/nextcloud/server/apps/spreed/vendor-bin/{namespace}/vendor",
                            "nextcloud-app-vendor-bin-cache",
                        ),
                    ]
                )
            for host, container, kind in extra_mounts:
                if container in mounted_containers:
                    continue
                mounts.append(f"{host}:{container}:rw")
                mounted_containers.add(container)
                metadata.setdefault("cache_mounts", []).append(
                    {
                        "kind": kind,
                        "host": str(host),
                        "container": container,
                        "mode": "rw",
                    }
                )

    if image_repo_envs:
        for workdir in visible_paths:
            if workdir in image_repo_envs:
                rows.setdefault(workdir, (image_repo_envs[workdir], ""))
        metadata["image_repo_envs"] = {
            workdir: image_repo_envs[workdir]
            for workdir in visible_paths
            if workdir in image_repo_envs
        }

    if rows:
        empty_env = env_dir / "empty.env"
        empty_env.write_text("", encoding="utf-8")
        empty_env_container = "/ecosync-debug-deps/env/empty.env"
        public_rows = {
            workdir: rows[workdir]
            for workdir in rows
            if any(
                workdir == visible or workdir.startswith(f"{visible}/")
                for visible in visible_paths
            )
        }
        if public_rows:
            write_case_debug_shims(shim_dir)
            mounts.append(f"{shim_dir}:/ecosync-debug-deps/shims:ro")
            env.append("ECOSYNC_DEBUG_SHIMS=/ecosync-debug-deps/shims")
            runtime_root = os.environ.get("ECOSYNC_AGENT_RUNTIME_ROOT", "").strip()
            shared_uv = Path(runtime_root) / "shared-tools" / "uv" if runtime_root else None
            host_uv = str(shared_uv) if shared_uv and shared_uv.is_file() else shutil.which("uv")
            if host_uv and Path(host_uv).is_file():
                mounts.append(f"{Path(host_uv).resolve()}:/ecosync-host-tools/uv:ro")
                metadata.setdefault("cache_mounts", []).append(
                    {
                        "kind": "shared-uv-tool",
                        "host": str(Path(host_uv).resolve()),
                        "container": "/ecosync-host-tools/uv",
                        "mode": "ro",
                    }
                )
            mounts.append(f"{env_dir}:/ecosync-debug-deps/env:ro")
            repo_env_files_path.write_text(
                "".join(
                    f"{workdir}\t{root}\t{env_file or empty_env_container}"
                    f"\t{cache_routes.get(workdir) or '-'}"
                    f"\t{profile_routes.get(workdir) or '-'}\n"
                    for workdir, (root, env_file) in sorted(public_rows.items())
                ),
                encoding="utf-8",
            )
            mounts.append(f"{repo_env_files_path}:/ecosync-debug-deps/repo-env-files.tsv:ro")
            env.append("ECOSYNC_REPO_ENV_FILES=/ecosync-debug-deps/repo-env-files.tsv")
            repo_envs_path.write_text(
                "".join(
                    f"{workdir}\t{root}\n"
                    for workdir, (root, _env_file) in sorted(public_rows.items())
                ),
                encoding="utf-8",
            )
            mounts.append(f"{repo_envs_path}:/ecosync-debug-deps/repo-envs.tsv:ro")
            env.append("ECOSYNC_REPO_ENVS=/ecosync-debug-deps/repo-envs.tsv")
    env.extend(task_debug_env_from_runner(task_id))
    shared_node_tools = shared_node_tool_cache_root()
    has_complete_runner_corepack = any(
        corepack_cache_has_package_manager(host, container)
        for host, container in exposed_runner_mounts
    )
    if (shared_node_tools / "corepack").exists() and not has_complete_runner_corepack:
        mounts.append(f"{shared_node_tools}:/ecosync-node-tool-cache:rw")
        env[:] = [item for item in env if not item.startswith("COREPACK_HOME=")]
        env.append("COREPACK_HOME=/ecosync-node-tool-cache/corepack")
        metadata.setdefault("cache_mounts", []).append(
            {
                "kind": "shared-node-tool-cache",
                "host": str(shared_node_tools),
                "container": "/ecosync-node-tool-cache",
                "mode": "rw",
            }
        )
    if not rows and not mounts and not env:
        metadata["missing_profiles"] = missing
        return [], [], metadata
    mounts = dedupe_mount_specs(mounts)
    metadata["cache_mounts"] = dedupe_cache_mount_metadata(metadata.get("cache_mounts") or [])
    metadata.update(
        {
            "enabled": True,
            "repo_envs": str(repo_envs_path),
            "mounts": mounts,
            "rows": mounted,
            "missing_profiles": missing,
        }
    )
    return mounts, env, metadata


def remove_workspaces(run_dir: Path, *, include_workspaces: bool = True, include_scratch: bool = True) -> list[str]:
    cleanup_images = (
        "ecosyncbench/base/android-sdk:36",
        "ecosyncbench/base/dotnet-sdk:8.0.100",
        "ecosyncbench/base/node:22-bookworm-slim",
        "ecosyncbench/base/python:3.14-slim",
        "ecosyncbench/deps/home-assistant-core-py:beaea2d99806",
        "ecosyncbench/deps/home-assistant-supervisor-py:2c6253e4b664",
        "ghcr.io/code-tmp/wideswe/agents/claude-code:2.1.139",
        "ghcr.io/code-tmp/wideswe/agents/codex:0.147.0",
    )

    def docker_image_present(image: str) -> bool:
        present = subprocess.run(
            ["docker", "image", "inspect", image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return present.returncode == 0

    def docker_delete_contents(path: Path) -> bool:
        for image in cleanup_images:
            if not docker_image_present(image):
                continue
            proc = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{path}:/cleanup",
                    "--entrypoint",
                    "sh",
                    image,
                    "-c",
                    "find /cleanup -mindepth 1 -maxdepth 1 -xdev -exec rm -rf -- {} +",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if proc.returncode == 0:
                return True
        return False

    def docker_chown(path: Path) -> None:
        uid = os.getuid()
        gid = os.getgid()
        for image in cleanup_images:
            if not docker_image_present(image):
                continue
            proc = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{path}:/cleanup",
                    "--entrypoint",
                    "sh",
                    image,
                    "-c",
                    f"chown -R {uid}:{gid} /cleanup",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if proc.returncode == 0:
                return
        raise PermissionError(f"could not chown root-owned workspace: {path}")

    def make_writable_and_retry(func: Any, path: str, exc_info: Any) -> None:
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
        if func in {os.unlink, os.remove, os.rmdir} or getattr(func, "__name__", "") in {
            "unlink",
            "remove",
            "rmdir",
        }:
            func(path)
            return
        raise exc_info[1]

    removed = []
    workspace_paths = []
    run_metadata = load_json_if_exists(run_dir / "run.json")
    if include_workspaces:
        dependency_path = run_dir / "cross_repo_dependencies"
        if dependency_path.exists() or dependency_path.is_symlink():
            workspace_paths.append(dependency_path)
        for key in ("agent_workspace", "evaluator_workspace"):
            value = run_metadata.get(key)
            if value:
                path = Path(value)
                if path.exists() or path.is_symlink():
                    workspace_paths.append(path)
        workspace_paths.extend(
            path
            for pattern in (
                "agent_workspace",
                "agent_workspace_*",
                "agent_debug_workspace",
                "agent_debug_workspace_*",
                "evaluator_workspace",
                "evaluator_workspace_*",
                "test_proxy",
            )
            for path in run_dir.glob(pattern)
            if path.is_dir() or path.is_symlink()
        )
    if include_scratch:
        workspace_paths.extend(path for path in run_dir.glob("agent_scratch*") if path.is_dir())
    normalized_paths = {
        path.resolve(strict=False)
        for path in workspace_paths
    }
    for path in sorted(normalized_paths):
        if not path.exists() and not path.is_symlink():
            continue
        try:
            if path.is_symlink():
                path.unlink()
            else:
                shutil.rmtree(path, onerror=make_writable_and_retry)
        except PermissionError:
            if docker_delete_contents(path):
                try:
                    path.rmdir()
                except OSError:
                    shutil.rmtree(path, onerror=make_writable_and_retry)
            else:
                docker_chown(path)
                shutil.rmtree(path, onerror=make_writable_and_retry)
        except OSError:
            if docker_delete_contents(path):
                try:
                    path.rmdir()
                except OSError:
                    shutil.rmtree(path, onerror=make_writable_and_retry)
            else:
                raise
        removed.append(str(path))
    return removed


def smoke_workspace_cleanup_mode(keep_workspaces: str, agent_exit_code: int) -> str:
    if keep_workspaces == "never":
        return "all" if agent_exit_code == 0 else "workspace-only"
    if keep_workspaces == "failed":
        # The Claude session used for a true continuation lives in agent_scratch.
        # Keep it together with the dirty workspace when the agent is interrupted.
        return "all" if agent_exit_code == 0 else "none"
    return "none"


def should_evaluate_after_agent_exit(
    agent_exit_code: int,
    *,
    agent_timed_out: bool,
    evaluate_on_agent_error: bool,
) -> bool:
    if agent_exit_code == 0:
        return True
    if agent_exit_code == 124 and agent_timed_out:
        return True
    return evaluate_on_agent_error


def run(cmd: list[str], log: Path | None = None, *, env: dict[str, str] | None = None) -> int:
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            handle.write("\n$ " + " ".join(cmd) + "\n")
            handle.flush()
            proc = subprocess.run(cmd, cwd=REPO_ROOT, env=env, text=True, stdout=handle, stderr=subprocess.STDOUT)
            handle.write(f"\n[exit_code={proc.returncode}]\n")
            handle.flush()
    else:
        proc = subprocess.run(cmd, cwd=REPO_ROOT, env=env, text=True)
    return proc.returncode


def collect_agent_diff_artifacts(
    task_id: str,
    run_dir: Path,
    pipeline_log: Path,
    harness_env: dict[str, str],
    steps: list[dict[str, Any]],
) -> tuple[Path, int, bool]:
    """Collect the current workspace diff, including after interrupted agent runs."""
    diff_dir = run_dir / "diffs"
    agent_workspace = agent_workspace_path(run_dir)
    collect_cmd = [
        "python3",
        str(HARNESS),
        "collect-diff",
        "--task",
        task_id,
        "--workspace",
        str(agent_workspace or (run_dir / "agent_workspace")),
        "--output-dir",
        str(diff_dir),
    ]
    collect_started = time.time()
    diff_collection_exit_code = (
        run(collect_cmd, pipeline_log, env=harness_env)
        if agent_workspace is not None and agent_workspace.exists()
        else 2
    )
    steps.append(
        step_record(
            "collect-agent-diff",
            collect_cmd,
            collect_started,
            diff_collection_exit_code,
        )
    )
    diff_collection_ok = (
        diff_collection_exit_code == 0 and (diff_dir / "diffs.json").is_file()
    )
    return diff_dir, diff_collection_exit_code, diff_collection_ok


def run_capture(cmd: list[str], *, env: dict[str, str] | None = None) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=REPO_ROOT, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return proc.returncode, proc.stdout


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def append_completed_step(log: Path, cmd: list[str], output: str, exit_code: int) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write("\n$ " + " ".join(cmd) + "\n")
        handle.write(output)
        if output and not output.endswith("\n"):
            handle.write("\n")
        handle.write(f"\n[exit_code={exit_code}]\n")


def step_record(name: str, cmd: list[str], started_at: float, exit_code: int) -> dict[str, Any]:
    finished_at = time.time()
    return {
        "name": name,
        "command": cmd,
        "exit_code": exit_code,
        "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at)),
        "finished_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(finished_at)),
        "duration_sec": round(finished_at - started_at, 3),
    }


def duration_by_step(steps: list[dict[str, Any]], name: str) -> float | None:
    for step in steps:
        if step.get("name") == name:
            value = step.get("duration_sec")
            return float(value) if value is not None else None
    return None


def pipeline_timing(started: float, finished_at: float, steps: list[dict[str, Any]], cleanup_sec: float | None = None) -> dict[str, Any]:
    timing: dict[str, Any] = {
        "total_sec": round(finished_at - started, 3),
        "prepare_agent_run_sec": duration_by_step(steps, "prepare-agent-run"),
        "run_agent_sec": duration_by_step(steps, "run-agent"),
        "evaluate_agent_sec": duration_by_step(steps, "evaluate-agent"),
    }
    if cleanup_sec is not None:
        timing["workspace_cleanup_sec"] = round(cleanup_sec, 3)
    return timing


def write_result_timing(
    result_file: Path,
    *,
    timing: dict[str, Any],
    started_at_utc: str,
    finished_at_utc: str,
    agent_profile: str | None,
    agent_id: str,
    model_id: str,
    run_id: str,
    agent_environment: str,
    sandbox_image: str | None,
    agent_exit_code: int,
    agent_timed_out: bool,
    run_valid: bool,
    run_invalid_reason: str | None,
    evaluation_resolved: bool,
) -> None:
    if not result_file.exists():
        return
    result = json.loads(result_file.read_text(encoding="utf-8"))
    result["timing"] = timing
    result["started_at_utc"] = started_at_utc
    result["finished_at_utc"] = finished_at_utc
    result["agent_profile"] = agent_profile
    result["agent_id"] = agent_id
    result["model_id"] = model_id
    result["run_id"] = run_id
    result["agent_environment"] = agent_environment
    result["sandbox_image"] = sandbox_image
    result["agent_exit_code"] = agent_exit_code
    result["agent_timed_out"] = agent_timed_out
    result["run_valid"] = run_valid
    result["run_invalid_reason"] = run_invalid_reason
    result["evaluation_resolved"] = evaluation_resolved
    result["resolved"] = bool(evaluation_resolved and run_valid)
    result_file.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    global TASK_DIR_OVERRIDE
    parser = argparse.ArgumentParser(description="Run one WIDESWE agent pipeline.")
    parser.add_argument("--task")
    parser.add_argument("--task-dir", type=Path, help="Explicit task directory outside benchmark/tasks.")
    parser.add_argument("--agent", choices=sorted(SUPPORTED_AGENTS))
    parser.add_argument("--agent-config", type=Path, help="YAML file containing reusable agent profiles.")
    parser.add_argument("--agent-profile", help="Profile name inside --agent-config.")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument(
        "--workspace-root",
        type=Path,
        help="Optional root for transient agent/evaluator workspaces; result metadata/logs remain under --run-dir.",
    )
    parser.add_argument("--profile-set", default=None)
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--max-api-requests", type=int)
    parser.add_argument(
        "--command",
        help="Required for command-driven agents such as claude-code and codex.",
    )
    parser.add_argument("--agent-id", help="Stable agent identifier, e.g. claude-code.")
    parser.add_argument("--model-id", help="Model identifier, e.g. deepseek-v4.")
    parser.add_argument("--run-id", help="Repeat/run identifier within an agent+model condition.")
    parser.add_argument(
        "--prompt-suffix-file",
        type=Path,
        help=(
            "Append this exact UTF-8 file to the agent-visible prompt after two "
            "newlines. The task source prompt is never modified."
        ),
    )
    parser.add_argument(
        "--workspace-scope",
        choices=sorted(WORKSPACE_SCOPES),
        default="ecosystem",
        help=(
            "Repository visibility/edit scope. ecosystem shows all repos in repos.yaml "
            "while scoring only included repos; single-repo shows and scores one included repo."
        ),
    )
    parser.add_argument("--single-repo", help="Included repo name required with --workspace-scope single-repo.")
    parser.add_argument(
        "--agent-environment",
        choices=sorted(AGENT_ENVIRONMENTS),
        default=None,
        help=(
            "Agent runtime environment type. agent-only images contain only the "
            "agent runtime; case-env images include the task dependency "
            "environment plus the agent; host is used for --sandbox none."
        ),
    )
    parser.add_argument(
        "--sandbox",
        choices=["none", "docker"],
        default=None,
        help="Run shell agents directly on the host or inside a Docker sandbox.",
    )
    parser.add_argument("--sandbox-image", help="Docker image used when --sandbox docker is selected.")
    parser.add_argument(
        "--sandbox-network",
        default=None,
        choices=["bridge", "host", "none", "llm-api-only"],
        help="Docker network mode. llm-api-only permits only allowlisted LLM API hosts.",
    )
    parser.add_argument(
        "--llm-api-allowed-host",
        action="append",
        help="HTTPS host allowed by llm-api-only mode. May be repeated.",
    )
    parser.add_argument(
        "--pass-env",
        action="append",
        help="Environment variable name to pass into Docker sandbox. May be repeated.",
    )
    parser.add_argument(
        "--sandbox-mount",
        action="append",
        help="Extra Docker volume mount for local-only agent sandbox runs. May be repeated.",
    )
    parser.add_argument(
        "--agent-runtime-mount",
        action="append",
        help=(
            "Reusable agent runtime volume mount for case-env runs, e.g. "
            "/host/claude-runtime:/opt/ecosync/agent-runtime:ro. May be repeated."
        ),
    )
    parser.add_argument("--sandbox-user", help="Docker --user value for sandbox runs, e.g. node or 1000:1000.")
    parser.add_argument(
        "--sandbox-env",
        action="append",
        help="Extra KEY=VALUE environment assignment for Docker sandbox runs. May be repeated.",
    )
    parser.add_argument(
        "--keep-workspaces",
        choices=["always", "failed", "never"],
        default="never",
        help=(
            "Whether to keep full agent/evaluator workspaces after evaluation. "
            "Default/recommended release mode is 'never', which prunes workspaces "
            "even when the agent fails. Lightweight artifacts are always kept; "
            "use 'failed' or 'always' only for one-off debugging."
        ),
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--reuse-agent-workspace",
        action="store_true",
        help=(
            "Reuse the workspace recorded in an existing run.json after an interrupted "
            "agent invocation. This preserves partial code changes and is mutually "
            "exclusive with --force."
        ),
    )
    parser.add_argument(
        "--skip-evaluation-on-agent-error",
        action="store_true",
        help=(
            "Compatibility flag. Agent command failures are skipped by default; "
            "use --evaluate-on-agent-error to force evaluation of partial changes."
        ),
    )
    parser.add_argument(
        "--evaluate-on-agent-error",
        action="store_true",
        help=(
            "Evaluate workspace changes even if the agent command exits nonzero. "
            "This is not recommended for formal runs because it can mix adapter "
            "failures with task-solving failures."
        ),
    )
    parser.add_argument(
        "--skip-evaluation",
        action="store_true",
        help=(
            "Run only prepare-agent-run and the agent command, then clean up. "
            "This is for no-cost adapter/container smoke checks; formal "
            "benchmark scoring must leave this unset."
        ),
    )
    args = parser.parse_args()
    if args.force and args.reuse_agent_workspace:
        raise SystemExit("--force and --reuse-agent-workspace are mutually exclusive")
    try:
        args.task, TASK_DIR_OVERRIDE = resolve_task_selection(args.task, args.task_dir, DEFAULT_TASK_ID)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    profile: dict[str, Any] = {}
    if args.agent_config or args.agent_profile:
        if not args.agent_config or not args.agent_profile:
            raise SystemExit("--agent-config and --agent-profile must be provided together")
        profile = load_agent_profile(args.agent_config, args.agent_profile)

    agent_name = first_value(args.agent, profile, "agent")
    if not agent_name:
        raise SystemExit("--agent is required unless provided by --agent-profile")
    if agent_name not in SUPPORTED_AGENTS:
        raise SystemExit(f"unsupported agent: {agent_name}")

    profile_set = first_value(args.profile_set, profile, "profile_set", "linux-docker")
    requested_timeout = formal_agent_resource_limit(
        "ECOSYNC_FORMAL_AGENT_TIMEOUT_SEC",
        first_value(args.timeout, profile, "timeout", MAX_AGENT_TIMEOUT_SEC),
    )
    timeout = min(max(1, requested_timeout), MAX_AGENT_TIMEOUT_SEC)
    requested_max_api_requests = formal_agent_resource_limit(
        "ECOSYNC_FORMAL_MAX_API_REQUESTS",
        first_value(args.max_api_requests, profile, "max_api_requests", MAX_API_REQUESTS),
    )
    max_api_requests = (
        0
        if requested_max_api_requests == 0
        else min(max(1, requested_max_api_requests), MAX_API_REQUESTS)
    )
    command = first_value(args.command, profile, "command")
    agent_id = first_value(args.agent_id, profile, "agent_id", agent_name)
    model_id = first_value(args.model_id, profile, "model_id", "unspecified-model")
    run_id = first_value(args.run_id, profile, "run_id", "run")
    sandbox = first_value(args.sandbox, profile, "sandbox", "none")
    sandbox_network = first_value(args.sandbox_network, profile, "sandbox_network", "bridge")
    profile_allowed_hosts = profile.get("llm_api_allowed_hosts") or []
    cli_allowed_hosts = args.llm_api_allowed_host or []
    llm_api_allowed_hosts = sorted({str(item).rstrip(".").lower() for item in [*profile_allowed_hosts, *cli_allowed_hosts]})
    agent_environment = first_value(args.agent_environment, profile, "agent_environment")
    agent_environment_was_defaulted = agent_environment is None
    if agent_environment is None:
        agent_environment = default_agent_environment(str(sandbox))
    if agent_environment not in AGENT_ENVIRONMENTS:
        raise SystemExit(f"unsupported agent_environment: {agent_environment}")
    sandbox_image, sandbox_image_source = resolve_sandbox_image(
        profile,
        args.task,
        args.sandbox_image,
        agent_id=str(agent_id),
        agent_environment=str(agent_environment),
    )
    if sandbox == "none" and agent_environment != "host":
        raise SystemExit("--agent-environment must be host when --sandbox none is used")
    if sandbox == "docker" and agent_environment == "host":
        raise SystemExit("--agent-environment host is only valid with --sandbox none")
    if sandbox_network == "llm-api-only" and not llm_api_allowed_hosts:
        raise SystemExit("llm-api-only requires llm_api_allowed_hosts in the profile or --llm-api-allowed-host")
    validate_offline_agent_command(str(agent_id), str(sandbox_network), command)
    docker_network = None if sandbox != "docker" else "none" if sandbox_network == "llm-api-only" else sandbox_network
    if sandbox == "docker" and agent_environment == "case-env" and not sandbox_image:
        raise SystemExit(
            "Docker agent runs default to --agent-environment case-env. "
            "Build/update the task case_base_image, provide an explicit "
            "--sandbox-image, or pass --agent-environment agent-only for a "
            "pure agent image."
        )
    if (
        sandbox == "docker"
        and agent_environment == "case-env"
        and agent_environment_was_defaulted
        and sandbox_image_source == "profile.sandbox_image"
    ):
        raise SystemExit(
            "Docker agent runs default to --agent-environment case-env, but this "
            "profile only provides a generic sandbox_image. Add sandbox_image_by_task "
            "only for a legacy task-specific case-env image, omit sandbox_image to "
            "use the task case_base_image, or pass --agent-environment agent-only "
            "for a pure agent image."
        )
    profile_env = profile.get("pass_env") or []
    cli_env = args.pass_env or []
    pass_env = [str(item) for item in [*profile_env, *cli_env]]
    profile_mounts = profile.get("sandbox_mounts") or []
    profile_runtime_mounts = profile.get("agent_runtime_mounts") or []
    cli_runtime_mounts = args.agent_runtime_mount or []
    cli_mounts = args.sandbox_mount or []
    agent_runtime_mounts = [str(item) for item in [*profile_runtime_mounts, *cli_runtime_mounts]]
    sandbox_mounts = [str(item) for item in [*profile_mounts, *agent_runtime_mounts, *cli_mounts]]
    sandbox_user = first_value(args.sandbox_user, profile, "sandbox_user")
    profile_sandbox_env = profile.get("sandbox_env") or []
    cli_sandbox_env = args.sandbox_env or []
    sandbox_env = [str(item) for item in [*profile_sandbox_env, *cli_sandbox_env]]

    run_dir = args.run_dir.resolve()
    if run_dir.exists() and args.force:
        remove_workspaces(run_dir)
        shutil.rmtree(run_dir)
    case_debug_deps: dict[str, Any] = {"enabled": False}

    started = time.time()
    started_at_utc = utc_now()
    pipeline_log = run_dir / "logs" / "pipeline.log"

    steps = []

    harness_env = os.environ.copy()
    if TASK_DIR_OVERRIDE is not None:
        harness_env["ECOSYNC_TASKS_DIR"] = str(TASK_DIR_OVERRIDE.parent)
    if args.reuse_agent_workspace:
        pipeline_log.parent.mkdir(parents=True, exist_ok=True)
        pipeline_log.write_text("", encoding="utf-8")
        reuse_cmd = ["reuse-agent-workspace", str(run_dir)]
        step_started = time.time()
        try:
            workspace = reusable_agent_workspace(run_dir, args.task)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            append_completed_step(pipeline_log, reuse_cmd, str(exc), 2)
            raise SystemExit(str(exc)) from exc
        append_completed_step(pipeline_log, reuse_cmd, str(workspace), 0)
        steps.append(step_record("reuse-agent-workspace", reuse_cmd, step_started, 0))
    else:
        prepare_cmd = [
            "python3",
            str(HARNESS),
            "prepare-agent-run",
            "--task",
            args.task,
            "--run-dir",
            str(run_dir),
            "--workspace-scope",
            args.workspace_scope,
        ]
        if args.workspace_root:
            prepare_cmd.extend(["--workspace-root", str(args.workspace_root.resolve())])
        if args.single_repo:
            prepare_cmd.extend(["--single-repo", args.single_repo])
        if args.force:
            prepare_cmd.append("--force")
        step_started = time.time()
        code, output = run_capture(prepare_cmd, env=harness_env)
        pipeline_log.parent.mkdir(parents=True, exist_ok=True)
        pipeline_log.write_text("", encoding="utf-8")
        append_completed_step(pipeline_log, prepare_cmd, output, code)
        steps.append(step_record("prepare-agent-run", prepare_cmd, step_started, code))
        if code != 0:
            raise SystemExit(code)
        workspace = agent_workspace_path(run_dir)
        if workspace is None or not workspace.is_dir():
            raise SystemExit(f"prepared agent workspace is missing: {workspace}")
    prompt_original: bytes | None = None
    prompt_suffix_file = (
        args.prompt_suffix_file.resolve() if args.prompt_suffix_file else None
    )
    if prompt_suffix_file is not None:
        if not prompt_suffix_file.is_file():
            raise SystemExit(f"missing prompt suffix file: {prompt_suffix_file}")
        try:
            prompt_original = append_prompt_suffix(
                workspace,
                prompt_suffix_file,
                run_dir,
                reuse_workspace=args.reuse_agent_workspace,
            )
        except (OSError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc

    input_snapshot = snapshot_run_inputs(
        run_dir=run_dir,
        task_id=args.task,
        workspace_scope=args.workspace_scope,
        single_repo=args.single_repo,
        agent_config=args.agent_config,
        agent_profile=args.agent_profile,
        profile=profile,
        prompt_original=prompt_original,
        prompt_suffix_file=prompt_suffix_file,
    )
    if sandbox == "docker" and agent_environment == "case-env":
        prepare_task_cache_root(args.task)
        debug_mounts, debug_env, case_debug_deps = case_debug_dependency_mounts(
            args.task,
            run_dir,
            args.workspace_scope,
        )
        sandbox_mounts.extend(debug_mounts)
        sandbox_env.extend(debug_env)
        source_mask_mounts = dependency_project_source_mask_mounts(args.task, run_dir)
        sandbox_mounts.extend(source_mask_mounts)
        case_debug_deps["dependency_project_source_masks"] = source_mask_mounts
        python_source_paths = task_agent_python_source_paths(args.task)
        python_metadata_path = materialize_agent_python_distribution_metadata(
            args.task, run_dir
        )
        python_paths = [
            *([python_metadata_path] if python_metadata_path else []),
            *python_source_paths,
        ]
        if python_paths:
            existing_python_path = env_assignment(sandbox_env, "PYTHONPATH")
            set_env_assignment(
                sandbox_env,
                "PYTHONPATH",
                ":".join(
                    [
                        *python_paths,
                        *([existing_python_path] if existing_python_path else []),
                    ]
                ),
            )
        case_debug_deps["agent_python_source_paths"] = python_source_paths
        case_debug_deps["agent_python_distribution_metadata"] = python_metadata_path
        cargo_workspace_patches = materialize_agent_cargo_workspace_patches(
            args.task, run_dir
        )
        case_debug_deps["agent_cargo_workspace_patches"] = cargo_workspace_patches
        boxo_kubo_prewarm = prewarm_boxo_kubo_agent_go_cache(
            args.task,
            run_dir,
            str(sandbox_image),
        )
        case_debug_deps["boxo_kubo_go_prewarm"] = boxo_kubo_prewarm
        if boxo_kubo_prewarm.get("enabled") and boxo_kubo_prewarm.get("ok") is False:
            append_completed_step(
                pipeline_log,
                ["prewarm-boxo-kubo-agent-go-cache"],
                str(boxo_kubo_prewarm.get("output_tail") or ""),
                int(boxo_kubo_prewarm.get("exit_code") or 1),
            )
            raise SystemExit(int(boxo_kubo_prewarm.get("exit_code") or 1))
        go_module_prewarm = (
            {"enabled": False, "reason": "handled-by-boxo-kubo-prewarm"}
            if boxo_kubo_prewarm.get("enabled")
            else prewarm_agent_go_modules(
                args.task,
                run_dir,
                str(sandbox_image),
                sandbox_mounts,
                sandbox_env,
            )
        )
        case_debug_deps["go_module_prewarm"] = go_module_prewarm
        if go_module_prewarm.get("enabled") and go_module_prewarm.get("ok") is False:
            append_completed_step(
                pipeline_log,
                ["prewarm-agent-go-modules"],
                str(go_module_prewarm.get("output_tail") or ""),
                int(go_module_prewarm.get("exit_code") or 1),
            )
            raise SystemExit(int(go_module_prewarm.get("exit_code") or 1))
        dotnet_framework_prewarm = (
            prewarm_agent_dotnet_frameworks(
                args.task,
                run_dir,
                str(sandbox_image),
                sandbox_mounts,
                sandbox_env,
            )
            if sandbox_network in {"none", "llm-api-only"}
            else {"enabled": False, "reason": "sandbox-network-enabled"}
        )
        case_debug_deps["dotnet_framework_prewarm"] = dotnet_framework_prewarm
        if (
            dotnet_framework_prewarm.get("enabled")
            and dotnet_framework_prewarm.get("ok") is False
        ):
            append_completed_step(
                pipeline_log,
                ["prewarm-agent-dotnet-frameworks"],
                str(dotnet_framework_prewarm.get("output_tail") or ""),
                int(dotnet_framework_prewarm.get("exit_code") or 1),
            )
            raise SystemExit(int(dotnet_framework_prewarm.get("exit_code") or 1))
        case_debug_deps["refreshed_dotnet_cache_markers"] = [
            str(path) for path in refresh_offline_dotnet_cache_markers(args.task, sandbox_network)
        ]
    sandbox_mounts = dedupe_mount_specs(sandbox_mounts)
    extend_missing_env_assignments(
        sandbox_env,
        cache_env_for_explicit_mounts(sandbox_mounts),
    )
    enforce_offline_package_manager_env(sandbox_network, sandbox_env)

    agent_cmd = [
        "python3",
        str(AGENT),
        "--agent",
        agent_name,
        "--task",
        args.task,
        "--task-dir",
        str(TASK_DIR_OVERRIDE) if TASK_DIR_OVERRIDE is not None else str(REPO_ROOT / "benchmark" / "tasks" / args.task),
        "--run-dir",
        str(run_dir),
        "--timeout",
        str(timeout),
        "--max-api-requests",
        str(max_api_requests),
        "--model-id",
        str(model_id),
        "--run-id",
        str(run_id),
        "--sandbox",
        str(sandbox),
        "--sandbox-network",
        str(sandbox_network),
        "--agent-environment",
        str(agent_environment),
    ]
    for host in llm_api_allowed_hosts:
        agent_cmd.extend(["--llm-api-allowed-host", host])
    if sandbox_image:
        agent_cmd.extend(["--sandbox-image", str(sandbox_image)])
    if agent_id:
        agent_cmd.extend(["--agent-id", str(agent_id)])
    for name in pass_env:
        agent_cmd.extend(["--pass-env", name])
    for mount in sandbox_mounts:
        agent_cmd.extend(["--sandbox-mount", mount])
    if sandbox_user:
        agent_cmd.extend(["--sandbox-user", str(sandbox_user)])
    for item in sandbox_env:
        agent_cmd.extend(["--sandbox-env", item])
    if command:
        agent_cmd.extend(["--command", str(command)])
    step_started = time.time()
    code = run(agent_cmd, pipeline_log, env=harness_env)
    steps.append(step_record("run-agent", agent_cmd, step_started, code))
    agent_exit_code = code
    agent_metadata = load_json_if_exists(run_dir / "logs" / "agent.json")
    agent_timed_out = agent_metadata.get("timed_out") is True
    agent_execution_reason = (
        agent_execution_invalid_reason(
            agent_id,
            run_dir,
            agent_exit_code=agent_exit_code,
            agent_timed_out=agent_timed_out,
        )
        if sandbox_network == "llm-api-only"
        else None
    )
    agent_retry_count = 0
    if agent_execution_reason == "agent_no_api_requests":
        agent_retry_count = 1
        retry_started = time.time()
        code = run(agent_cmd, pipeline_log, env=harness_env)
        steps.append(step_record("run-agent-empty-retry", agent_cmd, retry_started, code))
        agent_exit_code = code
        agent_metadata = load_json_if_exists(run_dir / "logs" / "agent.json")
        agent_timed_out = agent_metadata.get("timed_out") is True
        agent_execution_reason = agent_execution_invalid_reason(
            agent_id,
            run_dir,
            agent_exit_code=agent_exit_code,
            agent_timed_out=agent_timed_out,
        )
    case_debug_deps["isolation_cleanup"] = cleanup_isolated_agent_dependency_views(
        case_debug_deps
    )
    if args.skip_evaluation:
        diff_dir, diff_collection_exit_code, diff_collection_ok = (
            collect_agent_diff_artifacts(
                args.task,
                run_dir,
                pipeline_log,
                harness_env,
                steps,
            )
        )
        agent_artifacts_valid = (
            agent_exit_code == 0
            and not agent_timed_out
            and agent_execution_reason is None
            and diff_collection_ok
        )
        agent_artifacts_invalid_reason = (
            agent_execution_reason
            or (f"agent_exit_{agent_exit_code}" if agent_exit_code != 0 else None)
            or ("agent_timed_out" if agent_timed_out else None)
            or ("diff_collection_failed" if not diff_collection_ok else None)
        )
        cleanup_started = time.time()
        cleanup_errors: list[str] = []
        # run_agent.py owns the named container lifecycle. Do not issue a
        # second docker rm after --rm or timeout cleanup has already begun.
        container_cleanup: list[dict[str, Any]] = []
        cleanup_reason = "kept"
        removed_workspaces: list[str] = []
        cleanup_mode = smoke_workspace_cleanup_mode(args.keep_workspaces, agent_exit_code)
        if cleanup_mode == "all" and not diff_collection_ok:
            cleanup_mode = "scratch-only"
        if cleanup_mode == "all":
            try:
                removed_workspaces = remove_workspaces(run_dir)
                cleanup_reason = args.keep_workspaces
            except Exception as exc:  # pragma: no cover - best-effort cleanup must not hide agent diagnostics
                cleanup_errors.append(f"{type(exc).__name__}: {exc}")
                cleanup_reason = f"{args.keep_workspaces}-failed"
        elif cleanup_mode == "scratch-only":
            try:
                removed_workspaces = remove_workspaces(
                    run_dir,
                    include_workspaces=False,
                    include_scratch=True,
                )
                cleanup_reason = "failed-kept-workspaces-removed-scratch"
            except Exception as exc:  # pragma: no cover - best-effort cleanup must not hide agent diagnostics
                cleanup_errors.append(f"{type(exc).__name__}: {exc}")
                cleanup_reason = "failed-keep-workspaces-cleanup-failed"
        elif cleanup_mode == "workspace-only":
            try:
                removed_workspaces = remove_workspaces(
                    run_dir,
                    include_workspaces=True,
                    include_scratch=False,
                )
                cleanup_reason = "never-failed-kept-scratch"
            except Exception as exc:  # pragma: no cover - best-effort cleanup must not hide agent diagnostics
                cleanup_errors.append(f"{type(exc).__name__}: {exc}")
                cleanup_reason = "failed-keep-scratch-cleanup-failed"
        cleanup_sec = time.time() - cleanup_started
        finished_at = time.time()
        timing = pipeline_timing(started, finished_at, steps, cleanup_sec=cleanup_sec)
        summary = {
            "task_id": args.task,
            "agent": agent_name,
            "agent_config": str(args.agent_config) if args.agent_config else None,
            "agent_profile": args.agent_profile,
            "agent_id": agent_id,
            "model_id": model_id,
            "run_id": run_id,
            "workspace_scope": args.workspace_scope,
            "single_repo": args.single_repo,
            "agent_environment": agent_environment,
            "sandbox": sandbox,
            "sandbox_image": sandbox_image,
            "sandbox_image_source": sandbox_image_source,
            "sandbox_network": sandbox_network if sandbox == "docker" else None,
            "docker_network": docker_network,
            "llm_api_allowed_hosts": llm_api_allowed_hosts,
            "pass_env": sorted(set(pass_env)),
            "sandbox_mounts": sorted(set(sandbox_mounts)),
            "agent_runtime_mounts": sorted(set(agent_runtime_mounts)),
            "sandbox_user": sandbox_user,
            "sandbox_env": sorted(set(sandbox_env)),
            "case_debug_dependencies": case_debug_deps,
            "input_snapshot": input_snapshot,
            "profile_set": profile_set,
            "requested_timeout_sec": requested_timeout,
            "effective_timeout_sec": timeout,
            "requested_max_api_requests": requested_max_api_requests,
            "effective_max_api_requests": max_api_requests,
            "run_dir": str(run_dir),
            "agent_exit_code": agent_exit_code,
            "agent_timed_out": agent_timed_out,
            "agent_execution_invalid_reason": agent_execution_reason,
            "agent_retry_count": agent_retry_count,
            "agent_artifacts_valid": agent_artifacts_valid,
            "agent_artifacts_invalid_reason": agent_artifacts_invalid_reason,
            "diff_collection_exit_code": diff_collection_exit_code,
            "diff_dir": str(diff_dir),
            "run_valid": agent_artifacts_valid,
            "run_invalid_reason": agent_artifacts_invalid_reason,
            "evaluation_resolved": None,
            "resolved": None,
            "steps": steps,
            "duration_sec": round(finished_at - started, 3),
            "pipeline_log": str(pipeline_log),
            "result_file": None,
            "keep_workspaces": args.keep_workspaces,
            "removed_workspaces": removed_workspaces,
            "workspace_cleanup": cleanup_reason,
            "workspace_cleanup_errors": cleanup_errors,
            "container_cleanup": container_cleanup,
            "agent_metadata": agent_metadata,
            "auto_git_metadata_mounts": agent_metadata.get("auto_git_metadata_mounts", []),
            "sandbox_container_name": agent_metadata.get("sandbox_container_name"),
            "started_at_utc": started_at_utc,
            "finished_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(finished_at)),
            "timing": timing,
            "agent_only": True,
            "evaluation_skipped": True,
            "smoke_only": False,
        }
        (run_dir / "pipeline_result.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2))
        raise SystemExit(0 if agent_artifacts_valid else (agent_exit_code or diff_collection_exit_code or 2))
    skip_evaluation_on_agent_error = args.skip_evaluation_on_agent_error or not args.evaluate_on_agent_error
    if agent_execution_reason is not None or not should_evaluate_after_agent_exit(
        code,
        agent_timed_out=agent_timed_out,
        evaluate_on_agent_error=not skip_evaluation_on_agent_error,
    ):
        diff_dir, diff_collection_exit_code, diff_collection_ok = (
            collect_agent_diff_artifacts(
                args.task,
                run_dir,
                pipeline_log,
                harness_env,
                steps,
            )
        )
        cleanup_started = time.time()
        cleanup_reason = "kept"
        removed_workspaces: list[str] = []
        if args.keep_workspaces == "never" and diff_collection_ok:
            removed_workspaces = remove_workspaces(run_dir, include_scratch=False)
            cleanup_reason = "never-failed-kept-scratch"
        elif args.keep_workspaces == "never":
            cleanup_reason = "diff-collection-failed-kept-workspaces"
        elif args.keep_workspaces == "failed":
            cleanup_reason = "failed-kept-workspaces-and-scratch"
        cleanup_sec = time.time() - cleanup_started
        finished_at = time.time()
        timing = pipeline_timing(started, finished_at, steps, cleanup_sec=cleanup_sec)
        summary = {
            "task_id": args.task,
            "agent": agent_name,
            "agent_id": agent_id,
            "model_id": model_id,
            "run_id": run_id,
            "workspace_scope": args.workspace_scope,
            "single_repo": args.single_repo,
            "agent_environment": agent_environment,
            "sandbox": sandbox,
            "sandbox_image": sandbox_image,
            "sandbox_image_source": sandbox_image_source,
            "sandbox_network": sandbox_network if sandbox == "docker" else None,
            "docker_network": docker_network,
            "llm_api_allowed_hosts": llm_api_allowed_hosts,
            "sandbox_mounts": sorted(set(sandbox_mounts)),
            "agent_runtime_mounts": sorted(set(agent_runtime_mounts)),
            "sandbox_user": sandbox_user,
            "sandbox_env": sorted(set(sandbox_env)),
            "case_debug_dependencies": case_debug_deps,
            "input_snapshot": input_snapshot,
            "profile_set": profile_set,
            "requested_timeout_sec": requested_timeout,
            "effective_timeout_sec": timeout,
            "requested_max_api_requests": requested_max_api_requests,
            "effective_max_api_requests": max_api_requests,
            "run_dir": str(run_dir),
            "agent_exit_code": agent_exit_code,
            "agent_timed_out": agent_timed_out,
            "agent_execution_invalid_reason": agent_execution_reason,
            "agent_retry_count": agent_retry_count,
            "diff_collection_exit_code": diff_collection_exit_code,
            "diff_dir": str(diff_dir),
            "partial_diff_collected": diff_collection_ok,
            "run_valid": False,
            "run_invalid_reason": agent_execution_reason or f"agent_exit_{agent_exit_code}",
            "evaluation_resolved": None,
            "resolved": False,
            "result_file": None,
            "pipeline_log": str(pipeline_log),
            "steps": steps,
            "started_at_utc": started_at_utc,
            "finished_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(finished_at)),
            "duration_sec": round(finished_at - started, 3),
            "timing": timing,
            "keep_workspaces": args.keep_workspaces,
            "removed_workspaces": removed_workspaces,
            "workspace_cleanup": cleanup_reason,
            "agent_metadata": agent_metadata,
            "auto_git_metadata_mounts": agent_metadata.get("auto_git_metadata_mounts", []),
            "sandbox_container_name": agent_metadata.get("sandbox_container_name"),
        }
        (run_dir / "pipeline_result.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2))
        raise SystemExit(code or 2)

    eval_cmd = [
        "python3",
        str(HARNESS),
        "evaluate-agent",
        "--task",
        args.task,
        "--run-dir",
        str(run_dir),
        "--profile-set",
        str(profile_set),
        "--force",
    ]
    step_started = time.time()
    code = run(eval_cmd, pipeline_log, env=harness_env)
    steps.append(step_record("evaluate-agent", eval_cmd, step_started, code))

    result_file = run_dir / "result.json"
    result_data = load_json_if_exists(result_file)
    evaluation_resolved = bool(result_data.get("resolved"))
    run_invalid_reason = agent_execution_reason or evaluation_invalid_reason(
        agent_exit_code,
        result_data or None,
        agent_timed_out=agent_timed_out,
    )
    run_valid = run_invalid_reason is None
    resolved = bool(evaluation_resolved and run_valid)

    evaluation_finished_at = time.time()
    evaluation_finished_at_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(evaluation_finished_at))
    timing_before_cleanup = pipeline_timing(started, evaluation_finished_at, steps)
    write_result_timing(
        result_file,
        timing=timing_before_cleanup,
        started_at_utc=started_at_utc,
        finished_at_utc=evaluation_finished_at_utc,
        agent_profile=args.agent_profile,
        agent_id=agent_id,
        model_id=model_id,
        run_id=run_id,
        agent_environment=agent_environment,
        sandbox_image=sandbox_image,
        agent_exit_code=agent_exit_code,
        agent_timed_out=agent_timed_out,
        run_valid=run_valid,
        run_invalid_reason=run_invalid_reason,
        evaluation_resolved=evaluation_resolved,
    )

    cleanup_started = time.time()
    # run_agent.py owns the named container lifecycle. Repeating docker rm here
    # caused overlapping overlay2 teardown on the external Docker data root.
    container_cleanup: list[dict[str, Any]] = []
    cleanup_reason = "kept"
    removed_workspaces: list[str] = []
    if args.keep_workspaces == "never" or (args.keep_workspaces == "failed" and resolved):
        removed_workspaces = remove_workspaces(run_dir)
        cleanup_reason = args.keep_workspaces
    elif args.keep_workspaces == "failed" and not agent_timed_out and agent_exit_code == 0:
        removed_workspaces = remove_workspaces(run_dir, include_workspaces=False, include_scratch=True)
        cleanup_reason = "failed-kept-workspaces-removed-scratch"
    elif args.keep_workspaces == "failed":
        cleanup_reason = "failed-kept-workspaces-and-scratch"

    finished_at = time.time()
    cleanup_sec = finished_at - cleanup_started
    timing = pipeline_timing(started, finished_at, steps, cleanup_sec=cleanup_sec)
    summary = {
        "task_id": args.task,
        "agent": agent_name,
        "agent_config": str(args.agent_config) if args.agent_config else None,
        "agent_profile": args.agent_profile,
        "agent_id": agent_id,
        "model_id": model_id,
        "run_id": run_id,
        "workspace_scope": args.workspace_scope,
        "single_repo": args.single_repo,
        "agent_environment": agent_environment,
        "sandbox": sandbox,
        "sandbox_image": sandbox_image,
        "sandbox_image_source": sandbox_image_source,
        "sandbox_network": sandbox_network if sandbox == "docker" else None,
        "docker_network": docker_network,
        "llm_api_allowed_hosts": llm_api_allowed_hosts,
        "pass_env": sorted(set(pass_env)),
        "sandbox_mounts": sorted(set(sandbox_mounts)),
        "agent_runtime_mounts": sorted(set(agent_runtime_mounts)),
        "sandbox_user": sandbox_user,
        "sandbox_env": sorted(set(sandbox_env)),
        "case_debug_dependencies": case_debug_deps,
        "input_snapshot": input_snapshot,
        "profile_set": profile_set,
        "requested_timeout_sec": requested_timeout,
        "effective_timeout_sec": timeout,
        "requested_max_api_requests": requested_max_api_requests,
        "effective_max_api_requests": max_api_requests,
        "run_dir": str(run_dir),
        "agent_exit_code": agent_exit_code,
        "agent_timed_out": agent_timed_out,
        "agent_execution_invalid_reason": agent_execution_reason,
        "agent_retry_count": agent_retry_count,
        "run_valid": run_valid,
        "run_invalid_reason": run_invalid_reason,
        "evaluation_resolved": evaluation_resolved,
        "resolved": resolved,
        "steps": steps,
        "duration_sec": round(finished_at - started, 3),
        "pipeline_log": str(pipeline_log),
        "result_file": str(result_file) if result_file.exists() else None,
        "keep_workspaces": args.keep_workspaces,
        "removed_workspaces": removed_workspaces,
        "workspace_cleanup": cleanup_reason,
        "container_cleanup": container_cleanup,
        "agent_metadata": agent_metadata,
        "auto_git_metadata_mounts": agent_metadata.get("auto_git_metadata_mounts", []),
        "sandbox_container_name": agent_metadata.get("sandbox_container_name"),
        "started_at_utc": started_at_utc,
        "finished_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(finished_at)),
        "timing": timing,
    }
    (run_dir / "pipeline_result.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    if not run_valid:
        raise SystemExit(agent_exit_code or code or 2)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
