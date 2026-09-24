# syntax=docker/dockerfile:1.6
FROM rust:1.91-bookworm

ARG DEBIAN_FRONTEND=noninteractive
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
ARG http_proxy
ARG https_proxy
ARG all_proxy
ARG NO_PROXY
ARG no_proxy

ENV CARGO_HOME=/usr/local/cargo \
    RUSTUP_HOME=/usr/local/rustup \
    PATH=/usr/local/cargo/bin:$PATH \
    RUST_BACKTRACE=1 \
    RUSTC_BOOTSTRAP=1

RUN HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" ALL_PROXY="$ALL_PROXY" \
    http_proxy="$http_proxy" https_proxy="$https_proxy" all_proxy="$all_proxy" \
    NO_PROXY="$NO_PROXY" no_proxy="$no_proxy" \
    apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates \
      git \
      pkg-config \
      python3 \
      python3-venv \
      xz-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
