ARG ECOSYNC_PYTHON_UPSTREAM_IMAGE=python:3.13-slim
FROM ${ECOSYNC_PYTHON_UPSTREAM_IMAGE}

LABEL org.ecosyncbench.layer=base \
      org.ecosyncbench.runtime=python \
      org.ecosyncbench.version=3.13-slim

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
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace
