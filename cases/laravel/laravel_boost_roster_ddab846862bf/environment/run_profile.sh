#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
host_repo_root="$(pwd -P)"
image="ecosyncbench/base/php-composer:8.4"
cache_root="${ECOSYNC_LARAVEL_CACHE_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/laravel-boost-roster-ddab846862bf}}"
evaluator_vendor_root="${ECOSYNC_EVALUATOR_COMPOSER_VENDOR_CACHE_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/evaluator-dependency-cache/composer-vendor}}/laravel_boost_roster_ddab846862bf"

mkdir -p \
  "$workspace/.ecosyncbench/test-reports" \
  "$cache_root/composer/cache" \
  "$cache_root/composer/home" \
  "$evaluator_vendor_root/locks"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" \
      -f "$host_repo_root/benchmark/images/base/php-8.4-composer/Dockerfile" \
      "$host_repo_root"
  fi
}

dependency_fingerprint() {
  local repo_dir="$1"
  local root_version="$2"
  local update_roster="$3"
  local image_id
  image_id="$(docker image inspect --format '{{.Id}}' "$image")"
  {
    printf '%s\0%s\0%s\0' "$image_id" "$root_version" "$update_roster"
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

run_pest() {
  local repo_dir="$1"
  local root_version="$2"
  local junit_name="$3"
  local update_roster="$4"
  shift 4

  ensure_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image" bash -lc 'php -v >/dev/null && composer --version >/dev/null'
    return 0
  fi

  local dependency_id dependency_dir vendor_dir
  dependency_id="$(dependency_fingerprint "$repo_dir" "$root_version" "$update_roster")"
  dependency_dir="$evaluator_vendor_root/$junit_name/$dependency_id"
  vendor_dir="$dependency_dir/vendor"
  mkdir -p "$dependency_dir" "$vendor_dir"

  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-3600}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e COMPOSER_ALLOW_SUPERUSER=1 \
    -e COMPOSER_ROOT_VERSION="$root_version" \
    -e COMPOSER_HOME=/ecosync-cache/composer/home \
    -e COMPOSER_CACHE_DIR=/ecosync-cache/composer/cache \
    -e ECOSYNC_COMPOSER_LOCK="/evaluator-vendor-locks/$junit_name-$dependency_id.lock" \
    -e ECOSYNC_UPDATE_ROSTER="$update_roster" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/ecosync-cache" \
    -v "$evaluator_vendor_root/locks:/evaluator-vendor-locks" \
    -v "$dependency_dir:/evaluator-composer-deps" \
    -v "$vendor_dir:/workspace/$repo_dir/vendor" \
    -w "/workspace/$repo_dir" \
    "$image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /tmp/ecosync-home /workspace/.ecosyncbench/test-reports
      exec 9>"$ECOSYNC_COMPOSER_LOCK"
      flock 9
      composer config --global audit.block-insecure false
      cached_lock=0
      if [[ ! -f composer.lock && -f /evaluator-composer-deps/composer.lock ]]; then
        cp /evaluator-composer-deps/composer.lock composer.lock
        cached_lock=1
      fi
      composer install --no-interaction --prefer-dist --no-progress
      if [[ "$ECOSYNC_UPDATE_ROSTER" == "1" && "$cached_lock" == "0" ]]; then
        composer update laravel/roster --with-all-dependencies --no-interaction --prefer-dist --no-progress
      fi
      if [[ -f composer.lock && ! -f /evaluator-composer-deps/composer.lock ]]; then
        cp composer.lock /evaluator-composer-deps/composer.lock.tmp
        mv /evaluator-composer-deps/composer.lock.tmp /evaluator-composer-deps/composer.lock
      fi
      ./vendor/bin/pest --colors=never --log-junit "/workspace/.ecosyncbench/test-reports/'"$junit_name"'.xml" "$@"
    ' bash "$@"
}

case "$profile" in
  boost-hidden)
    run_pest "repos/laravel/boost" "dev-main" "$profile" 1 \
      tests/Feature/Install/GuidelineComposerTest.php \
      tests/Feature/Install/SkillComposerTest.php \
      tests/Unit/Install/ThirdPartyPackageTest.php
    ;;
  roster-hidden)
    run_pest "repos/laravel/roster" "dev-main" "$profile" 0 \
      tests/Unit/CheckTest.php \
      tests/Unit/RosterTest.php \
      tests/Unit/Scanners/ComposerTest.php \
      tests/Unit/Scanners/PackageLockTest.php
    ;;
  *)
    echo "Unknown profile for laravel_boost_roster_ddab846862bf: $profile" >&2
    exit 2
    ;;
esac
