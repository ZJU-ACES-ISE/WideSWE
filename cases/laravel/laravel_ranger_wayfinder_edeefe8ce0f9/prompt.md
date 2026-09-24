Would it be possible to support generating Typescript enums from PHP enums? This would enable codebases that use numeric enums without initializers and/or prefer TS enums to use wayfinder.

Currently this breaks code generation:

```php
enum State
{
    case None;
    case Open;
    case Done;
}
```

Steps To Reproduce

- Have php enums without initializer
- Install wayfinder dev-next and generate types

Currently, when collecting enums, `UnitEnum` cases (PHP enums without explicit values) are mapped to `null`.

For `UnitEnum` cases, return numeric indices (`0`, `1`, `2`...) based on the order of cases. `BackedEnum` cases should continue to return their actual values.

When generating TypeScript from the collected enum values, preserve numeric cases as numbers instead of quoting them as strings. The generated UnitEnum object must contain exactly the declared cases with their numeric values, while existing string-backed enum cases must continue to be exported as strings.
