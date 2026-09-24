#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_root="$task_dir"
while [[ "$repo_root" != "/" && ! -d "$repo_root/.git" ]]; do repo_root="$(dirname "$repo_root")"; done
if [[ ! -d "$repo_root/.git" ]]; then
  repo_root="$(git -C "$(pwd)" rev-parse --show-toplevel 2>/dev/null || true)"
fi
if [[ -z "$repo_root" || ! -d "$repo_root/.git" ]]; then
  echo "cannot locate repository root from $task_dir or current working directory" >&2
  exit 2
fi

workspace="$(cd "${ECOSYNC_WORKSPACE:?ECOSYNC_WORKSPACE required}" && pwd)"
export ECOSYNC_REPO_ROOT="$repo_root"
export ECOSYNC_BUILD_CONTEXT="$workspace"
export ECOSYNC_UID="${ECOSYNC_UID:-$(id -u)}"
export ECOSYNC_GID="${ECOSYNC_GID:-$(id -g)}"

mkdir -p "$workspace/.ecosyncbench/test-reports"

ensure_base_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect ecosyncbench/base/php-composer:8.4 >/dev/null 2>&1; then
    docker build -t ecosyncbench/base/php-composer:8.4 \
      -f "$repo_root/benchmark/images/base/php-8.4-composer/Dockerfile" \
      "$repo_root"
  fi
}

ensure_runner_script() {
  mkdir -p "$workspace/.ecosyncbench"
  cat > "$workspace/.ecosyncbench/run-phpunit.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
shift
mkdir -p /workspace/.ecosyncbench/test-reports
if [[ ! -d vendor ]]; then
  ln -s /opt/ecosync/deps/vendor vendor
fi
composer dump-autoload --no-interaction --no-scripts >/tmp/ecosync-composer-dump.log
phpunit_args=(--log-junit "/workspace/.ecosyncbench/test-reports/${profile}.xml")
if [[ -f ./phpunit && -x ./phpunit ]]; then
  SYMFONY_PHPUNIT_VERSION="${SYMFONY_PHPUNIT_VERSION:-13}" ./phpunit "${phpunit_args[@]}" "$@"
elif [[ -x vendor/bin/simple-phpunit ]]; then
  SYMFONY_PHPUNIT_VERSION="${SYMFONY_PHPUNIT_VERSION:-9.6}" vendor/bin/simple-phpunit "${phpunit_args[@]}" "$@"
else
  vendor/bin/phpunit "${phpunit_args[@]}" "$@"
fi
SH
  chmod +x "$workspace/.ecosyncbench/run-phpunit.sh"
}

compose_run() {
  local service="$1"
  local image="$2"
  local container_name="ecosync_${profile}_$$_${RANDOM}"
  ensure_base_image
  ensure_runner_script
  cd "$task_dir/environment"
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    ECOSYNC_WORKSPACE="$workspace" docker compose build "$service"
  fi
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker image inspect "$image" >/dev/null
    return 0
  fi
  ECOSYNC_WORKSPACE="$workspace" docker compose run --rm \
    --name "$container_name" \
    -v "$workspace/.ecosyncbench/run-phpunit.sh:/opt/ecosync/run-phpunit.sh:ro" \
    "$service"
}

case "$profile" in
  twig-hidden)
    compose_run twig-hidden ecosyncbench/deps/symfony-twig-composer:ecb310e129f5
    ;;
  symfony-hidden)
    compose_run symfony-hidden ecosyncbench/deps/symfony-framework-composer:e0cc934437d8
    ;;
  ux-hidden)
    compose_run ux-hidden ecosyncbench/deps/symfony-ux-twig-component-composer:aa8fbe407748
    ;;
  *)
    echo "Unknown profile for symfony_symfony_ux_9cfba5a7ef9f: $profile" >&2
    exit 2
    ;;
esac
