ARG ECOSYNC_PYTHON_BASE=ecosyncbench/base/python:3.12-uv
FROM ${ECOSYNC_PYTHON_BASE}

LABEL org.ecosyncbench.layer=base \
      org.ecosyncbench.ecosystem=ansible \
      org.ecosyncbench.runtime=python-django-postgres \
      org.ecosyncbench.version=3.12-uv

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
       build-essential curl gcc git libffi-dev libldap2-dev libpq-dev \
       libsasl2-dev libssl-dev libxml2-dev libxmlsec1-dev \
       libxmlsec1-openssl pkg-config postgresql postgresql-contrib \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
