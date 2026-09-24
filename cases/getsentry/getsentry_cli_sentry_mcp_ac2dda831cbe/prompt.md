Render trace tree labels and metadata from OpenTelemetry semantic attributes before falling back to existing Sentry span name rendering.

Cover the major semantic span areas commonly seen in traces: HTTP, GenAI, MCP, database, GraphQL, RPC/cloud SDK, messaging, FaaS, object stores, CloudEvents, CICD, feature flags, process, exception, and generic error attributes. Use an explicit attribute-priority list; the Sentry `op` field remains display metadata and does not choose semantic behavior.

Enhance `sentry local` for the agent monitoring launch with OTel semantic attribute rendering, JSON output, SSE reconnection, and several quality improvements. Transactions with GenAI, MCP, HTTP, database, and other OTel attributes should show rich labels, and the display should fall back to existing behavior when no semantic attributes are present.

Add `--format json` / `-F json` for NDJSON machine-readable output. Each envelope item should produce one JSON line with structured fields including semantic labels, stack frames, and source detection.

Add `--filter ai` to match transactions with GenAI or MCP OTel attributes. The SSE consumer should reconnect automatically with exponential backoff on connection loss, use `Last-Event-ID` to resume from where the stream left off, reset the retry counter after successful reconnection, and retry transient HTTP errors after a previous successful session.

Also keep the related quality improvements: fix the signal handler leak, deduplicate `parsePort`, preserve `SENTRY_TRACES_SAMPLE_RATE` when already set, and add a startup banner with ingest URL, SSE endpoint, and connection hints.
