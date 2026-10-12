# Environment Images

## Reproduction Contract

Release120 uses three kinds of image:

| Lock field | Purpose | Required for a normal run |
| --- | --- | --- |
| `case_base_image` | Multi-repository agent environment, including tool routing | Yes |
| `images` | Runtime images used by the evaluator's per-repository test commands | Yes |
| `build_parent_images` | Additional custom `FROM` images in the bundled Dockerfiles | Only for rebuilding those layers |

An evaluator dependency image is not interchangeable with the aggregate
case-base image. Do not retag a case-base image as an arbitrary dependency:
their filesystem layout, interpreter selection, and entrypoints may differ.
The runtime lock also includes required database and Redis service images.
These are declared under `required_service_images` in `test_profiles.yaml`;
they must be pulled before an offline evaluation, just like language runtimes.

The public namespace is `ghcr.io/code-tmp/wideswe`. Locks retain the historical
`ecosyncbench/...` local tags because test runners reference those names. The
pull command retrieves the pinned manifest and creates those aliases locally.
No personal credentials are needed to pull a public package.
The frozen release images target `linux/amd64`. Native ARM execution is not
validated; adding `--platform` does not establish equivalent benchmark behavior.
Upstream dependencies include public TLS test keys, documentation placeholders,
and development certificates. These are not credentials supplied by benchmark
users and must never be reused to secure a deployed service.

The image does not contain the complete experiment: pinned source revisions,
task definitions, agent credentials, and writable caches are managed separately.
New machines may need an initial dependency-cache preparation step. Image
availability alone does not prove that every offline test can run with an empty
cache. Do not claim a clean-machine validation based on a warm local host.
In particular, several runners install packages on first use, and some legacy
service runners use host ports. Do not assume arbitrary parallel runs are safe
or that an empty-cache offline run is supported without validating that case's
preparation workflow. Existing private cache directories are not release assets.
Evaluator network access is defined by the case runner; several use host or
bridge networking to restore dependencies. The agent's `llm-api-only` setting
does not turn evaluator runs into offline runs. An offline-reproduction claim
requires separate network isolation and validation, not merely that setting.

## Gold Control Without Model Calls

After fetching the sources and pulling the locked images, validate a task using
its gold patches and current hidden tests without calling an LLM:

```bash
python benchmark/harness/run_agent_pipeline.py \
  --task CASE_ID --agent gold --workspace-scope ecosystem \
  --profile-set linux-docker \
  --run-dir results/environment-validation/CASE_ID \
  --keep-workspaces never
```

For an empty-cache check, use a new cache root in a clean shell rather than
deleting existing caches. Set `ECOSYNC_CACHE_BASE_ROOT`,
`ECOSYNC_ISOLATED_GIT_CACHE`, and `ECOSYNC_EVALUATOR_NODE_MODULES_CACHE_ROOT` to
fresh directories, and do not inherit machine-specific shared-cache overrides.
Initial dependency restoration may require network access and take longer than
a warm run. Inspect both `pipeline_result.json` and `result.json`; an image
startup check alone is not a passing gold control.

The IPFS Boxo/Kubo Go workspace adapter defaults to `GOPROXY=off`. On a new
machine, explicitly set `ECOSYNC_EVALUATOR_GOPROXY=https://proxy.golang.org,direct`
for an initial gold dependency-preparation run. This permits module downloads;
it is not an offline evaluation. Unset the variable afterward and repeat the
gold control against the populated cache to check the default offline module
lookup. An explicit `GOPROXY` in a case runner takes precedence. No existing
evaluation default is changed by this opt-in preparation setting.

## Pulling

After the required lock entries have been published:

```bash
python benchmark/harness/pull_images.py --task CASE_ID
python benchmark/harness/ecosync_harness.py check-env --task CASE_ID
```

To prepare all tasks, without pulling the same local image alias repeatedly:

```bash
python benchmark/harness/pull_images.py --all
```

Add `--dry-run` to preview commands. A `not_published` entry fails before any
large download if the local image is absent. This is intentional: a proposed
registry reference is not evidence that an image has been released. The 121
dependency and build-parent references have been uploaded across 85 packages,
but these packages are still private. The pending entries must be made public
and anonymously verified before announcing a complete release.

