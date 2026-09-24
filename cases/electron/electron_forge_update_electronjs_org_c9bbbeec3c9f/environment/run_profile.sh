#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
image="ecosyncbench/deps/electron-yarn-node24:c9bbbeec3c9f"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/electron-msix-c9bbbeec3c9f}"
mkdir -p "$cache_root/yarn" "$workspace/.ecosyncbench/test-reports"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$task_dir/environment/electron-yarn-node24.Dockerfile" "$task_dir/environment"
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
      if [[ "$common_dir" == /* ]]; then
        common_abs="$common_dir"
      else
        common_abs="$(cd "$git_dir/$common_dir" && pwd)"
      fi
      [[ -d "$common_abs" ]] && printf '%s\0' "$common_abs:$common_abs:ro"
    fi
  done < <(find "$workspace/repos" -maxdepth 4 \( -type f -o -type d \) -name .git 2>/dev/null)
}

run_node_profile() {
  local repo_dir="$1"
  shift
  ensure_image
  local -a mounts=()
  local mount_spec
  while IFS= read -r -d '' mount_spec; do
    mounts+=(-v "$mount_spec")
  done < <(git_mounts)
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-2400}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e ELECTRON_SKIP_BINARY_DOWNLOAD=1 \
    -e PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    -e CYPRESS_INSTALL_BINARY=0 \
    -e YARN_ENABLE_GLOBAL_CACHE=1 \
    -e YARN_GLOBAL_FOLDER=/cache/yarn \
    -e ECOSYNC_PROFILE_NAME="$profile" \
    -e ECOSYNC_DOCKER_PREBUILD_ONLY="${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" \
    -e ECOSYNC_DOCKER_WARMUP_ONLY="${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    "${mounts[@]}" \
    -w "/workspace/$repo_dir" \
    "$image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home /cache/yarn
      yarn() { node .yarn/releases/yarn-4.10.3.cjs "$@"; }
      yarn install --immutable
      if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
        yarn node --version >/dev/null
        exit 0
      fi
      if [[ "${ECOSYNC_PROFILE_NAME:-}" == "forge-hidden" ]]; then
        yarn build
      fi
      if [[ "${ECOSYNC_PROFILE_NAME:-}" == "update_electron_app-hidden" ]]; then
        cat >/tmp/ecosync-win32-platform.js <<'"'"'JS'"'"'
Object.defineProperty(process, "platform", { value: "win32" });
Object.defineProperty(process, "arch", { value: "x64" });
JS
      fi
      "$@"
    ' bash "$@"
}

case "$profile" in
  forge-hidden)
    run_node_profile "repos/electron/forge" \
      yarn vitest run --project fast \
      packages/maker/msix/spec/MakerMSIX.spec.ts \
      --reporter=junit \
      --outputFile=/workspace/.ecosyncbench/test-reports/forge-hidden.xml
    ;;
  update_electron_app-hidden)
    run_node_profile "repos/electron/update-electron-app" \
      yarn jest --runInBand --setupFiles=/tmp/ecosync-win32-platform.js --json \
      --outputFile=/workspace/.ecosyncbench/test-reports/update-electron-app-hidden.json \
      --runTestsByPath test/index.test.ts
    ;;
  update_electronjs_org-hidden)
    run_node_profile "repos/electron/update.electronjs.org" \
      yarn vitest run \
      test/asset-platform.test.ts \
      test/msix.test.ts \
      --reporter=junit \
      --outputFile=/workspace/.ecosyncbench/test-reports/update-electronjs-org-hidden.xml
    ;;
  *)
    echo "Unknown profile for electron_forge_update_electronjs_org_c9bbbeec3c9f: $profile" >&2
    exit 2
    ;;
esac
