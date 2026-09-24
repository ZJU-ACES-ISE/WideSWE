Make `<svelte:boundary>` work during SSR and make server rendering errors use the nearest error boundary.

Currently, `<svelte:boundary>` does nothing on SSR. On the REPL, code like this:

```svelte
<svelte:boundary>
	template
	{#snippet failed()}
		error
	{/snippet}
</svelte:boundary>
```

will generate this server code:

```js
{
	function failed($$payload) {
		$$payload.out += `<!---->error`;
	}

	$$payload.out += `<!---->template`;
}
```

The template is written, but there is no try/catch and the `failed()` snippet is never called when an error occurs in the template. This produces an HTTP 500 error. It should render the `failed()` snippet instead.

Remote function errors should be handled consistently between client rendering and SSR. If a remote function throws an HTTP error during SSR, the appropriate `+error.svelte` route should be rendered instead of the static HTML fallback error page. If an error is thrown inside of a remote function, it should be handled by the nearest `svelte:boundary` rather than displaying the Svelte error page. It should not crash the app with `ERR_UNHANDLED_REJECTION`.

Allow `render(...)`, `mount(...)`, and `hydrate(...)` to accept a `transformError` option. It may synchronously or asynchronously return a sanitized, JSON-stringifiable value for the boundary's `failed` snippet and hydration. If `transformError` is not provided, if the boundary has no `failed` snippet or prop, or if `transformError` throws or rejects, the rendering error should propagate as before. Preserve server context when `transformError` is asynchronous.

When SvelteKit's experimental `handleRenderingErrors` option is enabled, an error during rendering should show the nearest `+error.svelte`, just as if the error had occurred in a `load` function. The error should first pass through `handleError`, and the resulting object should be provided directly to `+error.svelte` and other error boundaries.
