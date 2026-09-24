#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
image="ecosyncbench/base/rust:1.95-bookworm"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/rust-cache/apollographql-apollo-rs-router-location}"

mkdir -p "$cache_root/cargo-home" "$cache_root/target" "$workspace/.ecosyncbench/test-reports"

ensure_image() {
  docker image inspect "$image" >/dev/null 2>&1
}

run_cargo() {
  local workdir="$1"
  local target_subdir="$2"
  local command="$3"
  ensure_image
  docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e CARGO_HOME=/cache/cargo-home \
    -e CARGO_TARGET_DIR="/cache/target/${target_subdir}" \
    -e RUSTUP_HOME=/usr/local/rustup \
    -e RUSTUP_TOOLCHAIN=1.95.0-x86_64-unknown-linux-gnu \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -lc "$command"
}

if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
  ensure_image
  docker run --rm "$image" bash -lc 'export PATH=/usr/local/cargo/bin:$PATH; rustc --version >/dev/null && cargo --version >/dev/null'
  exit 0
fi

case "$profile" in
  apollo_rs-hidden)
    run_cargo "repos/apollographql/apollo-rs" "apollo-rs" '
      set -euo pipefail
      export PATH=/usr/local/cargo/bin:$PATH
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      set +e
      cargo test -p apollo-compiler --test main extensions -- --nocapture 2>&1 | tee /workspace/.ecosyncbench/test-reports/apollo-rs-hidden.log
      status=${PIPESTATUS[0]}
      set -e
      exit "$status"
    '
    ;;
  router-hidden)
    run_cargo "repos/apollographql/router" "router" '
      set -euo pipefail
      export PATH=/usr/local/cargo/bin:$PATH
      mkdir -p /workspace/.ecosyncbench/test-reports /tmp/ecosync-home
      set +e
      cargo test -p apollo-federation --test main composition::hints -- --nocapture 2>&1 | tee /workspace/.ecosyncbench/test-reports/router-hidden.log
      status=${PIPESTATUS[0]}
      set -e
      exit "$status"
    '
    ;;
  *)
    echo "Unknown profile for apollographql_apollo_rs_router_5d0be62a688a: $profile" >&2
    exit 2
    ;;
esac
