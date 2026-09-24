Cargo's `build.warnings=deny` should not block on hard warnings.

The `build.warnings` configuration allows control over warnings via Cargo. This config currently applies to all warnings, including hard warnings, not just lints. This is unfortunate, because the whole point of hard warnings is not to fail your build, and there is no way to allow or deny them.

For example, a build can fail with:

```text
warning: dropping unsupported crate type `dylib` for target `wasm32-wasip1`
warning: `std` (lib) generated 1 warning
error: warnings are denied by `build.warnings` configuration
```

The right behavior is for Cargo to only fail the build if at least one lint warning was emitted, and ignore hard warnings. Normal lint warnings should still cause `build.warnings=deny` to fail the build.

Rust bootstrap should use Cargo-managed warning denial: `--warnings=deny` should set `CARGO_BUILD_WARNINGS=deny` and enable Cargo's `-Zwarnings`, while `--warnings=warn` should leave both unset.
