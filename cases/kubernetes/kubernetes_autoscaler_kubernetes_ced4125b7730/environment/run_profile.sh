#!/usr/bin/env bash
set -euo pipefail
profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
repo_root="/opt/ecosyncbench"
image="ecosyncbench/base/go:1.26-bookworm"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/go-cache/kubernetes-observed-generation}"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$repo_root/benchmark/images/base/go-1.26-bookworm/Dockerfile" "$repo_root"
  fi
}

run_go() {
  local workdir="$1"
  local report="$2"
  local setup="$3"
  shift 3
  ensure_image
  mkdir -p "$cache_root" "$workspace/.ecosyncbench/test-reports"
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
    return 0
  fi
  docker run --rm \
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
    -v "$workspace:/workspace" \
    -v "$cache_root:/go-cache" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -c 'set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      if [[ "${ECOSYNC_SETUP:-}" == "kubernetes-etcd" ]]; then
        etcd_version="${ETCD_VERSION:-3.6.10}"
        os="$(go env GOOS)"
        arch="$(go env GOARCH)"
        cache_dir="/go-cache/etcd/etcd-v${etcd_version}-${os}-${arch}"
        mkdir -p /go-cache/etcd
        if [[ -d "$cache_dir" ]]; then
          :
        else
          installer_root="$PWD"
          if [[ ! -x "$installer_root/hack/install-etcd.sh" ]]; then
            installer_root=/workspace/repos/kubernetes/kubernetes
          fi
          if [[ -x "$installer_root/hack/install-etcd.sh" ]]; then
            (
              cd "$installer_root"
              hack/install-etcd.sh >/tmp/ecosync-install-etcd.log
              cp -a "third_party/etcd-v${etcd_version}-${os}-${arch}" "$cache_dir"
            )
          else
            echo "missing cached etcd and no Kubernetes hack/install-etcd.sh" >&2
            exit 2
          fi
        fi
        export PATH="$cache_dir:$PATH"
      fi
      go test -json -count=1 "$@" 2>&1 | tee "/workspace/.ecosyncbench/test-reports/${ECOSYNC_REPORT}.json"' bash "$@"
}

case "$profile" in
  autoscaler-hidden)
    run_go "repos/kubernetes/autoscaler/vertical-pod-autoscaler/test" "autoscaler-hidden" "kubernetes-etcd" ./integration/recommender
    ;;
  kubernetes-hidden)
    ECOSYNC_SETUP=kubernetes-etcd run_go "repos/kubernetes/kubernetes" "kubernetes-hidden" "kubernetes-etcd" ./pkg/controller/podautoscaler ./pkg/registry/autoscaling/horizontalpodautoscaler ./test/integration/apiserver/apidefinitions ./pkg/apis/autoscaling/validation
    ;;
  *)
    echo "Unknown profile for kubernetes_autoscaler_kubernetes_ced4125b7730: $profile" >&2
    exit 2
    ;;
esac
