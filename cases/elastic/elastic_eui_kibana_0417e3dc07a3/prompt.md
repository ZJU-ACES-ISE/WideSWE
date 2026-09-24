Track history of child flyouts.

Support advanced use cases where user flows can branch off into separate sub-flows with history-managed child flyouts.

When the state has historical sessions with child flyouts, show them in the history popover in the flyout menu.

Flyout Manager sessions should track a stack of child flyouts as `childHistory`. `FlyoutSession` should include `childTitle`, `childIconType`, and `childHistory`. Support an optional `level` argument in `goToFlyout(flyoutId, level?)`; when `level` is `'child'`, navigate to a child in the current session's history, while main-session navigation continues to work as before.

Show child history entries most recent first before previous main sessions. A history state with "zero depth" should not be surfaced in the history popover; opening one main flyout and one child should not add a standalone main history entry.

The Back button should return to the previous child before leaving the current main session. It should close the flyout being left while keeping the destination parent, previous child, or previous main session open.

Address a bug where child flyouts rendered as siblings to the main flyout, rather than nested in React, are not automatically closed when the main flyout closes. Closing a main flyout should cascade-close all active and historical correlated child flyouts without affecting unrelated sessions. This also applies to the System Flyout Service: `core.overlays.openSystemFlyout`.
