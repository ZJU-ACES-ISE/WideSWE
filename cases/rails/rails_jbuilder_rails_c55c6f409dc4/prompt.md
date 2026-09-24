Handle Rack's HTTP 422 status symbol transition from `:unprocessable_entity` to `:unprocessable_content`.

Using `:unprocessable_entity` emits a warning on Rack 3.2. In Rack v3.1.0, the symbol for HTTP status code 422 was changed from `:unprocessable_entity` to `:unprocessable_content`. Rails should support both `:unprocessable_entity` and `:unprocessable_content`, while scaffolds generated when using Rack 3.1 or higher should use `:unprocessable_content`.

Provide `ActionDispatch::Constants::UNPROCESSABLE_CONTENT` as the version-compatible HTTP 422 status symbol. It should resolve to `:unprocessable_content` with Rack 3.1 or higher and `:unprocessable_entity` with older Rack versions.

Generated API and regular scaffold controllers should use the version-compatible status for failed create and update operations, including both HTML and JSON responses: use `status: :unprocessable_content` with Rack 3.1 or higher and continue to use `status: :unprocessable_entity` with Rack 3.0 or lower.
