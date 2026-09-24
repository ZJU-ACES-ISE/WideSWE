FROM ruby:3.4-bookworm

LABEL org.ecosyncbench.layer=base \
      org.ecosyncbench.runtime=ruby \
      org.ecosyncbench.version=3.4-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    imagemagick \
    libvips42 \
    libsqlite3-dev \
    libxml2-dev \
    libxslt1-dev \
    libyaml-dev \
    nodejs \
    npm \
    pkg-config \
    sqlite3 \
    tzdata \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
