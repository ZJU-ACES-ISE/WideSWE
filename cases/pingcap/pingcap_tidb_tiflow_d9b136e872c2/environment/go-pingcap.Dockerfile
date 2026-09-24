FROM golang:1.25.10-bookworm
ENV DEBIAN_FRONTEND=noninteractive \
    GOFLAGS=-mod=mod \
    CGO_ENABLED=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      bash ca-certificates curl git build-essential make pkg-config \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
