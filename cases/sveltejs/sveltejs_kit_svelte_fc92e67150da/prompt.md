Navigation should not break remote function queries or reactive behaviour.

Having the same remote function query on more than one page and navigating between them breaks its ability to refresh. It appears to be related to caching, because navigating to a page without the query fixes the issue.

Reactive behaviour can also break after navigating away from a page and then back. The first time you click, the field reactively updates, but the second time, and any time after that, it does not update anymore.

The cache is tied to the effect lifecycle: once the query has zero dependents, it is removed. This results in a subtle bug: if a query is read in component A, the instance and its deriveds are created inside the current effect. If component B then reads the same query and component A is destroyed, the object is still alive in the cache but the deriveds belong to a now-destroyed effect, and refreshing the query only works if those deriveds have been kept alive.

Freeze the value of a derived if it was created inside a parent effect that is now destroyed. This prevents the sort of bug where a derived reads `foo.bar` even though `foo` is now `undefined`. If the derived is dirty, print a warning.

Queries used during render should not accidentally use stale data. If a query was not rendered on the server, it cannot be rendered during hydration. For example:

```svelte
<script>
	import { browser } from '$app/environment';
	const count = browser ? get_count() : null;
</script>
```

This should throw during hydration because `get_count` tried to access cached data that did not exist. This is almost always an undesirable mistake: it introduces a waterfall on the client and blocks hydration.

On the client, a query's data can only be accessed if the query was created in a tracking context, such as the top level of a script block, a derived, or an effect, and that tracking context is still alive. Query data cannot be created and used from a universal `load`, an event handler, or the top level of a module.

The methods that do not access query data remain available anywhere: `.refresh`, `.set`, and `.withOverride`. For one-off data access from a non-reactive context, `query().run()` should return a plain `Promise` resolving to the data, but `.run()` cannot be called during client or server rendering. `.refresh` should be a no-op if there is no cached query instance, and stale cached data should not be used after hydration.
