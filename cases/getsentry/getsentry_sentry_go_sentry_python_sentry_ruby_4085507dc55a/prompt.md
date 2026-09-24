Implement strict trace continuation.

Strict trace continuation controls whether the SDK continues traces from unknown third-party services that happen to be instrumented by Sentry, and fixes a security hole where incoming traces from other orgs can cause a DOS-like attack on another org by injecting Sentry propagation headers.

Add `StrictTraceContinuation` / `strict_trace_continuation` according to the strict trace continuation spec. Add `OrgID` / `org_id` client configuration, derive org ID from the DSN host when possible, for example `o123.ingest.sentry.io` -> `"123"`, and let an explicit `org_id` setting take precedence over DSN parsing for self-hosted/Relay setups.

Propagate `org_id` as `sentry-org_id` in outgoing baggage, including `sentry_baggage`, propagation context, and transaction head baggage.

Compare incoming `sentry-org_id` baggage against the SDK's own org ID per the decision matrix. When `strict_trace_continuation` is `false` by default, continue unless both org IDs are present and different. When `strict_trace_continuation` is `true`, continue only when both org IDs are present and equal, or when neither side has an org ID; otherwise start a new trace.
