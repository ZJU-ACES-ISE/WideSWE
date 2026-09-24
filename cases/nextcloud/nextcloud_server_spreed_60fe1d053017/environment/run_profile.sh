#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
host_repo_root="$(pwd -P)"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy}"
php_image="ecosyncbench/deps/nextcloud-php:8.4-gd-apcu-v1"

mkdir -p \
  "$workspace/.ecosyncbench/test-reports" \
  "$cache_root/composer-cache/nextcloud/server" \
  "$cache_root/composer-cache/nextcloud/spreed"

ensure_php_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$php_image" >/dev/null 2>&1; then
    docker build -t "$php_image" -f "$host_repo_root/benchmark/images/deps/nextcloud-php/Dockerfile" "$host_repo_root/benchmark/images/deps/nextcloud-php"
  fi
}

ensure_nextcloud_server_submodule() {
  git -C "$workspace/repos/nextcloud/server" submodule update --init --depth 1 3rdparty
}

docker_php() {
  local composer_cache="$1"
  local script="$2"
  ensure_php_image
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-3600}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e COMPOSER_CACHE_DIR=/composer-cache \
    -e COMPOSER_HOME=/tmp/composer \
    -v "$workspace:/workspace" \
    -v "$composer_cache:/composer-cache" \
    -w /workspace/repos/nextcloud/server \
    "$php_image" \
    bash -lc "$script"
}

server_install_sqlite() {
  cat <<'SCRIPT'
set -euo pipefail
mkdir -p /tmp/ecosync-home /tmp/composer /workspace/.ecosyncbench/test-reports config
composer install --no-interaction --prefer-dist --no-progress
rm -rf data config/config.php
mkdir -p data
cp tests/preseed-config.php config/config.php
php ./occ maintenance:install -vvv \
  --database=sqlite \
  --database-name=oc_autotest \
  --database-host=localhost \
  --database-user=oc_autotest \
  --database-pass=owncloud \
  --admin-user=admin \
  --admin-pass=admin \
  --data-dir="$(pwd)/data"
php -f tests/enable_all.php
SCRIPT
}

run_server_hidden() {
  ensure_nextcloud_server_submodule
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    ensure_php_image
    docker run --rm "$php_image" bash -lc 'php -m | grep -q pdo_sqlite && php -m | grep -q gd && composer --version >/dev/null'
    return 0
  fi
  local setup
  setup="$(server_install_sqlite)"
  docker_php "$cache_root/composer-cache/nextcloud/server" "$setup
lib/composer/bin/phpunit --configuration tests/phpunit-autotest.xml \
  --log-junit /workspace/.ecosyncbench/test-reports/server-hidden.xml \
  tests/lib/Comments/CommentTest.php
"
}

run_spreed_hidden() {
  ensure_nextcloud_server_submodule
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    ensure_php_image
    docker run --rm "$php_image" bash -lc 'php -m | grep -q pdo_sqlite && php -m | grep -q gd && composer --version >/dev/null'
    return 0
  fi
  local setup
  setup="$(server_install_sqlite)"
  docker_php "$cache_root/composer-cache/nextcloud/spreed" "$setup
rm -rf apps/spreed
mkdir -p apps/spreed
(cd /workspace/repos/nextcloud/spreed && tar --exclude=.git -cf - .) | (cd apps/spreed && tar -xf -)
cd apps/spreed
composer install --no-interaction --prefer-dist --no-progress
cd /workspace/repos/nextcloud/server
php ./occ app:enable --force spreed
cd apps/spreed
vendor/bin/phpunit --no-coverage --configuration tests/php/phpunit.xml \
  --log-junit /workspace/.ecosyncbench/test-reports/spreed-hidden.xml \
  tests/php/Chat/Parser/UserMentionTest.php
"
}

case "$profile" in
  server-hidden)
    run_server_hidden
    ;;
  spreed-hidden)
    run_spreed_hidden
    ;;
  *)
    echo "Unknown profile for nextcloud_server_spreed_60fe1d053017: $profile" >&2
    exit 2
    ;;
esac
