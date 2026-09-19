ARG ECOSYNC_NODE_UPSTREAM_IMAGE=m.daocloud.io/docker.io/library/node:24-bookworm-slim
FROM ${ECOSYNC_NODE_UPSTREAM_IMAGE}

LABEL org.ecosyncbench.layer=base \
      org.ecosyncbench.runtime=node \
      org.ecosyncbench.version=24-bookworm

ENV npm_config_audit=false \
    npm_config_fund=false \
    BUN_INSTALL=/opt/bun \
    PATH=/opt/bun/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git python3 make g++ \
    && corepack enable \
    && corepack prepare yarn@1.22.22 --activate \
    && corepack prepare pnpm@7.33.7 --activate \
    && npm install -g bun@1.1.45 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
