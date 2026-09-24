URLs don't include the base if Laravel is not installed in the root directory.

I have Laravel installed in a `v2` subdirectory. My local `APP_URL` is `http://localhost:8081/v2/`. None of the generated URLs contain this `v2` in the path. Adding `base: '/v2/'` to the Vite configuration does not affect the generated URLs.

Generated route URLs should include the path portion of `APP_URL`. For example, with `APP_URL=http://localhost:8081/v2`, `index.url()` should generate `/v2/posts` instead of `/posts`.

The change should handle the base path only. When `APP_URL` has no path, generated route paths should remain unchanged, and its port should not be injected into explicit route domains.
