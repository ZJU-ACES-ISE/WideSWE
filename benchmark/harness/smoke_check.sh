#!/usr/bin/env bash
set -euo pipefail

task="${1:?usage: smoke_check.sh CASE_ID}"
work_root="${ECOSYNC_SMOKE_WORKDIR:-/tmp/ecosyncbench-smoke-$task}"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

echo "[1/6] Python syntax"
python3 -m py_compile \
  benchmark/harness/ecosync_harness.py \
  benchmark/harness/run_agent_pipeline.py \
  benchmark/agents/run_agent.py

echo "[2/6] YAML parse"
python3 - "$task" <<'PY'
import sys
from pathlib import Path
import yaml

task = sys.argv[1]
base = Path("benchmark/tasks") / task
paths = [
    base / "repos.yaml",
    base / "task.yaml",
    base / "harness.yaml",
    base / "validation.yaml",
    base / "environment/docker_layers.yaml",
    base / "environment/test_profiles.yaml",
    base / "environment/docker-compose.yml",
]
paths.extend(sorted((base / "oracles").glob("*.yaml")))
for path in paths:
    if not path.exists():
        print(f"skip missing optional {path}")
        continue
    yaml.safe_load(path.read_text(encoding="utf-8"))
    print(f"ok {path}")
PY

echo "[3/6] Linux Docker base/gold hidden matrix"
python3 benchmark/harness/ecosync_harness.py validate-matrix \
  --task "$task" \
  --workdir "$work_root/matrix-linux-docker" \
  --profile-set linux-docker \
  --force

echo "[4/6] Gold agent pipeline"
python3 benchmark/harness/run_agent_pipeline.py \
  --task "$task" \
  --agent gold \
  --run-dir "$work_root/gold" \
  --profile-set linux-docker \
  --force

echo "[5/6] Noop agent pipeline must fail hidden checks"
if python3 benchmark/harness/run_agent_pipeline.py \
  --task "$task" \
  --agent noop \
  --run-dir "$work_root/noop" \
  --profile-set linux-docker \
  --force; then
  echo "noop unexpectedly passed" >&2
  exit 1
fi

echo "[6/6] Docker evaluator image availability"
if python3 benchmark/harness/ecosync_harness.py check-env --task "$task"; then
  echo "Docker evaluator images are present."
else
  echo "Docker evaluator images are missing. Run build-env or pull release images before linux-docker evaluation."
fi

echo "Smoke check completed."
