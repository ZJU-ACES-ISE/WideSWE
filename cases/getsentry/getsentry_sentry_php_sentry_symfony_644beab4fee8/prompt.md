Add `log_flush_threshold` support for buffered logs.

Add an option to perform a threshold based flushing of buffered logs. The option accepts `null` or a positive integer. By default, this option remains `null` and will only flush when the `flush` method is invoked.

For example, when `log_flush_threshold` is set to `2`, the second buffered log line should trigger the flush.

The threshold will also apply if using the Monolog handler since it funnels everything down to the `LogsAggregator`.

Integrate the same `log_flush_threshold` flag into Symfony and Laravel configuration so the framework integrations can pass the option through to the PHP SDK.
