#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
image="ecosyncbench/base/go-pingcap:1.25.10"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/go-cache/pingcap}"
mkdir -p "$cache_root" "$workspace/.ecosyncbench/test-reports"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$task_dir/environment/go-pingcap.Dockerfile" "$task_dir/environment"
  fi
}

run_in_go() {
  local workdir="$1"
  local script="$2"
  ensure_image
  docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e GOCACHE=/go-cache/build \
    -e GOMODCACHE=/go-cache/pkg/mod \
    -e GOPATH=/go-cache/gopath \
    -e GOFLAGS=-mod=mod \
    -e PATH=/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -v "$workspace:/workspace" \
    -v "$cache_root:/go-cache" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -c "$script"
}

case "$profile" in
  ticdc-hidden)
    run_in_go "repos/pingcap/ticdc" '
      set -uo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      status=0
      run_pkg() {
        out="$1"; shift
        echo "== go test $*"
        set +e
        go test -json -count=1 "$@" 2>&1 | tee "/workspace/.ecosyncbench/test-reports/${out}.json"
        code=${PIPESTATUS[0]}
        set -e
        if [ "$code" -ne 0 ]; then status="$code"; fi
      }
      run_pkg ticdc-topicmanager \
        -run "^(TestCreateTopic|TestCreateTopicWithDelay|TestGetPartitionNumMock)$" \
        ./downstreamadapter/sink/topicmanager
      run_pkg ticdc-pkg-sink-kafka \
        -run "^TestKafkaMaxRetryFromSinkURI$" \
        ./pkg/sink/kafka
      exit "$status"
    '
    ;;
  tiflow-hidden)
    run_in_go "repos/pingcap/tiflow" '
      set -uo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      status=0
      run_pkg() {
        out="$1"; shift
        echo "== go test $*"
        set +e
        go test -json -count=1 "$@" 2>&1 | tee "/workspace/.ecosyncbench/test-reports/${out}.json"
        code=${PIPESTATUS[0]}
        set -e
        if [ "$code" -ne 0 ]; then status="$code"; fi
      }
      run_pkg tiflow-ddlsink-mq \
        -run "^(TestGetDLLDispatchRuleByProtocol|TestNewKafkaDDLSinkFailed|TestNewPulsarDDLSink|TestPulsarDDLSinkNewSuccess|TestPulsarWriteDDLEventToZeroPartition|TestWriteCheckpointTsToDefaultTopic|TestWriteCheckpointTsToTableTopics|TestWriteCheckpointTsWhenCanalJsonTiDBExtensionIsDisable|TestWriteDDLEventToAllPartitions|TestWriteDDLEventToZeroPartition)$" \
        ./cdc/sink/ddlsink/mq
      run_pkg tiflow-pkg-sink-kafka \
        -run "^TestKafkaMaxRetryFromSinkURI$" \
        ./pkg/sink/kafka
      exit "$status"
    '
    ;;
  *)
    echo "Unknown profile for pingcap_ticdc_tiflow_5fcc6bc637ae: $profile" >&2
    exit 2
    ;;
esac
