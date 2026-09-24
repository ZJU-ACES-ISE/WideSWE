FROM rust:1.95-bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    cmake \
    git \
    libssl-dev \
    pkg-config \
    python3 \
  && rm -rf /var/lib/apt/lists/*
