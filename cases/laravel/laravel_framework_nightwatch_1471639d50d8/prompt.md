Capture the query connection type, read or write, for better debugging and logging.

Expose the read / write type to query executed events and query exceptions. There are scenarios where it is impossible to know for sure if a query was against the read or write connection from the existing exposed state, so the event and exception data should carry the correct query connection type.

Make the query connection type available as `readWriteType` with a value of `read`, `write`, or `null` where query event, exception, and query log metadata are exposed. It should report the actual connection used for explicit read/write connections, sticky reads after a write, transactions, forced write connections for reads, and nested queries without allowing one query to overwrite another query's type. Connections without separate read/write configuration should report `write`.

Update `QuerySensor` to capture the query connection type when running on Laravel v12.45.0 or later where this information is available. Represent it with `QueryConnectionType` values `Read`, `Write`, and `Unknown`; serialize known values as `read` or `write` in `connection_type`, and serialize `Unknown` as an empty string so older Laravel versions remain compatible.
