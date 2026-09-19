ARG ECOSYNC_GO_UPSTREAM_IMAGE=golang:1.25-bookworm
FROM ${ECOSYNC_GO_UPSTREAM_IMAGE}

LABEL org.ecosyncbench.layer=base \
      org.ecosyncbench.runtime=go \
      org.ecosyncbench.version=1.25-bookworm

ENV CGO_ENABLED=1

WORKDIR /workspace
