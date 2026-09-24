#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
repo_root="/opt/ecosyncbench"
image="ecosyncbench/base/rails-ruby:3.4-bookworm"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/ruby-cache/rails-jbuilder-rack422}"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$task_dir/environment/rails-ruby.Dockerfile" "$task_dir/environment"
  fi
}

run_ruby_tests() {
  local workdir="$1"
  local report_dir="$2"
  local load_paths="$3"
  local setup="$4"
  shift 4
  ensure_image
  mkdir -p "$cache_root/bundle" "$cache_root/locks" "$workspace/.ecosyncbench/test-reports"
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
    return 0
  fi
  docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e BUNDLE_PATH=/ruby-cache/bundle \
    -e BUNDLE_JOBS="${BUNDLE_JOBS:-4}" \
    -e BUNDLE_RETRY="${BUNDLE_RETRY:-3}" \
    -e ECOSYNC_BUNDLE_LOCK=/ruby-cache/locks/bundle-install.lock \
    -e ECOSYNC_REPORT_DIR="$report_dir" \
    -e ECOSYNC_RUBY_LOAD_PATHS="$load_paths" \
    -e ECOSYNC_SETUP="$setup" \
    -e ECOSYNC_SPLIT_FILES="${ECOSYNC_SPLIT_FILES:-0}" \
    -e PATH=/usr/local/bundle/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -v "$workspace:/workspace" \
    -v "$cache_root:/ruby-cache" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -c 'set -euo pipefail
      mkdir -p /tmp/ecosync-home "/workspace/.ecosyncbench/test-reports/${ECOSYNC_REPORT_DIR}"
      if [[ "${ECOSYNC_SETUP:-}" == "jbuilder-local-rails" ]]; then
        rails_path="/workspace/repos/rails/rails"
        if ! grep -q "ecosyncbench local rails path gems" Gemfile; then
          cat >> Gemfile <<RUBY

# ecosyncbench local rails path gems
gem "rails", path: "${rails_path}"
gem "activesupport", path: "${rails_path}/activesupport"
gem "actionpack", path: "${rails_path}/actionpack"
gem "actionview", path: "${rails_path}/actionview"
gem "activemodel", path: "${rails_path}/activemodel"
gem "activerecord", path: "${rails_path}/activerecord"
gem "actionmailer", path: "${rails_path}/actionmailer"
gem "activejob", path: "${rails_path}/activejob"
gem "actioncable", path: "${rails_path}/actioncable"
gem "activestorage", path: "${rails_path}/activestorage"
gem "actionmailbox", path: "${rails_path}/actionmailbox"
gem "actiontext", path: "${rails_path}/actiontext"
gem "railties", path: "${rails_path}/railties"
RUBY
        fi
      fi
      if [[ -f activestorage/test/service/configurations.example.yml && ! -f activestorage/test/service/configurations.yml ]]; then
        cat > activestorage/test/service/configurations.yml <<YAML
local:
  service: Disk
  root: <%= Dir.mktmpdir("active_storage_tests") %>
local_public:
  service: Disk
  root: <%= Dir.mktmpdir("active_storage_tests") %>
  public: true
YAML
      fi
      grep -q "minitest-reporters" Gemfile || printf "\ngem '\''minitest-reporters'\'', require: false\n" >> Gemfile
      bundle config set retry 5
      bundle config set timeout 60
      exec 9>"$ECOSYNC_BUNDLE_LOCK"
      flock 9
      for attempt in 1 2 3; do
        if bundle install; then
          break
        fi
        if [[ "$attempt" == "3" ]]; then
          exit 1
        fi
        sleep $((attempt * 5))
      done
      flock -u 9
      run_one() {
        local report_dir="$1"
        local load_paths="$2"
        local run_dir="$3"
        shift 3
        (
          cd "$run_dir"
          ECOSYNC_CURRENT_REPORT_DIR="$report_dir" ECOSYNC_CURRENT_LOAD_PATHS="$load_paths" bundle exec ruby -I. -Itest -e '\''
            ENV.fetch("ECOSYNC_CURRENT_LOAD_PATHS", "").split(":").reject(&:empty?).reverse_each { |path| $LOAD_PATH.unshift(File.expand_path(path)) }
            require "minitest/reporters"
            Minitest::Reporters.use! Minitest::Reporters::JUnitReporter.new("/workspace/.ecosyncbench/test-reports/" + ENV.fetch("ECOSYNC_CURRENT_REPORT_DIR"), true)
            ARGV.each { |file| require File.expand_path(file) }
            ARGV.replace(["--seed", "1"])
          '\'' "$@"
        )
      }
      run_native_rails_one() {
        local report_dir="$1"
        local run_dir="$2"
        shift 2
        (
          cd "$run_dir"
          BUILDKITE=1 BUILDKITE_JOB_ID="$report_dir" ./bin/test "$@" --seed 1
        )
      }
      if [[ "${ECOSYNC_SPLIT_FILES:-0}" == "1" ]]; then
        status=0
        rails_root="/workspace/repos/rails/rails"
        for file in "$@"; do
          slug="${file//\//__}"
          slug="${slug%.rb}"
          case "$file" in
            actionmailbox/*)
              run_dir="${rails_root}/actionmailbox"
              file_arg="${file#actionmailbox/}"
              file_load_paths="${rails_root}/actionmailbox/test:${rails_root}/actionpack/test:${rails_root}/actionview/test:${rails_root}/activejob/test:${rails_root}/activemodel/test:${rails_root}/activesupport/test"
              ;;
            actionpack/*)
              run_dir="${rails_root}/actionpack"
              file_arg="${file#actionpack/}"
              file_load_paths="${rails_root}/actionpack/test:${rails_root}/actionview/test:${rails_root}/activesupport/test"
              ;;
            activestorage/*)
              run_dir="${rails_root}/activestorage"
              file_arg="${file#activestorage/}"
              file_load_paths="${rails_root}/activestorage/test:${rails_root}/actionpack/test:${rails_root}/actionview/test:${rails_root}/activerecord/test:${rails_root}/activejob/test:${rails_root}/activemodel/test:${rails_root}/activesupport/test"
              ;;
            railties/*)
              run_dir="${rails_root}/railties"
              file_arg="${file#railties/}"
              file_load_paths="${rails_root}/railties/test:${rails_root}/activestorage/test:${rails_root}/actionpack/test:${rails_root}/actionview/test:${rails_root}/activerecord/test:${rails_root}/activejob/test:${rails_root}/activemodel/test:${rails_root}/activesupport/test"
              ;;
            *)
              run_dir="."
              file_arg="$file"
              file_load_paths="${ECOSYNC_RUBY_LOAD_PATHS:-}"
              ;;
          esac
          if ! run_native_rails_one "${ECOSYNC_REPORT_DIR}/${slug}" "$run_dir" "$file_arg"; then
            status=1
          fi
        done
        exit "$status"
      fi
      run_one "${ECOSYNC_REPORT_DIR}" "${ECOSYNC_RUBY_LOAD_PATHS:-}" "." "$@"' bash "$@"
}

case "$profile" in
  jbuilder-hidden)
    ECOSYNC_REPORT_DIR=jbuilder-hidden run_ruby_tests "repos/rails/jbuilder" "jbuilder-hidden" "" "jbuilder-local-rails" \
      test/scaffold_api_controller_generator_test.rb \
      test/scaffold_controller_generator_test.rb
    ;;
  rails-hidden)
    ECOSYNC_SPLIT_FILES=1 ECOSYNC_REPORT_DIR=rails-hidden run_ruby_tests "repos/rails/rails" "rails-hidden" "" "" \
      actionmailbox/test/controllers/ingresses/postmark/inbound_emails_controller_test.rb \
      actionmailbox/test/controllers/ingresses/relay/inbound_emails_controller_test.rb \
      actionpack/test/controller/rescue_test.rb \
      actionpack/test/dispatch/debug_exceptions_test.rb \
      activestorage/test/controllers/disk_controller_test.rb \
      railties/test/application/active_storage/uploads_integration_test.rb
    ;;
  *)
    echo "Unknown profile for rails_jbuilder_rails_c55c6f409dc4: $profile" >&2
    exit 2
    ;;
esac
