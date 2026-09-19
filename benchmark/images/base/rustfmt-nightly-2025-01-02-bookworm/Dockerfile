# syntax=docker/dockerfile:1.6
FROM rust:1.85-bookworm

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
ARG http_proxy
ARG https_proxy
ARG all_proxy
ARG NO_PROXY
ARG no_proxy

ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH \
    RUST_BACKTRACE=1

RUN HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" ALL_PROXY="$ALL_PROXY" \
    http_proxy="$http_proxy" https_proxy="$https_proxy" all_proxy="$all_proxy" \
    NO_PROXY="$NO_PROXY" no_proxy="$no_proxy" \
    apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates \
      cmake \
      git \
      libssl-dev \
      ninja-build \
      pkg-config \
      python3 \
      xz-utils \
    && rm -rf /var/lib/apt/lists/*

RUN HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" ALL_PROXY="$ALL_PROXY" \
    http_proxy="$http_proxy" https_proxy="$https_proxy" all_proxy="$all_proxy" \
    NO_PROXY="$NO_PROXY" no_proxy="$no_proxy" \
    rustup toolchain install nightly-2024-09-10 \
      --profile minimal \
      --component rustc-dev \
      --component llvm-tools \
    && \
    rustup toolchain install nightly-2025-01-02 \
      --profile minimal \
      --component rustc-dev \
      --component llvm-tools \
    && rustup default nightly-2025-01-02 \
    && for tool in cargo rustc rustdoc rustfmt rustup; do \
         ln -sf "/usr/local/cargo/bin/${tool}" "/usr/local/bin/${tool}"; \
       done

LABEL org.ecosyncbench.layer=base \
      org.ecosyncbench.runtime=rust \
      org.ecosyncbench.version=nightly-2024-09-10-nightly-2025-01-02-bookworm

WORKDIR /workspace
