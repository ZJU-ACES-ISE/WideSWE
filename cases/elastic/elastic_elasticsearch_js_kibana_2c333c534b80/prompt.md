# acceptedParams does not differentiate between query or body params

The `acceptedParams` API was added primarily to expose to Kibana whether `project_routing` is a supported parameter on a given API call. However, some APIs accept this as a query param while other require a body. To correctly inject project_routing Kibana needs to know if this is supported as a body/query param.

`TransportRequestMetadata.acceptedParams` must identify accepted path, body, and query parameters separately using the shape `{ path: string[], body: string[], query: string[] }`. For every generated API, each accepted parameter must appear in the category matching where that endpoint accepts it.

Requests must use that metadata to place `project_routing` in the query string or request body according to the endpoint contract. Query-based APIs such as `msearch` and `msearch_template` must preserve existing query parameters and must not override an existing `project_routing` value. Legacy flat-array `acceptedParams` inputs must remain compatible and treat a listed `project_routing` as a body parameter. APIs that do not accept it must not receive it, and PIT and NDJSON/bulk-body request behavior must remain correct.
