Bound `hf-mount` sidecar memory under crawler workloads and expose user-tunable controls for HF CSI volumes.

Under crawler workloads such as `find` or doc scrapers, the `hf-mount` sidecar could grow without limit and be OOM-killed because every path the kernel ever looked up stayed resident. Container memory limits alone are not enough: under cgroup-only pressure the kernel's dentry shrinker does not fire, so forget-driven eviction never triggers.

Add user-tunable `volumeAttributes` on HF CSI volumes to bound the resources consumed by the injected `hf-mount` sidecar. Include container resource controls such as `memoryLimit`, `memoryRequest`, `cpuLimit`, and `cpuRequest`, and in-process inode-table controls such as `inodeSoftLimit` and `lruSweepIntervalMs`.

Expose the corresponding `hf-mount` options as `--inode-soft-limit <N>` (default `0`, disabled) and `--lru-sweep-interval-ms <MS>` (default `5000`, only meaningful when the soft limit is enabled). The inode-table controls should evict old entries and keep memory bounded without relying only on container memory limits.

Keep the existing sidecar defaults of `cpu: 10m`, `memory: 32Mi`, and no resource limits until users opt in. The sidecar is shared by all HF CSI volumes in a pod, so for conflicting container-resource attributes the maximum value across volumes wins, independent of volume order. Invalid Kubernetes quantity strings are dropped and must not shadow valid values from another volume. If a request exceeds its limit, clamp the request down to the limit rather than rejecting pod admission.

Preserve safety for dirty files, pending deletes, local mkdir/symlink entries, inodes with open FUSE handles, the root inode, and directories with cached children.
