ARG ECOSYNC_NODE_BASE=ecosyncbench/base/node:24-bookworm
ARG ECOSYNC_NODE_VERSION=24.14.1
FROM ${ECOSYNC_NODE_BASE}
ARG ECOSYNC_NODE_VERSION

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      xz-utils \
      xvfb \
      libgtk2.0-0 \
      libgtk-3-0 \
      libnotify-dev \
      libnss3 \
      libxss1 \
      libasound2 \
      libxtst6 \
      libgbm1 \
      xauth \
      chromium \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) node_arch="linux-x64" ;; \
      arm64) node_arch="linux-arm64" ;; \
      *) echo "Unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    file="node-v${ECOSYNC_NODE_VERSION}-${node_arch}.tar.xz"; \
    curl -fsSLO "https://nodejs.org/dist/v${ECOSYNC_NODE_VERSION}/${file}"; \
    rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack; \
    tar -xJf "$file" -C /usr/local --strip-components=1; \
    rm "$file"; \
    corepack enable; \
    node --version
