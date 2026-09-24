#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
image="ecosyncbench/base/rails-ruby:3.4-bookworm"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/ruby-cache/rails-bootsnap-rails-faef8fbe7ee8}"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$task_dir/environment/rails-ruby.Dockerfile" "$task_dir/environment"
  fi
}

run_container() {
  ensure_image
  mkdir -p "$cache_root/bundle" "$cache_root/gems" "$cache_root/locks" "$workspace/.ecosyncbench/test-reports"
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
    -e GEM_HOME=/ruby-cache/gems \
    -e GEM_PATH=/ruby-cache/gems:/ruby-cache/bundle/ruby/3.4.0:/usr/local/bundle \
    -e BUNDLE_JOBS="${BUNDLE_JOBS:-4}" \
    -e BUNDLE_RETRY="${BUNDLE_RETRY:-3}" \
    -e ECOSYNC_BUNDLE_LOCK=/ruby-cache/locks/bundle-install.lock \
    -e ECOSYNC_PROFILE="$profile" \
    -e PATH=/ruby-cache/gems/bin:/usr/local/bundle/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -v "$workspace:/workspace" \
    -v "$cache_root:/ruby-cache" \
    -w /workspace \
    "$image" \
    bash -c 'set -euo pipefail
      mkdir -p /tmp/ecosync-home /workspace/.ecosyncbench/test-reports
      run_minitest_files() {
        local workdir="$1"
        local report_dir="$2"
        shift 2
        (
          cd "$workdir"
          if [[ -f .git && ! -d .git ]]; then
            mv .git .git.ecosyncbench-disabled
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
          if [[ -f Rakefile ]] && grep -q "Rake::ExtensionTask" Rakefile; then
            bundle exec rake compile
          fi
          mkdir -p "/workspace/.ecosyncbench/test-reports/${report_dir}"
          ECOSYNC_REPORT_DIR="$report_dir" bundle exec ruby -I. -Itest -Ilib -e '\''
            require "minitest/reporters"
            Minitest::Reporters.use! Minitest::Reporters::JUnitReporter.new("/workspace/.ecosyncbench/test-reports/" + ENV.fetch("ECOSYNC_REPORT_DIR"), true)
            ARGV.each { |file| require File.expand_path(file) }
            ARGV.replace(["--seed", "1"])
          '\'' "$@"
        )
      }
      run_rails_file() {
        local report_dir="$1"
        shift
        (
          cd /workspace/repos/rails/rails
          if [[ -f .git && ! -d .git ]]; then
            mv .git .git.ecosyncbench-disabled
          fi
          git -C /tmp config --global --unset init.defaultBranch >/dev/null 2>&1 || true
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
          gem install puma -v ">= 7.1" --no-document
          flock -u 9
          mkdir -p "/workspace/.ecosyncbench/test-reports/${report_dir}"
          BUILDKITE=1 BUILDKITE_JOB_ID="$report_dir" ECOSYNC_REPORT_DIR="$report_dir" bundle exec ruby \
            -I. \
            -Irailties/test \
            -Iactivesupport/test \
            -Iactionpack/test \
            -Iactionview/test \
            -Iactivemodel/test \
            -Iactiverecord/test \
            -Iactivejob/test \
            -Iactivestorage/test \
            -e '\''
              require "minitest/reporters"
              Minitest::Reporters.use! Minitest::Reporters::JUnitReporter.new("/workspace/.ecosyncbench/test-reports/" + ENV.fetch("ECOSYNC_REPORT_DIR"), true)
              ARGV.each { |file| require File.expand_path(file) }
              ARGV.replace(["--seed", "1"])
            '\'' "$@"
        )
      }

      case "$ECOSYNC_PROFILE" in
        bootsnap-hidden)
          run_minitest_files /workspace/repos/rails/bootsnap bootsnap-hidden \
            test/compile_cache/iseq_cache_test.rb \
            test/ecosync_compiler_contract_test.rb
          ;;
        rails-hidden)
          run_rails_file rails-hidden \
            railties/test/generators/app_generator_test.rb \
            railties/test/generators/ecosync_bootsnap_generator_test.rb
          ;;
        *)
          echo "Unknown Rails profile: $ECOSYNC_PROFILE" >&2
          exit 2
          ;;
      esac'
}

case "$profile" in
  bootsnap-hidden|rails-hidden)
    run_container
    ;;
  *)
    echo "Unknown profile for rails_bootsnap_rails_faef8fbe7ee8: $profile" >&2
    exit 2
    ;;
esac
