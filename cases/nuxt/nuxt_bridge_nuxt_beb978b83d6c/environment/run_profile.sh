#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"

image="ecosyncbench/base/node:22-bookworm"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/node-cache/nuxt-bridge-nuxt-beb978b83d6c}"

mkdir -p "$cache_root/corepack" "$cache_root/npm" "$cache_root/pnpm-store" "$cache_root/pnpm-home" "$cache_root/locks" "$workspace/.ecosyncbench/test-reports"

git_mounts() {
  local git_file git_dir common_dir common_abs
  [[ -d "$workspace/repos" ]] || return 0
  while IFS= read -r git_file; do
    if [[ -d "$git_file" ]]; then
      printf '%s\0' "$git_file:$git_file:ro"
      continue
    fi
    git_dir="$(sed -n 's/^gitdir: //p' "$git_file" | head -1)"
    [[ "$git_dir" == /* && -d "$git_dir" ]] || continue
    printf '%s\0' "$git_dir:$git_dir:ro"
    if [[ -f "$git_dir/commondir" ]]; then
      common_dir="$(sed -n '1p' "$git_dir/commondir")"
      if [[ "$common_dir" == /* ]]; then
        common_abs="$common_dir"
      else
        common_abs="$(cd "$git_dir/$common_dir" && pwd)"
      fi
      [[ -d "$common_abs" ]] && printf '%s\0' "$common_abs:$common_abs:ro"
    fi
  done < <(find "$workspace/repos" -maxdepth 4 \( -type f -o -type d \) -name .git 2>/dev/null)
}

dependency_fingerprint() {
  local workdir="$1"
  local repo_dir="$workspace/$workdir"
  local image_id
  image_id="$(docker image inspect --format '{{.Id}}' "$image")"
  {
    printf '%s\0%s\0' "$image_id" "$workdir"
    find "$repo_dir" \
      \( -path '*/node_modules' -o -path '*/.git' \) -prune -o \
      \( -name package.json -o -name pnpm-lock.yaml -o -name pnpm-workspace.yaml \) \
      -type f -print0 \
      | sort -z \
      | while IFS= read -r -d '' manifest; do
          printf '%s\0' "${manifest#"$repo_dir/"}"
          sha256sum "$manifest" | awk '{print $1}'
        done
  } | sha256sum | awk '{print $1}'
}

run_in_node() {
  local workdir="$1"
  local timeout_seconds="$2"
  local script="$3"
  local cache_key="${workdir//\//-}"
  local dependency_id node_modules_dir
  dependency_id="$(dependency_fingerprint "$workdir")"
  node_modules_dir="$cache_root/node_modules/$cache_key/$dependency_id"
  mkdir -p "$node_modules_dir"

  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image" bash -lc 'node --version >/dev/null && corepack --version >/dev/null'
    return 0
  fi

  local -a mounts=()
  local mount_spec
  while IFS= read -r -d '' mount_spec; do
    mounts+=(-v "$mount_spec")
  done < <(git_mounts)

  timeout "$timeout_seconds" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    -e COREPACK_HOME=/node-cache/corepack \
    -e PNPM_HOME=/node-cache/pnpm-home \
    -e npm_config_cache=/node-cache/npm \
    -e PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    -e PUPPETEER_SKIP_DOWNLOAD=1 \
    -e ECOSYNC_NODE_MODULES_LOCK="/node-cache/locks/${cache_key}-${dependency_id}.lock" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/node-cache" \
    -v "$node_modules_dir:/workspace/$workdir/node_modules" \
    "${mounts[@]}" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -lc "$script"
}

case "$profile" in
  bridge-hidden)
    run_in_node "repos/nuxt/bridge" "${ECOSYNC_PROFILE_TIMEOUT:-1800}" '
      set -euo pipefail
      export PATH="/node-cache/pnpm-home:$PATH"
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      corepack prepare pnpm@10.11.1 --activate
      pnpm config set store-dir /node-cache/pnpm-store
      exec 9>"$ECOSYNC_NODE_MODULES_LOCK"
      flock 9
      pnpm install --frozen-lockfile
      flock -u 9
      exec 8>/node-cache/locks/bridge-happy-dom.lock
      flock 8
      if [[ ! -d /node-cache/bridge-test-deps/node_modules/happy-dom ]]; then
        npm install --prefix /node-cache/bridge-test-deps --ignore-scripts happy-dom@17.6.3
      fi
      flock -u 8
      mkdir -p node_modules
      ln -sfn /node-cache/bridge-test-deps/node_modules/happy-dom node_modules/happy-dom
      report=/workspace/.ecosyncbench/test-reports/bridge-composables-vitest.json
      set +e
      pnpm exec vitest run packages/bridge/test/composables.nuxt.test.ts --reporter=json --outputFile="$report"
      status=$?
      test -s "$report"
      exit "$status"
    '
    ;;
  nuxt-hidden)
    run_in_node "repos/nuxt/nuxt" "${ECOSYNC_PROFILE_TIMEOUT:-1800}" '
      set -euo pipefail
      export PATH="/node-cache/pnpm-home:$PATH"
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      corepack prepare pnpm@10.6.5 --activate
      pnpm config set store-dir /node-cache/pnpm-store
      exec 9>"$ECOSYNC_NODE_MODULES_LOCK"
      flock 9
      pnpm install --frozen-lockfile
      flock -u 9
      pnpm dev:prepare
      pnpm test:prepare
      report=/workspace/.ecosyncbench/test-reports/nuxt-composables-vitest.json
      set +e
      pnpm exec vitest run -c vitest.nuxt.config.ts test/nuxt/composables.test.ts --reporter=json --outputFile="$report"
      status=$?
      test -s "$report"
      exit "$status"
    '
    ;;
  *)
    echo "Unknown profile for nuxt_bridge_nuxt_beb978b83d6c: $profile" >&2
    exit 2
    ;;
esac
