### Steps to reproduce

For the following `<script setup>`:

```ts
const { foo = "a" } = defineProps<{ foo?: string; }>();
const bar = ref<string>();
function init() {
  {
    var foo = "b";    // <- (1)
  }
  bar.value = foo;    // <- (2)
}
init();
```

### What is expected?

The `foo` at `(2)` is referencing the `var` declaration at `(1)`, although it is outside `(1)`'s lexical scope, since a `var` declaration is accessible throughout the whole `init` function. The reference at `(2)` should not be rewritten to `__props.foo`.

### What is actually happening?

`compiler-sfc` rewrites `foo` at `(2)` to `__props.foo`:

```js
const bar = ref();
function init() {
  {
    var foo = "b";
  }
  bar.value = __props.foo;
}
init();
```

This behavior is surprising and differs from Language Tools' hint.

Respect `var` hoisting throughout its containing function: references before the declaration, declarations inside nested blocks, and declarations in `for` initializers should all refer to the function-scoped local variable rather than the destructured prop. A `var` declared inside a nested function must not shadow the prop in the outer function. Language tooling hints should follow the same scope boundaries and mark only references that still resolve to props.
