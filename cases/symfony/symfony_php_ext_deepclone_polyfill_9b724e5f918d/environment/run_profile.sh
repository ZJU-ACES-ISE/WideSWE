#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
workspace="${ECOSYNC_WORKSPACE:?ECOSYNC_WORKSPACE required}"
image="ecosyncbench/base/php-composer-bcmath:8.4"
uid_gid="$(id -u):$(id -g)"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/php-cache/symfony}"
mkdir -p "$workspace/.ecosyncbench/test-reports" "$cache_root/composer" "$cache_root/locks"
image_id="$(docker image inspect "$image" --format '{{.Id}}')"
polyfill_lock_key="$({ printf '%s\n' "$image_id"; sha256sum "$workspace/repos/symfony/polyfill/composer.json"; } | sha256sum | cut -d' ' -f1)"

if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-}" == "1" ]]; then
  docker image inspect "$image" >/dev/null
  docker run --rm "$image" bash -c 'php -v >/dev/null && composer --version >/dev/null'
  exit 0
fi

run_php() {
  timeout 420 docker run --rm -u "$uid_gid" \
    -e HOME=/tmp/ecosync-home \
    -e COMPOSER_CACHE_DIR=/php-cache/composer \
    -e COMPOSER_ROOT_VERSION=1.x-dev \
    -e ECOSYNC_POLYFILL_LOCK_KEY="$polyfill_lock_key" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/php-cache" \
    -w /workspace \
    "$image" bash -c "$1"
}

case "$profile" in
  php_ext_deepclone-hidden)
    run_php '
      set -euo pipefail
      cd /workspace/repos/symfony/php-ext-deepclone
      phpize >/tmp/build.log 2>&1
      ./configure >>/tmp/build.log 2>&1
      make -j2 >>/tmp/build.log 2>&1
      EXT_DIR=$(php -r "echo ini_get(\"extension_dir\");")
      BCMATH="$EXT_DIR/bcmath.so"
      LOG=/tmp/phpt.log
      set +e
      TEST_PHP_ARGS="-q -d extension=$BCMATH" make test TESTS="tests/deepclone_from_array_refid_overflow.phpt tests/deepclone_from_array_unserialize_nonobject.phpt" >$LOG 2>&1
      rc=$?
      set -e
      REPORT=/workspace/.ecosyncbench/test-reports/php-ext-deepclone-hidden.xml
      php -r '\''
        $log=file_get_contents("/tmp/phpt.log");
        $tests=["tests/deepclone_from_array_refid_overflow.phpt","tests/deepclone_from_array_unserialize_nonobject.phpt"];
        $failures=0; $skips=0; $cases="";
        foreach ($tests as $t) {
          $name=basename($t);
          if (preg_match("/PASS .*\\[".preg_quote($t,"/")."\\]/", $log)) {
            $cases.="  <testcase classname=\"php_ext_deepclone.phpt\" name=\"".htmlspecialchars($name, ENT_XML1)."\"/>\n";
          } elseif (preg_match("/SKIP .*\\[".preg_quote($t,"/")."\\]/", $log)) {
            $skips++; $cases.="  <testcase classname=\"php_ext_deepclone.phpt\" name=\"".htmlspecialchars($name, ENT_XML1)."\"><skipped/></testcase>\n";
          } else {
            $failures++; $cases.="  <testcase classname=\"php_ext_deepclone.phpt\" name=\"".htmlspecialchars($name, ENT_XML1)."\"><failure message=\"PHPT failed\">".htmlspecialchars($log, ENT_XML1)."</failure></testcase>\n";
          }
        }
        echo "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<testsuite name=\"php_ext_deepclone-hidden\" tests=\"".count($tests)."\" failures=\"$failures\" errors=\"0\" skipped=\"$skips\">\n$cases</testsuite>\n";
      '\'' > "$REPORT"
      cat "$LOG"
      exit $rc
    '
    ;;
  polyfill-hidden)
    run_php '
      set -euo pipefail
      cd /workspace/repos/symfony/polyfill
      lockfile="/php-cache/locks/polyfill-${ECOSYNC_POLYFILL_LOCK_KEY}.lock"
      flock "${lockfile}.guard" bash -c '\''
        set -euo pipefail
        if [[ -s "$1" ]]; then cp "$1" composer.lock; composer install --no-interaction --prefer-dist --no-progress;
        else composer update --no-interaction --prefer-dist --no-progress; tmp="$1.tmp.$$"; cp composer.lock "$tmp"; mv "$tmp" "$1"; fi
      '\'' _ "$lockfile" >/tmp/composer.log 2>&1 || (tail -n 120 /tmp/composer.log; exit 1)
      SYMFONY_PHPUNIT_VERSION=9.6 ./phpunit --no-configuration --bootstrap vendor/autoload.php tests/DeepClone/DeepCloneTest.php --log-junit /workspace/.ecosyncbench/test-reports/polyfill-hidden.xml
    '
    ;;
  *) echo "Unknown profile: $profile" >&2; exit 2;;
esac
