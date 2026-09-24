#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
image="ecosyncbench/deps/dotnet-android-java-interop:8f49f22b1612"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/dotnet-cache/dotnet_android_java_interop_8f49f22b1612}"
mkdir -p "$cache_root/nuget" "$cache_root/dotnet" "$workspace/.ecosyncbench/test-reports"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$task_dir/environment/dotnet-jdk.Dockerfile" "$task_dir/environment"
  fi
}

run_dotnet() {
  local workdir="$1"
  local script="$2"
  ensure_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image" bash -lc 'dotnet --info >/dev/null && javac -version >/dev/null'
    return 0
  fi
  timeout "${ECOSYNC_PROFILE_TIMEOUT:-2400}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e DOTNET_CLI_HOME=/dotnet-cache/home \
    -e NUGET_PACKAGES=/dotnet-cache/nuget/ \
    -e DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1 \
    -e DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    -e JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
    -e JAVA_HOME_17_X64=/usr/lib/jvm/java-17-openjdk-amd64 \
    -e JAVA_HOME_11_X64=/usr/lib/jvm/java-17-openjdk-amd64 \
    -v "$workspace:/workspace" \
    -v "$cache_root:/dotnet-cache" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -lc "set -euo pipefail; mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home /dotnet-cache/home; ${script}"
}

case "$profile" in
  android-hidden)
    run_dotnet "repos/dotnet/android" '
      mkdir -p external
      rm -rf external/Java.Interop
      ln -s /workspace/repos/dotnet/java-interop external/Java.Interop
      dotnet test tests/Microsoft.Android.Sdk.TrimmableTypeMap.Tests/Microsoft.Android.Sdk.TrimmableTypeMap.Tests.csproj \
        --filter "FullyQualifiedName~TypeMapAssemblyGeneratorTests" \
        --results-directory /workspace/.ecosyncbench/test-reports/android-hidden \
        --logger "trx;LogFileName=android-hidden.trx"
    '
    ;;
  java_interop-hidden)
    run_dotnet "repos/dotnet/java-interop" '
      xat_cache=/dotnet-cache/xamarin-android-tools-091e3a66c0cfcb4fcaaaaa7bc9c5fa85947eecb6
      if [ ! -d "$xat_cache/.git" ]; then
        rm -rf "$xat_cache"
        git clone https://github.com/xamarin/xamarin-android-tools.git "$xat_cache"
      fi
      git -C "$xat_cache" fetch --depth 1 origin 091e3a66c0cfcb4fcaaaaa7bc9c5fa85947eecb6
      git -C "$xat_cache" checkout 091e3a66c0cfcb4fcaaaaa7bc9c5fa85947eecb6
      mkdir -p external bin/BuildDebug
      rm -rf external/xamarin-android-tools
      ln -s "$xat_cache" external/xamarin-android-tools
      dotnet build build-tools/Java.Interop.BootstrapTasks/Java.Interop.BootstrapTasks.csproj \
        /p:Configuration=Debug
      cat > bin/BuildDebug/JdkInfo.props <<'"'"'XML'"'"'
<Project xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <Choose>
    <When Condition=" '"'"'$(JdkJvmPath)'"'"' == '"'"''"'"' ">
      <PropertyGroup>
        <JdkJvmPath>/usr/lib/jvm/java-17-openjdk-amd64/lib/server/libjvm.so</JdkJvmPath>
      </PropertyGroup>
      <ItemGroup>
        <JdkIncludePath Include="/usr/lib/jvm/java-17-openjdk-amd64/include" />
        <JdkIncludePath Include="/usr/lib/jvm/java-17-openjdk-amd64/include/linux" />
      </ItemGroup>
    </When>
  </Choose>
  <PropertyGroup>
    <JavaApiDefineConstants Condition=" '"'"'$(JavaApiDefineConstants)'"'"' == '"'"''"'"' ">JAVA_API_11;JAVA_API_12;JAVA_API_13;JAVA_API_14;JAVA_API_15;JAVA_API_16;JAVA_API_17</JavaApiDefineConstants>
    <JavaMajorVersion Condition=" '"'"'$(JavaMajorVersion)'"'"' == '"'"''"'"' ">17</JavaMajorVersion>
    <JavaSdkDirectory Condition=" '"'"'$(JavaSdkDirectory)'"'"' == '"'"''"'"' ">/usr/lib/jvm/java-17-openjdk-amd64</JavaSdkDirectory>
    <JavaPath Condition=" '"'"'$(JavaPath)'"'"' == '"'"''"'"' ">/usr/lib/jvm/java-17-openjdk-amd64/bin/java</JavaPath>
    <JavaCPath Condition=" '"'"'$(JavaCPath)'"'"' == '"'"''"'"' ">/usr/lib/jvm/java-17-openjdk-amd64/bin/javac</JavaCPath>
    <JarPath Condition=" '"'"'$(JarPath)'"'"' == '"'"''"'"' ">/usr/lib/jvm/java-17-openjdk-amd64/bin/jar</JarPath>
    <DotnetToolPath Condition=" '"'"'$(DotnetToolPath)'"'"' == '"'"''"'"' ">dotnet</DotnetToolPath>
  </PropertyGroup>
</Project>
XML
      cat > bin/BuildDebug/JdkInfo.mk <<'"'"'MK'"'"'
export  JI_JAR_PATH          := /usr/lib/jvm/java-17-openjdk-amd64/bin/jar
export  JI_JAVA_PATH         := /usr/lib/jvm/java-17-openjdk-amd64/bin/java
export  JI_JAVAC_PATH        := /usr/lib/jvm/java-17-openjdk-amd64/bin/javac
export  JI_JDK_INCLUDE_PATHS := /usr/lib/jvm/java-17-openjdk-amd64/include /usr/lib/jvm/java-17-openjdk-amd64/include/linux
export  JI_JVM_PATH          := /usr/lib/jvm/java-17-openjdk-amd64/lib/server/libjvm.so
MK
      dotnet test tests/Java.Interop-Tests/Java.Interop-Tests.csproj \
        --filter "FullyQualifiedName~JniTypeTest" \
        --results-directory /workspace/.ecosyncbench/test-reports/java-interop-hidden \
        --logger "trx;LogFileName=java-interop-hidden.trx"
    '
    ;;
  *)
    echo "Unknown profile for dotnet_android_java_interop_8f49f22b1612: $profile" >&2
    exit 2
    ;;
esac
