ARG ECOSYNC_ANDROID_BASE_IMAGE=ecosyncbench/base/android-sdk:36
FROM ${ECOSYNC_ANDROID_BASE_IMAGE} AS warm

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO=home-assistant/android
ARG ECOSYNC_REPO_PATH=repos/home-assistant/android
ARG ECOSYNC_BASE_COMMIT
ARG ECOSYNC_DEPENDENCY_FINGERPRINT
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
ARG http_proxy
ARG https_proxy
ARG all_proxy
ARG NO_PROXY
ARG no_proxy
ARG JAVA_TOOL_OPTIONS
ARG ECOSYNC_GRADLE_DISTRIBUTION_URL=https://mirrors.cloud.tencent.com/gradle/gradle-9.4.1-bin.zip
ARG ECOSYNC_ANDROID_WARMUP_TASKS=""
ARG ECOSYNC_ANDROID_WARMUP_MODE=build

ENV GRADLE_USER_HOME=/root/.gradle \
    ANDROID_HOME=/opt/android-sdk-linux \
    ANDROID_SDK_ROOT=/opt/android-sdk-linux \
    JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64

RUN mkdir -p /opt/ecosync/gradle-init.d && cat > /opt/ecosync/gradle-init.d/mirrors.gradle <<'EOF'
beforeSettings { settings ->
    try {
        settings.pluginManagement.repositories.clear()
        settings.pluginManagement.repositories {
            maven { url = uri("https://mirrors.cloud.tencent.com/nexus/repository/maven-public/") }
            maven { url = uri("https://maven.aliyun.com/repository/gradle-plugin") }
            maven { url = uri("https://maven.aliyun.com/repository/google") }
            maven { url = uri("https://maven.aliyun.com/repository/public") }
            google()
            mavenCentral()
            gradlePluginPortal()
        }
    } catch (Throwable ignored) {
    }
}
settingsEvaluated { settings ->
    try {
        settings.dependencyResolutionManagement.repositories.clear()
        settings.dependencyResolutionManagement.repositories {
            exclusiveContent {
                forRepository {
                    maven { url = uri("https://jitpack.io") }
                }
                filter {
                    includeGroup("com.github.AppDevNext")
                    includeGroup("com.github.Dimezis")
                }
            }
            maven {
                url = uri("https://mirrors.cloud.tencent.com/nexus/repository/maven-public/")
                content {
                    excludeGroup("com.github.AppDevNext")
                    excludeGroup("com.github.Dimezis")
                }
            }
            maven {
                url = uri("https://maven.aliyun.com/repository/google")
                content {
                    excludeGroup("com.github.AppDevNext")
                    excludeGroup("com.github.Dimezis")
                }
            }
            maven {
                url = uri("https://maven.aliyun.com/repository/public")
                content {
                    excludeGroup("com.github.AppDevNext")
                    excludeGroup("com.github.Dimezis")
                }
            }
            google {
                content {
                    excludeGroup("com.github.AppDevNext")
                    excludeGroup("com.github.Dimezis")
                }
            }
            mavenCentral {
                content {
                    excludeGroup("com.github.AppDevNext")
                    excludeGroup("com.github.Dimezis")
                }
            }
        }
    } catch (Throwable ignored) {
    }
}
EOF

WORKDIR /src
COPY ${ECOSYNC_REPO_PATH}/ ./

