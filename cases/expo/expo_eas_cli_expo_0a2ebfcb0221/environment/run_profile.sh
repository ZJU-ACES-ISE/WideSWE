#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
image="ecosyncbench/base/node-expo:22"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/node-cache/expo}"
mkdir -p "$cache_root/corepack" "$cache_root/yarn" "$cache_root/pnpm/cache" "$cache_root/pnpm/home" "$cache_root/pnpm/store" "$cache_root/npm" "$workspace/.ecosyncbench/test-reports"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$task_dir/environment/node-expo.Dockerfile" "$task_dir/environment"
  fi
}

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
      if [[ "$common_dir" == /* ]]; then common_abs="$common_dir"; else common_abs="$(cd "$git_dir/$common_dir" && pwd)"; fi
      [[ -d "$common_abs" ]] && printf '%s\0' "$common_abs:$common_abs:ro"
    fi
  done < <(find "$workspace/repos" -maxdepth 4 \( -type f -o -type d \) -name .git 2>/dev/null)
}

run_in_node() {
  local workdir="$1"
  local script="$2"
  ensure_image
  local -a mounts=()
  local mount_spec
  while IFS= read -r -d '' mount_spec; do mounts+=(-v "$mount_spec"); done < <(git_mounts)
  docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e COREPACK_HOME=/node-cache/corepack \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e YARN_ENABLE_GLOBAL_CACHE=1 \
    -e YARN_GLOBAL_FOLDER=/node-cache/yarn/global \
    -e YARN_CACHE_FOLDER=/node-cache/yarn/cache \
    -e PNPM_HOME=/node-cache/pnpm/home \
    -e PNPM_STORE_DIR=/node-cache/pnpm/store \
    -e XDG_CACHE_HOME=/node-cache/pnpm/cache \
    -e npm_config_cache=/node-cache/npm \
    -e PATH=/node-cache/pnpm/home:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -v "$workspace:/workspace" \
    -v "$cache_root:/node-cache" \
    "${mounts[@]}" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -lc "$script"
}

case "$profile" in
  eas_cli-hidden)
    run_in_node "repos/expo/eas-cli" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home /node-cache/yarn/global /node-cache/yarn/cache
      corepack yarn install --immutable
      set +e
      corepack yarn --cwd packages/eas-cli jest src/user/__tests__/SessionManager-test.ts --runInBand --ci --json --outputFile=/workspace/.ecosyncbench/test-reports/eas-cli-sessionmanager.json
      code=$?
      set -e
      exit "$code"
    '
    ;;
  expo-hidden)
    run_in_node "repos/expo/expo" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home /node-cache/pnpm/cache /node-cache/pnpm/home /node-cache/pnpm/store
      corepack pnpm install --store-dir /node-cache/pnpm/store --offline --no-frozen-lockfile --ignore-scripts || \
        corepack pnpm install --store-dir /node-cache/pnpm/store --no-frozen-lockfile --ignore-scripts
      set +e
      (cd packages/@expo/cli && corepack pnpm exec jest src/api/user/__tests__/user-test.ts --runInBand --ci --json --outputFile=/workspace/.ecosyncbench/test-reports/expo-cli-user.json)
      code=$?
      set -e
      exit "$code"
    '
    ;;
  *)
    echo "Unknown profile for expo_eas_cli_expo_0a2ebfcb0221: $profile" >&2
    exit 2
    ;;
esac
