HTTP Routing V1 provider records contain raw `0.0.0.0` listen addresses instead of resolved interface addresses.

### Description

When Kubo sends provider records to an HTTP router via `PUT /routing/v1/providers`, the `Addrs` field in the request body contains the raw `Addresses.Swarm` listen addresses, such as `/ip4/0.0.0.0/tcp/24199`, instead of resolved interface addresses.

`ipfs id` correctly resolves `0.0.0.0` to actual network interface addresses, but the HTTP Routing V1 code path does not perform this resolution before sending provider records.

### What I was doing

1. Started a fresh Kubo node with default swarm config (`0.0.0.0` bind addresses).
2. Configured `Routing` to use an HTTP router, using a local mock server that records requests.
3. Added content via `ipfs add`.
4. Triggered `ipfs routing provide <cid>`.

### What I expected

The `PUT /routing/v1/providers` request sent to the HTTP router should contain resolved interface addresses, matching what `ipfs id` reports:

```text
/ip4/192.168.x.x/tcp/24199
/ip4/172.17.0.1/tcp/24199
/ip4/127.0.0.1/tcp/24199
/ip4/192.168.x.x/udp/24199/quic-v1
...
```

### What actually happened

The provider records sent to the HTTP router contain the raw unresolved listen addresses:

```text
/ip4/0.0.0.0/tcp/24199
/ip6/::/tcp/24199
/ip4/0.0.0.0/udp/24199/quic-v1
/ip6/::/udp/24199/quic-v1
/ip4/0.0.0.0/udp/24199/quic-v1/webtransport
/ip6/::/udp/24199/quic-v1/webtransport
/ip4/0.0.0.0/udp/24199/webrtc-direct
/ip6/::/udp/24199/webrtc-direct
```

At the same time, `ipfs id` correctly reports resolved addresses such as `/ip4/192.168.x.x/tcp/24199` and `/ip4/172.17.0.1/tcp/24199`.

### Impact

Any client that discovers a provider via an HTTP router receives useless `0.0.0.0` addresses and must fall back to a DHT `FIND_PEER` to locate the actual peer, defeating the purpose of the HTTP router.

HTTP routing provider records must use addresses resolved at provide-time instead of static configuration values captured at daemon startup. Prefer AutoNAT V2 confirmed reachable addresses when available, falling back to configured `Addresses.Swarm` addresses when they are not. `Addresses.Announce` remains a full static override; `Addresses.AppendAnnounce` is appended to dynamic, fallback, and `Addresses.Announce` results; and `Addresses.NoAnnounce` continues to filter `Addresses.Swarm` fallback addresses.

The HTTP routing client must support this provide-time behavior through a public `WithProviderInfoFunc` option whose callback supplies the current provider addresses whenever a provide request is made. Addresses returned by the callback must be included in the provider request. The existing static `WithProviderInfo` path must remain available when no callback is configured.
