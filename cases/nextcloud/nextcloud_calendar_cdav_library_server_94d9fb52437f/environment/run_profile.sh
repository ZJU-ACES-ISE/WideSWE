#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
host_repo_root="$(pwd -P)"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy}"
node_image="ecosyncbench/base/node:24-bookworm"
php_image="ecosyncbench/deps/nextcloud-php:8.4-gd-apcu-v1"
evaluator_vendor_root="${ECOSYNC_EVALUATOR_COMPOSER_VENDOR_CACHE_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/evaluator-dependency-cache/composer-vendor}}/nextcloud_calendar_cdav_library_server_94d9fb52437f"

mkdir -p "$workspace/.ecosyncbench/test-reports" \
  "$cache_root/npm-cache/nextcloud/calendar" \
  "$cache_root/npm-cache/nextcloud/cdav-library" \
  "$cache_root/composer-cache/nextcloud/calendar" \
  "$cache_root/composer-cache/nextcloud/server" \
  "$evaluator_vendor_root/locks"

docker_common_args=(
  --rm
  -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}"
  -e HOME=/tmp/ecosync-home
  -e CI=1
  -e FORCE_COLOR=0
  -v "$workspace:/workspace"
)

ensure_node_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$node_image" >/dev/null 2>&1; then
    docker build -t "$node_image" -f "$host_repo_root/benchmark/images/base/node-22-bookworm/Dockerfile" "$host_repo_root/benchmark/images/base/node-22-bookworm"
  fi
}

ensure_php_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$php_image" >/dev/null 2>&1; then
    docker build -t "$php_image" -f "$host_repo_root/benchmark/images/deps/nextcloud-php/Dockerfile" "$host_repo_root/benchmark/images/deps/nextcloud-php"
  fi
}

ensure_nextcloud_server_submodule() {
  git -C "$workspace/repos/nextcloud/server" submodule update --init --depth 1 3rdparty
}

composer_dependency_fingerprint() {
  local repo_dir="$1"
  shift
  local image_id
  image_id="$(docker image inspect --format '{{.Id}}' "$php_image")"
  {
    printf '%s\0' "$image_id"
    local manifest
    for manifest in "$@"; do
      printf '%s\0' "$manifest"
      if [[ -f "$workspace/$repo_dir/$manifest" ]]; then
        sha256sum "$workspace/$repo_dir/$manifest" | awk '{print $1}'
      else
        printf 'missing\n'
      fi
    done
  } | sha256sum | awk '{print $1}'
}

server_dependency_fingerprint() {
  composer_dependency_fingerprint "repos/nextcloud/server" \
    composer.json composer.lock \
    vendor-bin/behat/composer.json vendor-bin/behat/composer.lock \
    vendor-bin/cs-fixer/composer.json vendor-bin/cs-fixer/composer.lock \
    vendor-bin/openapi-extractor/composer.json vendor-bin/openapi-extractor/composer.lock \
    vendor-bin/phpunit/composer.json vendor-bin/phpunit/composer.lock \
    vendor-bin/psalm/composer.json vendor-bin/psalm/composer.lock \
    vendor-bin/rector/composer.json vendor-bin/rector/composer.lock
}

calendar_dependency_fingerprint() {
  composer_dependency_fingerprint "repos/nextcloud/calendar" \
    composer.json composer.lock \
    vendor-bin/cs-fixer/composer.json vendor-bin/cs-fixer/composer.lock \
    vendor-bin/phpunit/composer.json vendor-bin/phpunit/composer.lock \
    vendor-bin/psalm/composer.json vendor-bin/psalm/composer.lock
}

