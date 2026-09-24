Support deprecation metadata in package specs and expose it through package registry queries.

Implement support for the new `deprecated` field in package specifications, enabling package authors to mark entire packages or individual features (policy templates, inputs, data streams and variables) as deprecated.

Add the `deprecated` field to package-level content, input, and integration manifests; policy templates in input and integration manifests; inputs within integration policy templates; data streams; and data stream variables. Add `deprecation` as a new changelog type alongside enhancement, bugfix, and breaking-change.

The `deprecated` object requires `since` and a string `description`, and may include `replaced_by`. When `replaced_by` is used, it must identify the corresponding replacement type: `package` for a package, `policy_template` for a policy template, `input` for an input, `data_stream` for a data stream, and `variable` for a variable. If all inputs in an integration are deprecated, the package itself must also be marked as deprecated. These deprecation fields and the `deprecation` changelog type are supported from `format_version: 3.6.0`.

Package registry should show deprecation notice for a package or individual feature on any search or package query.

When a package is deprecated, the deprecation notice is populated on any version of the package served. This allows users with old versions to be aware of the package being deprecated. This does not happen on individual features, which appear deprecated under the version they have been deprecated.

When a package has multiple versions deprecated, the deprecation notice showed on multiple versions query is the latest. Meaning that, if a package has been deprecated in version `1.2.0`, and there is a version `1.3.0` with a different deprecated message, or no deprecation notice at all, the notice on `1.2.0` is propagated.
