Fix autofocus in SSR environments.

## Current behavior

When using the `TextField` component with the `autoFocus` prop in a Next.JS App Router project, the autofocus feature does not work as expected.

1. The `TextField` is not focused upon the initial page load or when the page is refreshed.
2. However, the `autoFocus` feature works correctly when there is a tab change in the browser window or when navigating between pages within the app.

When using the Field component and passing the `autoFocus` prop to `Field.Control`, `state.focused` is `false` and `data-focused` is not present on the initial render. The input is focused, but the focus outline is not present.

## Expected behavior

The `TextField` with the `autoFocus` prop should consistently focus on the input element:

1. On the initial page load.
2. When the page is refreshed.
3. When navigating between pages within the app.
4. When there is a tab change in the browser window.

For `Field.Control`, `state.focused` should be `true` and `data-focused` should be present on the field elements.

During SSR hydration, this behavior must work both when the browser has already focused the `autoFocus` element and when it has not focused it yet. Without `autoFocus`, an element focused before hydration must not be treated as an autofocus synchronization case.