run_node_vitest() {
  local repo_dir="$1"
  local report_dir="$2"
  local cache_dir="$3"
  shift 3
  ensure_node_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$node_image" bash -lc 'node --version >/dev/null && npm --version >/dev/null'
    return 0
  fi
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-3600}" docker run \
    "${docker_common_args[@]}" \
    -e CYPRESS_INSTALL_BINARY=0 \
    -e PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    -e PUPPETEER_SKIP_DOWNLOAD=1 \
    -e npm_config_cache=/npm-cache \
    -v "$cache_dir:/npm-cache" \
    -w "/workspace/$repo_dir" \
    "$node_image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /tmp/ecosync-home "/workspace/.ecosyncbench/test-reports/$1"
      marker="nextcloud-$1-npm-ci-v1"
      exec 9>"/npm-cache/$1-node-modules.lock"
      flock 9
      if [[ "$(cat node_modules/.ecosyncbench-npm-ci-ready 2>/dev/null || true)" != "$marker" ]]; then
        for attempt in 1 2 3; do
          npm ci --prefer-offline --no-audit --no-fund && break
          if [[ "$attempt" == 3 ]]; then
            exit 1
          fi
          sleep $((attempt * 5))
        done
        printf "%s\n" "$marker" > node_modules/.ecosyncbench-npm-ci-ready
      fi
      flock -u 9
      npx vitest run "${@:3}" --reporter=junit --outputFile="/workspace/.ecosyncbench/test-reports/$1/$2"
    ' \
    bash "$report_dir" junit.xml "$@"
}

run_server_install() {
  rm -rf "$workspace/repos/nextcloud/server/data" "$workspace/repos/nextcloud/server/config/config.php"
  mkdir -p "$workspace/repos/nextcloud/server/data"
  cp "$workspace/repos/nextcloud/server/tests/preseed-config.php" "$workspace/repos/nextcloud/server/config/config.php"
  php ./occ maintenance:install -vvv \
    --database=sqlite \
    --database-name=oc_autotest \
    --database-host=localhost \
    --database-user=oc_autotest \
    --database-pass=owncloud \
    --admin-user=admin \
    --admin-pass=admin \
    --data-dir="$(pwd)/data"
}

