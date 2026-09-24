Harden Home Assistant backup tar extraction.

The previous extraction behavior did not protect against a symlink whose target points outside the destination directory. A backup containing a symlink with a benign name and a target outside the destination, followed by a regular-file member whose name traverses the symlink, could write attacker-controlled bytes outside the extraction tempdir.

All backup restore extraction paths should reject archive members that would escape the destination, whether directly through path traversal or through a symlink. Restore must abort on the first rejected member without writing outside the destination. Valid backups, including internal symlinks, should continue to restore correctly.

When a restore reports its outcome through `.HA_RESTORE_RESULT`, an unsuccessful result should record the specific rejection reason, such as `AbsolutePathError`, `OutsideDestinationError`, or `LinkOutsideDestinationError`, so users can see why the restore failed.

The hardening must preserve uid/gid and file permissions, which is important for backups that contain files owned by non-root users. Behavior that resets uid/gid or enforces minimum permissions would break file ownership after restore.

This is a defense-in-depth improvement, not a full security boundary. Backups should still be considered trusted input.
