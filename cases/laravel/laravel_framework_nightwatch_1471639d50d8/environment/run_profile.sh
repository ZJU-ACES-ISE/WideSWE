#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_root="${ECOSYNC_REPO_ROOT:-$(pwd)}"
if [[ ! -d "$repo_root/.git" ]]; then
  echo "cannot locate repository root from cwd=$repo_root task_dir=$task_dir" >&2
  exit 2
fi

workspace="$(cd "${ECOSYNC_WORKSPACE:-$repo_root}" && pwd)"
image="ecosyncbench/deps/laravel-framework-nightwatch:1471639d50d8"
cache_root="${ECOSYNC_LARAVEL_CACHE_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/laravel-framework-nightwatch-1471639d50d8}}"
evaluator_vendor_root="${ECOSYNC_EVALUATOR_COMPOSER_VENDOR_CACHE_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/evaluator-dependency-cache/composer-vendor}}/laravel_framework_nightwatch_1471639d50d8"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build \
      -t "$image" \
      -f "$task_dir/environment/laravel-php.Dockerfile" \
      "$task_dir"
  fi
}

dependency_fingerprint() {
  local repo_dir="$1"
  local root_version="$2"
  local image_id
  image_id="$(docker image inspect --format '{{.Id}}' "$image")"
  {
    printf 'cache-schema=v1\0%s\0%s\0%s\0' "$image_id" "$repo_dir" "$root_version"
    local manifest
    for manifest in composer.json composer.lock; do
      printf '%s\0' "$manifest"
      if [[ -f "$workspace/$repo_dir/$manifest" ]]; then
        sha256sum "$workspace/$repo_dir/$manifest" | awk '{print $1}'
      else
        printf 'missing\n'
      fi
    done
  } | sha256sum | awk '{print $1}'
}

case "$profile" in
  framework-hidden|nightwatch-hidden)
    ensure_image
    ;;
  *)
    echo "Unknown profile for laravel_framework_nightwatch_1471639d50d8: $profile" >&2
    exit 2
    ;;
esac

if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
  exit 0
fi

framework_dependency_id="$(dependency_fingerprint "repos/laravel/framework" "12.x-dev")"
framework_dependency_dir="$evaluator_vendor_root/framework-hidden/$framework_dependency_id"
mkdir -p "$cache_root" "$framework_dependency_dir/vendor"

docker_mounts=(
  -v "$workspace:/workspace"
  -v "$cache_root:/ecosync-cache"
  -v "$task_dir/environment/run_laravel_profile_inside.sh:/opt/ecosync/run-laravel-profile.sh:ro"
  -v "$framework_dependency_dir:/evaluator-composer-deps/framework"
  -v "$framework_dependency_dir/vendor:/workspace/repos/laravel/framework/vendor"
)

if [[ "$profile" == "nightwatch-hidden" ]]; then
  nightwatch_dependency_id="$(dependency_fingerprint "repos/laravel/nightwatch" "1.x-dev")"
  nightwatch_dependency_dir="$evaluator_vendor_root/nightwatch-hidden/$nightwatch_dependency_id"
  mkdir -p "$nightwatch_dependency_dir/vendor"
  docker_mounts+=(
    -v "$nightwatch_dependency_dir:/evaluator-composer-deps/nightwatch"
    -v "$nightwatch_dependency_dir/vendor:/workspace/repos/laravel/nightwatch/vendor"
  )
fi

timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-3600}" docker run --rm \
  --name "ecosync_${profile}_$$_${RANDOM}" \
  -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
  -e HOME=/tmp/ecosync-home \
  -e ECOSYNC_WORKSPACE=/workspace \
  -e ECOSYNC_CACHE_ROOT=/ecosync-cache \
  -e COMPOSER_HOME=/ecosync-cache/composer/home \
  -e COMPOSER_CACHE_DIR=/ecosync-cache/composer/cache \
  -e CI=1 \
  -e FORCE_COLOR=0 \
  "${docker_mounts[@]}" \
  -w /workspace \
  "$image" \
  bash /opt/ecosync/run-laravel-profile.sh "$profile"
