Handle gRPC timeout header interoperability across Go and Java.

The gRPC protocol specifies "TimeoutValue -> {positive integer as ASCII string of at most 8 digits}". Negative timeout values are invalid, and newly generated `grpc-timeout` headers should not contain zero.

Newly generated gRPC-Java timeout headers for zero or already-expired timeouts should use at least 1 nanosecond (`1n`) instead of `0n`.

For interoperability with older gRPC-Java clients, the gRPC-Go server should still accept legacy zero-second `grpc-timeout` values such as `0S` and allow the RPC to time out immediately instead of returning an internal error. Negative, malformed, and over-eight-digit timeout values should remain invalid.
