# syntax=docker/dockerfile:1.6
ARG ECOSYNC_BASE_IMAGE=ecosyncbench/base/python:3.11-bookworm-ansible-django
FROM ${ECOSYNC_BASE_IMAGE} AS warm

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO
ARG ECOSYNC_REPO_PATH
ARG ECOSYNC_BASE_COMMIT
ARG ECOSYNC_DEPENDENCY_FINGERPRINT
ARG ECOSYNC_POETRY_VERSION=2.1.4
ARG ECOSYNC_POETRY_INSTALL_FLAGS="--with test --extras all --no-root"
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
    POETRY_CACHE_DIR=/root/.cache/pypoetry

RUN python -m venv /opt/ecosync/venv

RUN --mount=type=bind,source=${ECOSYNC_REPO_PATH},target=/src,readonly \
    --mount=type=bind,source=repos/ansible/django-ansible-base,target=/deps/django-ansible-base,readonly \
    --mount=type=cache,target=/root/.cache/pip \
    --mount=type=cache,target=/root/.cache/pypoetry \
    HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" ALL_PROXY="$ALL_PROXY" \
    http_proxy="$http_proxy" https_proxy="$https_proxy" all_proxy="$all_proxy" \
    NO_PROXY="$NO_PROXY" no_proxy="$no_proxy" \
    bash -c 'set -euo pipefail; export VIRTUAL_ENV=/opt/ecosync/venv; export PATH=/opt/ecosync/venv/bin:$PATH; hash -r; mkdir -p /tmp/src /tmp/django-ansible-base; cp -a /src/. /tmp/src/; rm -f /tmp/src/.git; cp -a /deps/django-ansible-base/. /tmp/django-ansible-base/; rm -f /tmp/django-ansible-base/.git; git -C /tmp/django-ansible-base init; git -C /tmp/django-ansible-base config user.email ecosyncbench@example.invalid; git -C /tmp/django-ansible-base config user.name EcosyncBench; git -C /tmp/django-ansible-base add .; git -C /tmp/django-ansible-base commit -m ecosyncbench-local-dab; git -C /tmp/django-ansible-base tag 2025.10.20; dab_ref="$(git -C /tmp/django-ansible-base rev-parse HEAD)"; old_ref="$(grep -m1 -A50 "name = \"django-ansible-base\"" /tmp/src/poetry.lock | grep -m1 "resolved_reference" | cut -d "\"" -f2)"; sed -i "0,/${old_ref}/s//${dab_ref}/" /tmp/src/poetry.lock; cd /tmp/src; python -m pip install --upgrade pip setuptools wheel; python -m pip install "poetry==${ECOSYNC_POETRY_VERSION}"; poetry config virtualenvs.create false; poetry config system-git-client true; git config --global url."file:///tmp/django-ansible-base".insteadOf "https://github.com/ansible/django-ansible-base.git"; SETUPTOOLS_SCM_PRETEND_VERSION_FOR_DJANGO_ANSIBLE_BASE=2025.11.24 poetry install $ECOSYNC_POETRY_INSTALL_FLAGS; if [[ -n "$ECOSYNC_EXTRA_PIP_PACKAGES" ]]; then python -m pip install $ECOSYNC_EXTRA_PIP_PACKAGES; fi'

FROM ${ECOSYNC_BASE_IMAGE}

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO
ARG ECOSYNC_BASE_COMMIT
ARG ECOSYNC_DEPENDENCY_FINGERPRINT

LABEL org.ecosyncbench.layer=deps \
      org.ecosyncbench.package_manager=poetry \
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
