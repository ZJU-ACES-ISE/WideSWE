ARG ECOSYNC_PYTHON_UPSTREAM_IMAGE=python:3.11-slim
FROM ${ECOSYNC_PYTHON_UPSTREAM_IMAGE}

LABEL org.ecosyncbench.layer=base \
      org.ecosyncbench.runtime=python \
      org.ecosyncbench.version=3.11-uv

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
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc g++ git curl libpq-dev pkg-config \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --upgrade pip setuptools wheel uv

WORKDIR /workspace
