Update tool schemas so parsing failures do not fail tool operations.

API responses do not always perfectly match the expected schemas. When API responses deviate from expected schemas but are still valid, tools currently fail with schema validation errors.

For example, the Mapbox API sometimes returns `brand_id` as an array instead of a single string. This causes the Search and Geocode tool to fail with:

```
MCP error -32602: Output validation error: Invalid structured content for tool search_and_geocode_tool: Expected string, received array at path features[4].properties.brand_id
```

The tool should accept both string and array formats for `brand_id`.

When output schema validation fails, log a warning with details about the validation failure and return the raw API data instead of throwing an error. Tools should remain functional even when API responses change or schemas are incomplete, and version information should remain available if its schema changes.

When schema validation succeeds, return the validated data normally. If no output schema is provided, return the raw data unchanged. The same graceful fallback should work for responses containing arrays, and validation warnings should include `Output schema validation failed`.

Apply the same non-fatal behavior to token creation, style listing, and token listing. Preserve each tool's normal response shape when returning raw data, including the `styles` and `tokens` collections.
