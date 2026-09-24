Fix the "orphaned flyout" bug for system flyouts that render in separate React roots.

There is a Kibana service, `core.overlays.openSystemFlyout()`, that renders flyouts imperatively and creates a new React root per flyout.

When the main flyout closes and the session is removed, the child's root stays mounted and the child re-renders as an unmanaged flyout, remaining visible.

Any matching system flyout should close automatically when its session is removed, whether it is the main or child flyout in that session. Cascade-close should work for `openSystemFlyout()` flows that render in separate React roots, and the flyout system example should reflect system behavior rather than manually closing child refs.

The shared flyout manager contract must provide a public, read-only event subscription through `getFlyoutManagerStore()` and `subscribeToEvents()`, with a `FlyoutManagerEvent` carrying `CLOSE_SESSION`. Whenever navigation or closing a flyout removes one or more sessions, one event must be emitted for each removed session to every active event subscriber.
