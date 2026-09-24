Fix pin operations hanging under pinned reprovide strategies and the Pebble panic during shutdown.

With `Provide.Strategy` set to `pinned`, `roots`, or `pinned+mfs`, including `+unique` and `+entities` variants, the reprovider streams the pin index to decide what to announce. `streamIndex` holds `pins.lock.RLock` for the channel's full lifetime, so when the reprovider drains the channel at DHT speed, the lock stays held for hours and starves `Pin`, `Unpin`, and `Flush` writers behind `sync.RWMutex` writer preference.

This was observed on a collab cluster while testing v0.41.0-rc2. Concurrent `ipfs pin ls` and `ipfs add` requests piled up behind the queued writer, and `ipfs add` waited many hours for the lock. The default `Provide.Strategy=all` is not affected, but nodes using pin-based strategies can encounter the stall.

Pebble also logs a panic during shutdown:

```text
Received interrupt signal, shutting down...
{"logger":"provider","msg":"provider keystore sync failed","err":"keystore is closed","strategy":"pinned+mfs+entities"}
panic: pebble: closed
...
github.com/ipfs/boxo/pinning/pinner/dspinner.(*pinner).snapshotIndex
github.com/ipfs/boxo/pinning/pinner/dspinner.(*pinner).streamIndex.func1()
```

Pin-touching operations should not remain blocked while a reprovide cycle processes a large pinset, and interrupting pin index streaming during shutdown should not access a closed datastore or panic.

If the backing datastore closes during streaming, the stream must terminate with a single error that identifies the pin stream as interrupted and indicates that shutdown is the likely cause. A stream started with an already-cancelled context must terminate promptly with cancellation and must not access the datastore.

During daemon shutdown, expected `keystore is closed` or `context canceled` interruptions from provider keystore synchronization must not be logged at Error level. A concurrent `ipfs pin ls --stream` must return promptly without observing a daemon panic; if it exits unsuccessfully, it must provide a meaningful error message.
