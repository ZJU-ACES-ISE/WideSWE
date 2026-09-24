#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
repo_root="${ECOSYNC_REPO_ROOT:-/opt/ecosyncbench}"
image="ecosyncbench/deps/elastic-go-python:1.26-querylog"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/elastic-beats-eck-querylog}"

mkdir -p \
  "$cache_root/go-mod" \
  "$cache_root/go-build" \
  "$cache_root/python" \
  "$workspace/.ecosyncbench/test-reports"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$task_dir/environment/elastic-go-python.Dockerfile" "$task_dir/environment"
  fi
}

run_in_docker() {
  local workdir="$1"
  local timeout_seconds="$2"
  local script="$3"
  local container_workspace="$workspace"

  ensure_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image" bash -lc '/usr/local/go/bin/go version >/dev/null && python3 --version >/dev/null'
    return 0
  fi

  local docker_args=()
  local docker_socket=/var/run/docker.sock
  if [[ "${DOCKER_HOST:-}" == unix://* ]]; then
    docker_socket="${DOCKER_HOST#unix://}"
  fi
  if [[ -S "$docker_socket" ]]; then
    docker_args+=(--group-add "$(stat -c '%g' "$docker_socket")" -v "$docker_socket:/var/run/docker.sock")
  fi
  if [[ -x /usr/bin/docker ]]; then
    docker_args+=(-v /usr/bin/docker:/usr/bin/docker:ro)
  fi
  if [[ -d /usr/libexec/docker/cli-plugins ]]; then
    docker_args+=(-v /usr/libexec/docker/cli-plugins:/usr/libexec/docker/cli-plugins:ro)
  fi
  if [[ -d /usr/lib/docker/cli-plugins ]]; then
    docker_args+=(-v /usr/lib/docker/cli-plugins:/usr/lib/docker/cli-plugins:ro)
  fi

  timeout "$timeout_seconds" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    --network host \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    "${docker_args[@]}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e GOTOOLCHAIN=auto \
    -e GOMODCACHE=/go-cache/mod \
    -e GOCACHE=/go-cache/build \
    -e PYTHON_ENV=/python-cache \
    -e ECOSYNC_CONTAINER_WORKSPACE="$container_workspace" \
    -e PATH=/usr/local/go/bin:/go/bin:/python-cache/build/ve/linux/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -v "$workspace:$container_workspace" \
    -v "$cache_root/go-mod:/go-cache/mod" \
    -v "$cache_root/go-build:/go-cache/build" \
    -v "$cache_root/python:/python-cache" \
    -v "$repo_root/repos/elastic/beats/.git:$repo_root/repos/elastic/beats/.git:ro" \
    -v "$repo_root/repos/elastic/cloud-on-k8s/.git:$repo_root/repos/elastic/cloud-on-k8s/.git:ro" \
    -w "$container_workspace/$workdir" \
    "$image" \
    bash -c "$script"
}

case "$profile" in
  beats-hidden)
    run_in_docker "repos/elastic/beats/filebeat" "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-3600}" '
      set -euo pipefail
      reports="$ECOSYNC_CONTAINER_WORKSPACE/.ecosyncbench/test-reports"
      compose_project="filebeat_9_4_0_c0708eec0d-snapshot"
      export ES_BEATS="$(cd .. && pwd)"
      export STACK_ENVIRONMENT=snapshot
      export TESTING_ENVIRONMENT=snapshot
      mkdir -p /tmp/ecosync-home "$reports"
      cleanup_filebeat_compose() {
        docker compose -p "$compose_project" rm --stop --force >/dev/null 2>&1 || true
        docker network rm "${compose_project}_default" >/dev/null 2>&1 || true
      }
      cleanup_filebeat_compose
      trap cleanup_filebeat_compose EXIT
      find .. -path "*cert*" -type d -exec chmod a+rx {} + 2>/dev/null || true
      find .. -path "*cert*" -type f -exec chmod a+r {} + 2>/dev/null || true
      if [[ -d ../testing/environments ]]; then
        find ../testing/environments -type d -exec chmod a+rx {} +
        find ../testing/environments -type f -exec chmod a+r {} +
      fi
      /usr/local/go/bin/go install github.com/magefile/mage@latest
      /go/bin/mage fields dashboards buildSystemTestBinary
      docker compose -p "$compose_project" up --detach --wait --wait-timeout 600 elasticsearch
      status=0
      set +e
      TESTING_FILEBEAT_MODULES=elasticsearch \
      TESTING_FILEBEAT_FILESETS=querylog \
      TESTING_FILEBEAT_FILEPATTERN="*.log" \
      INTEGRATION_TESTS=1 \
      RUN_AS_FILESTREAM=true \
      MODULES_PATH="$PWD/module" \
      ES_HOST=localhost \
      ES_USER=beats \
      ES_PASS=testing \
      ES_SUPERUSER_USER=admin \
      ES_SUPERUSER_PASS=testing \
      TESTING_FILEBEAT_ALLOW_OLDER=1 \
      DOCKER_COMPOSE_PROJECT_NAME="$compose_project" \
      python3 -m pytest \
        tests/system/test_json.py \
        tests/system/test_modules.py \
        tests/system/test_autodiscover.py \
        tests/system/test_reload_inputs.py \
        --junitxml="$reports/beats-querylog.xml" \
        | tee "$reports/beats-querylog.log"
      status=${PIPESTATUS[0]}
      set -e
      docker compose -p "$compose_project" logs --no-color elasticsearch \
        > "$reports/beats-elasticsearch.log" 2>&1 || true
      if [[ "$status" -ne 0 ]]; then
        exit "$status"
      fi
    '
    ;;
  cloud_on_k8s-hidden)
    run_in_docker "repos/elastic/cloud-on-k8s" "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-1800}" '
      set -euo pipefail
      reports="$ECOSYNC_CONTAINER_WORKSPACE/.ecosyncbench/test-reports"
      mkdir -p /tmp/ecosync-home "$reports"
      status=0
      run_go() {
        local report="$1"
        shift
        set +e
        /usr/local/go/bin/go test -count=1 -json "$@" 2>&1 | tee "$reports/${report}"
        local rc=${PIPESTATUS[0]}
        set -e
        if [[ "$rc" -ne 0 ]]; then
          status=1
        fi
      }
      run_go cloud-on-k8s-stackmon-common.json -run "^(Test_newBeatConfig|TestConfigVolumeName|TestCAVolumeName)$" ./pkg/controller/common/stackmon
      run_go cloud-on-k8s-elasticsearch-stackmon.json -run "^(TestWithMonitoring|TestMetricbeatConfig|TestQuerylogFilebeatConfig)$" ./pkg/controller/elasticsearch/stackmon
      exit "$status"
    '
    ;;
  *)
    echo "Unknown profile for elastic_beats_cloud_on_k8s_7807f1ca05af: $profile" >&2
    exit 2
    ;;
esac
