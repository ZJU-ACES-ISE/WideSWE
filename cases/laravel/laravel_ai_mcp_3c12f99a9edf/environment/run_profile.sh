#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
host_repo_root="$(pwd -P)"
image="ecosyncbench/base/php-composer:8.4"
cache_root="${ECOSYNC_LARAVEL_CACHE_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/laravel-ai-mcp-3c12f99a9edf}}"
evaluator_vendor_root="${ECOSYNC_EVALUATOR_COMPOSER_VENDOR_CACHE_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/evaluator-dependency-cache/composer-vendor}}/laravel_ai_mcp_3c12f99a9edf"

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
  local image_id
  image_id="$(docker image inspect --format '{{.Id}}' "$image")"
  {
    printf '%s\0%s\0' "$image_id" "$root_version"
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

run_php_tests() {
  local repo_dir="$1"
  local root_version="$2"
  local junit_name="$3"
  local runner="$4"
  shift 4

  ensure_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image" bash -lc 'php -m | grep -q pdo_sqlite && php -m | grep -q gmp && composer --version >/dev/null'
    return 0
  fi

  local dependency_id dependency_dir vendor_dir
  dependency_id="$(dependency_fingerprint "$repo_dir" "$root_version")"
  dependency_dir="$evaluator_vendor_root/$junit_name/$dependency_id"
  vendor_dir="$dependency_dir/vendor"
  mkdir -p "$dependency_dir" "$vendor_dir"

  local framework_mount=()
  local framework_response_factory="$workspace/repos/laravel/framework/src/Illuminate/Routing/ResponseFactory.php"
  local vendor_response_factory="/workspace/$repo_dir/vendor/laravel/framework/src/Illuminate/Routing/ResponseFactory.php"
  if [[ -f "$framework_response_factory" && -f "$vendor_dir/laravel/framework/src/Illuminate/Routing/ResponseFactory.php" ]]; then
    framework_mount=(
      -v "$framework_response_factory:$vendor_response_factory:ro"
    )
  fi

  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-2400}" docker run --rm \
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
    -v "$workspace:/workspace" \
    -v "$cache_root:/ecosync-cache" \
    -v "$evaluator_vendor_root/locks:/evaluator-vendor-locks" \
    -v "$dependency_dir:/evaluator-composer-deps" \
    -v "$vendor_dir:/workspace/$repo_dir/vendor" \
    "${framework_mount[@]}" \
    -w "/workspace/$repo_dir" \
    "$image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /tmp/ecosync-home /workspace/.ecosyncbench/test-reports
      exec 9>"$ECOSYNC_COMPOSER_LOCK"
      flock 9
      composer config --global audit.block-insecure false
      if [[ ! -f composer.lock && -f /evaluator-composer-deps/composer.lock ]]; then
        cp /evaluator-composer-deps/composer.lock composer.lock
      fi
      composer install --no-interaction --prefer-dist --no-progress
      if [[ -f composer.lock && ! -f /evaluator-composer-deps/composer.lock ]]; then
        cp composer.lock /evaluator-composer-deps/composer.lock.tmp
        mv /evaluator-composer-deps/composer.lock.tmp /evaluator-composer-deps/composer.lock
      fi
      if [[ "'"$runner"'" == "pest" ]]; then
        ./vendor/bin/pest --colors=never --log-junit "/workspace/.ecosyncbench/test-reports/'"$junit_name"'.xml" "$@"
      else
        ./vendor/bin/phpunit --colors=never --log-junit "/workspace/.ecosyncbench/test-reports/'"$junit_name"'.xml" "$@"
      fi
    ' bash "$@"
}

case "$profile" in
  ai-hidden)
    run_php_tests "repos/laravel/ai" "dev-main" "$profile" phpunit \
      tests/Feature/StreamableAgentResponseStreamingTest.php
    ;;
  mcp-hidden)
    run_php_tests "repos/laravel/mcp" "1.x-dev" "$profile" pest \
      tests/Feature/Server/HttpTransportStreamTest.php
    ;;
  *)
    echo "Unknown profile for laravel_ai_mcp_3c12f99a9edf: $profile" >&2
    exit 2
    ;;
esac
