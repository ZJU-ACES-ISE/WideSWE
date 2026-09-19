# syntax=docker/dockerfile:1.6
ARG ECOSYNC_BASE_IMAGE=ecosyncbench/base/node:22-bookworm
FROM ${ECOSYNC_BASE_IMAGE} AS warm

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO
ARG ECOSYNC_REPO_PATH
ARG ECOSYNC_BASE_COMMIT
ARG ECOSYNC_DEPENDENCY_FINGERPRINT
ARG ECOSYNC_INSTALL_FLAGS="--ignore-scripts"
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
ARG http_proxy
ARG https_proxy
ARG all_proxy
ARG NO_PROXY
ARG no_proxy

WORKDIR /tmp/ecosync-project
RUN --mount=type=bind,source=${ECOSYNC_REPO_PATH},target=/src,readonly \
    --mount=type=cache,target=/root/.npm \
    HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" ALL_PROXY="$ALL_PROXY" \
    http_proxy="$http_proxy" https_proxy="$https_proxy" all_proxy="$all_proxy" \
    NO_PROXY="$NO_PROXY" no_proxy="$no_proxy" \
    bash -c 'set -euo pipefail; cp -a /src/. /tmp/ecosync-project/; npm ci ${ECOSYNC_INSTALL_FLAGS}'

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

WORKDIR /opt/ecosync/deps
COPY --from=warm /tmp/ecosync-project/node_modules ./node_modules
