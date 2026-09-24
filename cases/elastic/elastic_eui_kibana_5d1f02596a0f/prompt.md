**Describe the bug**

The issue is that the ref is not passed to the underlying `EuiManagedFlyout` (and ultimately `EuiFlyoutComponent`) when the `session` prop is used (e.g. `session="start"` or `session="inherit"`).

The `EuiFlyout` component delegates to `EuiFlyoutMain` or `EuiFlyoutChild` when a session is active. Neither of these components accept or forward a `ref`. Furthermore, `EuiManagedFlyout`, which they both render, creates its own internal `ref` for `useResizeObserver` and does not accept an external `ref`.

**Impact and severity**

It blocks the "Unified Doc Viewer" opt-in in Kibana because it makes the flyout content not keyboard accessible.

**To Reproduce**

Steps to reproduce the behavior:
1. Create a component that renders `<EuiFlyout session="start" ref={myRef}>`.
2. Observe that `myRef.current` remains `null` or `undefined` even after the flyout is mounted.
3. Compare with `<EuiFlyout ref={myRef}>` (without session), where `myRef.current` is correctly populated.

**Expected behavior**

The `ref` passed to `EuiFlyout` should be forwarded to the underlying DOM element regardless of whether the `session` prop is used or not.

Enabling flyout sessions in the Unified Doc Viewer must not otherwise change its existing behavior. ES|QL results should continue to omit the single-document and surrounding-document views, document profile customizations should still determine how the flyout is rendered, and a customized flyout title should remain visible in the session flyout.
