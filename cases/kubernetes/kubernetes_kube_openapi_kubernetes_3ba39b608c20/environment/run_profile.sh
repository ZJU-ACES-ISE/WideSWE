#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
repo_root="/opt/ecosyncbench"
image="ecosyncbench/base/go:1.26-bookworm"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/go-cache/kubernetes-openapi-model-names-3ba39b608c20}"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$repo_root/benchmark/images/base/go-1.26-bookworm/Dockerfile" "$repo_root"
  fi
}

run_go() {
  local workdir="$1"
  local report="$2"
  local setup="${3:-}"
  shift 3
  ensure_image
  mkdir -p "$cache_root" "$workspace/.ecosyncbench/test-reports"
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm \
      -e PATH=/usr/local/go/bin:/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
      "$image" bash -c 'go version'
    return 0
  fi
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-2400}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e GOCACHE=/go-cache/build \
    -e GOMODCACHE=/go-cache/pkg/mod \
    -e GOPATH=/go-cache/gopath \
    -e GOTOOLCHAIN=local \
    -e PATH=/usr/local/go/bin:/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -e ECOSYNC_REPORT="$report" \
    -e ECOSYNC_SETUP="$setup" \
    --tmpfs /tmp:rw,exec,nosuid,nodev,size=8g,mode=1777 \
    -v "$workspace:/workspace" \
    -v "$cache_root:/go-cache" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -c 'set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      if [[ "${ECOSYNC_SETUP:-}" == "kubernetes-etcd" ]]; then
        export GOFLAGS="${GOFLAGS:+$GOFLAGS }-mod=readonly"
        etcd_version="${ETCD_VERSION:-}"
        os="$(go env GOOS)"
        arch="$(go env GOARCH)"
        mkdir -p third_party /go-cache/etcd
        if [[ -z "$etcd_version" ]]; then
          cached_etcd="$(find /go-cache/etcd -maxdepth 1 -type d -name "etcd-v*-${os}-${arch}" | sort -V | tail -n 1)"
          if [[ -n "$cached_etcd" ]]; then
            etcd_dir="$(basename "$cached_etcd")"
            ln -sfn "$cached_etcd" "third_party/$etcd_dir"
            ln -sfn "$etcd_dir" third_party/etcd
          elif [[ -x hack/install-etcd.sh ]]; then
            hack/install-etcd.sh >/tmp/ecosync-install-etcd.log
            installed_etcd="$(find third_party -maxdepth 1 -type d -name "etcd-v*-${os}-${arch}" | sort -V | tail -n 1)"
            test -n "$installed_etcd"
            etcd_dir="$(basename "$installed_etcd")"
            cp -a "$installed_etcd" "/go-cache/etcd/$etcd_dir"
            ln -sfn "$etcd_dir" third_party/etcd
          else
            echo "missing cached etcd and no hack/install-etcd.sh in $PWD" >&2
            exit 2
          fi
        else
          cache_dir="/go-cache/etcd/etcd-v${etcd_version}-${os}-${arch}"
          if [[ -d "$cache_dir" ]]; then
          ln -sfn "$cache_dir" "third_party/etcd-v${etcd_version}-${os}-${arch}"
          ln -sfn "etcd-v${etcd_version}-${os}-${arch}" third_party/etcd
          else
          if [[ -x hack/install-etcd.sh ]]; then
            hack/install-etcd.sh >/tmp/ecosync-install-etcd.log
              installed_etcd="$(find third_party -maxdepth 1 -type d -name "etcd-v*-${os}-${arch}" | sort -V | tail -n 1)"
              test -n "$installed_etcd"
              cp -a "$installed_etcd" "$cache_dir"
          else
            echo "missing cached etcd and no hack/install-etcd.sh in $PWD" >&2
            exit 2
          fi
          fi
        fi
        export PATH="$PWD/third_party/etcd:$PATH"
      fi
      go test -json -count=1 "$@" 2>&1 | tee "/workspace/.ecosyncbench/test-reports/${ECOSYNC_REPORT}.json"' bash "$@"
}

case "$profile" in
  kube_openapi-hidden)
    run_go "repos/kubernetes/kube-openapi/test/integration" "kube-openapi-hidden" "" .
    ;;
  kubernetes-hidden)
    run_go "repos/kubernetes/kubernetes" "kubernetes-hidden" "kubernetes-etcd" \
      ./staging/src/k8s.io/api \
      ./staging/src/k8s.io/apiextensions-apiserver/pkg/apis \
      ./staging/src/k8s.io/apiextensions-apiserver/pkg/controller/openapi/builder \
      ./staging/src/k8s.io/apiextensions-apiserver/pkg/controller/openapi \
      ./staging/src/k8s.io/apiserver/pkg/endpoints/openapi \
      ./staging/src/k8s.io/apiserver/pkg/server \
      ./staging/src/k8s.io/kube-aggregator/pkg/apis \
      ./staging/src/k8s.io/kube-aggregator/pkg/controllers/openapiv3/aggregator \
      ./test/integration/apiserver/openapi
    ;;
  *)
    echo "Unknown profile for kubernetes_kube_openapi_kubernetes_3ba39b608c20: $profile" >&2
    exit 2
    ;;
esac
