ARG ECOSYNC_NODE_BASE=ecosyncbench/base/node:22-bookworm
ARG ECOSYNC_NODE_VERSION=22.16.0
FROM ${ECOSYNC_NODE_BASE}
ARG ECOSYNC_NODE_VERSION

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      chromium \
      libasound2 \
      libgbm1 \
      libgtk-3-0 \
      libgtk2.0-0 \
      libnotify-dev \
      libnss3 \
      libxss1 \
      libxtst6 \
      xauth \
      xvfb \
      xz-utils \
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
