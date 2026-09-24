FROM ecosyncbench/base/rails-ruby:3.4-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    libsqlite3-dev \
    libyaml-dev \
    nodejs \
    pkg-config \
  && rm -rf /var/lib/apt/lists/*
