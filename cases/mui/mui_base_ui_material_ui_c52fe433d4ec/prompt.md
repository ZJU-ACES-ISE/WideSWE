Improve `useControlled()` Strict Mode handling.

When an array is provided, React in strict mode will render twice, the instances will be different, and the warning triggers. This can show a console warning in components that use `useControlled()`, even though the default value has not meaningfully changed.

The warning should still help developers spot cases where the default value changes after a component is initialized, but it should not fire just because Strict Mode re-renders equivalent array defaults with different instances.
