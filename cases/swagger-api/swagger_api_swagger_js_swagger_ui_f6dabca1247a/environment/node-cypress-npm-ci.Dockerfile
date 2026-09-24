FROM ecosyncbench/base/node:22-bookworm

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO
ARG ECOSYNC_REPO_PATH
ARG ECOSYNC_BASE_COMMIT
ARG ECOSYNC_DEPENDENCY_FINGERPRINT

LABEL org.ecosyncbench.layer="deps" \
      org.ecosyncbench.task_id="${ECOSYNC_TASK_ID}" \
      org.ecosyncbench.repo="${ECOSYNC_REPO}" \
      org.ecosyncbench.base_commit="${ECOSYNC_BASE_COMMIT}" \
      org.ecosyncbench.dependency_fingerprint="${ECOSYNC_DEPENDENCY_FINGERPRINT}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      libasound2 \
      libatk-bridge2.0-0 \
      libatk1.0-0 \
      libcups2 \
      libdrm2 \
      libgbm1 \
      libgtk-3-0 \
      libnss3 \
      libxcomposite1 \
      libxdamage1 \
      libxfixes3 \
      libxkbcommon0 \
      libxrandr2 \
      xvfb \
    && rm -rf /var/lib/apt/lists/*

ENV CYPRESS_CACHE_FOLDER=/opt/ecosync/cypress-cache

WORKDIR /opt/ecosync/deps
COPY ${ECOSYNC_REPO_PATH}/package.json ./package.json
COPY ${ECOSYNC_REPO_PATH}/package-lock.json ./package-lock.json
RUN npm ci \
    && npx cypress verify
