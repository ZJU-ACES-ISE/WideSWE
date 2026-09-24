#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
repo_root="$(pwd -P)"
go_image="ecosyncbench/base/go:1.26-bookworm"
integrations_image="ecosyncbench/deps/elastic-integrations-policy:7d6ebc81c77c"
elastic_stack_version="${ECOSYNC_ELASTIC_STACK_VERSION:-9.3.3}"
elasticsearch_start_period_seconds="${ECOSYNC_ELASTICSEARCH_START_PERIOD_SECONDS:-900}"
kibana_start_period_seconds="${ECOSYNC_KIBANA_START_PERIOD_SECONDS:-1200}"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/elastic_beats_integrations_7d6ebc81c77c}"

mkdir -p \
  "$workspace/.ecosyncbench/test-reports" \
  "$cache_root/go-build" \
  "$cache_root/go-mod" \
  "$cache_root/go-bin" \
  "$cache_root/home"

ensure_go_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$go_image" >/dev/null 2>&1; then
    docker build -t "$go_image" \
      -f "$repo_root/benchmark/images/base/go-1.26-bookworm/Dockerfile" \
      "$repo_root/benchmark/images/base/go-1.26-bookworm"
  fi
}

ensure_integrations_image() {
  ensure_go_image
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$integrations_image" >/dev/null 2>&1; then
    docker build -t "$integrations_image" \
      -f "$task_dir/environment/integrations-policy.Dockerfile" \
      "$task_dir/environment"
  fi
}

docker_go() {
  ensure_go_image
  docker run --rm \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/cache/home \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e GOCACHE=/cache/go-build \
    -e GOMODCACHE=/cache/go-mod \
    -e GOBIN=/cache/go-bin \
    -e PATH=/cache/go-bin:/usr/local/go/bin:/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    "$go_image" \
    "$@"
}

docker_go_timed() {
  ensure_go_image
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-3600}" docker run --rm \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/cache/home \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e GOCACHE=/cache/go-build \
    -e GOMODCACHE=/cache/go-mod \
    -e GOBIN=/cache/go-bin \
    -e PATH=/cache/go-bin:/usr/local/go/bin:/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    "$go_image" \
    "$@"
}

run_beats() {
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker_go bash -c '/usr/local/go/bin/go version >/dev/null'
    return 0
  fi

  docker_go_timed bash -c '
    set -euo pipefail
    mkdir -p /workspace/.ecosyncbench/test-reports /cache/home
    cd /workspace/repos/elastic/beats
    set +e
    /usr/local/go/bin/go test -count=1 -json ./x-pack/filebeat/input/azureeventhub \
      > /workspace/.ecosyncbench/test-reports/beats-azureeventhub-go-test.json
    status=$?
    set -e
    test -s /workspace/.ecosyncbench/test-reports/beats-azureeventhub-go-test.json
    exit "$status"
  '
}

run_integrations() {
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    ensure_integrations_image
    docker run --rm "$integrations_image" bash -lc '/usr/local/go/bin/go version >/dev/null && docker --version >/dev/null'
    return 0
  fi

  ensure_integrations_image
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-3600}" docker run --rm \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    --group-add "$(stat -c '%g' /var/run/docker.sock)" \
    --network host \
    -e HOME="$cache_root/home" \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e GOCACHE="$cache_root/go-build" \
    -e GOMODCACHE="$cache_root/go-mod" \
    -e GOBIN="$cache_root/go-bin" \
    -e ECOSYNC_ELASTIC_STACK_VERSION="$elastic_stack_version" \
    -e ECOSYNC_ELASTICSEARCH_START_PERIOD_SECONDS="$elasticsearch_start_period_seconds" \
    -e ECOSYNC_KIBANA_START_PERIOD_SECONDS="$kibana_start_period_seconds" \
    -e PATH="$cache_root/go-bin:/usr/local/go/bin:/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v /usr/libexec/docker/cli-plugins/docker-compose:/usr/local/bin/docker-compose-v2:ro \
    -v "$workspace:/workspace" \
    -v "$cache_root:$cache_root" \
    "$integrations_image" \
    bash -c '
    set -euo pipefail
    mkdir -p /workspace/.ecosyncbench/test-reports "$HOME" "$GOBIN"
    mkdir -p /tmp/ecosync-bin
cat > /tmp/ecosync-bin/docker <<'"'"'SH'"'"'
#!/usr/bin/env bash
if [[ "${1:-}" == "compose" ]]; then
  shift
  is_up=0
  for arg in "$@"; do
    [[ "$arg" == "up" ]] && is_up=1
  done
  if [[ "$is_up" == "1" ]]; then
    override=/tmp/ecosync-elasticsearch-compose-override.yml
    cat > "$override" <<YAML
