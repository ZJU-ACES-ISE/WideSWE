Currently, logs from `tlscommon` package print `ca_trusted_fingerprint` provided by user and also those that are available on the server. This could be a sensitive information and should not be shipped.

TLS configuration loading must use a logger supplied by the caller rather than shared global logging state. `LoadTLSConfig` and `LoadTLSServerConfig` must accept a `*logp.Logger`, and TLS warnings and certificate-verification diagnostics must be emitted through the logger associated with that configuration.

Existing TLS and HTTP transport callers must continue to work with the logger-aware contract, passing their component-local logger through the relevant configuration and transport APIs. Call paths without a component logger must not fall back to sensitive global logging.
