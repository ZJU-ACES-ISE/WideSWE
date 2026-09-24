FROM ecosyncbench/base/java-maven:17-bookworm

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
ARG http_proxy
ARG https_proxy
ARG all_proxy
ARG NO_PROXY
ARG no_proxy

RUN HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" ALL_PROXY="$ALL_PROXY" \
    http_proxy="$http_proxy" https_proxy="$https_proxy" all_proxy="$all_proxy" \
    NO_PROXY="$NO_PROXY" no_proxy="$no_proxy" \
    apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      build-essential \
      ca-certificates \
      curl \
      git \
      libc6-dev \
      make \
      pkg-config \
      procps \
      python3 \
      tcl \
      tcl-dev \
    && rm -rf /var/lib/apt/lists/*

ENV MAVEN_OPTS="-Dmaven.repo.local=/cache/m2 -Xmx2g"
WORKDIR /workspace
