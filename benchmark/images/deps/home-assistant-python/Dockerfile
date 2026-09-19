# syntax=docker/dockerfile:1.6
ARG ECOSYNC_PYTHON_BASE_IMAGE=ecosyncbench/base/python:3.14-slim
FROM ${ECOSYNC_PYTHON_BASE_IMAGE} AS warm

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO
ARG ECOSYNC_REPO_PATH
ARG ECOSYNC_BASE_COMMIT
ARG ECOSYNC_DEPENDENCY_FINGERPRINT
ARG ECOSYNC_REQUIREMENTS_FILES="requirements.txt requirements_test.txt"
ARG ECOSYNC_EXTRA_PIP_PACKAGES=""
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
ARG http_proxy
ARG https_proxy
ARG all_proxy
ARG NO_PROXY
ARG no_proxy

ENV VIRTUAL_ENV=/opt/ecosync/venv \
    PATH=/opt/ecosync/venv/bin:$PATH \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_PYTHON_VERSION_WARNING=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=8

WORKDIR /workspace

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc g++ \
    && rm -rf /var/lib/apt/lists/* \
    && python -m venv "$VIRTUAL_ENV" \
    && HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" ALL_PROXY="$ALL_PROXY" \
       http_proxy="$http_proxy" https_proxy="$https_proxy" all_proxy="$all_proxy" \
       NO_PROXY="$NO_PROXY" no_proxy="$no_proxy" \
       "$VIRTUAL_ENV/bin/python" -m pip install --upgrade pip setuptools wheel \
    && true

RUN --mount=type=bind,source=${ECOSYNC_REPO_PATH},target=/src,readonly \
    --mount=type=cache,target=/root/.cache/pip \
    HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" ALL_PROXY="$ALL_PROXY" \
    http_proxy="$http_proxy" https_proxy="$https_proxy" all_proxy="$all_proxy" \
    NO_PROXY="$NO_PROXY" no_proxy="$no_proxy" \
    bash -lc 'set -euo pipefail; cd /src; for requirement_file in $ECOSYNC_REQUIREMENTS_FILES; do "$VIRTUAL_ENV/bin/python" -m pip install -r "$requirement_file"; done'

RUN --mount=type=cache,target=/root/.cache/pip \
    HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" ALL_PROXY="$ALL_PROXY" \
    http_proxy="$http_proxy" https_proxy="$https_proxy" all_proxy="$all_proxy" \
    NO_PROXY="$NO_PROXY" no_proxy="$no_proxy" \
    bash -lc 'set -euo pipefail; if [[ -n "$ECOSYNC_EXTRA_PIP_PACKAGES" ]]; then "$VIRTUAL_ENV/bin/python" -m pip install $ECOSYNC_EXTRA_PIP_PACKAGES; fi'

FROM ${ECOSYNC_PYTHON_BASE_IMAGE}

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO
ARG ECOSYNC_BASE_COMMIT
ARG ECOSYNC_DEPENDENCY_FINGERPRINT

LABEL org.ecosyncbench.layer=deps \
      org.ecosyncbench.ecosystem=home-assistant \
      org.ecosyncbench.repo=$ECOSYNC_REPO \
      org.ecosyncbench.base_commit=$ECOSYNC_BASE_COMMIT \
      org.ecosyncbench.dependency_fingerprint=$ECOSYNC_DEPENDENCY_FINGERPRINT \
      org.ecosyncbench.task=$ECOSYNC_TASK_ID

ENV VIRTUAL_ENV=/opt/ecosync/venv \
    PATH=/opt/ecosync/venv/bin:$PATH \
    LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libstdc++.so.6 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends dbus-daemon git libpulse0 libturbojpeg0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=warm /opt/ecosync/venv /opt/ecosync/venv
WORKDIR /workspace
