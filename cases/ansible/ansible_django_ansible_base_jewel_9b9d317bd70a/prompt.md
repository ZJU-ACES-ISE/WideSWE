Optimize related-field and summary-field fetching to avoid N+1 ForeignKey queries in list views.

`related_fields()` loads each ForeignKey object from the database just to build a URL, causing lazy-load queries for each object in list views. It should produce the same URLs without those queries, and null ForeignKeys should not appear in `related`. `get_summary_fields()` also loads ForeignKey objects when the ForeignKey is null; null ForeignKeys should be skipped without a query, while summary content for non-null relationships remains unchanged and preloaded relationships do not cause extra queries.

The user list endpoint should avoid per-user ForeignKey and platform-auditor queries while preserving correct `related`, `summary_fields`, `url`, and `is_platform_auditor` values. Detail responses should remain unaffected.
