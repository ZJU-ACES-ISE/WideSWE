#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
repo_root="/opt/ecosyncbench"
image="ecosyncbench/base/go:1.26-bookworm"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/go-cache/kubernetes-nlb-targetgroup-attributes}"

ensure_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$repo_root/benchmark/images/base/go-1.26-bookworm/Dockerfile" "$repo_root"
  fi
}

run_go() {
  local workdir="$1"
  local report="$2"
  shift 2
  ensure_image
  mkdir -p "$cache_root" "$workspace/.ecosyncbench/test-reports"
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm \
      -e PATH=/usr/local/go/bin:/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
      "$image" bash -c 'go version'
    return 0
  fi
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-1800}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e GOCACHE=/go-cache/build \
    -e GOMODCACHE=/go-cache/pkg/mod \
    -e GOPATH=/go-cache/gopath \
    -e GOTOOLCHAIN=local \
    -e PATH=/usr/local/go/bin:/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -e ECOSYNC_REPORT="$report" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/go-cache" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -c 'set -euo pipefail; mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home; go test -json -count=1 "$@" 2>&1 | tee "/workspace/.ecosyncbench/test-reports/${ECOSYNC_REPORT}.json"' bash "$@"
}

case "$profile" in
  cloud_provider_aws-hidden)
    run_go "repos/kubernetes/cloud-provider-aws" "cloud-provider-aws-hidden" -run 'TestTargetGroupAttributesPublicContract|TestElbProtocolsAreEqual|TestAWSARNEquals|TestIsNLB|TestIsLBExternal|TestSyncElbListeners|TestElbListenersAreEqual|TestBuildTargetGroupName|TestFilterTargetNodes|TestCloud_findInstancesForELB|TestCloud_chunkTargetDescriptions|TestCloud_diffTargetGroupTargets|TestCloud_computeTargetGroupExpectedTargets|TestEnsureSSLNegotiationPolicyErrorHandling|TestCreateSubnetMappings|TestCloud_removeOwnedSecurityGroups|TestCloud_buildSecurityGroupRuleReferences|TestCloud_ensureTargetGroupTargets' ./pkg/providers/v1
    ;;
  kops-hidden)
    run_go "repos/kubernetes/kops" "kops-hidden" -run 'TestPolicyGeneration|TestEmptyPolicy' ./pkg/model/iam
    ;;
  *)
    echo "Unknown profile for kubernetes_cloud_provider_aws_kops_b75c3552e425: $profile" >&2
    exit 2
    ;;
esac
