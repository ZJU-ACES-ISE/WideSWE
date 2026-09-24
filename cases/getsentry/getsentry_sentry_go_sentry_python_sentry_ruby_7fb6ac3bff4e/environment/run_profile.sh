#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
reports_dir="$workspace/.ecosyncbench/test-reports"
cache_root="${ECOSYNC_GETSENTRY_SDK_CACHE_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/getsentry-sentry-sdk-7fb6ac3bff4e}}"

go_image="ecosyncbench/base/go:1.26-bookworm"
python_image="ecosyncbench/deps/getsentry-sentry-python-py310:7fb6ac3bff4e"
ruby_image="ecosyncbench/deps/getsentry-sentry-ruby:7fb6ac3bff4e"

mkdir -p "$reports_dir" "$cache_root/go-mod" "$cache_root/go-build" "$cache_root/pip" "$cache_root/tox" "$cache_root/bundle" "$cache_root/gems"

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

python_tox_fingerprint="$(dependency_fingerprint "$python_image" "$workspace/repos/getsentry/sentry-python" tox.ini pyproject.toml setup.cfg setup.py)"
python_tox="$cache_root/tox/sentry-python-$python_tox_fingerprint"
mkdir -p "$python_tox"

ensure_go_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$go_image" >/dev/null 2>&1; then
    docker build -t "$go_image" -f "$task_dir/../../../../../../benchmark/images/base/go-1.26-bookworm/Dockerfile" "$task_dir/../../../../../../benchmark/images/base/go-1.26-bookworm"
  fi
}

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

run_go() {
  ensure_go_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$go_image" bash -lc 'export PATH="/usr/local/go/bin:$PATH"; go version >/dev/null'
    return 0
  fi

  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-1200}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e GOMODCACHE=/go-cache/mod \
    -e GOCACHE=/go-cache/build \
    -v "$workspace:/workspace" \
    -v "$cache_root:/go-cache" \
    -w /workspace/repos/getsentry/sentry-go \
    "$go_image" \
    bash -lc '
      set -euo pipefail
      export PATH="/usr/local/go/bin:$PATH"
      mkdir -p /tmp/ecosync-home /workspace/.ecosyncbench/test-reports
      go test -json -count=1 . ./internal/http \
        > /workspace/.ecosyncbench/test-reports/sentry-go-transport.json
    '
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
      python -m tox -q --notest -e py3.10-common
      if [[ "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
        .tox/py3.10-common/bin/python -m pytest --version >/dev/null
        exit 0
      fi
      .tox/py3.10-common/bin/python -m pytest -q -o addopts="" \
        tests/test_transport.py \
        --junitxml=/workspace/.ecosyncbench/test-reports/sentry-python-transport.xml
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
      RUBYLIB="$formatter_lib${RUBYLIB:+:$RUBYLIB}" bundle exec rspec spec/sentry/transport/http_transport_spec.rb \
        --require rspec_junit_formatter \
        --format RspecJunitFormatter \
        --out /workspace/.ecosyncbench/test-reports/sentry-ruby-transport.xml
    '
}

case "$profile" in
  sentry_go-hidden)
    run_go
    ;;
  sentry_python-hidden)
    run_python
    ;;
  sentry_ruby-hidden)
    run_ruby
    ;;
  *)
    echo "Unknown profile for getsentry_sentry_go_sentry_python_sentry_ruby_7fb6ac3bff4e: $profile" >&2
    exit 2
    ;;
esac
