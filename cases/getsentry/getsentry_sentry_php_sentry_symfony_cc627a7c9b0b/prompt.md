Add runtime context isolation for persistent PHP runtimes.

Introduce a `RuntimeContextManager` and `RuntimeContext`. They are meant to isolate data and telemetry per request to allow better support with alternative runtimes such as FrankenPHP and RoadRunner, which keep memory intact between requests.

Provide `startContext()`, `endContext(?int $timeout = null)`, and `withContext(callable $callback, ?int $timeout = null)` lifecycle APIs. A context should isolate scope and propagation data, and ending it should flush its telemetry resources. `withContext` should always end the context, including when the callback throws, return the callback result, and reuse an already active context for nested calls.

The changes introduced are built with Swoole/OpenSwoole in mind, but the initial version should not provide support for those extensions.

Use the context to properly isolate data between requests and messages and prevent data leaking between requests when using worker runtimes such as FrankenPHP or RoadRunner.

In Symfony, start and end a runtime context around each main request. When message isolation is enabled, also isolate each handled or failed message and always end its context.

Because message context isolation breaks current behaviour, introduce a new flag `isolate_context_by_message`, which defaults to `false` to keep current behaviour unchanged. When enabled, it will inherit all data that was set before a message is received.
