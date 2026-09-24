#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
workspace="${ECOSYNC_WORKSPACE:?ECOSYNC_WORKSPACE required}"
report_dir="$workspace/.ecosyncbench/test-reports"
mkdir -p "$report_dir"

cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/huggingface-hf-csi-mount-c1fa2e}"
mkdir -p "$cache_root/go-build" "$cache_root/go-mod" "$cache_root/cargo-home" "$cache_root/cargo-git" "$cache_root/cargo-target"

go_image="ecosyncbench/base/go:1.26-bookworm"
rust_image="ecosyncbench/base/rust:1.89-bookworm"

if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
  case "$profile" in
    hf_csi_driver-hidden) docker image inspect "$go_image" >/dev/null ;;
    hf_mount-hidden) docker image inspect "$rust_image" >/dev/null ;;
    *) echo "unknown profile: $profile" >&2; exit 2 ;;
  esac
  exit 0
fi

case "$profile" in
  hf_csi_driver-hidden)
    docker run --rm \
      -v "$workspace:/workspace" \
      -v "$cache_root/go-build:/root/.cache/go-build" \
      -v "$cache_root/go-mod:/go/pkg/mod" \
      -w /workspace/repos/huggingface/hf-csi-driver \
      "$go_image" \
      bash -lc 'export PATH=/go/bin:/usr/local/go/bin:$PATH; set -o pipefail; go test -count=1 -json ./pkg/webhook | tee /workspace/.ecosyncbench/test-reports/hf_csi_driver-hidden.json'
    ;;
  hf_mount-hidden)
    docker run --rm \
      -v "$workspace:/workspace" \
      -v "$cache_root/cargo-home:/usr/local/cargo/registry" \
      -v "$cache_root/cargo-git:/usr/local/cargo/git" \
      -v "$cache_root/cargo-target:/workspace/repos/huggingface/hf-mount/target" \
      -w /workspace/repos/huggingface/hf-mount \
      "$rust_image" \
      bash -lc 'export PATH=/usr/local/cargo/bin:$PATH CARGO_NET_GIT_FETCH_WITH_CLI=true; set -o pipefail; cargo test --features fuse --test warm_cache_bench -- --nocapture && cargo test --features fuse --test inode_lru_contract -- --nocapture'
    ;;
  *)
    echo "unknown profile: $profile" >&2
    exit 2
    ;;
esac
