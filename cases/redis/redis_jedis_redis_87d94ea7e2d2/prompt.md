Add support for new Redis Stream commands.

Add `XDELEX` and `XACKDEL` for streams, and extend `XADD` and `XTRIM`.

`XDELEX key [KEEPREF | DELREF | ACKED] IDS numids id [id ...]` extends `XDEL`, offering enhanced control over message entry deletion with respect to consumer groups. `KEEPREF` deletes the specified entries from the stream but preserves existing references to these entries in all consumer groups' PEL. `DELREF` deletes the specified entries from the stream and also removes all references to these entries from all consumer groups' pending entry lists. `ACKED` only trims entries that were read and acknowledged by all consumer groups. The `IDS` block can appear at any position in the command.

`XACKDEL key group [KEEPREF | DELREF | ACKED] IDS numids id [id ...]` combines `XACK` and `XDEL`: it acknowledges specified message IDs in the given consumer group and attempts to delete corresponding stream entries. `KEEPREF` preserves references in other consumer groups, `DELREF` removes references from all consumer groups, and `ACKED` deletes only entries acknowledged by all groups.

For both commands, return one result per ID: `-1` when the ID does not exist, `1` when the entry is deleted, and `2` when it is acknowledged or considered but not deleted because references remain.

Extend `XTRIM` and `XADD` to include optional `KEEPREF`, `DELREF`, or `ACKED` parameters for consumer group handling and trimming behavior. With `XADD ... ACKED`, stop trimming if referenced entries prevent reaching `MAXLEN`.

In Jedis, represent the policies as `StreamDeletionPolicy.KEEP_REFERENCES`, `DELETE_REFERENCES`, and `ACKNOWLEDGED`. Return per-ID values as `StreamEntryDeletionResult.NOT_FOUND`, `DELETED`, and `ACKNOWLEDGED_NOT_DELETED`, mapped to `-1`, `1`, and `2`. Support `xackdel` and `xdelex` string and binary overloads, including policy overloads, across direct, unified, and pipeline stream APIs; extend `XAddParams` and `XTrimParams` with the same policy support.
