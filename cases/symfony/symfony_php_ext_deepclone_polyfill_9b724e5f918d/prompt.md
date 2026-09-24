Reject malformed `deepclone_from_array()` input consistently in the native extension and PHP polyfill.

`deepclone_from_array()` reconstructs a value graph from an array payload. There are two malformed-input cases that should fail cleanly.

A serialized class-name blob that does not `unserialize()` to an object, such as an `i:`, `s:` or `a:` form, must be rejected with a `\ValueError` instead of being stored and treated as an object.

A `PHP_INT_MIN` reference id on the object-reference, named-closure, and `prepared` paths should not reach `-$id`, overflow, emit a runtime warning, or abort under undefined-behavior sanitizer builds. These malformed payloads should throw a clean `\ValueError`.

Keep ordinary negative reference ids working unchanged.
