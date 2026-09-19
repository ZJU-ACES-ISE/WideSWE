ARG ECOSYNC_ANDROID_BASE_IMAGE=ecosyncbench/base/android-sdk:36
FROM ${ECOSYNC_ANDROID_BASE_IMAGE} AS warm

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO=bitwarden/android
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
ARG ECOSYNC_ANDROID_WARMUP_TEST=*PushManagerTest*

ENV GRADLE_USER_HOME=/root/.gradle \
    ANDROID_HOME=/opt/android-sdk-linux \
    ANDROID_SDK_ROOT=/opt/android-sdk-linux

WORKDIR /src
COPY repos/bitwarden/android/ ./

RUN --mount=type=secret,id=github_token,required=false \
    HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" ALL_PROXY="$ALL_PROXY" \
    http_proxy="$http_proxy" https_proxy="$https_proxy" all_proxy="$all_proxy" \
    NO_PROXY="$NO_PROXY" no_proxy="$no_proxy" \
    JAVA_TOOL_OPTIONS="$JAVA_TOOL_OPTIONS" \
    ECOSYNC_GRADLE_DISTRIBUTION_URL="$ECOSYNC_GRADLE_DISTRIBUTION_URL" \
    ECOSYNC_ANDROID_WARMUP_TEST="$ECOSYNC_ANDROID_WARMUP_TEST" \
    bash -lc 'if [ -f /run/secrets/github_token ]; then export GITHUB_TOKEN="$(cat /run/secrets/github_token)"; fi; sed -i "s#^distributionUrl=.*#distributionUrl=${ECOSYNC_GRADLE_DISTRIBUTION_URL//:/\\\\:}#; s#^networkTimeout=.*#networkTimeout=120000#" gradle/wrapper/gradle-wrapper.properties; ./gradlew app:testStandardDebugUnitTest --tests "$ECOSYNC_ANDROID_WARMUP_TEST" --no-daemon --stacktrace --max-workers=1'

FROM ${ECOSYNC_ANDROID_BASE_IMAGE}

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO=bitwarden/android
ARG ECOSYNC_BASE_COMMIT
ARG ECOSYNC_DEPENDENCY_FINGERPRINT

LABEL org.ecosyncbench.layer=deps \
      org.ecosyncbench.ecosystem=bitwarden \
      org.ecosyncbench.repo=$ECOSYNC_REPO \
      org.ecosyncbench.base_commit=$ECOSYNC_BASE_COMMIT \
      org.ecosyncbench.dependency_fingerprint=$ECOSYNC_DEPENDENCY_FINGERPRINT \
      org.ecosyncbench.task=$ECOSYNC_TASK_ID

ENV GRADLE_USER_HOME=/root/.gradle \
    ANDROID_HOME=/opt/android-sdk-linux \
    ANDROID_SDK_ROOT=/opt/android-sdk-linux

COPY --from=warm /root/.gradle /root/.gradle
WORKDIR /workspace/repos/bitwarden/android
