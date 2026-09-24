Fix model binding key resolution for camelCase route handler parameters.

When a route handler uses a parameter variable that uses camelCase, the resulting route type does not include the model's key name when generated. This applies for models that use multi-word names, since the default resource-controller generator uses camelCase for the model instance, such as `$wayfinderBug`.

A route parameter such as `wayfinder_bug` should not be limited to only a string or number value when the handler parameter is camelCase. It should also support an object with an `id`, for example `{ id: string | number }`, so the model and its key are resolved correctly.

Cover routes whose URI parameter is snake_case while the action parameter is camelCase, such as `{audit_entry}` mapped to `$auditEntry`, and make generated route arguments resolve the correct binding key.
