#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
shift
mkdir -p /workspace/.ecosyncbench/test-reports
deps_root="${ECOSYNC_DEPS_ROOT:-/opt/ecosync}"
if [[ ! -d vendor && -d "$deps_root/deps/vendor" ]]; then
  cp -a "$deps_root/deps/vendor" vendor
fi
composer dump-autoload --no-interaction --no-scripts >/tmp/ecosync-composer-dump.log
phpunit_args=(--colors=never)
if ./vendor/bin/phpunit --help 2>/dev/null | grep -q -- "--fail-on-risky"; then
  phpunit_args+=(--fail-on-risky)
fi
phpunit_args+=(--log-junit "/workspace/.ecosyncbench/test-reports/${profile}.xml")
./vendor/bin/phpunit "${phpunit_args[@]}" "$@"
