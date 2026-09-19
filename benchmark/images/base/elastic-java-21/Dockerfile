ARG ECOSYNC_ELASTIC_JAVA_BASE=eclipse-temurin:21-jdk
FROM ${ECOSYNC_ELASTIC_JAVA_BASE}

LABEL org.ecosyncbench.layer=base \
      org.ecosyncbench.runtime=elastic-java \
      org.ecosyncbench.version=21

ENV GRADLE_USER_HOME=/gradle-cache \
    RUNTIME_JAVA_HOME=/opt/java/openjdk

RUN set -eux; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      bash \
      ca-certificates \
      coreutils \
      findutils \
      git \
      procps \
      unzip; \
    rm -rf /var/lib/apt/lists/*; \
    mkdir -p "$GRADLE_USER_HOME"; \
    chmod 0777 "$GRADLE_USER_HOME"; \
    java --version; \
    javac --version; \
    git --version

WORKDIR /workspace
