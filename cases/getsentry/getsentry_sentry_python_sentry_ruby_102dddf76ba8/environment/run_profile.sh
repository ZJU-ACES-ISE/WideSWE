#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
reports_dir="$workspace/.ecosyncbench/test-reports"
cache_root="${ECOSYNC_GETSENTRY_SDK_CACHE_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/getsentry-sentry-python-ruby-102dddf76ba8}}"
python_image="ecosyncbench/deps/getsentry-sentry-python-py310:102dddf76ba8"
ruby_image="ecosyncbench/deps/getsentry-sentry-ruby:102dddf76ba8"

mkdir -p "$reports_dir" "$cache_root/pip" "$cache_root/tox" "$cache_root/bundle" "$cache_root/gems" "$cache_root/test-gems"

dependency_fingerprint() {
  local namespace="$1"
  local repo_root="$2"
  shift 2
  {
    printf '%s\0' "$namespace"
    local relative
    for relative in "$@"; do
      printf '%s\0' "$relative"
      if [[ -f "$repo_root/$relative" ]]; then
        sha256sum "$repo_root/$relative" | cut -d ' ' -f1
      else
        printf '<missing>\n'
      fi
    done
  } | sha256sum | cut -c1-20
}

python_tox_fingerprint="$(dependency_fingerprint \
  "$python_image|newrelic=8.11.0|anyio<4" \
  "$workspace/repos/getsentry/sentry-python" \
  tox.ini pyproject.toml setup.cfg setup.py)"
python_tox="$cache_root/tox/sentry-python-$python_tox_fingerprint"
mkdir -p "$python_tox"

ensure_python_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$python_image" >/dev/null 2>&1; then
    docker build -t "$python_image" -f "$task_dir/environment/getsentry-sentry-python-py310.Dockerfile" "$task_dir/environment"
  fi
}

ensure_ruby_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$ruby_image" >/dev/null 2>&1; then
    docker build -t "$ruby_image" -f "$task_dir/environment/getsentry-sentry-ruby.Dockerfile" "$task_dir/environment"
  fi
}

run_python() {
  ensure_python_image
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-1800}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e PIP_CACHE_DIR=/cache/pip \
    -e ECOSYNC_DOCKER_PREBUILD_ONLY="${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" \
    -e ECOSYNC_DOCKER_WARMUP_ONLY="${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    -v "$python_tox:/workspace/repos/getsentry/sentry-python/.tox" \
    -w /workspace/repos/getsentry/sentry-python \
    "$python_image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /tmp/ecosync-home /workspace/.ecosyncbench/test-reports
      if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
        python -m tox --version >/dev/null
        exit 0
      fi
      python -m tox -q --notest -e py3.10-celery-v5.2
      python -m tox -q --notest -e py3.10-httpx-v0.23
      .tox/py3.10-celery-v5.2/bin/python -m pip install -q "newrelic==8.11.0"
      .tox/py3.10-httpx-v0.23/bin/python -m pip install -q "anyio<4"
      if [[ "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
        .tox/py3.10-celery-v5.2/bin/python -m pytest --version >/dev/null
        .tox/py3.10-httpx-v0.23/bin/python -m pytest --version >/dev/null
        exit 0
      fi
      status=0
      .tox/py3.10-celery-v5.2/bin/python -m pytest -q -o addopts="" \
        tests/integrations/celery/test_celery.py \
        --junitxml=/workspace/.ecosyncbench/test-reports/sentry-python-celery-hidden.xml || status=$?
      .tox/py3.10-httpx-v0.23/bin/python -m pytest -q -o addopts="" \
        tests/integrations/httpx/test_httpx.py \
        --junitxml=/workspace/.ecosyncbench/test-reports/sentry-python-httpx-hidden.xml || status=$?
      exit "$status"
    '
}

run_ruby() {
  ensure_ruby_image
  local ruby_offline=0
  if find "$cache_root/test-gems/gems" -maxdepth 1 -type d -name 'rspec_junit_formatter-*' -print -quit 2>/dev/null | grep -q .; then
    ruby_offline=1
  fi
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-1800}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e BUNDLE_PATH=/cache/bundle/${ECOSYNC_MATRIX_PHASE:-default} \
    -e BUNDLE_APP_CONFIG=/cache/bundle-app-config/${ECOSYNC_MATRIX_PHASE:-default} \
    -e BUNDLE_ALLOW_OFFLINE_INSTALL=true \
    -e GEM_HOME=/cache/gems/${ECOSYNC_MATRIX_PHASE:-default} \
    -e ECOSYNC_RUBY_OFFLINE="$ruby_offline" \
    -e ECOSYNC_DOCKER_PREBUILD_ONLY="${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" \
    -e ECOSYNC_DOCKER_WARMUP_ONLY="${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    -w /workspace/repos/getsentry/sentry-ruby/sentry-ruby \
    "$ruby_image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /tmp/ecosync-home /workspace/.ecosyncbench/test-reports
      if [[ -f ../.git ]]; then
        rm -f ../.git
        git -C .. init -q
        git -C .. config user.email ecosyncbench@example.invalid
        git -C .. config user.name EcosyncBench
        git -C .. add .
        git -C .. commit -q -m ecosyncbench-matrix-snapshot
      fi
      if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
        bundle --version >/dev/null
        exit 0
      fi
      if [[ "$ECOSYNC_RUBY_OFFLINE" == "1" ]]; then
        bundle install --local --jobs "${BUNDLE_JOBS:-4}"
      else
        bundle install --jobs "${BUNDLE_JOBS:-4}" --retry 3
        gem install rspec_junit_formatter --version "~> 0.6" \
          --install-dir /cache/test-gems --no-document
      fi
      if [[ "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
        bundle exec rspec --version >/dev/null
        exit 0
      fi
      formatter_lib="$(find /cache/test-gems/gems -maxdepth 2 -path "*/rspec_junit_formatter-*/lib" -type d -print -quit)"
      test -n "$formatter_lib"
      RUBYLIB="$formatter_lib${RUBYLIB:+:$RUBYLIB}" bundle exec rspec spec/sentry/net/http_spec.rb \
        --require rspec_junit_formatter \
        --format RspecJunitFormatter \
        --out /workspace/.ecosyncbench/test-reports/sentry-ruby-hidden.xml
    '
}

case "$profile" in
  sentry_python-hidden)
    run_python
    ;;
  sentry_ruby-hidden)
    run_ruby
    ;;
  *)
    echo "Unknown profile for getsentry_sentry_python_sentry_ruby_102dddf76ba8: $profile" >&2
    exit 2
    ;;
esac