run_server_phpunit() {
  ensure_php_image
  ensure_nextcloud_server_submodule
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$php_image" bash -lc 'php -m | grep -q pdo_sqlite && php -m | grep -q gd && composer --version >/dev/null'
    return 0
  fi
  local dependency_id dependency_dir calendar_dependency_id calendar_dependency_dir calendar_app_dir
  dependency_id="$(server_dependency_fingerprint)"
  dependency_dir="$evaluator_vendor_root/server-hidden/$dependency_id"
  calendar_dependency_id="$(calendar_dependency_fingerprint)"
  calendar_dependency_dir="$evaluator_vendor_root/calendar-hidden/$calendar_dependency_id"
  calendar_app_dir="$workspace/repos/nextcloud/server/apps/calendar"
  mkdir -p \
    "$dependency_dir/vendor" \
    "$dependency_dir/vendor-bin/behat/vendor" \
    "$dependency_dir/vendor-bin/cs-fixer/vendor" \
    "$dependency_dir/vendor-bin/openapi-extractor/vendor" \
    "$dependency_dir/vendor-bin/phpunit/vendor" \
    "$dependency_dir/vendor-bin/psalm/vendor" \
    "$dependency_dir/vendor-bin/rector/vendor" \
    "$calendar_dependency_dir/vendor" \
    "$calendar_dependency_dir/vendor-bin/cs-fixer/vendor" \
    "$calendar_dependency_dir/vendor-bin/phpunit/vendor" \
    "$calendar_dependency_dir/vendor-bin/psalm/vendor" \
    "$calendar_app_dir/vendor" \
    "$calendar_app_dir/vendor-bin/cs-fixer/vendor" \
    "$calendar_app_dir/vendor-bin/phpunit/vendor" \
    "$calendar_app_dir/vendor-bin/psalm/vendor"
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-3600}" docker run \
    "${docker_common_args[@]}" \
    -e COMPOSER_CACHE_DIR=/composer-cache \
    -e COMPOSER_HOME=/tmp/composer \
    -e ECOSYNC_COMPOSER_LOCK=/evaluator-composer-deps/install.lock \
    -v "$cache_root/composer-cache/nextcloud/server:/composer-cache" \
    -v "$dependency_dir:/evaluator-composer-deps" \
    -v "$dependency_dir/vendor:/workspace/repos/nextcloud/server/vendor" \
    -v "$dependency_dir/vendor-bin/behat/vendor:/workspace/repos/nextcloud/server/vendor-bin/behat/vendor" \
    -v "$dependency_dir/vendor-bin/cs-fixer/vendor:/workspace/repos/nextcloud/server/vendor-bin/cs-fixer/vendor" \
    -v "$dependency_dir/vendor-bin/openapi-extractor/vendor:/workspace/repos/nextcloud/server/vendor-bin/openapi-extractor/vendor" \
    -v "$dependency_dir/vendor-bin/phpunit/vendor:/workspace/repos/nextcloud/server/vendor-bin/phpunit/vendor" \
    -v "$dependency_dir/vendor-bin/psalm/vendor:/workspace/repos/nextcloud/server/vendor-bin/psalm/vendor" \
    -v "$dependency_dir/vendor-bin/rector/vendor:/workspace/repos/nextcloud/server/vendor-bin/rector/vendor" \
    -v "$calendar_dependency_dir/vendor:/workspace/repos/nextcloud/server/apps/calendar/vendor" \
    -v "$calendar_dependency_dir/vendor-bin/cs-fixer/vendor:/workspace/repos/nextcloud/server/apps/calendar/vendor-bin/cs-fixer/vendor" \
    -v "$calendar_dependency_dir/vendor-bin/phpunit/vendor:/workspace/repos/nextcloud/server/apps/calendar/vendor-bin/phpunit/vendor" \
    -v "$calendar_dependency_dir/vendor-bin/psalm/vendor:/workspace/repos/nextcloud/server/apps/calendar/vendor-bin/psalm/vendor" \
    -w /workspace/repos/nextcloud/server \
    "$php_image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /tmp/ecosync-home /tmp/composer /workspace/.ecosyncbench/test-reports/server
      exec 9>"$ECOSYNC_COMPOSER_LOCK"
      flock 9
      composer install --no-interaction --no-progress
      run_server_install() {
        rm -rf data config/config.php
        mkdir -p data
        cp tests/preseed-config.php config/config.php
        php ./occ maintenance:install -vvv --database=sqlite --database-name=oc_autotest --database-host=localhost --database-user=oc_autotest --database-pass=owncloud --admin-user=admin --admin-pass=admin --data-dir="$(pwd)/data"
      }
      run_server_install
      php -f tests/enable_all.php
      lib/composer/bin/phpunit --configuration tests/phpunit-autotest.xml --log-junit /workspace/.ecosyncbench/test-reports/server/junit.xml apps/dav/tests/unit/CalDAV/CalDavBackendTest.php
    '
}

