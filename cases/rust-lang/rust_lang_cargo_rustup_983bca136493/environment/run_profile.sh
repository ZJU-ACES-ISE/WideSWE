#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
cargo_image="ecosyncbench/base/base-rust:1.94.1"
cargo_toolchain="/usr/local/rustup/toolchains/1.94.1-x86_64-unknown-linux-gnu"
rustup_image="ecosyncbench/base/rust:1.89-bookworm"
rustup_toolchain="/usr/local/rustup/toolchains/1.89.0-x86_64-unknown-linux-gnu"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/rust-cache/rust-lang-cargo-rustup-983bca136493}"

mkdir -p "$cache_root/cargo-home" "$cache_root/tool-bin" "$cache_root/target" "$cache_root/locks" "$workspace/.ecosyncbench/test-reports"

ensure_image() {
  local image="$1"
  docker image inspect "$image" >/dev/null 2>&1
}

run_rust() {
  local image="$1"
  local toolchain_dir="$2"
  local workdir="$3"
  local target_subdir="$4"
  local command="$5"
  ensure_image "$image"
  mkdir -p "$cache_root/tool-bin/$target_subdir" "$cache_root/target/$target_subdir"
  docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e FORCE_COLOR=0 \
    -e CARGO_HOME=/cache/cargo-home \
    -e CARGO_TARGET_DIR="/cache/target/${target_subdir}" \
    -e ECOSYNC_RUST_BIN="/cache/tool-bin/${target_subdir}" \
    -e ECOSYNC_CARGO_TARGET_LOCK="/cache/locks/${target_subdir}.lock" \
    -e RUSTUP_HOME=/usr/local/rustup \
    -e ECOSYNC_RUST_TOOLCHAIN_DIR="$toolchain_dir" \
    -e ECOSYNC_RUST_ENV_SETUP="$rust_env_setup" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    -w "/workspace/$workdir" \
    "$image" \
    bash -c "set -euo pipefail; exec 9>\"\$ECOSYNC_CARGO_TARGET_LOCK\"; flock 9; $command"
}

rust_env_setup='
  export PATH=$ECOSYNC_RUST_BIN:$ECOSYNC_RUST_TOOLCHAIN_DIR/bin:/usr/local/cargo/bin:$PATH
  unset RUSTUP_TOOLCHAIN RUSTUP_TOOLCHAIN_SOURCE
  mkdir -p "$ECOSYNC_RUST_BIN" "$CARGO_HOME/bin" /tmp/ecosync-home
  ln -sfn /usr/local/cargo/bin/rustup "$CARGO_HOME/bin/cargo"
  for tool in cargo rustc rustdoc rustfmt rustup; do
    rm -f "$ECOSYNC_RUST_BIN/${tool}"
    if [[ "$tool" == "cargo" && -x "$ECOSYNC_RUST_TOOLCHAIN_DIR/bin/cargo" ]]; then
      cat > "$ECOSYNC_RUST_BIN/cargo" <<\SH
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == +* ]]; then
  shift
fi
exec "$ECOSYNC_RUST_TOOLCHAIN_DIR/bin/cargo" "$@"
SH
      chmod +x "$ECOSYNC_RUST_BIN/cargo"
    elif [[ "$tool" == "rustup" && -x "/usr/local/cargo/bin/rustup" ]]; then
      ln -s "/usr/local/cargo/bin/rustup" "$ECOSYNC_RUST_BIN/${tool}"
    elif [[ -x "$ECOSYNC_RUST_TOOLCHAIN_DIR/bin/${tool}" ]]; then
      ln -s "$ECOSYNC_RUST_TOOLCHAIN_DIR/bin/${tool}" "$ECOSYNC_RUST_BIN/${tool}"
    fi
  done
  /usr/local/cargo/bin/rustup toolchain link stable "$ECOSYNC_RUST_TOOLCHAIN_DIR" >/dev/null 2>&1 || true
'

if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
  case "$profile" in
    cargo-hidden)
      run_rust "$cargo_image" "$cargo_toolchain" "repos/rust-lang/cargo" "cargo" '
        set -euo pipefail
        eval "$ECOSYNC_RUST_ENV_SETUP"
        cargo fetch --locked
      '
      ;;
    rustup-hidden)
      run_rust "$rustup_image" "$rustup_toolchain" "repos/rust-lang/rustup" "rustup" '
        set -euo pipefail
        eval "$ECOSYNC_RUST_ENV_SETUP"
        cargo fetch --locked
      '
      ;;
    *)
      echo "Unknown profile for rust_lang_cargo_rustup_983bca136493: $profile" >&2
      exit 2
      ;;
  esac
  exit 0
fi

case "$profile" in
  cargo-hidden)
    run_rust "$cargo_image" "$cargo_toolchain" "repos/rust-lang/cargo" "cargo" '
      set -euo pipefail
      eval "$ECOSYNC_RUST_ENV_SETUP"
      cargo test --test testsuite rustup
    '
    ;;
  rustup-hidden)
    run_rust "$rustup_image" "$rustup_toolchain" "repos/rust-lang/rustup" "rustup" '
      set -euo pipefail
      eval "$ECOSYNC_RUST_ENV_SETUP"
      cargo test --features=test --test test_bonanza suite::cli_rustup
    '
    ;;
  *)
    echo "Unknown profile for rust_lang_cargo_rustup_983bca136493: $profile" >&2
    exit 2
    ;;
esac
