FROM node:24-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        clang \
        ccache \
        g++ \
        git \
        make \
        ninja-build \
        python3 \
    && rm -rf /var/lib/apt/lists/*

ENV npm_config_audit=false \
    npm_config_fund=false
