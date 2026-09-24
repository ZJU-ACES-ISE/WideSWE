#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
image="ecosyncbench/base/php-composer-bcmath:8.4.23"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/php-cache/symfony}"
mkdir -p "$cache_root/composer" "$cache_root/locks" "$workspace/.ecosyncbench/test-reports"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$task_dir/environment/symfony-php84.Dockerfile" "$task_dir/environment"
  fi
}

run_in_php() {
  local workdir="$1"
  local script="$2"
  ensure_image
  local image_id polyfill_lock_key symfony_lock_key
  image_id="$(docker image inspect "$image" --format '{{.Id}}')"
  polyfill_lock_key="$({ printf '%s\n' "$image_id"; sha256sum "$workspace/repos/symfony/polyfill/composer.json"; } | sha256sum | cut -d' ' -f1)"
  symfony_lock_key="$({ printf '%s\nphpunit/phpunit:^11.5\n' "$image_id"; sha256sum "$workspace/repos/symfony/polyfill/composer.json" "$workspace/repos/symfony/symfony/src/Symfony/Component/VarExporter/composer.json"; } | sha256sum | cut -d' ' -f1)"
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-1200}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e COMPOSER_HOME=/tmp/ecosync-composer \
    -e COMPOSER_CACHE_DIR=/php-cache/composer \
    -e COMPOSER_ALLOW_SUPERUSER=1 \
    -e COMPOSER_ROOT_VERSION=1.40.x-dev \
    -e ECOSYNC_POLYFILL_LOCK_KEY="$polyfill_lock_key" \
    -e ECOSYNC_SYMFONY_LOCK_KEY="$symfony_lock_key" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/php-cache" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -lc "$script"
}

if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
  ensure_image
  docker run --rm "$image" bash -lc 'php -v >/dev/null && php -m | grep -q bcmath && composer --version >/dev/null'
  exit 0
fi