RUN --mount=type=secret,id=github_token,required=false \
    --mount=type=cache,target=/root/.gradle-buildkit-cache \
    HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" ALL_PROXY="$ALL_PROXY" \
    http_proxy="$http_proxy" https_proxy="$https_proxy" all_proxy="$all_proxy" \
    NO_PROXY="$NO_PROXY" no_proxy="$no_proxy" \
    JAVA_TOOL_OPTIONS="$JAVA_TOOL_OPTIONS" \
    ECOSYNC_GRADLE_DISTRIBUTION_URL="$ECOSYNC_GRADLE_DISTRIBUTION_URL" \
    ECOSYNC_ANDROID_WARMUP_TASKS="$ECOSYNC_ANDROID_WARMUP_TASKS" \
    ECOSYNC_ANDROID_WARMUP_MODE="$ECOSYNC_ANDROID_WARMUP_MODE" \
    bash -lc 'set -euo pipefail; export GRADLE_USER_HOME=/root/.gradle-buildkit-cache; mkdir -p "$GRADLE_USER_HOME"; if [ -f /run/secrets/github_token ]; then export GITHUB_TOKEN="$(cat /run/secrets/github_token)"; fi; if [ -f .github/mock-google-services.json ]; then for module in app automotive wear; do if [ -d "$module" ]; then cp .github/mock-google-services.json "$module/google-services.json"; fi; done; fi; if [ -f .git ]; then rm -f .git; git init -q; git config user.email ecosyncbench@example.invalid; git config user.name WIDESWE; git add -A; git commit -qm ecosyncbench-warmup; git tag 0.0.0; fi; rm -f gradle/gradle-daemon-jvm.properties; sed -i "s#^distributionUrl=.*#distributionUrl=${ECOSYNC_GRADLE_DISTRIBUTION_URL//:/\\\\:}#; s#^networkTimeout=.*#networkTimeout=120000#" gradle/wrapper/gradle-wrapper.properties; if [ "$ECOSYNC_ANDROID_WARMUP_MODE" = "build" ] && [ -n "$ECOSYNC_ANDROID_WARMUP_TASKS" ]; then ./gradlew --init-script /opt/ecosync/gradle-init.d/mirrors.gradle $ECOSYNC_ANDROID_WARMUP_TASKS --no-daemon --no-configuration-cache --stacktrace --info --max-workers=2 -Dorg.gradle.daemon=false -Dorg.gradle.parallel=false -Dorg.gradle.vfs.watch=false -Dorg.gradle.internal.http.connectionTimeout=60000 -Dorg.gradle.internal.http.socketTimeout=60000 -Dorg.gradle.jvmargs="-Xmx2g -XX:MaxMetaspaceSize=768m -Dfile.encoding=UTF-8"; fi; rm -rf /root/.gradle; mkdir -p /root/.gradle; cp -a "$GRADLE_USER_HOME"/. /root/.gradle/'
RUN mkdir -p /opt/android-sdk-linux/ndk

FROM ${ECOSYNC_ANDROID_BASE_IMAGE}

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO=home-assistant/android
ARG ECOSYNC_BASE_COMMIT
ARG ECOSYNC_DEPENDENCY_FINGERPRINT
ARG ECOSYNC_ANDROID_WARMUP_TASKS=""
ARG ECOSYNC_ANDROID_WARMUP_MODE=build

LABEL org.ecosyncbench.layer=deps \
      org.ecosyncbench.ecosystem=home-assistant \
      org.ecosyncbench.repo=$ECOSYNC_REPO \
      org.ecosyncbench.base_commit=$ECOSYNC_BASE_COMMIT \
      org.ecosyncbench.dependency_fingerprint=$ECOSYNC_DEPENDENCY_FINGERPRINT \
      org.ecosyncbench.gradle_warmup_tasks="$ECOSYNC_ANDROID_WARMUP_TASKS" \
      org.ecosyncbench.task=$ECOSYNC_TASK_ID

ENV GRADLE_USER_HOME=/root/.gradle \
    ANDROID_HOME=/opt/android-sdk-linux \
    ANDROID_SDK_ROOT=/opt/android-sdk-linux \
    JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64

COPY --from=warm /opt/ecosync/gradle-init.d /opt/ecosync/gradle-init.d
COPY --from=warm /root/.gradle /root/.gradle
COPY --from=warm /opt/android-sdk-linux/ndk /opt/android-sdk-linux/ndk
RUN if [ "${ECOSYNC_ANDROID_WARMUP_MODE:-build}" = "build" ] && [ -n "${ECOSYNC_ANDROID_WARMUP_TASKS:-}" ]; then \
      test -d /root/.gradle/caches/modules-2/files-2.1 \
      && find /root/.gradle/caches/modules-2/files-2.1 -path '*org.ajoberstar.reckon.settings.gradle.plugin*' -print -quit | grep -q .; \
    fi
WORKDIR /workspace/repos/home-assistant/android
