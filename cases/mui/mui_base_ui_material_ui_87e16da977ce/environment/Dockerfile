ARG ECOSYNC_BASE_IMAGE=ecosyncbench/base/node:22-bookworm
FROM ${ECOSYNC_BASE_IMAGE}

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
ARG http_proxy
ARG https_proxy
ARG all_proxy
ARG NO_PROXY
ARG no_proxy

LABEL org.ecosyncbench.layer=deps \
      org.ecosyncbench.task_id=mui_base_ui_material_ui_87e16da977ce \
      org.ecosyncbench.runtime=node-pnpm

RUN HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" ALL_PROXY="$ALL_PROXY" \
    http_proxy="$http_proxy" https_proxy="$https_proxy" all_proxy="$all_proxy" \
    NO_PROXY="$NO_PROXY" no_proxy="$no_proxy" \
    apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      git \
      libasound2 \
      libatk-bridge2.0-0 \
      libatk1.0-0 \
      libatspi2.0-0 \
      libcairo2 \
      libcups2 \
      libdbus-1-3 \
      libdrm2 \
      libgbm1 \
      libglib2.0-0 \
      libnspr4 \
      libnss3 \
      libpango-1.0-0 \
      libx11-6 \
      libx11-xcb1 \
      libxcb1 \
      libxcomposite1 \
      libxdamage1 \
      libxext6 \
      libxfixes3 \
      libxkbcommon0 \
      libxrandr2 \
    && rm -rf /var/lib/apt/lists/* \
    && corepack enable

WORKDIR /workspace
