Add a plugins API and JSX support, and expose `print(...)` for Svelte ASTs.

Make `esrap` pluggable, so that it can be used to print any AST composed of `{ type: string, ... }` nodes rather than just `estree` and its TypeScript extensions. That includes Svelte ASTs. Add the plugins API and JSX support, using the suggested API with some slight tweaks in naming.

`print(ast, language, options?)` should accept one AST node and return `{ code, map }`. A custom language is a `Visitors<NodeType>` object whose node-type handlers can write output and visit child nodes, with `_` as the fallback visitor. Ship built-in `ts()` and experimental `tsx()` languages from `esrap/languages/ts` and `esrap/languages/tsx`; both accept quote and comments options.

Expose this through `print(...)` from `svelte/compiler`. The main motivation is to make it easier to write preprocessors and provide more robust lower-level utilities. Other potential uses include migrations, `sv add`, or having a `format` button in the playground.

`svelte/compiler`'s `print(ast, options?)` should accept a node produced by `parse(..., { modern: true })`, or any sub-node within that modern AST, and return valid Svelte `code` with a source `map`. Root comments should be preserved through the comments array used by the pluggable printer.

As a side-effect, quality of compiler output should be slightly better in certain cases, such as when encountering comments inside nodes. Re-use the `esrap` version that is already installed alongside Svelte and keep it current with new features.
