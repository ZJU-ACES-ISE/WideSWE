#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
host_repo_root="$(pwd -P)"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy}"
case_cache="elastic-package-registry-package-spec-13d610054b4c"
go_image="ecosyncbench/base/go:1.26-bookworm"

mkdir -p "$workspace/.ecosyncbench/test-reports" \
  "$cache_root/go-cache/$case_cache/go-build" \
  "$cache_root/go-cache/$case_cache/go-mod"

docker_common_args=(
  --rm
  -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}"
  -e HOME=/tmp/ecosync-home
  -e CI=1
  -e FORCE_COLOR=0
  -v "$workspace:/workspace"
  -e PATH=/go/bin:/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  -e GOCACHE=/go-build-cache
  -e GOMODCACHE=/go-mod-cache
  -v "$cache_root/go-cache/$case_cache/go-build:/go-build-cache"
  -v "$cache_root/go-cache/$case_cache/go-mod:/go-mod-cache"
)

ensure_go_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$go_image" >/dev/null 2>&1; then
    docker build -t "$go_image" \
      -f "$host_repo_root/benchmark/images/base/go-1.26-bookworm/Dockerfile" \
      "$host_repo_root/benchmark/images/base/go-1.26-bookworm"
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

run_go_json() {
  local repo_dir="$1"
  local report="$2"
  shift 2
  ensure_go_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$go_image" bash -c 'go version >/dev/null'
    return 0
  fi
  local -a mounts=()
  local mount_spec
  while IFS= read -r -d '' mount_spec; do
    mounts+=(-v "$mount_spec")
  done < <(git_mounts)
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-3600}" docker run \
    "${docker_common_args[@]}" \
    -e ECOSYNC_REPORT="$report" \
    "${mounts[@]}" \
    -w "/workspace/$repo_dir" \
    "$go_image" \
    bash -c 'set -euo pipefail; mkdir -p /tmp/ecosync-home /workspace/.ecosyncbench/test-reports; set +e; go test -count=1 -json "$@" > "/workspace/.ecosyncbench/test-reports/$ECOSYNC_REPORT"; status=$?; set -e; test -s "/workspace/.ecosyncbench/test-reports/$ECOSYNC_REPORT"; exit "$status"' \
    -- "$@"
}

case "$profile" in
  package_registry-hidden)
    run_go_json repos/elastic/package-registry package-registry-hidden.json \
      . ./internal/storage ./packages ./storage
    ;;
  package_spec-hidden)
    run_go_json repos/elastic/package-spec/code/go package-spec-hidden.json \
      ./internal/validator/semantic ./pkg/validator
    ;;
  *)
    echo "Unknown profile for elastic_package_registry_package_spec_13d610054b4c: $profile" >&2
    exit 2
    ;;
esac
