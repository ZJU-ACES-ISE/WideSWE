Fix alert threshold compatibility for the `OUTSIDE_STREAM_PROCESSOR_METRIC_THRESHOLD` event type.

The Atlas alerts API returns both the `threshold` and `metricsThreshold` / `metricThreshold` fields for this event type due to backwards compatibility. The API now accepts metric threshold values, but the `threshold` field is still returned on responses to avoid breaking changes.

This causes downstream infrastructure providers to produce inconsistent applies or send duplicate threshold fields during alert-configuration updates, including `DUPLICATE_THRESHOLD_FIELD` and an unexpected `.threshold_config` block after apply. Creating or updating this alert type should preserve the metric threshold configuration without adding an unexpected threshold block or sending duplicate threshold representations back to Atlas.

When an Atlas response contains both representations, downstream state and update requests should retain only the threshold form configured by the user: metric-threshold configurations must not acquire a legacy threshold block, and legacy-threshold configurations must not acquire a metric-threshold block.
