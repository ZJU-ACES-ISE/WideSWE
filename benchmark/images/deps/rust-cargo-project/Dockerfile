# syntax=docker/dockerfile:1.6
ARG ECOSYNC_BASE_IMAGE=ecosyncbench/base/rustfmt-nightly:2025-01-02-bookworm
FROM ${ECOSYNC_BASE_IMAGE} AS warm

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO
ARG ECOSYNC_REPO_PATH
ARG ECOSYNC_PROJECT_DIR="."
ARG ECOSYNC_BASE_COMMIT
ARG ECOSYNC_DEPENDENCY_FINGERPRINT
ARG ECOSYNC_RUSTUP_TOOLCHAIN
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
ARG http_proxy
ARG https_proxy
ARG all_proxy
ARG NO_PROXY
ARG no_proxy

ENV CARGO_HOME=/opt/ecosync/cargo \
    RUSTUP_HOME=/usr/local/rustup \
    PATH=/usr/local/cargo/bin:$PATH \
    RUSTC_BOOTSTRAP=1

WORKDIR /src

RUN --mount=type=bind,source=${ECOSYNC_REPO_PATH},target=/src,readonly \
    HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" ALL_PROXY="$ALL_PROXY" \
    http_proxy="$http_proxy" https_proxy="$https_proxy" all_proxy="$all_proxy" \
    NO_PROXY="$NO_PROXY" no_proxy="$no_proxy" \
    bash -lc 'set -euo pipefail; if [[ -n "${ECOSYNC_RUSTUP_TOOLCHAIN:-}" ]]; then export RUSTUP_TOOLCHAIN="$ECOSYNC_RUSTUP_TOOLCHAIN"; fi; mkdir -p "$CARGO_HOME"; rm -rf /tmp/ecosync-rust-project; cp -a "$ECOSYNC_PROJECT_DIR" /tmp/ecosync-rust-project; cd /tmp/ecosync-rust-project; cargo fetch --locked'

FROM ${ECOSYNC_BASE_IMAGE}

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO
ARG ECOSYNC_BASE_COMMIT
ARG ECOSYNC_DEPENDENCY_FINGERPRINT

ENV CARGO_HOME=/opt/ecosync/cargo \
    RUSTUP_HOME=/usr/local/rustup \
    PATH=/usr/local/cargo/bin:$PATH \
    RUSTC_BOOTSTRAP=1 \
    RUST_BACKTRACE=1

LABEL org.ecosyncbench.layer=deps \
      org.ecosyncbench.runtime=rust \
      org.ecosyncbench.task_id="${ECOSYNC_TASK_ID}" \
      org.ecosyncbench.repo="${ECOSYNC_REPO}" \
      org.ecosyncbench.base_commit="${ECOSYNC_BASE_COMMIT}" \
      org.ecosyncbench.dependency_fingerprint="${ECOSYNC_DEPENDENCY_FINGERPRINT}" \
      org.ecosyncbench.version=nightly-2025-01-02-bookworm

COPY --from=warm /opt/ecosync/cargo /opt/ecosync/cargo
WORKDIR /workspace
