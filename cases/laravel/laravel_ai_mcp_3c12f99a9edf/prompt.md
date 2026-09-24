Fix generator-returning stream callbacks under Octane.

When running Laravel under Octane with FrankenPHP, a generator callback passed to `response()->stream()` returns a `200 OK` response with `Content-Length: 0` and no body. The same streamed response implemented with `echo` + `flush()` works and returns the expected SSE body.

This looks related to the Octane branch in `Illuminate\Routing\ResponseFactory::stream()` that detects generator callbacks when `$_SERVER['LARAVEL_OCTANE']` is set. Streaming responses such as SSE/event streams should work under Octane when callbacks return generators or iterables, and should behave like they do outside this Octane path.

Where supported, this includes both the default SSE stream and the Vercel protocol stream.

The fix should keep callbacks that echo directly unchanged and avoid double-emitting them.
