ARG ECOSYNC_RUST_UPSTREAM_IMAGE=ecosyncbench/base/rust:1.89-bookworm
FROM ${ECOSYNC_RUST_UPSTREAM_IMAGE}

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
ARG http_proxy
ARG https_proxy
ARG all_proxy
ARG NO_PROXY
ARG no_proxy

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_PYTHON_VERSION_WARNING=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

RUN HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" ALL_PROXY="$ALL_PROXY" \
    http_proxy="$http_proxy" https_proxy="$https_proxy" all_proxy="$all_proxy" \
    NO_PROXY="$NO_PROXY" no_proxy="$no_proxy" \
    apt-get update \
    && apt-get install -y --no-install-recommends \
      build-essential \
      ca-certificates \
      cmake \
      curl \
      git \
      libssl-dev \
      pkg-config \
      protobuf-compiler \
      python3 \
      python3-pip \
      python3-venv \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --break-system-packages --no-cache-dir --upgrade pip uv \
    && ln -sf /usr/bin/python3 /usr/local/bin/python

WORKDIR /workspace
