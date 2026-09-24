### Describe the bug

Route with remote `query` which has `redirect` results in 500 `undefined` error.

```svelte
<script lang="ts">
  import { verifyUser } from "$lib/remote/auth.remote";

  await verifyUser()
</script>

<p>protected</p>
```

```ts
import { query } from "$app/server";
import { redirect } from "@sveltejs/kit";

export const verifyUser = query(() => {
  redirect(307, '/')
})
```

### Severity

Serious, but I can work around it.

### Additional Information

There is no error if query is wrapped inside `<svelte:boundary>`.

An awaited remote query redirect during page rendering should produce a real redirect response with the original status and location, not a 500 response, and it should not invoke the application's `handleError` hook. This should work when the query is used directly on a page or through a shared layout.

Async render-time rejections, including ordinary errors as well as redirects, must not be reported as unhandled before the render result is awaited, but must still surface unchanged when awaited during server rendering or hydration.
