# WIDESWE

WIDESWE is a benchmark for software-engineering tasks whose implementation may
span multiple repositories in the same ecosystem. Release120 contains 120
tasks. Each RQ1 run gives the agent one shared request and an ecosystem
workspace, then evaluates the resulting cross-repository patch with hidden
tests.

The task definitions are published in the
[WIDESWE Hugging Face dataset](https://huggingface.co/datasets/wwww369/WIDESWE).
This repository contains the RQ1 harness, Claude Code and Codex adapters,
container definitions, and the reusable portion of the data-construction
pipeline. It does not contain model credentials, agent trajectories,
experimental outputs, raw pull-request text, or manual-review records.

## Requirements

- Linux
- Python 3.11 or newer
- Git
- Docker Engine with the Compose plugin
- Node.js, npm, and ripgrep when building mounted agent runtimes

Install the Python dependencies:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

## Prepare Release120

Download the task definitions and create the flat task index expected by the
harness:

```bash
python scripts/download_dataset.py --output-dir data/release120
export ECOSYNC_TASKS_DIR="$PWD/data/release120/tasks"
```

The downloader stores the Hugging Face snapshot at `data/release120/`. The
original hierarchy remains under
`data/release120/cases/<ecosystem>/<case_id>/`; the script also creates
`data/release120/tasks/<case_id>` symlinks so the harness can address cases by
ID. The whole `data/` directory is excluded from Git.

Fetch the exact source revisions referenced by all cases:

```bash
python scripts/fetch_sources.py \
  --tasks-dir "$ECOSYNC_TASKS_DIR" \
  --output-dir "$HOME/.cache/wideswe/source-repos"
export ECOSYNC_SOURCE_REPOS_ROOT="$HOME/.cache/wideswe/source-repos"
```

`ECOSYNC_TASKS_DIR` points to case metadata and test definitions.
`ECOSYNC_SOURCE_REPOS_ROOT` points to the local cache of pinned upstream Git
revisions. Agent and evaluator workspaces are created from these two roots;
neither directory should be copied into this repository.

The `ECOSYNC_*` environment-variable prefix and `.ecosyncbench` workspace
metadata directory are retained as a compatibility interface for the frozen
Release120 images. They are implementation identifiers; the benchmark and
release name is WIDESWE.

Pull the frozen images referenced by a case:

```bash
python benchmark/harness/pull_images.py --task CASE_ID
```

## Configure An Agent

Copy `benchmark/agents/agent_configs.example.yaml` to an ignored local file and
set the model and endpoint fields. Credential values must remain in environment
variables and must never be added to YAML.

Claude Code and Codex credentials are not part of WIDESWE. Keep API keys and
local authentication files outside this checkout, expose only the required
environment variable to the selected local profile, and never place a key in a
command, YAML file, Dockerfile, image layer, result directory, or Git commit.

For case-container runs, build the pinned, credential-free agent runtimes and
place them at the paths configured in the local profile:

```bash
sudo mkdir -p /opt/wideswe/agent-runtimes
sudo chown "$USER" /opt/wideswe/agent-runtimes
python benchmark/agents/prepare_agent_runtime.py \
  --output-root /opt/wideswe/agent-runtimes
```

## Run RQ1

Run one case through agent execution and hidden-test evaluation:

```bash
python benchmark/harness/run_user_flow.py \
  --mode full \
  --task CASE_ID \
  --workspace-scope ecosystem \
  --agent-config benchmark/agents/agent_configs.local.yaml \
  --agent-profile claude-code-case-env \
  --run-id seed001
```

Run a list of cases serially with the same RQ1 configuration:

```bash
python benchmark/harness/run_agent_matrix.py \
  --task-file case_ids.txt \
  --workspace-scope ecosystem \
  --agent-config benchmark/agents/agent_configs.local.yaml \
  --agent-profile claude-code-case-env \
  --run-id seed001 \
  --continue-on-error
```

Results, diffs, logs, and trajectories are written under `results/`. Transient
workspaces are removed by default after their durable artifacts are saved.

## Repository Layout

- `benchmark/harness/`: RQ1 workspace preparation, agent orchestration, and evaluation.
- `benchmark/agents/`: Claude Code and Codex adapters and pinned runtime builders.
- `benchmark/images/`: base and dependency image definitions.
- `data_mining/`: reusable case-discovery and construction scripts.
- `scripts/`: dataset and source-repository bootstrap tools.
