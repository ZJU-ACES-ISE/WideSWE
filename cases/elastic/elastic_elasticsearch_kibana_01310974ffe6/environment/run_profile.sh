#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_root="${ECOSYNC_REPO_ROOT:-$(pwd)}"
if [[ ! -d "$repo_root/.git" ]]; then
  echo "cannot locate repository root from cwd=$repo_root task_dir=$task_dir" >&2
  exit 2
fi

workspace="$(cd "${ECOSYNC_WORKSPACE:-$repo_root}" && pwd)"
cache_root="${ECOSYNC_ELASTIC_CACHE_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/elastic-elasticsearch-kibana-01310974ffe6}}"
shared_corepack_root="${ECOSYNC_SHARED_COREPACK_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/evaluator-dependency-cache/corepack}}"
container_home="$workspace/.ecosyncbench/home"
mkdir -p "$cache_root"/{gradle,yarn} "$container_home"
if [[ ! -s "$shared_corepack_root/v1/yarn/1.22.21/lib/cli.js" ]]; then
  echo "missing offline Corepack yarn@1.22.21 cache: $shared_corepack_root" >&2
  exit 2
fi

git_mount_args=()
for repo in "$workspace/repos/elastic/elasticsearch" "$workspace/repos/elastic/kibana"; do
  if [[ -e "$repo/.git" ]]; then
    git_common_dir="$(git -C "$repo" rev-parse --git-common-dir 2>/dev/null || true)"
    if [[ -n "$git_common_dir" && "$git_common_dir" = /* && -d "$git_common_dir" ]]; then
      git_mount_args+=( -v "$git_common_dir:$git_common_dir:ro" )
    fi
  fi
done

case "$profile" in
  elasticsearch-hidden)
    image="ecosyncbench/base/elastic-java:21"
    docker_workdir="/workspace/repos/elastic/elasticsearch"
    ;;
  kibana-hidden)
    image="ecosyncbench/base/elastic-node:22.22.0"
    docker_workdir="/workspace/repos/elastic/kibana"
    ;;
  *)
    echo "Unknown profile for elastic_elasticsearch_kibana_01310974ffe6: $profile" >&2
    exit 2
    ;;
esac

if ! docker image inspect "$image" >/dev/null 2>&1; then
  echo "missing required base image: $image" >&2
  exit 2
fi

if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
  exit 0
fi

docker run --rm \
  --ulimit nofile=65535:65535 \
  --user "$(id -u):$(id -g)" \
  -e HOME=/workspace/.ecosyncbench/home \
  -e ECOSYNC_WORKSPACE=/workspace \
  -e GRADLE_USER_HOME=/gradle-cache \
  -e COREPACK_HOME=/corepack-cache \
  -e YARN_CACHE_FOLDER=/yarn-cache \
  -e ECOSYNC_GRADLE_MAX_WORKERS="${ECOSYNC_GRADLE_MAX_WORKERS:-2}" \
  -v "$workspace:/workspace" \
  -v "$cache_root/gradle:/gradle-cache" \
  -v "$shared_corepack_root:/corepack-cache:ro" \
  -v "$cache_root/yarn:/yarn-cache" \
  -v "$task_dir/environment/run_elastic_profile_inside.sh:/opt/ecosync/run-elastic-profile.sh:ro" \
  "${git_mount_args[@]}" \
  -w "$docker_workdir" \
  "$image" \
  bash /opt/ecosync/run-elastic-profile.sh "$profile"
