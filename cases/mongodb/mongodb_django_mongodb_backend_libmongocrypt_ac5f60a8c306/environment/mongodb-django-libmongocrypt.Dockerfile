FROM ecosyncbench/base/python:3.13-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    cmake \
    curl \
    g++ \
    gcc \
    git \
    gnupg \
    libmemcached-dev \
    libssl-dev \
    make \
    ninja-build \
    pkg-config \
    procps \
    wget \
    zlib1g-dev \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
