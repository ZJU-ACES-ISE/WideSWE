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
image="ecosyncbench/deps/getsentry-php-symfony-laravel-logs:644beab4fee8"
cache_root="${ECOSYNC_GETSENTRY_LOGS_CACHE_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/getsentry-php-symfony-laravel-logs-644beab4fee8}}"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build \
      -t "$image" \
      -f "$task_dir/environment/getsentry-php-symfony-laravel-logs.Dockerfile" \
      "$task_dir/environment"
  fi
}

case "$profile" in
  sentry_php-hidden|sentry_symfony-hidden|sentry_laravel-hidden)
    ensure_image
    ;;
  *)
    echo "Unknown profile for getsentry_sentry_php_sentry_symfony_644beab4fee8: $profile" >&2
    exit 2
    ;;
esac

if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
  exit 0
fi

mkdir -p "$cache_root"
vendor_cache_root="$cache_root/vendor-cache/getsentry_sentry_php_sentry_symfony_644beab4fee8"
vendor_mounts=()

add_vendor_mount() {
  local profile_name="$1"
  local repo_name="$2"
  local host_vendor="$vendor_cache_root/$profile_name/vendor"
  local workspace_vendor="$workspace/repos/getsentry/$repo_name/vendor"
  mkdir -p "$host_vendor" "$workspace_vendor"
  vendor_mounts+=(
    -v "$host_vendor:/workspace/repos/getsentry/$repo_name/vendor"
  )
}

case "$profile" in
  sentry_php-hidden)
    add_vendor_mount sentry_php-hidden sentry-php
    ;;
  sentry_symfony-hidden)
    add_vendor_mount sentry_php-hidden sentry-php
    add_vendor_mount sentry_symfony-hidden sentry-symfony
    ;;
  sentry_laravel-hidden)
    add_vendor_mount sentry_php-hidden sentry-php
    add_vendor_mount sentry_laravel-hidden sentry-laravel
    ;;
esac

docker run --rm \
  -e ECOSYNC_WORKSPACE=/workspace \
  -e ECOSYNC_CACHE_ROOT=/ecosync-cache \
  -e COMPOSER_HOME=/ecosync-cache/composer/home \
  -e COMPOSER_CACHE_DIR=/ecosync-cache/composer/cache \
  -v "$workspace:/workspace" \
  -v "$cache_root:/ecosync-cache" \
  "${vendor_mounts[@]}" \
  -w /workspace \
  "$image" \
  bash /opt/ecosync/run-getsentry-logs-profile.sh "$profile"
