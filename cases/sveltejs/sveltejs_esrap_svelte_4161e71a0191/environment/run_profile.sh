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
cache_root="${ECOSYNC_SVELTEJS_CACHE_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/sveltejs-esrap-svelte-4161e71a0191}}"
export ECOSYNC_REPO_ROOT="$repo_root"
export ECOSYNC_BUILD_CONTEXT="$workspace"
export ECOSYNC_UID="${ECOSYNC_UID:-$(id -u)}"
export ECOSYNC_GID="${ECOSYNC_GID:-$(id -g)}"
mkdir -p "$cache_root/locks"

ensure_base_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect ecosyncbench/base/node:22-bookworm >/dev/null 2>&1; then
    docker build -t ecosyncbench/base/node:22-bookworm \
      -f "$repo_root/benchmark/images/base/node-22-bookworm/Dockerfile" \
      "$repo_root"
  fi
}

ensure_runner_script() {
  mkdir -p "$workspace/.ecosyncbench"
  cat > "$workspace/.ecosyncbench/run-sveltejs-vitest.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
shift
mkdir -p /workspace/.ecosyncbench/test-reports

copy_node_modules_from_layer() {
  local deps_root="/opt/ecosync/deps/project"
  [[ -d "$deps_root" ]] || return 0
  mkdir -p /cache/locks
  exec 9>"/cache/locks/${profile}-node-modules.lock"
  flock 9

  if [[ -d "$deps_root/node_modules" && ! -f node_modules/.ecosync-layer-ready ]]; then
    mkdir -p node_modules
    if [[ "$profile" == "svelte-hidden" ]]; then
      tar -C "$deps_root/node_modules" --exclude='./esrap' -cf - . | tar -C node_modules -xf -
    else
      cp -a "$deps_root/node_modules/." node_modules/
    fi
    touch node_modules/.ecosync-layer-ready
  fi

  while IFS= read -r module_dir; do
    local rel="${module_dir#"$deps_root"/}"
    [[ "$rel" == "node_modules" ]] && continue
    if [[ ! -e "$rel" ]]; then
      mkdir -p "$(dirname "$rel")"
      cp -a "$module_dir" "$rel"
    fi
  done < <(find "$deps_root" -path '*/node_modules' -type d -prune 2>/dev/null)
  flock -u 9
}

prepare_pnpm() {
  local version="${1:?pnpm version required}"
  corepack prepare "pnpm@${version}" --activate >/tmp/ecosync-corepack.log 2>&1
}

run_vitest() {
  local report="/workspace/.ecosyncbench/test-reports/${profile}.xml"
  ./node_modules/.bin/vitest run --reporter=junit --outputFile="$report" "$@"
}

case "$profile" in
  esrap-hidden)
    prepare_pnpm 9.8.0
    copy_node_modules_from_layer
    if [[ ! -d node_modules ]]; then
      pnpm install --frozen-lockfile --ignore-scripts
    fi
    run_vitest "$@"
    ;;
  svelte-hidden)
    prepare_pnpm 10.4.0
    copy_node_modules_from_layer
    if [[ ! -d node_modules ]]; then
      pnpm install --frozen-lockfile --ignore-scripts
    fi

    pushd /workspace/repos/sveltejs/esrap >/dev/null
    prepare_pnpm 9.8.0
    if [[ ! -d node_modules ]]; then
      pnpm install --frozen-lockfile --ignore-scripts
    fi
    popd >/dev/null

    run_vitest "$@"
    ;;
  *)
    echo "Unknown profile: $profile" >&2
    exit 2
    ;;
esac
SH
  chmod +x "$workspace/.ecosyncbench/run-sveltejs-vitest.sh"
}

compose_run() {
  local service="$1"
  local image="$2"
  local container_name="ecosync_${profile}_$$_${RANDOM}"
  local -a extra_mounts=()
  ensure_base_image
  ensure_runner_script
  cd "$task_dir/environment"
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    ECOSYNC_WORKSPACE="$workspace" docker compose build "$service"
  fi
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
    return 0
  fi
  if [[ "$service" == "svelte-hidden" ]]; then
    extra_mounts=(
      -v "$workspace/repos/sveltejs/esrap:/workspace/repos/sveltejs/svelte/node_modules/esrap"
    )
  fi
  ECOSYNC_WORKSPACE="$workspace" docker compose run --rm \
    --name "$container_name" \
    -v "$cache_root:/cache" \
    -v "$workspace/.ecosyncbench/run-sveltejs-vitest.sh:/opt/ecosync/run-sveltejs-vitest.sh:ro" \
    "${extra_mounts[@]}" \
    "$service"
}

case "$profile" in
  esrap-hidden)
    compose_run esrap-hidden ecosyncbench/deps/sveltejs-esrap-pnpm:4161e71a0191
    ;;
  svelte-hidden)
    compose_run svelte-hidden ecosyncbench/deps/sveltejs-svelte-pnpm:4161e71a0191
    ;;
  *)
    echo "Unknown profile for sveltejs_esrap_svelte_4161e71a0191: $profile" >&2
    exit 2
    ;;
esac
