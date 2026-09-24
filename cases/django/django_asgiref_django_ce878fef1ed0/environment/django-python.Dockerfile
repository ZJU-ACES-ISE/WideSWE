# syntax=docker/dockerfile:1.6
FROM ecosyncbench/base/python:3.13-slim

ARG DEBIAN_FRONTEND=noninteractive
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
ARG http_proxy
ARG https_proxy
ARG all_proxy
ARG NO_PROXY
ARG no_proxy

RUN HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" ALL_PROXY="$ALL_PROXY" \
    http_proxy="$http_proxy" https_proxy="$https_proxy" all_proxy="$all_proxy" \
    NO_PROXY="$NO_PROXY" no_proxy="$no_proxy" \
    apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates \
      gcc \
      git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
