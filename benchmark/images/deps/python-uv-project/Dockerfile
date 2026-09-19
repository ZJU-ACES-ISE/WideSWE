# syntax=docker/dockerfile:1.6
ARG ECOSYNC_BASE_IMAGE=ecosyncbench/base/python:3.12-uv
FROM ${ECOSYNC_BASE_IMAGE} AS warm

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO
ARG ECOSYNC_REPO_PATH
ARG ECOSYNC_BASE_COMMIT
ARG ECOSYNC_DEPENDENCY_FINGERPRINT
ARG ECOSYNC_UV_PROJECT_DIR="."
ARG ECOSYNC_UV_SYNC_FLAGS="--frozen --group dev --no-default-groups --no-install-project"
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
    UV_PROJECT_ENVIRONMENT=/opt/ecosync/venv \
    UV_LINK_MODE=copy

WORKDIR /src

RUN --mount=type=bind,source=${ECOSYNC_REPO_PATH},target=/src,readonly \
    --mount=type=cache,target=/root/.cache/uv \
    HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" ALL_PROXY="$ALL_PROXY" \
    http_proxy="$http_proxy" https_proxy="$https_proxy" all_proxy="$all_proxy" \
    NO_PROXY="$NO_PROXY" no_proxy="$no_proxy" \
    bash -lc 'set -euo pipefail; cd "$ECOSYNC_UV_PROJECT_DIR"; uv sync $ECOSYNC_UV_SYNC_FLAGS'

FROM ${ECOSYNC_BASE_IMAGE}

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO
ARG ECOSYNC_BASE_COMMIT
ARG ECOSYNC_DEPENDENCY_FINGERPRINT

LABEL org.ecosyncbench.layer=deps \
      org.ecosyncbench.task_id="${ECOSYNC_TASK_ID}" \
      org.ecosyncbench.repo="${ECOSYNC_REPO}" \
      org.ecosyncbench.base_commit="${ECOSYNC_BASE_COMMIT}" \
      org.ecosyncbench.dependency_fingerprint="${ECOSYNC_DEPENDENCY_FINGERPRINT}"

ENV VIRTUAL_ENV=/opt/ecosync/venv \
    PATH=/opt/ecosync/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=warm /opt/ecosync/venv /opt/ecosync/venv
WORKDIR /workspace
