#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
image="ecosyncbench/base/java-maven:21-ubuntu"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/maven-cache/jenkinsci-mcp-warnings-ff9a04d18a82}"
mkdir -p "$cache_root/m2" "$workspace/.ecosyncbench/test-reports"
common_maven_flags="-B -ntp -s /tmp/ecosync-home/.m2/settings.xml -Dmaven.repo.local=/m2 -Dmaven.gitcommitid.skip=true -Denforcer.skip=true -Daether.connector.connectTimeout=60000 -Daether.connector.requestTimeout=120000 -Dmaven.wagon.rto=120000"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$task_dir/environment/java-maven-21.Dockerfile" "$task_dir/environment"
  fi
}

run_maven() {
  local workdir="$1"
  local script="$2"
  ensure_image
  docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e MAVEN_OPTS="-Dmaven.repo.local=/m2 -Dmaven.ext.class.path=/workspace/.ecosyncbench/jenkins-hpi-lifecycle-mapping.jar" \
    -v "$workspace:/workspace" \
    -v "$cache_root/m2:/m2" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -lc "$script"
}

write_maven_settings='
mkdir -p /tmp/ecosync-home/.m2
cat > /tmp/ecosync-home/.m2/settings.xml <<'"'"'XML'"'"'
<settings xmlns="http://maven.apache.org/SETTINGS/1.2.0"
          xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
          xsi:schemaLocation="http://maven.apache.org/SETTINGS/1.2.0 https://maven.apache.org/xsd/settings-1.2.0.xsd">
  <profiles>
    <profile>
      <id>jenkins-public</id>
      <repositories>
        <repository>
          <id>repo.jenkins-ci.org</id>
          <url>https://repo.jenkins-ci.org/public/</url>
        </repository>
      </repositories>
      <pluginRepositories>
        <pluginRepository>
          <id>repo.jenkins-ci.org</id>
          <url>https://repo.jenkins-ci.org/public/</url>
        </pluginRepository>
      </pluginRepositories>
    </profile>
  </profiles>
  <activeProfiles>
    <activeProfile>jenkins-public</activeProfile>
  </activeProfiles>
</settings>
XML
'

write_hpi_lifecycle_mapping='
mkdir -p /workspace/.ecosyncbench/maven-ext/META-INF/plexus
cat > /workspace/.ecosyncbench/maven-ext/META-INF/plexus/components.xml <<'"'"'XML'"'"'
<component-set>
  <components>
    <component>
      <role>org.apache.maven.lifecycle.mapping.LifecycleMapping</role>
      <role-hint>hpi</role-hint>
      <implementation>org.apache.maven.lifecycle.mapping.DefaultLifecycleMapping</implementation>
      <configuration>
        <lifecycles>
          <lifecycle>
            <id>default</id>
            <phases>
              <validate>org.jenkins-ci.tools:maven-hpi-plugin:validate,org.jenkins-ci.tools:maven-hpi-plugin:validate-hpi</validate>
              <process-resources>org.apache.maven.plugins:maven-resources-plugin:resources</process-resources>
              <compile>org.apache.maven.plugins:maven-compiler-plugin:compile</compile>
              <process-classes>org.kohsuke:access-modifier-checker:1.31:enforce</process-classes>
              <generate-test-sources>org.jenkins-ci.tools:maven-hpi-plugin:insert-test</generate-test-sources>
              <process-test-resources>org.apache.maven.plugins:maven-resources-plugin:testResources</process-test-resources>
              <test-compile>org.apache.maven.plugins:maven-compiler-plugin:testCompile,org.jenkins-ci.tools:maven-hpi-plugin:resolve-test-dependencies</test-compile>
              <process-test-classes>org.jenkins-ci.tools:maven-hpi-plugin:test-runtime</process-test-classes>
              <test>org.apache.maven.plugins:maven-surefire-plugin:test</test>
              <package>org.jenkins-ci.tools:maven-hpi-plugin:hpi</package>
            </phases>
          </lifecycle>
        </lifecycles>
      </configuration>
    </component>
    <component>
      <role>org.apache.maven.artifact.handler.ArtifactHandler</role>
      <role-hint>executable-war</role-hint>
      <implementation>org.apache.maven.artifact.handler.DefaultArtifactHandler</implementation>
      <configuration>
        <type>executable-war</type>
        <extension>war</extension>
        <packaging>war</packaging>
        <language>java</language>
        <addedToClasspath>true</addedToClasspath>
      </configuration>
    </component>
  </components>
</component-set>
XML
(cd /workspace/.ecosyncbench/maven-ext && jar cf /workspace/.ecosyncbench/jenkins-hpi-lifecycle-mapping.jar META-INF/plexus/components.xml)
'

case "$profile" in
  mcp_server_plugin-hidden)
    if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
      run_maven "repos/jenkinsci/mcp-server-plugin" '
        set -euo pipefail
        '"$write_maven_settings"'
        '"$write_hpi_lifecycle_mapping"'
        mvn '"$common_maven_flags"' -DskipTests test-compile
        mkdir -p target/test-classes/plugins
        mvn '"$common_maven_flags"' -DskipTests -DjenkinsHome=target/test-classes hpi:hpl
        cp target/test-classes/plugins/*.hpl target/test-classes/the.hpl
      '
      exit 0
    fi
    run_maven "repos/jenkinsci/mcp-server-plugin" '
      set -euo pipefail
      rm -rf target/surefire-reports target/failsafe-reports
      '"$write_maven_settings"'
      '"$write_hpi_lifecycle_mapping"'
      mvn '"$common_maven_flags"' -DskipTests test-compile
      mkdir -p target/test-classes/plugins
      mvn '"$common_maven_flags"' -DskipTests -DjenkinsHome=target/test-classes hpi:hpl
      cp target/test-classes/plugins/*.hpl target/test-classes/the.hpl
      set +e
      mvn '"$common_maven_flags"' -Dtest=EndPointTest,WarningsExtensionTest test
      code=$?
      set -e
      exit "$code"
    '
    ;;
  warnings_ng_plugin-hidden)
    if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
      run_maven "repos/jenkinsci/warnings-ng-plugin" '
        set -euo pipefail
        '"$write_maven_settings"'
        '"$write_hpi_lifecycle_mapping"'
        mvn '"$common_maven_flags"' -pl plugin -am -DskipTests test-compile
        mkdir -p plugin/target/test-classes/plugins
        mvn '"$common_maven_flags"' -pl plugin -DskipTests -DjenkinsHome=target/test-classes hpi:hpl
        cp plugin/target/test-classes/plugins/*.hpl plugin/target/test-classes/the.hpl
      '
      exit 0
    fi
    run_maven "repos/jenkinsci/warnings-ng-plugin" '
      set -euo pipefail
      rm -rf plugin/target/surefire-reports plugin/target/failsafe-reports
      '"$write_maven_settings"'
      '"$write_hpi_lifecycle_mapping"'
      mvn '"$common_maven_flags"' -pl plugin -am -DskipTests test-compile
      mkdir -p plugin/target/test-classes/plugins
      mvn '"$common_maven_flags"' -pl plugin -DskipTests -DjenkinsHome=target/test-classes hpi:hpl
      cp plugin/target/test-classes/plugins/*.hpl plugin/target/test-classes/the.hpl
      set +e
      mvn '"$common_maven_flags"' -pl plugin -Dtest=WarningsMcpServerExtensionTest test
      code=$?
      set -e
      exit "$code"
    '
    ;;
  *)
    echo "Unknown profile for jenkinsci_mcp_server_plugin_warnings_ng_plugin_ff9a04d18a82: $profile" >&2
    exit 2
    ;;
esac
