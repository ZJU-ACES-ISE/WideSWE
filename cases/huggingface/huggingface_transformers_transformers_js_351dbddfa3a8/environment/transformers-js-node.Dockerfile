FROM ecosyncbench/base/node:24-bookworm

RUN corepack enable && corepack prepare pnpm@10.28.1 --activate
