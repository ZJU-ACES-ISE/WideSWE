#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_root="${ECOSYNC_REPO_ROOT:-/opt/ecosyncbench}"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
base_image="ecosyncbench/base/php-composer-bcmath:8.4"
image="ecosyncbench/deps/doctrine-php84-mongodb:4b8bd06bc1c5"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/php-cache/doctrine-4b8bd06bc1c5}"
uid="${ECOSYNC_UID:-$(id -u)}"
gid="${ECOSYNC_GID:-$(id -g)}"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$base_image" >/dev/null 2>&1; then
    docker build -t "$base_image" \
      -f "$repo_root/benchmark/images/base/php-8.4-composer-bcmath/Dockerfile" \
      "$repo_root/benchmark/images/base/php-8.4-composer-bcmath"
  fi
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" \
      -f "$task_dir/environment/doctrine-php84-mongodb.Dockerfile" \
      "$task_dir/environment"
  fi
}

run_php() {
  local script="$1"
  local use_mongo="${2:-0}"
  ensure_image
  mkdir -p "$cache_root/composer/cache" "$cache_root/composer/home" "$workspace/.ecosyncbench/test-reports"
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image" bash -lc 'php -v >/dev/null && php -m | grep -q "^mongodb$" && composer --version >/dev/null'
    return 0
  fi

  local network_args=()
  local mongo_name=""
  if [[ "$use_mongo" == "1" ]]; then
    mongo_name="ecosync_mongo_${profile}_$$_${RANDOM}"
    docker run -d --rm --name "$mongo_name" mongo:7 --quiet >/dev/null
    trap 'docker stop "$mongo_name" >/dev/null 2>&1 || true' EXIT
    for _ in $(seq 1 60); do
      if docker exec "$mongo_name" mongosh --quiet --eval "db.adminCommand({ ping: 1 }).ok" >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
    network_args=(--network "container:$mongo_name")
  fi

  local exit_code=0
  if timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-2400}" docker run --rm \
      --name "ecosync_${profile}_$$_${RANDOM}" \
      "${network_args[@]}" \
      -u "$uid:$gid" \
      -e HOME=/tmp/ecosync-home \
      -e CI=1 \
      -e COMPOSER_HOME=/composer-cache/home \
      -e COMPOSER_CACHE_DIR=/composer-cache/cache \
      -e COMPOSER_ALLOW_SUPERUSER=1 \
      -e COMPOSER_NO_INTERACTION=1 \
      -e DOCTRINE_MONGODB_SERVER=mongodb://127.0.0.1:27017 \
      -e MONGODB_URI=mongodb://127.0.0.1:27017 \
      -v "$workspace:/workspace" \
      -v "$cache_root/composer:/composer-cache" \
      -w /workspace \
      "$image" \
      bash -lc "$script"; then
    exit_code=0
  else
    exit_code=$?
  fi

  if [[ -n "$mongo_name" ]]; then
    docker stop "$mongo_name" >/dev/null 2>&1 || true
    trap - EXIT
  fi

  return "$exit_code"
}

case "$profile" in
  doctrinemongodbbundle-hidden)
    run_php '
      set -euo pipefail
      cd /workspace/repos/doctrine/DoctrineMongoDBBundle
      export COMPOSER_ROOT_VERSION=5.0.x-dev
      composer config --global audit.block-insecure false
      composer config minimum-stability dev
      composer config prefer-stable true
      composer config repositories.ecosync-odm "{\"type\":\"path\",\"url\":\"/workspace/repos/doctrine/mongodb-odm\",\"options\":{\"symlink\":false,\"versions\":{\"doctrine/mongodb-odm\":\"2.16.x-dev\"}}}"
      composer config repositories.ecosync-persistence "{\"type\":\"path\",\"url\":\"/workspace/repos/doctrine/persistence\",\"options\":{\"symlink\":false,\"versions\":{\"doctrine/persistence\":\"4.1.x-dev\"}}}"
      composer update --with-all-dependencies --prefer-dist --no-progress
      vendor/bin/phpunit \
        --log-junit /workspace/.ecosyncbench/test-reports/doctrinemongodbbundle-hidden.xml \
        tests/DependencyInjection/ConfigurationTest.php
    ' 1
    ;;
  mongodb_odm-hidden)
    run_php '
      set -euo pipefail
      cd /workspace/repos/doctrine/mongodb-odm
      export COMPOSER_ROOT_VERSION=2.16.x-dev
      composer config --global audit.block-insecure false
      composer config minimum-stability dev
      composer config prefer-stable true
      composer config repositories.ecosync-persistence "{\"type\":\"path\",\"url\":\"/workspace/repos/doctrine/persistence\",\"options\":{\"symlink\":false,\"versions\":{\"doctrine/persistence\":\"4.1.x-dev\"}}}"
      composer update --with-all-dependencies --prefer-dist --no-progress
      vendor/bin/phpunit \
        --log-junit /workspace/.ecosyncbench/test-reports/mongodb_odm-hidden.xml \
        tests/Doctrine/ODM/MongoDB/Tests/ConfigurationTest.php \
        tests/Doctrine/ODM/MongoDB/Tests/Functional/PropertyHooksTest.php \
        tests/Doctrine/ODM/MongoDB/Tests/Functional/ReferencesTest.php \
        tests/Doctrine/ODM/MongoDB/Tests/Functional/Ticket/GH852Test.php \
        tests/Doctrine/ODM/MongoDB/Tests/Mapping/AnnotationDriverTest.php \
        tests/Doctrine/ODM/MongoDB/Tests/Mapping/AttributeDriverTest.php \
        tests/Doctrine/ODM/MongoDB/Tests/Mapping/BasicInheritanceMappingTest.php \
        tests/Doctrine/ODM/MongoDB/Tests/Mapping/ClassMetadataTest.php \
        tests/Doctrine/ODM/MongoDB/Tests/Mapping/XmlMappingDriverTest.php \
        tests/Doctrine/ODM/MongoDB/Tests/Proxy/Factory/ProxyFactoryTest.php \
        tests/Doctrine/ODM/MongoDB/Tests/UnitOfWorkTest.php
    ' 1
    ;;
  persistence-hidden)
    run_php '
      set -euo pipefail
      cd /workspace/repos/doctrine/persistence
      export COMPOSER_ROOT_VERSION=4.1.x-dev
      composer config --global audit.block-insecure false
      composer update --prefer-dist --no-progress
      vendor/bin/phpunit \
        --log-junit /workspace/.ecosyncbench/test-reports/persistence-hidden.xml \
        tests/ManagerRegistryTest.php
    ' 0
    ;;
  *)
    echo "Unknown profile for doctrine_doctrinemongodbbundle_mongodb_odm_persistence_4b8bd06bc1c5: $profile" >&2
    exit 2
    ;;
esac
