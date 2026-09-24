FROM ecosyncbench/base/node:24-bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    git \
    python3 \
  && rm -rf /var/lib/apt/lists/*

RUN corepack enable && corepack prepare yarn@4.10.3 --activate

WORKDIR /workspace
