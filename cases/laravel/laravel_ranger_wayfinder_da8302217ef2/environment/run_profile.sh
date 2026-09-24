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
image="ecosyncbench/deps/laravel-ranger-wayfinder:da8302217ef2"
cache_root="${ECOSYNC_LARAVEL_CACHE_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/laravel-ranger-wayfinder-da8302217ef2}}"
evaluator_vendor_root="${ECOSYNC_EVALUATOR_COMPOSER_VENDOR_CACHE_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/evaluator-dependency-cache/composer-vendor}}/laravel_ranger_wayfinder_da8302217ef2"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build \
      -t "$image" \
      -f "$task_dir/environment/laravel-php-node.Dockerfile" \
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

restore_cached_lock() {
  local repo_dir="$1"
  local dependency_dir="$2"
  if [[ ! -f "$workspace/$repo_dir/composer.lock" && -f "$dependency_dir/composer.lock" ]]; then
    cp "$dependency_dir/composer.lock" "$workspace/$repo_dir/composer.lock"
  fi
}

save_generated_lock() {
  local repo_dir="$1"
  local dependency_dir="$2"
  if [[ -f "$workspace/$repo_dir/composer.lock" && ! -f "$dependency_dir/composer.lock" ]]; then
    cp "$workspace/$repo_dir/composer.lock" "$dependency_dir/composer.lock.tmp"
    mv "$dependency_dir/composer.lock.tmp" "$dependency_dir/composer.lock"
  fi
}

case "$profile" in
  ranger-hidden|wayfinder-hidden)
    ensure_image
    ;;
  *)
    echo "Unknown profile for laravel_ranger_wayfinder_da8302217ef2: $profile" >&2
    exit 2
    ;;
esac

if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
  exit 0
fi

case "$profile" in
  ranger-hidden)
    repo_dir="repos/laravel/ranger"
    root_version="1.x-dev"
    ;;
  wayfinder-hidden)
    repo_dir="repos/laravel/wayfinder"
    root_version="1.x-dev"
    ;;
esac

dependency_id="$(dependency_fingerprint "$repo_dir" "$root_version")"
dependency_dir="$evaluator_vendor_root/$profile/$dependency_id"
container_workdir="/workspace/$repo_dir"
mkdir -p "$cache_root" "$dependency_dir/vendor"

exec 8>"$dependency_dir/cache.lock"
flock 8
restore_cached_lock "$repo_dir" "$dependency_dir"

status=0
docker run --rm \
  -e ECOSYNC_WORKSPACE=/workspace \
  -e ECOSYNC_CACHE_ROOT=/ecosync-cache \
  -e COMPOSER_HOME=/ecosync-cache/composer/home \
  -e COMPOSER_CACHE_DIR=/ecosync-cache/composer/cache \
  -e npm_config_cache=/ecosync-cache/npm \
  -v "$workspace:/workspace" \
  -v "$cache_root:/ecosync-cache" \
  -v "$dependency_dir/vendor:/workspace/$repo_dir/vendor" \
  -w "$container_workdir" \
  "$image" \
  bash /opt/ecosync/run-laravel-profile.sh "$profile" || status=$?

save_generated_lock "$repo_dir" "$dependency_dir"
exit "$status"
