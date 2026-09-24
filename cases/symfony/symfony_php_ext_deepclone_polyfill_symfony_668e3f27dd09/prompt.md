Add `deepclone_hydrate()` as a shared object hydration primitive, with matching native extension and PHP polyfill behavior, and use it from Symfony VarExporter.

Provide `deepclone_hydrate(object|string $object_or_class, array $scoped_vars = [], array $mangled_vars = []): object`. The function should instantiate a class without calling its constructor, or hydrate an existing object, and set properties including private, protected, and readonly ones.

Support `$scoped_vars` keyed by declaring class, and `$mangled_vars` accepting mangled key format such as `"\0Class\0prop"` and `"\0*\0prop"` for convenience with `(array)` casts. Support the special `"\0"` key for SPL classes such as `ArrayObject`, `ArrayIterator`, and `SplObjectStorage`. When both inputs target the same property, `$mangled_vars` should overwrite `$scoped_vars`.

Preserve PHP `&` references and apply instantiability validation matching `deepclone_from_array()`. Reject integer property keys, NUL-containing or mangled-shape property names inside `$scoped_vars`, malformed keys in `$mangled_vars`, non-array values in `$scoped_vars`, and scopes that are not the target class or one of its parents with `ValueError`.

Symfony VarExporter `Hydrator::hydrate()` and `Instantiator::instantiate()` should delegate directly to `deepclone_hydrate()` from `symfony/polyfill-deepclone` or the native `ext-deepclone`, while keeping the lazy proxy property-scope data available where it is still used.
