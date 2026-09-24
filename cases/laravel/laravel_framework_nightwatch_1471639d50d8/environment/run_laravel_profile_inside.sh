#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
workspace="${ECOSYNC_WORKSPACE:-/workspace}"
cache_root="${ECOSYNC_CACHE_ROOT:-/ecosync-cache}"

export COMPOSER_ALLOW_SUPERUSER=1
export COMPOSER_HOME="${COMPOSER_HOME:-$cache_root/composer/home}"
export COMPOSER_CACHE_DIR="${COMPOSER_CACHE_DIR:-$cache_root/composer/cache}"
export CI=1
export FORCE_COLOR=0

mkdir -p "${HOME:-/tmp/ecosync-home}" "$COMPOSER_HOME" "$COMPOSER_CACHE_DIR" "$workspace/.ecosyncbench/test-reports"

composer_install_cached() {
  local dir="$1"
  local root_version="$2"
  local dependency_dir="$3"
  (
    cd "$dir"
    export COMPOSER_ROOT_VERSION="$root_version"
    composer config --global audit.block-insecure false
    if [[ ! -f composer.lock && -f "$dependency_dir/composer.lock" ]]; then
      cp "$dependency_dir/composer.lock" composer.lock
    fi
    composer install \
      --no-interaction \
      --no-progress \
      --prefer-dist
    if [[ -f composer.lock && ! -f "$dependency_dir/composer.lock" ]]; then
      cp composer.lock "$dependency_dir/composer.lock.tmp"
      mv "$dependency_dir/composer.lock.tmp" "$dependency_dir/composer.lock"
    fi
  )
}

link_nightwatch_to_local_framework() {
  local nightwatch="$workspace/repos/laravel/nightwatch"
  local framework="$workspace/repos/laravel/framework"
  [[ -d "$nightwatch/vendor/laravel" && -d "$framework/src/Illuminate" ]] || return 0

  rm -rf "$nightwatch/vendor/laravel/framework"
  mkdir -p "$nightwatch/vendor/laravel/framework"
  (
    cd "$framework"
    tar --exclude=.git --exclude=vendor --exclude=node_modules -cf - .
  ) | (
    cd "$nightwatch/vendor/laravel/framework"
    tar -xf -
  )
  (
    cd "$nightwatch"
    composer dump-autoload --no-interaction --no-scripts
  )
}

case "$profile" in
  framework-hidden)
    exec 8>/evaluator-composer-deps/framework/cache.lock
    flock 8
    composer_install_cached \
      "$workspace/repos/laravel/framework" \
      "12.x-dev" \
      "/evaluator-composer-deps/framework"
    mkdir -p "$workspace/.ecosyncbench/test-reports/framework-hidden"
    (
      cd "$workspace/repos/laravel/framework"
      ./vendor/bin/phpunit \
        --colors=never \
        --log-junit "$workspace/.ecosyncbench/test-reports/framework-hidden/framework-hidden.xml" \
        tests/Integration/Database/DatabaseConnectionsTest.php
    )
    ;;
  nightwatch-hidden)
    exec 8>/evaluator-composer-deps/framework/cache.lock
    flock 8
    exec 9>/evaluator-composer-deps/nightwatch/cache.lock
    flock 9
    composer_install_cached \
      "$workspace/repos/laravel/framework" \
      "12.x-dev" \
      "/evaluator-composer-deps/framework"
    composer_install_cached \
      "$workspace/repos/laravel/nightwatch" \
      "1.x-dev" \
      "/evaluator-composer-deps/nightwatch"
    link_nightwatch_to_local_framework
    sed -Ei "s/(const VERSION = )'[^']+';/\1'12.45.0';/" \
      "$workspace/repos/laravel/nightwatch/vendor/laravel/framework/src/Illuminate/Foundation/Application.php"
    mkdir -p "$workspace/.ecosyncbench/test-reports/nightwatch-hidden"
    (
      cd "$workspace/repos/laravel/nightwatch"
      ./vendor/bin/phpunit \
        --colors=never \
        --log-junit "$workspace/.ecosyncbench/test-reports/nightwatch-hidden/nightwatch-hidden.xml" \
        tests/Feature/Sensors/QuerySensorTest.php \
        tests/Unit/ArchitectureTest.php
    )
    ;;
  *)
    echo "Unknown profile: $profile" >&2
    exit 2
    ;;
esac
