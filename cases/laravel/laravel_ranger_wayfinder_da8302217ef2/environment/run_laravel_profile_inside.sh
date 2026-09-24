#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
workspace="${ECOSYNC_WORKSPACE:-/workspace}"
cache_root="${ECOSYNC_CACHE_ROOT:-/ecosync-cache}"

export COMPOSER_ALLOW_SUPERUSER=1
export COMPOSER_HOME="${COMPOSER_HOME:-$cache_root/composer/home}"
export COMPOSER_CACHE_DIR="${COMPOSER_CACHE_DIR:-$cache_root/composer/cache}"
export npm_config_cache="${npm_config_cache:-$cache_root/npm}"
export CI=1

mkdir -p "$COMPOSER_HOME" "$COMPOSER_CACHE_DIR" "$npm_config_cache" "$workspace/.ecosyncbench/test-reports"

composer_install() {
  local dir="$1"
  local root_version="$2"
  (
    cd "$dir"
    export COMPOSER_ROOT_VERSION="$root_version"
    composer config --global audit.block-insecure false
    composer install --no-interaction --no-progress --prefer-dist
  )
}

case "$profile" in
  ranger-hidden)
    composer_install "$workspace/repos/laravel/ranger" "1.x-dev"
    mkdir -p "$workspace/.ecosyncbench/test-reports/ranger-hidden"
    (
      cd "$workspace/repos/laravel/ranger"
      ./vendor/bin/pest \
        --colors=never \
        --log-junit "$workspace/.ecosyncbench/test-reports/ranger-hidden/ranger-hidden.xml" \
        tests/Feature/RoutesTest.php
    )
    ;;
  wayfinder-hidden)
    composer_install "$workspace/repos/laravel/wayfinder" "1.x-dev"
    mkdir -p "$workspace/.ecosyncbench/test-reports/wayfinder-hidden"
    (
      cd "$workspace/repos/laravel/wayfinder"
      composer run build --no-interaction
      npm install --prefer-offline --no-audit --fund=false
      npx vitest run tests/CamelCaseRouteParameter.test.ts \
        --reporter=default \
        --reporter=junit \
        --outputFile="$workspace/.ecosyncbench/test-reports/wayfinder-hidden/wayfinder-hidden.xml"
    )
    ;;
  *)
    echo "Unknown profile: $profile" >&2
    exit 2
    ;;
esac
