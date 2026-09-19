ARG ECOSYNC_ANDROID_JAVA_BASE=ecosyncbench/base/elastic-java:21
FROM ${ECOSYNC_ANDROID_JAVA_BASE}

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
ARG http_proxy
ARG https_proxy
ARG all_proxy
ARG NO_PROXY
ARG no_proxy

LABEL org.ecosyncbench.layer=base \
      org.ecosyncbench.runtime=android-sdk \
      org.ecosyncbench.version=36 \
      org.ecosyncbench.android.platforms="31,33,36" \
      org.ecosyncbench.android.build_tools="34.0.0,35.0.0,36.0.0"

ENV GRADLE_USER_HOME=/root/.gradle \
    ANDROID_HOME=/opt/android-sdk-linux \
    ANDROID_SDK_ROOT=/opt/android-sdk-linux \
    PATH=/opt/android-sdk-linux/platform-tools:/opt/android-sdk-linux/cmdline-tools/latest/bin:$PATH

RUN set -eux; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openjdk-17-jdk-headless; \
    rm -rf /var/lib/apt/lists/*

ARG ECOSYNC_ANDROID_CMDLINE_TOOLS_URL=https://dl.google.com/android/repository/commandlinetools-linux-13114758_latest.zip
ARG ECOSYNC_ANDROID_PLATFORM_31_URL=https://dl.google.com/android/repository/platform-31_r01.zip
ARG ECOSYNC_ANDROID_PLATFORM_33_URL=https://dl.google.com/android/repository/platform-33-ext3_r03.zip
ARG ECOSYNC_ANDROID_PLATFORM_36_URL=https://dl.google.com/android/repository/platform-36_r02.zip
ARG ECOSYNC_ANDROID_BUILD_TOOLS_34_URL=https://dl.google.com/android/repository/build-tools_r34-linux.zip

RUN set -eux; \
    mkdir -p "${ANDROID_HOME}/cmdline-tools"; \
    curl -fsSL "${ECOSYNC_ANDROID_CMDLINE_TOOLS_URL}" -o /tmp/android-commandlinetools.zip; \
    unzip -q /tmp/android-commandlinetools.zip -d /tmp/android-commandlinetools; \
    mv /tmp/android-commandlinetools/cmdline-tools "${ANDROID_HOME}/cmdline-tools/latest"; \
    rm -rf /tmp/android-commandlinetools /tmp/android-commandlinetools.zip

RUN HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" ALL_PROXY="$ALL_PROXY" \
    http_proxy="$http_proxy" https_proxy="$https_proxy" all_proxy="$all_proxy" \
    NO_PROXY="$NO_PROXY" no_proxy="$no_proxy" \
    yes | "${ANDROID_HOME}/cmdline-tools/latest/bin/sdkmanager" --licenses >/dev/null || true; \
    "${ANDROID_HOME}/cmdline-tools/latest/bin/sdkmanager" \
      "platform-tools" \
      "build-tools;35.0.0" \
      "build-tools;36.0.0"

RUN set -eux; \
    mkdir -p "${ANDROID_HOME}/platforms" "${ANDROID_HOME}/build-tools"; \
    curl -fsSL "${ECOSYNC_ANDROID_PLATFORM_31_URL}" -o /tmp/platform-31.zip; \
    unzip -q /tmp/platform-31.zip -d "${ANDROID_HOME}/platforms"; \
    mv "${ANDROID_HOME}/platforms/android-12" "${ANDROID_HOME}/platforms/android-31"; \
    rm -f /tmp/platform-31.zip; \
    curl -fsSL "${ECOSYNC_ANDROID_PLATFORM_33_URL}" -o /tmp/platform-33.zip; \
    unzip -q /tmp/platform-33.zip -d "${ANDROID_HOME}/platforms"; \
    mv "${ANDROID_HOME}/platforms/android-13" "${ANDROID_HOME}/platforms/android-33"; \
    rm -f /tmp/platform-33.zip; \
    curl -fsSL "${ECOSYNC_ANDROID_PLATFORM_36_URL}" -o /tmp/platform-36.zip; \
    unzip -q /tmp/platform-36.zip -d "${ANDROID_HOME}/platforms"; \
    rm -f /tmp/platform-36.zip; \
    curl -fsSL "${ECOSYNC_ANDROID_BUILD_TOOLS_34_URL}" -o /tmp/build-tools-34.zip; \
    unzip -q /tmp/build-tools-34.zip -d "${ANDROID_HOME}/build-tools"; \
    mv "${ANDROID_HOME}/build-tools/android-14" "${ANDROID_HOME}/build-tools/34.0.0"; \
    rm -f /tmp/build-tools-34.zip; \
    chmod -R a+rX "${ANDROID_HOME}"

WORKDIR /workspace
