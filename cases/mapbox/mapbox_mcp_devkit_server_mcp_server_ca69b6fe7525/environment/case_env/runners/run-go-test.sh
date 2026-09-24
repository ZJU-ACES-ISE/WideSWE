#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
shift
mkdir -p /workspace/.ecosyncbench/test-reports
deps_root="${ECOSYNC_DEPS_ROOT:-/opt/ecosync}"
export GOMODCACHE="${GOMODCACHE:-$deps_root/gomodcache}"
export GOPATH="${GOPATH:-$deps_root/gopath}"
export GOCACHE="${GOCACHE:-/tmp/ecosync-gocache}"
export PATH="/usr/local/go/bin:$PATH"
go test -count=1 -json "$@" | tee "/workspace/.ecosyncbench/test-reports/${profile}.json"