## Custom Parents And Source Builds

The following two task Dockerfiles extend custom parents, not Docker Hub images:

| Task | Required parent |
| --- | --- |
| `dotnet_roslyn_roslyn_analyzers_58962ed4d289` | `ecosyncbench/deps/dotnet-roslyn-sdk-redirector:84722594faef` |
| `huggingface_diffusers_peft_228aeaf4924e` | `ecosyncbench/deps/huggingface-lora-hotswap:2b18f883fcaa` |

Their `build_parent_images` entries record the release location and, once
published, the immutable manifest digest. Retrieve them with:

```bash
python benchmark/harness/pull_images.py --task CASE_ID --include-build-parents
```

The shipped thin Dockerfiles can then be rebuilt, for example:

```bash
docker build --network none --pull=false \
  -t wideswe-local/roslyn-analyzers:rebuilt \
  -f cases/dotnet/dotnet_roslyn_roslyn_analyzers_58962ed4d289/environment/dotnet-roslyn-analyzers.Dockerfile \
  cases/dotnet/dotnet_roslyn_roslyn_analyzers_58962ed4d289/environment

docker build --network none --pull=false \
  -t wideswe-local/diffusers-peft:rebuilt \
  -f cases/huggingface/huggingface_diffusers_peft_228aeaf4924e/environment/huggingface-diffusers-peft.Dockerfile \
  cases/huggingface/huggingface_diffusers_peft_228aeaf4924e/environment
```

These commands rebuild the task-specific thin layer, **not the parent from
source**. The repository does not currently provide a complete, independently
validated source-build chain for all 120 environments. `build-env` expects an
`environment/build_env.sh`, which is absent from this snapshot. It is not the
Release120 installation method. A Dockerfile reconstructed from image history
must not be presented as the original validated recipe.

## Maintainer Procedure

All commands honor `DOCKER_HOST`. Use the intended daemon explicitly; never
publish from an unrelated Docker installation. The preparation script reads
only task metadata and image metadata, and makes anonymous registry requests.
It never pushes, loads Docker credentials, or modifies prompts or hidden tests.

1. Inventory the validated local images and verify existing case-base packages:

   ```bash
   python scripts/prepare_image_release.py --verify-case-bases --write-locks
   ```

2. Review image configurations, histories, and **all retained layers** for
   credentials, personal paths, author identity, and private material. Metadata
   checks alone are not a complete image audit. Keep findings and local machine
   details out of the release. Do not publish a layer that previously contained
   a secret merely because a later layer deletes it.

3. Preview the dependency and parent-image uploads. This does not push or write
   lock files:

   ```bash
   python benchmark/harness/registry_tools/publish_deps_images.py \
     --all --include-build-parents
   ```

4. Only after publication approval and completion of the privacy review, use
   the same command with `--push`. Add `--transport docker` to reuse daemon-side
   layer uploads and avoid large temporary archives. Authenticate using an
   external Docker credential store, never a credential in a command, file tracked by Git,
   Dockerfile, or layer. Uploads are deduplicated by release reference and image
   ID. Locks remain `not_published` after upload because upload success does not
   establish public visibility.

5. Make the intended packages publicly readable, then verify anonymous access
   and exact equality of registry config digests to the reviewed local image IDs:

   ```bash
   python scripts/prepare_image_release.py --verify-all --write-locks
   python benchmark/harness/pull_images.py --all --include-build-parents --dry-run
   python -m unittest discover -s benchmark/harness -p 'test_release_images.py'
   ```

6. On a separate clean machine or isolated daemon, pull and run the selected
   controls without API calls. Do not remove production images, workspaces,
   caches, or experiment results to simulate a clean machine. Validate the
   agent runtime mounting and hidden-test execution before claiming full
   end-to-end reproducibility.

7. Review the exact staged diff and image audit for credentials, private
   machine paths, personal authentication files, and experiment data before
   committing or publishing. A digest identifies image contents; it does not
   hide sensitive information contained in those contents.
