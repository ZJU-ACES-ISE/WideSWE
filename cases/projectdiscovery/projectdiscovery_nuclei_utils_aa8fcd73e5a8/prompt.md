Path-based fuzzing should send the expected payload and correctly fuzz all path items.

### URL encoding

Currently, URL encoding is enabled by default, which is beneficial in many cases. However, it encodes space characters as `+` instead of `%20`. This can be a problem for users who want to encode spaces as `%20` rather than `+`.

The user has this payload `"(select 1 from sleep(5)"` when fuzzing on path, and expects the URL-encoded request to be sent as:

```
https://example.com/shop/category/(select%201%20from%20sleep(5)/display
```

However, the payload is encoded as:

```
https://example.com/shop/category/(select+1+from+sleep(5)/display
```

### Numeric path parts

Path-based fuzzing also skips some items. The path-based SQL injection integration test `fuzz/fuzz-path-sqli.yaml` returns 0 results instead of the expected 1 result because the numeric path part is not fuzzed.

### Expected behavior

Correct fuzzing of all path items. Path components should use path-appropriate encoding so spaces are represented as `%20`, and numeric path parts should not be skipped.

Path-specific encoding and decoding should preserve literal `+`, `/`, `=`, and `&` characters, while percent-encoding path-significant characters such as `?`, `#`, and `@`, as well as control and non-ASCII characters. Valid percent escapes should decode correctly, malformed escapes should remain intact, and encoding followed by decoding should recover the original path value.
