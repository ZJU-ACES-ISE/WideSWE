ARG ECOSYNC_NODE_BASE=ecosyncbench/base/node:22-bookworm
FROM ${ECOSYNC_NODE_BASE}

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      git \
      xz-utils \
      python3 \
      make \
      g++ \
      chromium \
      libgtk2.0-0 \
      libgtk-3-0 \
      libnotify-dev \
      libnss3 \
      libxss1 \
      libasound2 \
      libxtst6 \
      libgbm1 \
      xauth \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) node_arch="linux-x64" ;; \
      arm64) node_arch="linux-arm64" ;; \
      *) echo "Unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    file="node-v16.19.1-${node_arch}.tar.xz"; \
    curl -fsSLO "https://nodejs.org/dist/v16.19.1/${file}"; \
    rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack; \
    tar -xJf "$file" -C /usr/local --strip-components=1; \
    rm "$file"; \
    corepack enable; \
    node --version; \
    npm --version; \
    yarn --version || true