services:
  elasticsearch:
    healthcheck:
      start_period: ${ECOSYNC_ELASTICSEARCH_START_PERIOD_SECONDS}s
    tmpfs:
      - /usr/share/elasticsearch/data:rw,size=4g,mode=0777
  kibana:
    healthcheck:
      start_period: ${ECOSYNC_KIBANA_START_PERIOD_SECONDS}s
    volumes:
      - ecosyncbench-kibana-runtime:/usr/share/kibana
volumes:
  ecosyncbench-kibana-runtime:
    external: true
    name: ecosyncbench-kibana-9.3.3-runtime-v2
YAML
    compose_args=()
    for arg in "$@"; do
      if [[ "$arg" == "up" ]]; then
        compose_args+=("-f" "$override")
      fi
      compose_args+=("$arg")
    done
    exec /usr/local/bin/docker-compose-v2 "${compose_args[@]}"
  fi
  exec /usr/local/bin/docker-compose-v2 "$@"
fi
exec /usr/bin/docker "$@"
SH
    chmod +x /tmp/ecosync-bin/docker
    export PATH=/tmp/ecosync-bin:$PATH
    if ! command -v elastic-package >/dev/null 2>&1; then
      /usr/local/go/bin/go install github.com/elastic/elastic-package@latest
    fi
    cd /workspace/repos/elastic/integrations/packages/m365_defender
    policy_dir="$PWD/data_stream/event/_dev/test/policy"
    expected_backup="$(mktemp -d)"
    baseline_dir="$(mktemp -d)"
    for expected in "$policy_dir"/*.expected; do
      cp "$expected" "$expected_backup/"
    done
    git -C /workspace/repos/elastic/integrations show HEAD:packages/m365_defender/data_stream/event/_dev/test/policy/test-default.expected \
      > "$baseline_dir/test-default.expected"
    git -C /workspace/repos/elastic/integrations show HEAD:packages/m365_defender/data_stream/event/_dev/test/policy/test-all.expected \
      > "$baseline_dir/test-all.expected"
    cleanup_stack() {
      status=$?
      trap - EXIT
      if [[ "$status" -ne 0 ]]; then
        docker ps -a --filter name=elastic-package-stack-kibana-1 || true
        docker inspect elastic-package-stack-kibana-1 || true
        docker logs --tail 400 elastic-package-stack-kibana-1 || true
      fi
      rm -f "$policy_dir"/*.expected
      cp "$expected_backup"/*.expected "$policy_dir/"
      rm -rf "$expected_backup" "$baseline_dir"
      elastic-package stack down || true
      elastic-package clean || true
      exit "$status"
    }
    trap cleanup_stack EXIT
    elastic-package stack up -d --version "$ECOSYNC_ELASTIC_STACK_VERSION"
    set +e
    elastic-package test policy -v --data-streams alert,incident,vulnerability --report-format xUnit \
      > /workspace/.ecosyncbench/test-reports/integrations-m365-defender-policy-p2p.raw.log
    p2p_status=$?
    awk '"'"'index($0, "<?xml"){emit=1} emit{print} index($0, "</testsuites>"){emit=0}'"'"' \
      /workspace/.ecosyncbench/test-reports/integrations-m365-defender-policy-p2p.raw.log \
      > /workspace/.ecosyncbench/test-reports/integrations-m365-defender-policy-p2p.xml
    elastic-package test policy -v --data-streams event --generate --report-format xUnit \
      > /workspace/.ecosyncbench/test-reports/integrations-m365-defender-policy-event.raw.log
    generation_status=$?
    generated_report_dir=/workspace/.ecosyncbench/test-reports/integrations-m365-defender-generated-policy
    rm -rf "$generated_report_dir"
    mkdir -p "$generated_report_dir"
    for generated in "$policy_dir"/*.expected; do
      [[ -e "$generated" ]] || continue
      cp "$generated" "$generated_report_dir/"
    done
    bash "$policy_dir/semantic_policy_test.sh" \
      "$policy_dir" \
      /workspace/.ecosyncbench/test-reports/integrations-m365-defender-policy-semantic.xml \
      "$baseline_dir"
    semantic_status=$?
    set -e
    if [[ "$p2p_status" -ne 0 || "$generation_status" -ne 0 || "$semantic_status" -ne 0 ]]; then
      exit 1
    fi
    test -s /workspace/.ecosyncbench/test-reports/integrations-m365-defender-policy-p2p.xml
  '
}

case "$profile" in
  beats-hidden)
    run_beats
    ;;
  integrations-hidden)
    run_integrations
    ;;
  *)
    echo "Unknown profile for elastic_beats_integrations_7d6ebc81c77c: $profile" >&2
    exit 2
    ;;
esac
