Kafka sink should reintroduce retry, or at least make it configurable, without causing messages to be sent in the wrong order.

Currently CDC will not retry when meeting a failure because retrying would cause messages to be sent in wrong order. There is also a data-loss bug related to retrying.

However, the current behavior of failing the sink immediately on error leads to bad side effects that affect user experience: sink errors are recorded in metrics and lead to alerts, and hard-reset via sink-error is known to cause another Kafka client bug. Transient Kafka send errors such as `broken pipe` should not fail the sink immediately.

Kafka sink should retry transient producer send failures by default while preserving message ordering. Add a sink-URI option `max-retry` to configure the retry budget. Its default value is `5`; non-negative values override the default, `0` disables producer retry, and negative values are ignored.
