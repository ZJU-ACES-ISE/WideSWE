#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
uid="${ECOSYNC_UID:-$(id -u)}"
gid="${ECOSYNC_GID:-$(id -g)}"

dotnet_image="ecosyncbench/base/dotnet-sdk:10.0"
kibana_image="ecosyncbench/deps/elastic-cloudid-node:f176a87d6aaa"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/matrix-cache/elastic-cloudid-f176a87d6aaa}"

mkdir -p \
  "$workspace/.ecosyncbench/test-reports" \
  "$cache_root/dotnet/nuget" \
  "$cache_root/dotnet/tmp" \
  "$cache_root/node/home" \
  "$cache_root/node/corepack" \
  "$cache_root/node/yarn" \
  "$cache_root/node/npm" \
  "$cache_root/node/kibana-node_modules"

docker_git_mounts() {
  local git_file git_dir common_dir common_abs
  [[ -d "$workspace/repos" ]] || return 0
  while IFS= read -r git_file; do
    git_dir="$(sed -n 's/^gitdir: //p' "$git_file" | head -1)"
    [[ "$git_dir" == /* && -d "$git_dir" ]] || continue
    printf '%s\0' "$git_dir:$git_dir"
    if [[ -f "$git_dir/commondir" ]]; then
      common_dir="$(sed -n '1p' "$git_dir/commondir")"
      if [[ "$common_dir" == /* ]]; then
        common_abs="$common_dir"
      else
        common_abs="$(cd "$git_dir/$common_dir" && pwd)"
      fi
      [[ -d "$common_abs" ]] && printf '%s\0' "$common_abs:$common_abs"
    fi
  done < <(find "$workspace/repos" -maxdepth 4 -type f -name .git 2>/dev/null)
}

ensure_dotnet_image() {
  docker image inspect "$dotnet_image" >/dev/null 2>&1
}

ensure_kibana_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$kibana_image" >/dev/null 2>&1; then
    docker build -t "$kibana_image" -f "$task_dir/environment/kibana-node16.Dockerfile" "$task_dir/environment"
  fi
}

run_dotnet() {
  local script="$1"
  ensure_dotnet_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$dotnet_image" bash -lc 'dotnet --list-sdks | grep -q "^10\\."'
    return 0
  fi
  timeout "${ECOSYNC_PROFILE_TIMEOUT:-3600}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "$uid:$gid" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e DOTNET_CLI_HOME=/tmp/ecosync-home \
    -e DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    -e DOTNET_NOLOGO=1 \
    -e DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1 \
    -e DOTNET_GENERATE_ASPNET_CERTIFICATE=false \
    -e DOTNET_SYSTEM_NET_SECURITY_NOREVOCATIONCHECKBYDEFAULT=true \
    -e NUGET_PACKAGES=/dotnet-cache/nuget \
    -e TMPDIR=/dotnet-cache/tmp \
    -v "$workspace:/workspace" \
    -v "$cache_root/dotnet:/dotnet-cache" \
    -w /workspace/repos/elastic/elastic-transport-net \
    "$dotnet_image" \
    bash -lc "$script"
}

run_kibana() {
  local script="$1"
  local -a git_mounts=()
  local mount_spec
  ensure_kibana_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$kibana_image" bash -lc 'node --version | grep -q v16.19.1 && yarn --version >/dev/null'
    return 0
  fi
  while IFS= read -r -d '' mount_spec; do
    git_mounts+=(-v "$mount_spec")
  done < <(docker_git_mounts)
  timeout "${ECOSYNC_PROFILE_TIMEOUT:-5400}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "$uid:$gid" \
    -e HOME=/node-cache/home \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    -e COREPACK_HOME=/node-cache/corepack \
    -e YARN_CACHE_FOLDER=/node-cache/yarn \
    -e YARN_GLOBAL_FOLDER=/node-cache/yarn-berry \
    -e npm_config_cache=/node-cache/npm \
    -e CYPRESS_INSTALL_BINARY=0 \
    -e PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    -e PUPPETEER_SKIP_DOWNLOAD=1 \
    -v "$workspace:/workspace" \
    -v "$cache_root/node:/node-cache" \
    -v "$cache_root/node/kibana-node_modules:/workspace/repos/elastic/kibana/node_modules" \
    "${git_mounts[@]}" \
    -w /workspace/repos/elastic/kibana \
    "$kibana_image" \
    bash -lc "$script"
}

case "$profile" in
  elastic_transport_net-hidden)
    run_dotnet '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports/elastic-transport-net-hidden /tmp/ecosync-home /dotnet-cache/tmp
      report=/workspace/.ecosyncbench/test-reports/elastic-transport-net-hidden/elastic-transport-net-hidden.trx
      set +e
      dotnet test tests/Elastic.Transport.Tests/Elastic.Transport.Tests.csproj \
        --framework net10.0 \
        --filter "FullyQualifiedName~CloudNodePoolTests" \
        --logger "trx;LogFileName=elastic-transport-net-hidden.trx" \
        --results-directory /workspace/.ecosyncbench/test-reports/elastic-transport-net-hidden \
        --verbosity minimal
      rc=$?
      set -e
      if [[ "$rc" -ne 0 && ! -s "$report" ]]; then
        fallback=/workspace/.ecosyncbench/test-reports/elastic-transport-net-hidden/elastic-transport-net-hidden-compile-failures.xml
        classname=Elastic.Transport.Tests.Components.NodePool.CloudNodePoolTests
        mapfile -t methods < <(
          grep -A1 "\\[Fact\\]" tests/Elastic.Transport.Tests/Components/NodePool/CloudNodePoolTests.cs \
            | sed -n "s/.*public[[:space:]]\\+void[[:space:]]\\+\\([A-Za-z_][A-Za-z0-9_]*\\)[[:space:]]*(.*/\\1/p"
        )
        {
          printf "<testsuite name=\"%s\" tests=\"%s\" failures=\"%s\" errors=\"0\" skipped=\"0\">\\n" "$classname" "${#methods[@]}" "${#methods[@]}"
          for method in "${methods[@]}"; do
            printf "  <testcase classname=\"%s\" name=\"%s.%s\">\\n" "$classname" "$classname" "$method"
            printf "    <failure message=\"dotnet test failed during compilation before test execution\">Hidden upstream test module did not compile against the base implementation.</failure>\\n"
            printf "  </testcase>\\n"
          done
          printf "</testsuite>\\n"
        } > "$fallback"
      fi
      test -s "$report" || test -s /workspace/.ecosyncbench/test-reports/elastic-transport-net-hidden/elastic-transport-net-hidden-compile-failures.xml
      exit "$rc"
    '
    ;;
  kibana-hidden)
    run_kibana '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /node-cache/home /node-cache/corepack /node-cache/yarn /node-cache/npm
      corepack prepare yarn@1.22.22 --activate
      yarn install --frozen-lockfile --non-interactive
      if [[ ! -s packages/kbn-repo-packages/package-map.json ]]; then
        node - <<'"'"'NODE'"'"'
const { updatePackageMap, getRepoRelsSync } = require("./packages/kbn-repo-packages");
updatePackageMap(process.cwd(), Array.from(getRepoRelsSync(process.cwd(), ["**/kibana.jsonc"])));
NODE
      fi
      status=0
      run_jest_group() {
        local report="$1"
        shift
        set +e
        node scripts/jest.js "$@" \
          --runInBand \
          --json \
          --outputFile="$report" \
          --colors=false
        rc=$?
        set -e
        test -s "$report" || rc=1
        if [[ "$rc" -ne 0 && "$status" -eq 0 ]]; then
          status="$rc"
        fi
      }
      run_jest_group /workspace/.ecosyncbench/test-reports/kibana-cloud-hidden.json \
        --config x-pack/plugins/cloud/jest.config.js \
        x-pack/plugins/cloud/public/cloud_id_contract.hidden.test.ts \
        x-pack/plugins/cloud/public/plugin.test.ts \
        x-pack/plugins/cloud/server/cloud_id_contract.hidden.test.ts \
        x-pack/plugins/cloud/server/plugin.test.ts
      exit "$status"
    '
    ;;
  *)
    echo "Unknown profile for elastic_elastic_transport_net_kibana_f176a87d6aaa: ${profile}" >&2
    exit 2
    ;;
esac
