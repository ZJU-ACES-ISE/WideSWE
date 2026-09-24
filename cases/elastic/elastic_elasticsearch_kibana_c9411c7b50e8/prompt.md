Add automatic step derivation for PromQL range queries in ES|QL when `step` is omitted.

Range queries can provide `buckets=<n>` instead of `step`, and when neither parameter is provided the parser defaults to `buckets=100`.

Auto-bucketing currently requires explicit `start` and `end`.

During translation to ES|QL, the effective step duration is derived from the same date-rounding logic used by `BUCKET` over `[start, end, buckets]`, and then used to build the `step` column.

The goal is to reduce query boilerplate while keeping bucketed results deterministic. For example, `start=00:00`, `end=00:01`, `buckets=3` produces steps at `00:00`, `00:20`, and `00:40`; with default buckets over one minute, the inferred step is 10 seconds.

Tighten parameter validation: `step` and `buckets` are mutually exclusive for range queries, and both duration/integer values must be positive.

Keep the feature BWC-safe for clusters that do not support the `buckets` parameter.

Add a `buckets` parameter to PromQL. Make the following behavior explicit:

- `step` is optional.
- If `step` is not set, it is derived from `buckets`, `start`, and `end`.
- If `buckets` is not set, it defaults to 100.
- If `buckets` is set, or `start` and `end` are set, still pass `step` as next column.
- `step` and `buckets` are mutually exclusive for validation and autocomplete.
