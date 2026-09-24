# Fix Fleet auto-install content packages

Fleet checks ingested `data_stream.dataset` values to see if any content packages match that can be auto installed. For some reason the ES|QL query in the task doesn't return the `hostmetricsreceiver.otel` dataset, but it is returned when running the same query in Dev Tools.

The task should discover the expected datasets and install a matching content package such as `system_otel`.

Dataset discovery must not omit matching datasets when many distinct dataset values are present. Datasets already covered by installed content packages should be excluded from new installation decisions.

Support changing the task interval with `xpack.fleet.autoInstallContentPackages.taskInterval`. Use the prerelease flag from settings to support auto installing prerelease content packages such as `system_otel`.

Fleet performs dataset discovery as `kibana_system`. That identity must be able to read integration data streams matching `logs-*`, `metrics-*`, and `traces-*` so their dataset values are visible to the query.
