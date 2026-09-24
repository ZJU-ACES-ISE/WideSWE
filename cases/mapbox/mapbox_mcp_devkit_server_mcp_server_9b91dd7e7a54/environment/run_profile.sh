#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_root="${ECOSYNC_REPO_ROOT:-}"
if [[ -z "$repo_root" || ! -d "$repo_root/.git" ]]; then
  repo_root="$PWD"
  while [[ "$repo_root" != "/" && ! -d "$repo_root/.git" ]]; do repo_root="$(dirname "$repo_root")"; done
fi
if [[ ! -d "$repo_root/.git" ]]; then echo "cannot locate repository root from $task_dir or $PWD" >&2; exit 2; fi

workspace="$(cd "${ECOSYNC_WORKSPACE:-$repo_root}" && pwd)"
export ECOSYNC_REPO_ROOT="$repo_root"
export ECOSYNC_BUILD_CONTEXT="$workspace"
export ECOSYNC_UID="${ECOSYNC_UID:-$(id -u)}"
export ECOSYNC_GID="${ECOSYNC_GID:-$(id -g)}"

ensure_base_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect ecosyncbench/base/node:22-bookworm >/dev/null 2>&1; then
    docker build -t ecosyncbench/base/node:22-bookworm \
      -f "$repo_root/benchmark/images/base/node-22-bookworm/Dockerfile" \
      "$repo_root"
  fi
}

ensure_runner_script() {
  mkdir -p "$workspace/.ecosyncbench"
cat > "$workspace/.ecosyncbench/run-mapbox-vitest.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
shift
export PATH=/opt/ecosync/deps/node_modules/.bin:/workspace/node_modules/.bin:$PATH
mkdir -p /workspace/.ecosyncbench/test-reports
if [[ ! -e node_modules ]]; then
  ln -s /opt/ecosync/deps/node_modules node_modules
fi
report="/workspace/.ecosyncbench/test-reports/${profile}.xml"
npx vitest run "$@" \
  --maxWorkers=2 \
  --minWorkers=1 \
  --reporter=default \
  --reporter=junit \
  --outputFile.junit="$report"
SH
  chmod +x "$workspace/.ecosyncbench/run-mapbox-vitest.sh"
}

compose_run() {
  local service="$1"
  local image="$2"
  local container_name="ecosync_${profile}_$$_${RANDOM}"
  ensure_base_image
  ensure_runner_script
  cd "$task_dir/environment"
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    ECOSYNC_WORKSPACE="$workspace" docker compose build "$service"
  fi
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
    return 0
  fi
  ECOSYNC_WORKSPACE="$workspace" docker compose run --rm \
    --name "$container_name" \
    -v "$workspace/.ecosyncbench/run-mapbox-vitest.sh:/opt/ecosync/run-mapbox-vitest.sh:ro" \
    "$service"
}

case "$profile" in
  mcp_devkit_server-hidden)
    compose_run mcp_devkit_server-hidden ecosyncbench/deps/mapbox-mcp-devkit-server-npm:9b91dd7e7a54-node22
    ;;
  mcp_server-hidden)
    compose_run mcp_server-hidden ecosyncbench/deps/mapbox-mcp-server-npm:9b91dd7e7a54-node22-shallow-full
    ;;
  *)
    echo "Unknown profile for mapbox_mcp_devkit_server_mcp_server_9b91dd7e7a54: $profile" >&2
    exit 2
    ;;
esac
