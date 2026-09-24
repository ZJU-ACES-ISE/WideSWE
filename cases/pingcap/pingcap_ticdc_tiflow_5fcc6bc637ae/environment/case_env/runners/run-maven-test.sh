#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
test_selector="${2:?maven test selector required}"
mkdir -p /workspace/.ecosyncbench/test-reports
deps_root="${ECOSYNC_DEPS_ROOT:-/opt/ecosync}"
export MAVEN_OPTS="-Dmaven.repo.local=$deps_root/m2 ${MAVEN_OPTS:-}"
export GIT_CONFIG_GLOBAL="${GIT_CONFIG_GLOBAL:-/tmp/ecosyncbench-gitconfig}"
touch "$GIT_CONFIG_GLOBAL"
if [[ -f .git ]]; then
  gitdir="$(sed -n 's/^gitdir: //p' .git | head -1)"
  if [[ -n "$gitdir" && ! -d "$gitdir" ]]; then
    rm -f .git
  fi
fi
if [[ ! -d .git ]]; then
  git -c safe.directory="$PWD" init -q
fi
git -c safe.directory="$PWD" config user.email ecosyncbench@example.invalid
git -c safe.directory="$PWD" config user.name WIDESWE
if ! git -c safe.directory="$PWD" rev-parse --verify HEAD >/dev/null 2>&1; then
  git -c safe.directory="$PWD" commit --allow-empty -qm ecosyncbench-maven-metadata
fi
mvn -B -Dgit.commit.id.skip=true -Dformatter.skip=true -DskipITs=true -DskipIntegrationTests=true -DskipScenarioTests=true -Dtest="$test_selector" test
find target/surefire-reports -maxdepth 1 -name '*.xml' -type f -exec cp {} /workspace/.ecosyncbench/test-reports/ \; 2>/dev/null || true
