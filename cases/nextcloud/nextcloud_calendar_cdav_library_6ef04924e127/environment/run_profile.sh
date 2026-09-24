#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
node_image="ecosyncbench/base/node:24-bookworm"
php_image="ecosyncbench/deps/nextcloud-php:8.4-apcu-gd-v1"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy}"
evaluator_vendor_root="${ECOSYNC_EVALUATOR_COMPOSER_VENDOR_CACHE_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/evaluator-dependency-cache/composer-vendor}}/nextcloud_calendar_cdav_library_6ef04924e127"
mkdir -p \
  "$cache_root/node-cache/nextcloud/npm" \
  "$cache_root/php-cache/nextcloud/composer" \
  "$evaluator_vendor_root/locks" \
  "$workspace/.ecosyncbench/test-reports"

ensure_node_image() {
  docker image inspect "$node_image" >/dev/null 2>&1 || docker build -t "$node_image" -f "$task_dir/environment/nextcloud-node24.Dockerfile" "$task_dir/environment"
}

ensure_php_image() {
  docker image inspect "$php_image" >/dev/null 2>&1 || docker build -t "$php_image" -f "$task_dir/environment/nextcloud-php84.Dockerfile" "$task_dir/environment"
}

server_dependency_fingerprint() {
  local repo_dir="$1"
  local image_id
  image_id="$(docker image inspect --format '{{.Id}}' "$php_image")"
  {
    printf '%s\0' "$image_id"
    local manifest
    for manifest in \
      composer.json composer.lock \
      vendor-bin/behat/composer.json vendor-bin/behat/composer.lock \
      vendor-bin/cs-fixer/composer.json vendor-bin/cs-fixer/composer.lock \
      vendor-bin/openapi-extractor/composer.json vendor-bin/openapi-extractor/composer.lock \
      vendor-bin/phpunit/composer.json vendor-bin/phpunit/composer.lock \
      vendor-bin/psalm/composer.json vendor-bin/psalm/composer.lock \
      vendor-bin/rector/composer.json vendor-bin/rector/composer.lock; do
      printf '%s\0' "$manifest"
      if [[ -f "$workspace/$repo_dir/$manifest" ]]; then
        sha256sum "$workspace/$repo_dir/$manifest" | awk '{print $1}'
      else
        printf 'missing\n'
      fi
    done
  } | sha256sum | awk '{print $1}'
}

run_node() {
  local workdir="$1"
  local script="$2"
  ensure_node_image
  docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e npm_config_cache=/node-cache/npm \
    -v "$workspace:/workspace" \
    -v "$cache_root/node-cache/nextcloud:/node-cache" \
    -w "/workspace/$workdir" \
    "$node_image" \
    bash -lc "$script"
}

run_php() {
  local workdir="$1"
  local script="$2"
  ensure_php_image
  local dependency_id dependency_dir
  dependency_id="$(server_dependency_fingerprint "$workdir")"
  dependency_dir="$evaluator_vendor_root/server-hidden/$dependency_id"
  mkdir -p \
    "$dependency_dir/vendor" \
    "$dependency_dir/vendor-bin/behat/vendor" \
    "$dependency_dir/vendor-bin/cs-fixer/vendor" \
    "$dependency_dir/vendor-bin/openapi-extractor/vendor" \
    "$dependency_dir/vendor-bin/phpunit/vendor" \
    "$dependency_dir/vendor-bin/psalm/vendor" \
    "$dependency_dir/vendor-bin/rector/vendor"
  local git_mount_args=()
  local git_common_dir
  git_common_dir="$(git -C "$workspace/$workdir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
  if [[ -n "$git_common_dir" && -d "$git_common_dir" ]]; then
    git_mount_args=(-v "$git_common_dir:$git_common_dir")
  fi
  docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e COMPOSER_ALLOW_SUPERUSER=1 \
    -e COMPOSER_HOME=/tmp/ecosync-composer \
    -e COMPOSER_CACHE_DIR=/php-cache/composer \
    -e ECOSYNC_COMPOSER_LOCK=/evaluator-composer-deps/install.lock \
    -v "$workspace:/workspace" \
    -v "$cache_root/php-cache/nextcloud:/php-cache" \
    -v "$dependency_dir:/evaluator-composer-deps" \
    -v "$dependency_dir/vendor:/workspace/$workdir/vendor" \
    -v "$dependency_dir/vendor-bin/behat/vendor:/workspace/$workdir/vendor-bin/behat/vendor" \
    -v "$dependency_dir/vendor-bin/cs-fixer/vendor:/workspace/$workdir/vendor-bin/cs-fixer/vendor" \
    -v "$dependency_dir/vendor-bin/openapi-extractor/vendor:/workspace/$workdir/vendor-bin/openapi-extractor/vendor" \
    -v "$dependency_dir/vendor-bin/phpunit/vendor:/workspace/$workdir/vendor-bin/phpunit/vendor" \
    -v "$dependency_dir/vendor-bin/psalm/vendor:/workspace/$workdir/vendor-bin/psalm/vendor" \
    -v "$dependency_dir/vendor-bin/rector/vendor:/workspace/$workdir/vendor-bin/rector/vendor" \
    "${git_mount_args[@]}" \
    -w "/workspace/$workdir" \
    "$php_image" \
    bash -lc "$script"
}