case "$profile" in
  php_ext_deepclone-hidden)
    run_in_php "repos/symfony/php-ext-deepclone" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home /tmp/ecosync-composer
      phpize
      ./configure --enable-deepclone
      make -j"$(nproc)"
      ext_dir="$(php -r '\''echo ini_get("extension_dir");'\'')"
      bcmath_so="$ext_dir/bcmath.so"
      if [ ! -f "$bcmath_so" ]; then
        echo "Missing bcmath extension at $bcmath_so" >&2
        exit 97
      fi
      TESTS=(tests/deepclone_hydrate.phpt)
      set +e
      make test TESTS="${TESTS[*]}" TEST_PHP_ARGS="-q -d extension=$bcmath_so" > /tmp/ecosync-phpt.out 2>&1
      code=$?
      set -e
      REPORT=/workspace/.ecosyncbench/test-reports/php-ext-deepclone-hidden.xml
      TEST_LIST=$(printf "%s\n" "${TESTS[@]}")
      TEST_LIST="$TEST_LIST" php -r '\''
        $log = file_get_contents("/tmp/ecosync-phpt.out");
        $cleanLog = preg_replace("/\033\\[[0-9;]*m/", "", $log);
        $tests = array_values(array_filter(explode("\n", getenv("TEST_LIST"))));
        $failures = 0;
        $skips = 0;
        $cases = "";
        foreach ($tests as $test) {
          $name = basename($test);
          $quoted = preg_quote($test, "/");
          if (preg_match("/PASS .*\\[".$quoted."\\]/", $cleanLog)) {
            $cases .= "  <testcase classname=\"php_ext_deepclone.phpt\" name=\"".htmlspecialchars($name, ENT_XML1)."\"/>\n";
          } elseif (preg_match("/SKIP .*\\[".$quoted."\\]/", $cleanLog)) {
            $skips++;
            $cases .= "  <testcase classname=\"php_ext_deepclone.phpt\" name=\"".htmlspecialchars($name, ENT_XML1)."\"><skipped/></testcase>\n";
          } else {
            $failures++;
            $cases .= "  <testcase classname=\"php_ext_deepclone.phpt\" name=\"".htmlspecialchars($name, ENT_XML1)."\"><failure message=\"PHPT failed\">".htmlspecialchars(substr($cleanLog, -20000), ENT_XML1)."</failure></testcase>\n";
          }
        }
        echo "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<testsuite name=\"php_ext_deepclone-hidden\" tests=\"".count($tests)."\" failures=\"$failures\" errors=\"0\" skipped=\"$skips\">\n$cases</testsuite>\n";
      '\'' > "$REPORT"
      cat /tmp/ecosync-phpt.out
      exit "$code"
    '
    ;;
  polyfill-hidden)
    run_in_php "repos/symfony/polyfill" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home /tmp/ecosync-composer
      lockfile="/php-cache/locks/polyfill-${ECOSYNC_POLYFILL_LOCK_KEY}.lock"
      flock "${lockfile}.guard" bash -c '\''
        set -euo pipefail
        if [[ -s "$1" ]]; then cp "$1" composer.lock; composer install --no-interaction --prefer-dist --no-progress;
        else composer update --no-interaction --prefer-dist --no-progress; tmp="$1.tmp.$$"; cp composer.lock "$tmp"; mv "$tmp" "$1"; fi
      '\'' _ "$lockfile"
      if [ -x vendor/bin/simple-phpunit ]; then vendor/bin/simple-phpunit --version >/dev/null 2>&1 || true; fi
      testbin="$(find vendor/bin/.phpunit -maxdepth 2 -type f -name phpunit | sort | tail -n 1)"
      if [ -z "$testbin" ]; then testbin=vendor/bin/phpunit; fi
      php "$testbin" --no-configuration --bootstrap vendor/autoload.php --log-junit /workspace/.ecosyncbench/test-reports/polyfill-hidden.xml tests/DeepClone/DeepCloneTest.php
    '
    ;;
  symfony-hidden)
    run_in_php "." '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home /tmp/ecosync-composer

      cd /workspace/repos/symfony/php-ext-deepclone
      phpize >/tmp/ecosync-deepclone-build.log 2>&1
      ./configure --enable-deepclone >>/tmp/ecosync-deepclone-build.log 2>&1
      make -j"$(nproc)" >>/tmp/ecosync-deepclone-build.log 2>&1
      deepclone_so=/workspace/repos/symfony/php-ext-deepclone/modules/deepclone.so
      if [ ! -f "$deepclone_so" ]; then
        cat /tmp/ecosync-deepclone-build.log
        echo "Missing built deepclone extension at $deepclone_so" >&2
        exit 97
      fi

      cd /workspace/repos/symfony/symfony/src/Symfony/Component/VarExporter
      composer config repositories.ecosync-polyfill path /workspace/repos/symfony/polyfill
      composer require --dev phpunit/phpunit:^11.5 --no-update --no-interaction
      lockfile="/php-cache/locks/symfony-${ECOSYNC_SYMFONY_LOCK_KEY}.lock"
      flock "${lockfile}.guard" bash -c '\''
        set -euo pipefail
        if [[ -s "$1" ]]; then cp "$1" composer.lock; composer install --no-interaction --prefer-dist --no-progress;
        else composer update --no-interaction --prefer-dist --no-progress; tmp="$1.tmp.$$"; cp composer.lock "$tmp"; mv "$tmp" "$1"; fi
      '\'' _ "$lockfile"
      php -d "extension=$deepclone_so" vendor/bin/phpunit --no-configuration --bootstrap vendor/autoload.php --log-junit /workspace/.ecosyncbench/test-reports/symfony-hidden.xml Tests/InstantiatorTest.php Tests/ProxyHelperTest.php
    '
    ;;
  *)
    echo "Unknown profile for symfony_php_ext_deepclone_polyfill_symfony_668e3f27dd09: $profile" >&2
    exit 2
    ;;
esac
