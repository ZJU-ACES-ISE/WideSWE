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
image="ecosyncbench/deps/getsentry-php-runtime:cc627a7c9b0b"
cache_root="${ECOSYNC_GETSENTRY_PHP_CACHE_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/getsentry-php-symfony-cc627a7c9b0b}}"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build \
      -t "$image" \
      -f "$task_dir/environment/getsentry-php-runtime.Dockerfile" \
      "$task_dir/environment"
  fi
}

case "$profile" in
  sentry_php-hidden|sentry_symfony-hidden)
    ensure_image
    ;;
  *)
    echo "Unknown profile for getsentry_sentry_php_sentry_symfony_cc627a7c9b0b: $profile" >&2
    exit 2
    ;;
esac

if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
  exit 0
fi

mkdir -p "$cache_root"
docker run --rm \
  -e ECOSYNC_WORKSPACE=/workspace \
  -e ECOSYNC_CACHE_ROOT=/ecosync-cache \
  -e COMPOSER_HOME=/ecosync-cache/composer/home \
  -e COMPOSER_CACHE_DIR=/ecosync-cache/composer/cache \
  -v "$workspace:/workspace" \
  -v "$cache_root:/ecosync-cache" \
  -w /workspace \
  "$image" \
  bash /opt/ecosync/run-getsentry-php-profile.sh "$profile"
