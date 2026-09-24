### Problem

Cargo simply ignores `rust-toolchain.toml` overrides when used by the `--git` option to install a crate, and it can wrongly use the current directory's `rust-toolchain.toml` even when it is unrelated to the git repository being installed.

### Steps

To reproduce the first issue, install a crate from a git repository that contains a `rust-toolchain.toml`:

```shell
cargo install --git=https://github.com/helix-editor/helix helix-term
```

To reproduce the second issue, run `cargo install` from another Rust repository that contains a `rust-toolchain.toml`:

```shell
git clone https://github.com/nushell/nushell && cd nushell
cargo install --git=https://github.com/helix-editor/helix helix-term
# or
cargo install du-dust
```

In both cases, the toolchain is selected from the parent environment rather than the package being installed. This also applies to `rustup override`, which is stored in a user config file.

When `cargo install` is invoked with an implicitly selected non-default toolchain, warn that the default toolchain has been overridden and that rustup selected the toolchain based on the parent environment. Do not warn when the toolchain is selected explicitly with `+toolchain` or when the default toolchain is used.

Proxied tools should receive the source of the active toolchain through `RUSTUP_TOOLCHAIN_SOURCE`, using one of `cli`, `env`, `path-override`, `toolchain-file`, or `default`. Cargo should warn for `env`, `path-override`, and `toolchain-file`, identifying the source as an environment variable, rustup directory override, or rustup toolchain file respectively. The warning should suggest `cargo +stable install` and explain that rustup selects the toolchain from the parent environment. Do not emit this warning for `cli`, `default`, an unset source, or an unrecognized source.
