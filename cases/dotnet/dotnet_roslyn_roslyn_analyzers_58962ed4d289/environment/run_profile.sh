#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
image="ecosyncbench/deps/dotnet-roslyn-analyzers-extension-members:58962ed4d289"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/dotnet-roslyn-analyzers-58962ed4d289}"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$task_dir/environment/dotnet-roslyn-analyzers.Dockerfile" "$task_dir/environment"
  fi
}

run_dotnet() {
  local workdir="$1"
  local timeout_seconds="$2"
  local script="$3"
  ensure_image
  mkdir -p "$cache_root/nuget" "$cache_root/tmp" "$workspace/.ecosyncbench/test-reports"
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image" bash -lc 'dotnet --list-sdks'
    return 0
  fi
  timeout "$timeout_seconds" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
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
    -v "$cache_root:/dotnet-cache" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -lc "$script"
}

case "$profile" in
  roslyn-hidden)
    run_dotnet "repos/dotnet/roslyn" "${ECOSYNC_PROFILE_TIMEOUT:-7200}" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports/roslyn-hidden /tmp/ecosync-home /dotnet-cache/tmp
      dotnet --list-sdks
      dotnet test src/Workspaces/CoreTest/Microsoft.CodeAnalysis.Workspaces.UnitTests.csproj \
        --framework net8.0 \
        --filter "FullyQualifiedName~AdhocWorkspaceTests" \
        --logger "trx;LogFileName=roslyn-hidden.trx" \
        --results-directory /workspace/.ecosyncbench/test-reports/roslyn-hidden \
        --verbosity minimal
      test -s /workspace/.ecosyncbench/test-reports/roslyn-hidden/roslyn-hidden.trx
    '
    ;;
  roslyn_analyzers-hidden)
    run_dotnet "repos/dotnet/roslyn-analyzers" "${ECOSYNC_PROFILE_TIMEOUT:-7200}" '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports/roslyn-analyzers-hidden /tmp/ecosync-home /dotnet-cache/tmp
      dotnet --list-sdks
      dotnet test src/NetAnalyzers/UnitTests/Microsoft.CodeAnalysis.NetAnalyzers.UnitTests.csproj \
        --filter "FullyQualifiedName~MarkMembersAsStaticTests|FullyQualifiedName~DynamicInterfaceCastableImplementationTests" \
        --logger "trx;LogFileName=roslyn-analyzers-hidden.trx" \
        --results-directory /workspace/.ecosyncbench/test-reports/roslyn-analyzers-hidden \
        --verbosity minimal
      test -s /workspace/.ecosyncbench/test-reports/roslyn-analyzers-hidden/roslyn-analyzers-hidden.trx
    '
    ;;
  *)
    echo "Unknown profile for dotnet_roslyn_roslyn_analyzers_58962ed4d289: $profile" >&2
    exit 2
    ;;
esac
