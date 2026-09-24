ARG ECOSYNC_JAVA_BASE=eclipse-temurin:21-jdk
FROM ${ECOSYNC_JAVA_BASE}

RUN apt-get update \
  && apt-get install -y --no-install-recommends bash ca-certificates coreutils findutils git procps unzip \
  && rm -rf /var/lib/apt/lists/*

ENV GRADLE_USER_HOME=/gradle-cache \
    RUNTIME_JAVA_HOME=/opt/java/openjdk
