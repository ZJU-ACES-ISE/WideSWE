ARG ECOSYNC_BASE_IMAGE=ecosyncbench/base/node:22-bookworm
FROM ${ECOSYNC_BASE_IMAGE}

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO
ARG ECOSYNC_REPO_PATH
ARG ECOSYNC_BASE_COMMIT
ARG ECOSYNC_DEPENDENCY_FINGERPRINT
ARG ECOSYNC_INSTALL_FLAGS="--ignore-scripts"

LABEL org.ecosyncbench.layer="deps" \
      org.ecosyncbench.task_id="${ECOSYNC_TASK_ID}" \
      org.ecosyncbench.repo="${ECOSYNC_REPO}" \
      org.ecosyncbench.base_commit="${ECOSYNC_BASE_COMMIT}" \
      org.ecosyncbench.dependency_fingerprint="${ECOSYNC_DEPENDENCY_FINGERPRINT}"

WORKDIR /opt/ecosync/deps
COPY ${ECOSYNC_REPO_PATH}/package.json ./package.json
COPY ${ECOSYNC_REPO_PATH}/package-lock.json ./package-lock.json
RUN --mount=type=cache,target=/root/.npm npm ci ${ECOSYNC_INSTALL_FLAGS}
