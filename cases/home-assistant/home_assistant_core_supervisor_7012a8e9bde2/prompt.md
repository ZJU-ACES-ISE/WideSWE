Avoid adding `Content-Type` to responses that do not have a body.

The current code sets the `Content-Type` header for all responses to the result's `content_type` property if upstream does not set one. The default value for `content_type` is `application/octet-stream`.

For responses that do not have a body, like `204 No Content` or `304 Not Modified`, adding a default `Content-Type` header is unnecessary and potentially misleading. Empty-body responses, including `HEAD`, should not be read or streamed as body responses.

If upstream explicitly provides a `Content-Type`, preserve it even for an empty-body response, as required for `HEAD`. If upstream does not provide one, do not add the default `application/octet-stream` to an empty-body response. Responses that contain a body should continue to preserve an upstream `Content-Type` or use the existing default when one is absent.
