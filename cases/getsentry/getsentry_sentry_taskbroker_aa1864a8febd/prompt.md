Add TaskBroker context headers and hooks, then use them to propagate Sentry `ViewerContext` through TaskBroker tasks.

Add headers and hooks to the taskbroker client. To fully implicitly pass context across the wire, add hooks to both pack it into headers on the producer side and unpack it on the consumer side automatically.

Define a `ContextHook` protocol with `on_dispatch()` and `on_execute()` hooks. Registered hooks should run automatically when task activations are created and executed, and multiple hooks should all be applied. Without registered hooks, existing behavior remains unchanged.

Add a `ViewerContextHook` that implicitly propagates `ViewerContext` through TaskBroker task headers.

On dispatch, the hook reads from the `get_viewer_context()` contextvar and writes the available context fields to a single JSON task activation header named `sentry-viewer-context`. If there is no viewer context, it should not add the header. This happens automatically in `create_activation()` with no callsite changes needed.

On execution, the hook reads that header back and wraps the task callable in `viewer_context_scope()`, making `get_viewer_context()` available inside every task and restoring the previous context afterwards. Missing or invalid context headers should leave the execution context unchanged.
