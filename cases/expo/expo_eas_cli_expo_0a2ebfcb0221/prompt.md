Instead of waiting for sessions to expire server-side, logout should immediately invalidate the session. Users often have too many sessions at once, most of them unused.

Sessions used to be shared with the website, but are now separate with internal OAuth flow, so logging out from CLI won't affect website session. This would affect existing CLI sessions though.

If server-side invalidation fails, logout should still remove the credential locally, which is the existing behavior, and the credential will soon expire.
