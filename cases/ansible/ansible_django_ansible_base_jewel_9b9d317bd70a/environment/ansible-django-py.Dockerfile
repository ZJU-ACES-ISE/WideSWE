ARG ECOSYNC_BASE_IMAGE=ecosyncbench/base/python:3.12-uv
FROM ${ECOSYNC_BASE_IMAGE}

LABEL org.ecosyncbench.layer=base \
      org.ecosyncbench.runtime=python-django-postgres \
      org.ecosyncbench.ecosystem=ansible

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
ARG http_proxy
ARG https_proxy
ARG all_proxy
ARG NO_PROXY
ARG no_proxy

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_PYTHON_VERSION_WARNING=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        gcc \
        git \
        libffi-dev \
        libldap2-dev \
        libpq-dev \
        libsasl2-dev \
        libssl-dev \
        libxml2-dev \
        libxmlsec1-dev \
        libxmlsec1-openssl \
        pkg-config \
        postgresql \
        postgresql-contrib \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
