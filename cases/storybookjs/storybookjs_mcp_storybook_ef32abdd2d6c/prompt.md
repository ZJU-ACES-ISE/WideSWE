### Describe the bug

Currently:
1. When `componentPath` does not successfully resolve, the manifest generation skips generating data for that component.
2. When a story source snippet does not successfully render, the manifest generation falls back to `() => <Component />`.

In both cases, this obscures the underlying problem, which could be a bug in the manifest generation or the user using an unsupported construct. Instead of hiding the error, the manifest generator should report it cleanly with the line number and context, so that the user can debug.

Supported stories should continue to produce accurate snippets, including function- and variable-style stories, CSF variants, custom or meta-level render functions, top-level exported functions, nested args and children, and falsy values such as `false`, `0`, and empty strings. Unsupported expressions should be represented as errors rather than fabricated fallback snippets.

The component manifest format should include a required component `path`. Component and example entries may contain an `error` object with a `message`, and an example's `snippet` may be absent. Manifest consumers must accept this format and handle errors and missing snippets without hiding the underlying problem.
