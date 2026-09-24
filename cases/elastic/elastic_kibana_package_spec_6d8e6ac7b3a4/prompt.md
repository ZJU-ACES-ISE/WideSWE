Support OTel input packages that collect data with multiple signal types.

Add a new boolean manifest field `dynamic_signal_types` under `policy_template`, available from package specification format `3.6.0`. Setting it to `true` is only valid for input packages whose policy-template input is `otelcol`.

Enable otelcol input packages to collect multiple signal types. `dynamic_signal_types` is a boolean field:
- Default behavior is false; the otel input package works as usual, collecting only one signal type defined in the configuration.
- When enabled, the input package is able to collect all signal types defined under `config.pipelines`.

Recognize `logs`, `metrics`, and `traces` in `config.pipelines`, including both simple names such as `logs` and qualified names such as `logs/otlp`, and generate a different transform only for each signal defined in the pipelines.

Generate index templates for `logs`, `metrics`, and `traces`, using the configured dataset for each signal type. When `dynamic_signal_types` is false or absent, keep the existing single-signal behavior based on the configured data-stream type.

When `dynamic_signal_types` is enabled, hide the UI element in "advanced options" that allowed users to select the data-stream type. Keep this selector for false or absent values and for non-`otelcol` inputs.