run_calendar_phpunit() {
  ensure_php_image
  ensure_nextcloud_server_submodule
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$php_image" bash -lc 'php -m | grep -q pdo_sqlite && php -m | grep -q gd && composer --version >/dev/null'
    return 0
  fi
  local dependency_id dependency_dir vendor_dir app_dir
  dependency_id="$(calendar_dependency_fingerprint)"
  dependency_dir="$evaluator_vendor_root/calendar-hidden/$dependency_id"
  vendor_dir="$dependency_dir/vendor"
  app_dir="$workspace/repos/nextcloud/server/apps/calendar"
  mkdir -p \
    "$vendor_dir" \
    "$dependency_dir/vendor-bin/cs-fixer/vendor" \
    "$dependency_dir/vendor-bin/phpunit/vendor" \
    "$dependency_dir/vendor-bin/psalm/vendor"
  rm -rf "$app_dir"
  mkdir -p \
    "$app_dir/vendor" \
    "$app_dir/vendor-bin/cs-fixer/vendor" \
    "$app_dir/vendor-bin/phpunit/vendor" \
    "$app_dir/vendor-bin/psalm/vendor"
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-3600}" docker run \
    "${docker_common_args[@]}" \
    -e COMPOSER_CACHE_DIR=/composer-cache \
    -e COMPOSER_HOME=/tmp/composer \
    -e ECOSYNC_COMPOSER_LOCK=/evaluator-composer-deps/install.lock \
    -v "$cache_root/composer-cache/nextcloud/calendar:/composer-cache" \
    -v "$dependency_dir:/evaluator-composer-deps" \
    -v "$vendor_dir:/workspace/repos/nextcloud/calendar/vendor" \
    -v "$vendor_dir:/workspace/repos/nextcloud/server/apps/calendar/vendor" \
    -v "$dependency_dir/vendor-bin/cs-fixer/vendor:/workspace/repos/nextcloud/calendar/vendor-bin/cs-fixer/vendor" \
    -v "$dependency_dir/vendor-bin/cs-fixer/vendor:/workspace/repos/nextcloud/server/apps/calendar/vendor-bin/cs-fixer/vendor" \
    -v "$dependency_dir/vendor-bin/phpunit/vendor:/workspace/repos/nextcloud/calendar/vendor-bin/phpunit/vendor" \
    -v "$dependency_dir/vendor-bin/phpunit/vendor:/workspace/repos/nextcloud/server/apps/calendar/vendor-bin/phpunit/vendor" \
    -v "$dependency_dir/vendor-bin/psalm/vendor:/workspace/repos/nextcloud/calendar/vendor-bin/psalm/vendor" \
    -v "$dependency_dir/vendor-bin/psalm/vendor:/workspace/repos/nextcloud/server/apps/calendar/vendor-bin/psalm/vendor" \
    -w /workspace/repos/nextcloud/server \
    "$php_image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /tmp/ecosync-home /tmp/composer /workspace/.ecosyncbench/test-reports/calendar-php
      exec 9>"$ECOSYNC_COMPOSER_LOCK"
      flock 9
      cd /workspace/repos/nextcloud/calendar
      composer install --no-interaction --no-progress
      cd /workspace/repos/nextcloud/server
      rm -rf data config/config.php
      mkdir -p data apps/calendar
      find apps/calendar -mindepth 1 -maxdepth 1 ! -name vendor ! -name vendor-bin -exec rm -rf {} +
      cp tests/preseed-config.php config/config.php
      (cd /workspace/repos/nextcloud/calendar && tar \
        --exclude=./.git \
        --exclude=./vendor \
        --exclude=./vendor-bin/cs-fixer/vendor \
        --exclude=./vendor-bin/phpunit/vendor \
        --exclude=./vendor-bin/psalm/vendor \
        -cf - .) | (cd apps/calendar && tar -xf -)
      php ./occ maintenance:install -vvv --database=sqlite --database-name=oc_autotest --database-host=localhost --database-user=oc_autotest --database-pass=owncloud --admin-user=admin --admin-pass=admin --data-dir="$(pwd)/data"
      php ./occ app:enable calendar
      cd apps/calendar
      vendor/bin/phpunit --no-coverage --configuration phpunit.unit.xml --log-junit /workspace/.ecosyncbench/test-reports/calendar-php/junit.xml tests/php/unit/Controller/SettingsControllerTest.php tests/php/unit/Service/CalendarInitialStateServiceTest.php
    '
}

run_calendar_js() {
  run_node_vitest \
    repos/nextcloud/calendar \
    calendar-js \
    "$cache_root/npm-cache/nextcloud/calendar" \
    tests/javascript/unit/defaults/defaultAlarmProvider.test.js \
    tests/javascript/unit/models/calendar.test.js \
    tests/javascript/unit/store/settings.test.js
}

run_cdav_library_js() {
  run_node_vitest \
    repos/nextcloud/cdav-library \
    cdav-library \
    "$cache_root/npm-cache/nextcloud/cdav-library" \
    test/unit/models/calendarTest.js \
    test/unit/parserTest.js \
    test/unit/propset/calendarPropSetTest.js
}

case "$profile" in
  calendar-hidden)
    profile_status=0
    run_calendar_js || profile_status=$?
    run_calendar_phpunit || profile_status=$?
    exit "$profile_status"
    ;;
  cdav_library-hidden)
    run_cdav_library_js
    ;;
  server-hidden)
    run_server_phpunit
    ;;
  *)
    echo "Unknown profile for nextcloud_calendar_cdav_library_server_94d9fb52437f: $profile" >&2
    exit 2
    ;;
esac
