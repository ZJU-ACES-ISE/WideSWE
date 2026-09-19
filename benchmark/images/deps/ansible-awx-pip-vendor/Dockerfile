# syntax=docker/dockerfile:1.6
ARG ECOSYNC_BASE_IMAGE=ecosyncbench/base/python:3.11-bookworm-ansible-django
FROM ${ECOSYNC_BASE_IMAGE} AS warm

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO
ARG ECOSYNC_REPO_PATH
ARG ECOSYNC_BASE_COMMIT
ARG ECOSYNC_DEPENDENCY_FINGERPRINT
ARG ECOSYNC_EXTRA_PIP_PACKAGES=""

LABEL org.ecosyncbench.layer=deps \
      org.ecosyncbench.ecosystem=ansible \
      org.ecosyncbench.task=${ECOSYNC_TASK_ID} \
      org.ecosyncbench.repo=${ECOSYNC_REPO} \
      org.ecosyncbench.base_commit=${ECOSYNC_BASE_COMMIT} \
      org.ecosyncbench.dependency_fingerprint=${ECOSYNC_DEPENDENCY_FINGERPRINT}

RUN python -m venv /opt/ecosync/venv
WORKDIR /src

RUN --mount=type=bind,source=${ECOSYNC_REPO_PATH},target=/src,readonly \
    --mount=type=bind,source=.ecosyncbench/vendor/awx_plugins.interfaces,target=/vendor/awx_plugins.interfaces,readonly \
    --mount=type=bind,source=.ecosyncbench/vendor/awx-plugins,target=/vendor/awx-plugins,readonly \
    --mount=type=cache,target=/root/.cache/pip \
    HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" ALL_PROXY="$ALL_PROXY" \
    http_proxy="$http_proxy" https_proxy="$https_proxy" all_proxy="$all_proxy" \
    NO_PROXY="$NO_PROXY" no_proxy="$no_proxy" \
    bash -c 'set -euo pipefail; \
      export VIRTUAL_ENV=/opt/ecosync/venv; export PATH=/opt/ecosync/venv/bin:$PATH; hash -r; \
      python -m pip install --upgrade pip setuptools wheel; \
      grep -v "git+https://github.com/ansible/awx_plugins.interfaces.git" requirements/requirements.txt > /tmp/requirements.txt; \
      python -m pip install -r /tmp/requirements.txt; \
      python -m pip install /vendor/awx_plugins.interfaces; \
      python -m pip install "/vendor/awx-plugins[credentials-github-app]"; \
      python -m pip install -r requirements/requirements_dev.txt; \
      if [[ -n "$ECOSYNC_EXTRA_PIP_PACKAGES" ]]; then python -m pip install $ECOSYNC_EXTRA_PIP_PACKAGES; fi'

FROM ${ECOSYNC_BASE_IMAGE}
ENV VIRTUAL_ENV=/opt/ecosync/venv
ENV PATH=/opt/ecosync/venv/bin:$PATH
COPY --from=warm /opt/ecosync/venv /opt/ecosync/venv
WORKDIR /workspace
