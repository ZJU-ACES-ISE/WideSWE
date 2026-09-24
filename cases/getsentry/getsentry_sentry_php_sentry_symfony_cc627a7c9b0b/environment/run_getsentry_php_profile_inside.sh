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
    php -r '
      $file = "composer.json";
      $data = json_decode(file_get_contents($file), true);
      $data["repositories"]["ecosync-sentry-php"] = [
        "type" => "path",
        "url" => "../sentry-php",
        "options" => [
          "symlink" => true,
          "versions" => ["sentry/sentry" => "4.23.99"]
        ]
      ];
      file_put_contents($file, json_encode($data, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES) . PHP_EOL);
    '
    test -d "$sentry_php_dir"
    composer require phpunit/phpunit:'^9.6.34' --dev --no-interaction --no-update
    composer update --no-progress --no-interaction --prefer-dist --no-install --no-scripts --no-audit
    composer install --no-progress --no-interaction --prefer-dist --no-scripts
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
      tests/FunctionsTest.php \
      tests/Integration/FrankenPhpWorkerModeIntegrationTest.php \
      tests/Integration/RoadRunnerWorkerModeIntegrationTest.php \
      tests/SentrySdkTest.php \
      tests/phpt-oom/php84/out_of_memory_fatal_error_increases_memory_limit.phpt \
      tests/phpt-oom/php85/out_of_memory_fatal_error_increases_memory_limit.phpt \
      tests/phpt/error_handler_captures_errors_not_silencable_on_php_8_and_up.phpt \
      tests/phpt/error_handler_respects_capture_silenced_errors_option.phpt \
      tests/phpt/error_handler_respects_error_types_option_regardless_of_error_reporting.phpt \
      tests/phpt/error_listener_integration_respects_error_types_option.phpt \
      tests/phpt/php84/error_handler_captures_fatal_error.phpt \
      tests/phpt/php84/fatal_error_integration_captures_fatal_error.phpt \
      tests/phpt/php84/fatal_error_integration_respects_error_types_option.phpt \
      tests/phpt/php85/error_handler_captures_fatal_error.phpt \
      tests/phpt/php85/fatal_error_integration_captures_fatal_error.phpt \
      tests/phpt/php85/fatal_error_integration_respects_error_types_option.phpt \
      tests/phpt/serialize_broken_class.phpt \
      tests/phpt/serialize_callable_that_makes_autoloader_throw.phpt \
      tests/phpt/test_callable_serialization.phpt
    ;;
  sentry_symfony-hidden)
    install_sentry_php_deps
    install_sentry_symfony_deps
    run_phpunit_each "$workspace/repos/getsentry/sentry-symfony" "$profile" \
      tests/DependencyInjection \
      tests/End2End/End2EndTest.php \
      tests/End2End/RuntimeContextEnd2EndTest.php \
      tests/End2End/TracingEnd2EndTest.php \
      tests/EventListener/MessengerListenerTest.php \
      tests/Integration/RequestFetcherTest.php
    ;;
  *)
    echo "Unknown profile for getsentry_sentry_php_sentry_symfony_cc627a7c9b0b: $profile" >&2
    exit 2
    ;;
esac
