ARG ECOSYNC_BASE_IMAGE=ecosyncbench/base/node:22-bookworm
FROM ${ECOSYNC_BASE_IMAGE}

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO
ARG ECOSYNC_REPO_PATH
ARG ECOSYNC_BASE_COMMIT
ARG ECOSYNC_DEPENDENCY_FINGERPRINT
ARG ECOSYNC_PNPM_VERSION

LABEL org.ecosyncbench.layer="deps" \
      org.ecosyncbench.task_id="${ECOSYNC_TASK_ID}" \
      org.ecosyncbench.repo="${ECOSYNC_REPO}" \
      org.ecosyncbench.base_commit="${ECOSYNC_BASE_COMMIT}" \
      org.ecosyncbench.dependency_fingerprint="${ECOSYNC_DEPENDENCY_FINGERPRINT}"

ENV PNPM_HOME=/opt/ecosync/pnpm \
    PNPM_STORE_DIR=/opt/ecosync/pnpm-store \
    PATH=/opt/ecosync/pnpm:$PATH

WORKDIR /opt/ecosync/repo
COPY ${ECOSYNC_REPO_PATH}/ ./
RUN corepack enable \
    && corepack prepare "pnpm@${ECOSYNC_PNPM_VERSION}" --activate \
    && pnpm config set store-dir "${PNPM_STORE_DIR}" \
    && pnpm fetch --prod=false
