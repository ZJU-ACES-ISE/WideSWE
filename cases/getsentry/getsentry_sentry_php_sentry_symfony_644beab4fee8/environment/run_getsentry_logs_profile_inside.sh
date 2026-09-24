#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
workspace="${ECOSYNC_WORKSPACE:-/workspace}"
cache_root="${ECOSYNC_CACHE_ROOT:-/ecosync-cache}"

export COMPOSER_ALLOW_SUPERUSER=1
export COMPOSER_HOME="${COMPOSER_HOME:-$cache_root/composer/home}"
export COMPOSER_CACHE_DIR="${COMPOSER_CACHE_DIR:-$cache_root/composer/cache}"
export SYMFONY_DEPRECATIONS_HELPER="${SYMFONY_DEPRECATIONS_HELPER:-max[self]=0}"

mkdir -p "$COMPOSER_HOME" "$COMPOSER_CACHE_DIR" "$workspace/.ecosyncbench/test-reports"

composer_global_setup() {
  composer config --global audit.block-insecure false
}

set_path_repository() {
  local repo_dir="$1"
  local package="$2"
  local target="$3"
  local version="$4"
  (
    cd "$repo_dir"
    php -r '
      $file = "composer.json";
      $package = $argv[1];
      $target = $argv[2];
      $version = $argv[3];
      $name = "ecosync-" . strtr($package, "/", "-");
      $data = json_decode(file_get_contents($file), true);
      $data["repositories"][$name] = [
        "type" => "path",
        "url" => $target,
        "options" => [
          "symlink" => true,
          "versions" => [$package => $version]
        ]
      ];
      file_put_contents($file, json_encode($data, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES) . PHP_EOL);
    ' "$package" "$target" "$version"
  )
}

install_sentry_php_deps() {
  local repo_dir="$workspace/repos/getsentry/sentry-php"
  (
    cd "$repo_dir"
    composer_global_setup
    composer remove carthage-software/mago phpstan/phpstan friendsofphp/php-cs-fixer --dev --no-interaction --no-update || true
    composer require phpunit/phpunit:'^9.6.34' --dev --no-interaction --no-update
    composer update --no-progress --no-interaction --prefer-dist --no-install --no-plugins --no-scripts --no-audit
    composer install --no-progress --no-interaction --prefer-dist --no-plugins --no-scripts
  )
}

install_sentry_symfony_deps() {
  local repo_dir="$workspace/repos/getsentry/sentry-symfony"
  local sentry_php_dir="$workspace/repos/getsentry/sentry-php"
  (
    cd "$repo_dir"
    composer_global_setup
    composer remove vimeo/psalm phpstan/phpstan friendsofphp/php-cs-fixer --dev --no-interaction --no-update || true
  )
  set_path_repository "$repo_dir" "sentry/sentry" "../sentry-php" "4.23.99"
  (
    cd "$repo_dir"
    test -d "$sentry_php_dir"
    composer require phpunit/phpunit:'^9.6.34' --dev --no-interaction --no-update
    composer update --no-progress --no-interaction --prefer-dist --no-install --no-scripts --no-audit
    composer install --no-progress --no-interaction --prefer-dist --no-scripts
  )
}

install_sentry_laravel_deps() {
  local repo_dir="$workspace/repos/getsentry/sentry-laravel"
  local sentry_php_dir="$workspace/repos/getsentry/sentry-php"
  (
    cd "$repo_dir"
    composer_global_setup
    composer remove phpstan/phpstan friendsofphp/php-cs-fixer --dev --no-interaction --no-update || true
  )
  set_path_repository "$repo_dir" "sentry/sentry" "../sentry-php" "4.23.99"
  (
    cd "$repo_dir"
    test -d "$sentry_php_dir"
    composer update --no-progress --no-interaction --prefer-dist --no-install --no-plugins --no-scripts --no-audit
    composer install --no-progress --no-interaction --prefer-dist --no-plugins --no-scripts
  )
}

run_phpunit_each() {
  local repo_dir="$1"
  local junit_name="$2"
  shift 2
  mkdir -p "$workspace/.ecosyncbench/test-reports/$junit_name"
  local failed=0
  local index=0
  (
    cd "$repo_dir"
    for test_path in "$@"; do
      index=$((index + 1))
      safe_name="$(printf '%s' "$test_path" | sed -E 's#[^0-9A-Za-z_.-]+#_#g')"
      set +e
      ./vendor/bin/phpunit \
        --colors=never \
        --log-junit "$workspace/.ecosyncbench/test-reports/$junit_name/$(printf '%02d' "$index")-${safe_name}.xml" \
        "$test_path"
      code=$?
      set -e
      if [[ "$code" -ne 0 ]]; then
        failed=1
      fi
    done
    exit "$failed"
  )
}

case "$profile" in
  sentry_php-hidden)
    install_sentry_php_deps
    run_phpunit_each "$workspace/repos/getsentry/sentry-php" "$profile" \
      tests/Logs/LogsAggregatorTest.php \
      tests/OptionsTest.php
    ;;
  sentry_symfony-hidden)
    install_sentry_php_deps
    install_sentry_symfony_deps
    run_phpunit_each "$workspace/repos/getsentry/sentry-symfony" "$profile" \
      tests/DependencyInjection \
      tests/End2End/LoggingFlushThresholdEnd2EndTest.php
    ;;
  sentry_laravel-hidden)
    install_sentry_php_deps
    install_sentry_laravel_deps
    run_phpunit_each "$workspace/repos/getsentry/sentry-laravel" "$profile" \
      test/Sentry/Features/LogLogsIntegrationTest.php \
      test/Sentry/Laravel/LaravelContainerConfigOptionsTest.php
    ;;
  *)
    echo "Unknown profile for getsentry_sentry_php_sentry_symfony_644beab4fee8: $profile" >&2
    exit 2
    ;;
esac
