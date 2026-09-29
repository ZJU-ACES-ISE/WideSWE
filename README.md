<div align="center">

# WideSWE
### Can Coding Agents Coordinate Changes Across Repositories?

**Baoyi Wang\*, Xingliang Wang\*, Jinyang Wu, Keming Wu, Chen Zhi, Jianwei Yin**

Zhejiang University &nbsp; &middot; &nbsp; Tsinghua University<br>
<sub>* Equal contribution &nbsp; &middot; &nbsp; Corresponding author: Chen Zhi</sub>

<p>
  <a href="https://arxiv.org/abs/2609.33382"><img src="https://img.shields.io/badge/Paper-arXiv%3A2609.33382-B34D55?style=for-the-badge&amp;logo=arxiv&amp;logoColor=white" alt="Paper on arXiv"></a>
  <a href="https://github.com/ZJU-ACES-ISE/WideSWE"><img src="https://img.shields.io/badge/Code-GitHub-24332F?style=for-the-badge&amp;logo=github&amp;logoColor=white" alt="Code on GitHub"></a>
  <a href="https://zju-aces-ise.github.io/WideSWE/"><img src="https://img.shields.io/badge/Website-Project_Page-167D8D?style=for-the-badge" alt="Project website"></a>
</p>

**One shared request. Multiple repositories. Complete task resolution.**

</div>

## Overview

WideSWE is a benchmark of 120 real-world software-engineering tasks across 41
software ecosystems, requiring coordinated changes across multiple repositories
in the same ecosystem. It contains 60 bug fixes and 60 features. Each run gives the agent
one shared request and a historical ecosystem workspace containing the target
repositories and, where available, additional context repositories. Hidden
tests evaluate the resulting changes; a task succeeds only when every target
repository passes all required checks.

This repository contains all 120 task definitions, the evaluation harness,
Claude Code and Codex adapters, container definitions, and the reusable portion
of the data-construction pipeline.

[Browse tasks](https://zju-aces-ise.github.io/WideSWE/#tasks) &nbsp; &middot; &nbsp;
[Set up](#requirements) &nbsp; &middot; &nbsp;
[Run evaluation](#run-evaluation) &nbsp; &middot; &nbsp;
[Citation](#citation)

## Main Results

The strongest configuration fully solves **42.50%** of tasks. Task success requires
every target repository to pass all required checks, not just a successful change
in one repository.

[![Task success on 120 WideSWE tasks: GPT-5.6-sol with Codex CLI 42.50%; Qwen 3.8 Max with Claude Code 37.50%; Claude Opus 5 with Claude Code 35.00%; GPT-5.6-sol with Claude Code 32.50%; DeepSeek V4 Pro with Claude Code 26.67%; GLM 5.3 with Claude Code 20.00%; Gemini 3.8 Flash with Claude Code 10.83%.](assets/task_success.png)](https://zju-aces-ise.github.io/WideSWE/#results)

<p align="center"><sub>Task success (%) on the same 120 tasks. Source: Table 1 of the <a href="https://arxiv.org/abs/2609.33382">paper</a>.</sub></p>

<details>
<summary><strong>Benchmark construction and ecosystem coverage</strong></summary>

![WideSWE construction: linked changes, manual review, prompt and hidden-test construction, and coverage of 41 software ecosystems.](assets/benchmark.png)

</details>

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

Task definitions are stored under `cases/<ecosystem>/<case_id>/` and indexed by
`release120.tsv`. The repository includes a flat symlink index under
`benchmark/tasks/`, which is the harness default, so no dataset download step is
required.

Fetch the exact source revisions referenced by all cases:

```bash
python scripts/fetch_sources.py \
  --tasks-dir benchmark/tasks \
  --output-dir "$HOME/.cache/wideswe/source-repos"
export ECOSYNC_SOURCE_REPOS_ROOT="$HOME/.cache/wideswe/source-repos"
```

`benchmark/tasks` points to the bundled case metadata and test definitions.
`ECOSYNC_SOURCE_REPOS_ROOT` points to the local cache of pinned upstream Git
revisions. Agent and evaluator workspaces are created from these two roots;
the source cache should not be copied into this repository.

The `ECOSYNC_*` environment-variable prefix and `.ecosyncbench` workspace
metadata directory are retained as a compatibility interface for the frozen
Release120 images. They are implementation identifiers; the benchmark and
release name is WIDESWE.

Build the frozen environment images referenced by a case:

```bash
python benchmark/harness/ecosync_harness.py build-env --task CASE_ID
```

## Configure An Agent

Copy `benchmark/agents/agent_configs.example.yaml` to
`benchmark/agents/agent_configs.local.yaml` (ignored by Git), then configure the
profile for the agent you want to run:

| Agent profile | Model | API endpoint (`sandbox_env`) | Credential environment variable |
| --- | --- | --- | --- |
| `claude-code-case-env` | Set `model_id` and `ANTHROPIC_MODEL` in `sandbox_env` to the same model identifier. | `ANTHROPIC_BASE_URL` | `ANTHROPIC_AUTH_TOKEN` |
| `codex-case-env` | Set `model_id`; the command passes it to Codex. | `OPENAI_BASE_URL` | `OPENAI_API_KEY` |

When changing the API endpoint, also update `llm_api_allowed_hosts` to its
hostname (without the scheme or path). Keep `sandbox_network: llm-api-only`.
Provide the credential through the environment variable listed above; the
profile's `pass_env` forwards it into the container. Never add credential
values to YAML.

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

## Run Evaluation

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

Run a list of cases serially with the same configuration:

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

- `benchmark/harness/`: workspace preparation, agent orchestration, and evaluation.
- `benchmark/agents/`: Claude Code and Codex adapters and pinned runtime builders.
- `benchmark/images/`: base and dependency image definitions.
- `benchmark/tasks/`: flat index of the 120 bundled tasks.
- `cases/`: prompts, pinned revisions, environments, gold patches, and hidden tests.
- `release120.tsv`: canonical task order and summary metadata.
- `data_mining/`: reusable case-discovery and construction scripts.
- `scripts/`: source-repository bootstrap and release utility scripts.

## Citation

```bibtex
@misc{wang2026wideswe,
  title         = {WideSWE: Can Coding Agents Coordinate Changes Across Repositories?},
  author        = {Baoyi Wang and Xingliang Wang and Jinyang Wu and Keming Wu and Chen Zhi and Jianwei Yin},
  year          = {2026},
  eprint        = {2609.33382},
  archivePrefix = {arXiv},
  primaryClass  = {cs.SE},
  url           = {https://arxiv.org/abs/2609.33382}
}
```

<p align="center">
  <a href="https://arxiv.org/abs/2609.33382">Paper</a> &nbsp; &middot; &nbsp;
  <a href="https://github.com/ZJU-ACES-ISE/WideSWE">Code</a> &nbsp; &middot; &nbsp;
  <a href="https://zju-aces-ise.github.io/WideSWE/">Website</a>
</p>
