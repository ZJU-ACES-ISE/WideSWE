Command to clean the reprovider queue.

### Description

In a situation in which:

- One has a huge reprovider queue that has been stored to disk.
- One decides to update the reprovider strategy.

There is no way to clean the existing queue. It would be nice to have.

When `Reprovider.Strategy` changes and Kubo restarts, automatically clear the existing provide queue so that only content matching the new strategy will be announced to the network.

Also provide an `ipfs provide clear` command for manually clearing the provide queue. Report how many items were removed.

`ipfs provide clear` must succeed even when the provider system is disabled. Its normal text output must use the form `removed <count> items from provide queue`; clearing an already empty queue therefore reports `removed 0 items from provide queue`. The `-q` option must suppress output, while `--enc=json` must return the removed-item count as a non-negative JSON integer.

Clearing the queue must remove both entries currently held in memory and entries already persisted to disk, return the total number removed, and leave the queue empty when it is opened again.
