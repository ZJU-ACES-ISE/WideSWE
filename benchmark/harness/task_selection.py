from __future__ import annotations

from pathlib import Path


def resolve_task_selection(
    task: str | None,
    task_dir: Path | None,
    default_task: str | None,
) -> tuple[str, Path | None]:
    if task_dir is None:
        task_id = task or default_task
        if not task_id:
            raise ValueError("--task is required when --task-dir is not provided")
        return task_id, None

    resolved = task_dir.resolve()
    if not (resolved / "task.yaml").exists():
        raise ValueError(f"--task-dir does not contain task.yaml: {resolved}")

    task_id = task or resolved.name
    if resolved.name != task_id:
        raise ValueError(f"--task-dir basename must match --task: {resolved.name} != {task_id}")
    return task_id, resolved
