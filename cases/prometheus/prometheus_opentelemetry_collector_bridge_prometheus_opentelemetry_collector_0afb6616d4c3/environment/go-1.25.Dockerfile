FROM golang:1.25-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates git make \
    && rm -rf /var/lib/apt/lists/*

ENV GOPATH=/go \
    GOMODCACHE=/go/pkg/mod \
    GOCACHE=/tmp/ecosync-go-build \
    PATH=/usr/local/go/bin:/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
