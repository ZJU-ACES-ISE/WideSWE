Introduce a `BY` clause to `CHANGE_POINT`. It accepts one or more grouping expressions, and change point detection is performed independently for each group. The existing requirement of at least 22 values and limit to the first 1,000 values apply per group.

Add a change point view in Discover. The `CHANGE_POINT BY` functionality is available, and the view should support many change points, multiple groupings, no groupings, and TS queries.

Support ES|QL change point workflows using commands such as:

```esql
FROM kibana_sample_data_logs
| STATS avg_bytes=AVG(bytes) BY geo.dest, day=BUCKET(timestamp, 1d)
| CHANGE_POINT avg_bytes ON day BY geo.dest
| WHERE type IS NOT NULL
```
