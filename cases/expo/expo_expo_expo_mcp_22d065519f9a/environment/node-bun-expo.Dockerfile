FROM ecosyncbench/base/node:22-bookworm
RUN corepack enable \
    && corepack prepare yarn@1.22.22 --activate \
    && npm install -g bun@1.3.4
WORKDIR /workspace
