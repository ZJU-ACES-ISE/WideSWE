Custom baggage is overwritten with Sentry values on outgoing requests.

### Python SDK

Currently, any library that is patched by Sentry tries to append the baggage header whenever possible, for example `httpx`. When an outgoing request already has custom data in the `baggage` header, the destination of the call does not receive the original custom baggage. Other integrations use a similar approach to the `httpx` one and may be affected too.

To reproduce:

- Initialize a project with `sentry-sdk==1.26.0` and `httpx`.
- Initialize Sentry with `sentry_sdk.init(default_integrations=True, auto_enabling_integrations=True)`.
- Make a call with an `httpx` client such as `client.get("/path", headers={"baggage": "custom=value"})`.

### Ruby SDK

`Sentry::Utils::HttpTracing#set_propagation_headers` overwrites propagation headers on outgoing HTTP requests. While this is fine for the Sentry-specific `sentry-trace` header, the `baggage` header is a W3C standard shared across multiple systems. Pre-existing baggage entries may be set by OpenTelemetry, application code, or other instrumentation.

To reproduce:

- Set a custom `baggage` header on an outgoing HTTP request, for example `routingKey=myvalue,tenantId=123`.
- Ensure `propagate_traces` is enabled and the target URL matches `trace_propagation_targets`.
- Make the request using `Net::HTTP`, `Faraday`, or `Excon`.
- Inspect the outgoing request headers.

### Expected Result

When a `baggage` header already exists on the outgoing request, Sentry should merge its entries with the existing value by joining them with a comma (`,`), as per the W3C Baggage specification. The Sentry baggage and the original custom baggage values should both be present.

### Actual Result

Sentry baggage overwrites the whole `baggage` value, leaving Sentry values only. The original custom entries are lost.
