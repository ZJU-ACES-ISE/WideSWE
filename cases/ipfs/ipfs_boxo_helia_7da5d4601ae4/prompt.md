bitswap/server: wantlist overflows fails in a toxic maner preventing any data transfer

This is a bug I introduced in 9cb5cb54d40b57084d1221ba83b9e6bb3fcc3197 when fixing CVE-2023-25568.

You can see that many gateways instances have more than 1024 entries.

Theses three sections of code are needed to fix CVE-2023-25568, they make the server ignore any new queries overflowing 1024. Previously the server would remember infinitely many CIDs eventually OOMing.
However the truncation code always prefer keeping existing entries over new ones.

This means we can get in a situation where we get stuck:
- let's say the client send a 2048 entries wantlist.
- out of theses we only have 1/3 of the CIDs, and theses are uniformly distributed.
- the server truncate the wantlist to 1024.
- the server starts serving theses entries first, out of theses we don't have 683 CIDs.
- we now only have 341 effective wantlist size. This is because the bitswap server never cleanup entries after sending `DONT_HAVE`.
  The point of this feature is for `-1` scalling, if the server is also downloading the same blocks, it might get them after having already sent `DONT_HAVE`, then the server can either send the block or the a `HAVE` message overriding the previous `DONT_HAVE`.
- This repeat each time shrinking the usable wantlist on this connection because the part of **the wantlist the server is willing to keep fills up with CIDs it does not have**.
  Eventually reaching 0

(note: the client can send CANCEL or a message with the full flag and theses 1024 "stuck" CIDs will be cleaned out properly, but the client isn't smart enough to realize this is happening)

When the peer wantlist reaches its size limit, overflow handling should prevent it from becoming permanently filled with unusable entries. It should account for request priority, entry age, and whether the requested block is available, so useful incoming wants can still be served while the wantlist remains bounded.
