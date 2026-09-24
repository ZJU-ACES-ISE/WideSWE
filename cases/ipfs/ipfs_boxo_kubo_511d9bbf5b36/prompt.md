Carefully audit and reason through inline CIDs

Inspired by: https://github.com/ipfs/go-ipfs/issues/6007

I'm worried (well, more than worried, this is happening in #6007) we can accidentally:

1. Take an "inlined" block.
2. Mutate it.
3. Generate a new _really big_ CID using the ID hash function because the original inline block used it.

We may need to change our dagmodifier logic to special-case the identity hash function.

### Required behavior

Identity CIDs use multihash code `0x00` and inline data directly into the CID. Enforce a maximum identity digest size of 128 bytes consistently across CID validation, block loading, gateways, DAG mutation, and user-facing commands. Identity digests at or below 128 bytes remain valid, including identity digests below the normal minimum size for cryptographic hashes; larger identity digests must be rejected.

The `verifcid` API must expose `DefaultMinDigestSize` as 20, `DefaultMaxDigestSize` as 128, and `DefaultMaxIdentityDigestSize` as 128. `Allowlist` must provide `MinDigestSize(code uint64) int` and `MaxDigestSize(code uint64) int`. Validation failures must support `errors.Is` with `ErrDigestTooSmall` and `ErrDigestTooLarge`; the existing `ErrBelowMinimumHashLength` and `ErrAboveMaximumHashLength` names must remain compatible aliases. Errors for an oversized identity digest must clearly report the actual and maximum sizes.

`DagModifier` must preserve identity hashing while the encoded data remains within `DefaultMaxIdentityDigestSize`, then switch to the configured non-identity CID prefix, or the default IPFS hash when the configured prefix is also identity. Provide `safePrefixForSize(originalPrefix cid.Prefix, dataSize int) (cid.Prefix, bool)` with this behavior. Preserve the CID version, codec, and other applicable prefix fields when switching hashes.

Raw nodes must preserve their raw codec for modifications that can remain raw. Appending beyond a raw node so that it must grow into a multi-block file must preserve the original content by converting it to a UnixFS structure; an oversized identity CID produced by that growth must use a non-identity hash.

For Kubo commands, small inputs must continue to work with `--hash=identity`. `ipfs add` must reject identity data over 128 bytes with a `digest too large` error. `--inline-limit` may not exceed `DefaultMaxIdentityDigestSize`; a value exactly at the maximum remains valid, while an excessive value must report `inline-limit <value> exceeds maximum allowed size of 128 bytes`. Data larger than the selected inline limit should use the configured hash instead of identity. `ipfs files write` must preserve identity hashing for small results and switch to the configured hash when a mutation would exceed the identity CID limit.
