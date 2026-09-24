Keep Symfony DeepClone v0.3.0 hydrate behavior equivalent between the native extension and the PHP polyfill.

Land the v0.3.0 feature set on `deepclone_hydrate()` with the collapsed signature `deepclone_hydrate(object|string $object_or_class, array $vars = [], int $flags = 0): object`. `$vars` is the scoped per-class shape by default. Pass `DEEPCLONE_HYDRATE_MANGLED_VARS` to interpret it as a flat mangled-key array matching the `(array) $obj` shape. A NUL-prefixed top-level key in scoped mode should raise a `ValueError` pointing at the missing flag.

Support `DEEPCLONE_HYDRATE_CALL_HOOKS` with `ReflectionProperty::setValue` semantics and `DEEPCLONE_HYDRATE_NO_LAZY_INIT` with `setRawValueWithoutLazyInitialization` semantics: skip the lazy initializer and realize the object when the last lazy property is set. `CALL_HOOKS` and `NO_LAZY_INIT` are mutually exclusive; `MANGLED_VARS` composes with either, and unknown flag bits should raise `ValueError`.

Reject non-array scope buckets instead of silently continuing. In `deepclone_from_array()`, reject a property scope that is not a loaded class name. Property replacement must also be destructor-reentrance safe: a destructor that reads the same slot should observe the new value.

Align typed-property hydration with raw-value engine semantics so wrong-type writes into typed slots are no longer silently allowed. Preserve private-shadowing roundtrip semantics through `to_array` / `from_array`.

Mirror the same forgiving-hydrate behaviors in both implementations: when a readonly slot already holds an identical value (`===`), silently skip the write; when the payload writes `null` into a non-nullable typed property, restore the uninitialized state instead of raising `TypeError`, except for hooked properties; and when the property is typed with a single, possibly nullable backed enum, cast scalar payload values through `Enum::from()`. Unknown enum values should raise the standard `ValueError`; unions, intersections, and non-backed enums should not use this cast. These decisions rest on the property type, so set hooks on enum-typed properties receive the cast enum case rather than the raw scalar.