if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
  case "$profile" in
    calendar-hidden|cdav_library-hidden)
      ensure_node_image
      docker run --rm "$node_image" bash -lc 'node --version >/dev/null && npm --version >/dev/null'
      ;;
    server-hidden)
      ensure_php_image
      docker run --rm "$php_image" bash -lc 'php -v >/dev/null && composer --version >/dev/null'
      ;;
  esac
  exit 0
fi

case "$profile" in
  calendar-hidden)
    run_node "repos/nextcloud/calendar" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      exec 9>/node-cache/calendar-node-modules.lock
      flock 9
      if [[ "$(cat node_modules/.ecosyncbench-npm-ci-ready 2>/dev/null || true)" != "nextcloud-calendar-npm-ci-v1" ]]; then
        for attempt in 1 2 3; do
          npm ci --prefer-offline --no-audit --no-fund && break
          if [[ "$attempt" == 3 ]]; then
            exit 1
          fi
          sleep $((attempt * 5))
        done
        printf "%s\n" "nextcloud-calendar-npm-ci-v1" > node_modules/.ecosyncbench-npm-ci-ready
      fi
      flock -u 9
      npx vitest run tests/javascript/unit/models/calendar.test.js --reporter=junit --outputFile=/workspace/.ecosyncbench/test-reports/calendar-hidden.xml
    '
    ;;
  cdav_library-hidden)
    run_node "repos/nextcloud/cdav-library" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      exec 9>/node-cache/cdav-library-node-modules.lock
      flock 9
      if [[ "$(cat node_modules/.ecosyncbench-npm-ci-ready 2>/dev/null || true)" != "nextcloud-cdav-library-npm-ci-v1" ]]; then
        for attempt in 1 2 3; do
          npm ci --prefer-offline --no-audit --no-fund && break
          if [[ "$attempt" == 3 ]]; then
            exit 1
          fi
          sleep $((attempt * 5))
        done
        printf "%s\n" "nextcloud-cdav-library-npm-ci-v1" > node_modules/.ecosyncbench-npm-ci-ready
      fi
      flock -u 9
      npx vitest run test/unit/clientTest.js --reporter=junit --outputFile=/workspace/.ecosyncbench/test-reports/cdav-library-hidden.xml
    '
    ;;
  server-hidden)
    git -C "$workspace/repos/nextcloud/server" submodule update --init --depth 1 3rdparty
    run_php "repos/nextcloud/server" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home /tmp/ecosync-composer config
      exec 9>"$ECOSYNC_COMPOSER_LOCK"
      flock 9
      composer install --no-interaction --prefer-dist --no-progress
      set +e
      NOCOVERAGE=1 ./autotest.sh sqlite ../apps/dav/tests/unit/CalDAV/CalendarTest.php
      status=$?
      set -e
      cp tests/autotest-results-sqlite.xml /workspace/.ecosyncbench/test-reports/server-calendar-hidden.xml
      exit "$status"
    '
    ;;
  *)
    echo "Unknown profile for nextcloud_calendar_cdav_library_6ef04924e127: $profile" >&2
    exit 2
    ;;
esac
