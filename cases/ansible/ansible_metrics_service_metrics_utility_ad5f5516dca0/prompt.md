Run collectors for feature flags and task statistics, and add the utility part of both collectors: feature flags and metrics-service execution times.

Add `feature_flags_service` and `FeatureFlagsAnonymizedRollup` to include enabled feature flags in the anonymized report under `feature_flags`. Add `task_executions_service` and `TaskExecutionsAnonymizedRollup` to collect task execution observability for a `since` and `until` window, defaulting to the previous full UTC day, and expose it under `observability_by_tasks`.

Run task execution collection through `collect_daily_metrics`, schedule feature-flag and task-execution collection, and correctly log task execution times and failed executions. Retries must continue to collect the originally scheduled day.
