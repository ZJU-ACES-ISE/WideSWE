The natural way to share global, per-request state in asyncio is through contextvars. In Django, this is typically used via `asgiref.local.Local`. However, Django's async signal dispatch currently uses `asyncio.gather`, which internally creates new tasks (`asyncio.create_task`). This breaks context propagation, since each task gets its own copy of the context. As a result, it's impossible to set a global context-based variable inside a signal handler and have it shared with other signal handlers or parts of the same request/response cycle.

The value set inside the signal handler is lost because the handler runs in a separate task with its own context.

Signal handlers should run in the same async context as the request, preserving `ContextVar` and `asgiref.local.Local` state.

This context-sharing behavior should be consistent for synchronous, asynchronous, and mixed receiver sets across `send()`, `send_robust()`, `asend()`, and `asend_robust()`.

The shared async contract must allow a custom `contextvars.Context` to be supplied to `sync_to_async`. Concurrent tasks using that supplied context should share updates through it, while the caller's current context remains isolated.
