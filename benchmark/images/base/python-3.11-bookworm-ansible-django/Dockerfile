ARG ECOSYNC_PYTHON_BASE=ecosyncbench/base/python:3.11-bookworm-uv
FROM ${ECOSYNC_PYTHON_BASE}

LABEL org.ecosyncbench.layer=base \
      org.ecosyncbench.runtime=python \
      org.ecosyncbench.version=3.11-bookworm-ansible-django

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
ARG http_proxy
ARG https_proxy
ARG all_proxy
ARG NO_PROXY
ARG no_proxy

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libffi-dev libkrb5-dev libldap2-dev libsasl2-dev libssl-dev \
       gnupg libxml2-dev libxmlsec1-dev libxmlsec1-openssl pkg-config \
       postgresql-client \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
