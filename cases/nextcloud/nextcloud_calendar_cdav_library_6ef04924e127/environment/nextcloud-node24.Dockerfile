FROM ecosyncbench/base/node:22-bookworm

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends xz-utils \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) node_arch="linux-x64" ;; \
      arm64) node_arch="linux-arm64" ;; \
      *) echo "Unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    file="$(curl -fsSL https://nodejs.org/dist/latest-v24.x/SHASUMS256.txt | awk -v a="$node_arch" '$2 ~ a "\\.tar\\.xz$" { print $2; exit }')"; \
    test -n "$file"; \
    curl -fsSLO "https://nodejs.org/dist/latest-v24.x/$file"; \
    rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack; \
    tar -xJf "$file" -C /usr/local --strip-components=1; \
    rm "$file"; \
    node --version; \
    npm --version
