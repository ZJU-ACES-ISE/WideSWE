Log messages containing printf format specifiers crash on Windows.

When `enable_logs` is active (the default), log messages that contain printf format specifiers are interpreted as printf format strings, causing undefined behavior. The `%n` specifier is a guaranteed crash on Windows due to MSVC's CRT invalid parameter handler.

We hit this with a shipped game on Windows. A user experienced consistent crashes on startup whenever the Sentry GDExtension loaded successfully. Crash dumps from three separate sessions all showed the same crash at `_invoke_watson` inside `libsentry.windows.release.x86_64.dll`.

### Cause

In `NativeSDK::log()`, the log body is passed as the first argument to `sentry_log_*`:

```cpp
sentry_log_info(body.utf8(), attributes);
```

The `sentry_log_*` functions treat this argument as a printf format string. With `logs_with_attributes` enabled, the `attributes` object is consumed as the first vararg, leaving the format string to be processed against an empty va_list.

If the body contains `%` characters, `vsnprintf` reads past the va_list. On MSVC, `%n` is disabled by default (since VS 2015) and triggers the invalid parameter handler (`_invoke_watson`), terminating the process immediately.

The crash path:
1. Any Godot log message flows through `NativeSDK::log()`.
2. `sentry_log_info(body.utf8(), attributes)` treats the message as a printf format string.
3. `construct_log()` in sentry-native calls `vsnprintf(NULL, 0, message, args_copy)` with an exhausted va_list.
4. MSVC CRT hits `%n` -> `_invalid_parameter_noinfo` -> `_invoke_watson` -> process killed.

### Reproduction

```gdscript
# In any autoload or _ready():
print("test %n done")
```

Build a Windows export with `enable_logs` at its default value (`true`). The game crashes on startup when this message is logged.

The log body should be preserved as-is, including `%` sequences, without creating printf message-template or parameter attributes. Caller-provided structured attributes should remain unchanged.

### Affected versions

Reproduced on 1.2.0. The same code pattern is present in 1.4.1 (not tested).

### Workaround

Setting `enable_logs` to `false` should avoid the buggy code path, since `NativeSDK::log()` is not called. Crash reporting and breadcrumbs are unaffected.

```ini
[sentry]
options/enable_logs=false
```

The native logging contract must provide `sentry_log(level, body, attributes)` for plain-string messages. It must store the body as-is, preserve the requested level and caller-provided attributes, skip printf message-template and parameter extraction, accept null attributes, and follow the existing enabled/disabled logging behavior. Ownership of `attributes` is transferred to the call, and existing formatted logging and lifecycle behavior must remain unchanged.
