FROM ecosyncbench/base/rails-ruby:3.4-bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    git \
    libffi-dev \
    pkg-config \
  && rm -rf /var/lib/apt/lists/*
