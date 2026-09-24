#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
repo_root="${ECOSYNC_REPO_ROOT:-/opt/ecosyncbench}"
image="ecosyncbench/base/go:1.26-bookworm"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/go-cache/elastic_beats_elastic_agent_libs_caea539132d1}"

mkdir -p "$cache_root/mod" "$cache_root/build" "$workspace/.ecosyncbench/test-reports"

ensure_image() {
  if docker image inspect "$image" >/dev/null 2>&1; then
    return 0
  fi
  docker build -t "$image" \
    -f "$repo_root/benchmark/images/base/go-1.26-bookworm/Dockerfile" \
    "$repo_root/benchmark/images/base/go-1.26-bookworm"
}

run_go_profile() {
  local workdir="$1"
  local report="$2"
  local timeout_seconds="$3"
  local script="$4"

  ensure_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm \
      -e PATH=/usr/local/go/bin:/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
      "$image" bash -lc '/usr/local/go/bin/go version >/dev/null'
    return 0
  fi

  timeout "$timeout_seconds" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    --network host \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e GOTOOLCHAIN=auto \
    -e GOMODCACHE=/go-cache/mod \
    -e GOCACHE=/go-cache/build \
    -e PATH=/usr/local/go/bin:/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -v "$workspace:/workspace" \
    -v "$cache_root:/go-cache" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -lc "set -euo pipefail; mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home; ${script} 2>&1 | tee /workspace/.ecosyncbench/test-reports/${report}"
}

case "$profile" in
  elastic_agent_libs-hidden)
    run_go_profile "repos/elastic/elastic-agent-libs" "elastic-agent-libs-hidden.json" "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-1800}" \
      '/usr/local/go/bin/go build ./transport/tlscommon ./transport/httpcommon && /usr/local/go/bin/go test -count=1 -json ./transport/tlscommon/loggercontract ./transport/httpcommon'
    ;;
  beats-hidden)
    run_go_profile "repos/elastic/beats" "beats-hidden.json" "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-3600}" \
      '/usr/local/go/bin/go mod edit -replace github.com/elastic/elastic-agent-libs=/workspace/repos/elastic/elastic-agent-libs && /usr/local/go/bin/go build ./filebeat/input/kafka ./filebeat/input/mqtt ./filebeat/input/redis ./filebeat/inputsource/tcp ./heartbeat/monitors/active/http ./heartbeat/monitors/active/tcp ./libbeat/common/transport/transptest ./libbeat/esleg/eslegclient ./libbeat/otelbeat/oteltranslate ./libbeat/outputs/kafka ./libbeat/outputs/logstash ./libbeat/outputs/redis ./libbeat/processors/add_cloud_metadata ./libbeat/processors/translate_ldap_attribute ./metricbeat/helper ./metricbeat/helper/server/http ./metricbeat/module/aerospike ./metricbeat/module/aerospike/namespace ./metricbeat/module/kafka ./metricbeat/module/mongodb ./metricbeat/module/mongodb/collstats ./metricbeat/module/mongodb/dbstats ./metricbeat/module/mongodb/metrics ./metricbeat/module/mongodb/replstatus ./metricbeat/module/mongodb/status ./metricbeat/module/mysql ./metricbeat/module/mysql/query ./metricbeat/module/redis ./x-pack/filebeat/input/cel ./x-pack/filebeat/input/entityanalytics/provider/activedirectory ./x-pack/filebeat/input/entityanalytics/provider/azuread/authenticator/oauth2 ./x-pack/filebeat/input/entityanalytics/provider/azuread/fetcher/graph ./x-pack/filebeat/input/entityanalytics/provider/jamf ./x-pack/filebeat/input/entityanalytics/provider/okta ./x-pack/filebeat/input/http_endpoint ./x-pack/filebeat/input/httpjson ./x-pack/filebeat/input/lumberjack ./x-pack/filebeat/input/salesforce ./x-pack/filebeat/input/streaming ./x-pack/libbeat/common/aws ./x-pack/libbeat/common/cloudfoundry ./x-pack/metricbeat/module/sql/query && /usr/local/go/bin/go test -count=1 -json ./libbeat/common/transport/tlsloggercontract ./filebeat/input/redis ./metricbeat/mb ./metricbeat/module/mongodb/replstatus'
    ;;
  *)
    echo "Unknown profile for elastic_beats_elastic_agent_libs_caea539132d1: $profile" >&2
    exit 2
    ;;
esac
