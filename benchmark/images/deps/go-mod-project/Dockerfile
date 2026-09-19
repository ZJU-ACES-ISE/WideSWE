ARG ECOSYNC_BASE_IMAGE=ecosyncbench/base/go:1.26-bookworm
FROM ${ECOSYNC_BASE_IMAGE}

ARG ECOSYNC_TASK_ID
ARG ECOSYNC_REPO
ARG ECOSYNC_REPO_PATH
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

LABEL org.ecosyncbench.layer=deps \
      org.ecosyncbench.task_id="${ECOSYNC_TASK_ID}" \
      org.ecosyncbench.repo="${ECOSYNC_REPO}" \
      org.ecosyncbench.base_commit="${ECOSYNC_BASE_COMMIT}" \
      org.ecosyncbench.dependency_fingerprint="${ECOSYNC_DEPENDENCY_FINGERPRINT}"

WORKDIR /opt/ecosync/deps
COPY ${ECOSYNC_REPO_PATH}/go.mod ./go.mod
COPY ${ECOSYNC_REPO_PATH}/go.sum ./go.sum
ENV GOMODCACHE=/opt/ecosync/gomodcache \
    GOPATH=/opt/ecosync/gopath
RUN HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" ALL_PROXY="$ALL_PROXY" \
    http_proxy="$http_proxy" https_proxy="$https_proxy" all_proxy="$all_proxy" \
    NO_PROXY="$NO_PROXY" no_proxy="$no_proxy" \
    sh -c 'set -eu; \
      for attempt in 1 2 3 4 5; do \
        if /usr/local/go/bin/go mod download; then exit 0; fi; \
        echo "go mod download failed on attempt ${attempt}; retrying" >&2; \
        sleep $((attempt * 10)); \
      done; \
      /usr/local/go/bin/go mod download'
