Surface Snuba rate-limit metadata consistently in Sentry access logs.

Some access logs show that a 429 was returned but are missing info, most notably a `rate_limit_type`.

Access logs for Snuba-throttled requests should set `rate_limit_type` to `RateLimitType.SNUBA`, mark the request as rate-limited, and include the available Snuba metadata as `snuba_policy`, `snuba_quota_unit`, `snuba_storage_key`, `snuba_quota_used`, and `snuba_rejection_threshold`. Standard rate-limit fields that Snuba does not provide should remain unset. Missing optional quota details should not break 429 handling or access logging.

For the max byte scanning policies, the API response is missing a `quota_allowance`. The response should include `quota_allowance` so that rate-limit and quota metadata remain available to consumers and access logs.
