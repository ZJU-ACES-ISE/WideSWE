FROM ecosyncbench/base/node:22-bookworm
RUN corepack enable \
    && corepack prepare pnpm@11.1.1 --activate \
    && corepack prepare pnpm@11.3.0 --activate \
    && corepack prepare pnpm@11.9.0 --activate
WORKDIR /workspace
