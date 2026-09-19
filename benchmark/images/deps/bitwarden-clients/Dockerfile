ARG ECOSYNC_NODE_BASE_IMAGE=ecosyncbench/base/node:22-bookworm-slim
FROM ${ECOSYNC_NODE_BASE_IMAGE} AS install

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO=bitwarden/clients
ARG ECOSYNC_BASE_COMMIT
ARG ECOSYNC_DEPENDENCY_FINGERPRINT
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
ARG http_proxy
ARG https_proxy
ARG all_proxy
ARG NO_PROXY
ARG no_proxy

ENV npm_config_audit=false \
    npm_config_fund=false

WORKDIR /src
COPY repos/bitwarden/clients/package.json repos/bitwarden/clients/package-lock.json ./
COPY repos/bitwarden/clients/apps ./apps
COPY repos/bitwarden/clients/libs ./libs
COPY repos/bitwarden/clients/bitwarden_license ./bitwarden_license
COPY repos/bitwarden/clients/scripts ./scripts
COPY repos/bitwarden/clients/*.js repos/bitwarden/clients/*.json repos/bitwarden/clients/*.ts ./

RUN --mount=type=cache,id=ecosyncbench-npm-bitwarden-clients,target=/root/.npm \
    HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" ALL_PROXY="$ALL_PROXY" \
    http_proxy="$http_proxy" https_proxy="$https_proxy" all_proxy="$all_proxy" \
    NO_PROXY="$NO_PROXY" no_proxy="$no_proxy" \
    npm ci --ignore-scripts --cache /root/.npm

FROM ${ECOSYNC_NODE_BASE_IMAGE}

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO=bitwarden/clients
ARG ECOSYNC_BASE_COMMIT
ARG ECOSYNC_DEPENDENCY_FINGERPRINT

LABEL org.ecosyncbench.layer=deps \
      org.ecosyncbench.ecosystem=bitwarden \
      org.ecosyncbench.repo=$ECOSYNC_REPO \
      org.ecosyncbench.base_commit=$ECOSYNC_BASE_COMMIT \
      org.ecosyncbench.dependency_fingerprint=$ECOSYNC_DEPENDENCY_FINGERPRINT \
      org.ecosyncbench.task=$ECOSYNC_TASK_ID

ENV npm_config_audit=false \
    npm_config_fund=false

COPY --from=install /src/node_modules /opt/ecosync/node_modules
WORKDIR /workspace/repos/bitwarden/clients
