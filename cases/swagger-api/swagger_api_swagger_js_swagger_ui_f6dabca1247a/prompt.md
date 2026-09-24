When an OpenAPI 3.1.0 parameter contains a complex union type, the formatting of requests is not applied properly.

This affects parameters with:
- a union type which includes `array` (`type: [array, ...]`)
- complex schemas with `anyOf`/`oneOf` and nested `type: array`

Fix the encoding of these parameters.

For `style: form` with `explode: true`, an array value selected through a union, `anyOf`, `oneOf`, or nested combined schema should be encoded as repeated query parameters, such as `parameters=a&parameters=b`. A primitive string value should remain a single query parameter, including when the string itself looks like serialized JSON.

When an OAS3 parameter schema requires an array but the provided value or example is a string, request execution should show a validation error instead of leaving the loader running. Existing required-array validation must continue to report a missing required value.
