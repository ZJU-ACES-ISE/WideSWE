Add generator support for probabilistic sampling across TensorDict and TorchRL.

There is currently no way to make sampling from a `ProbabilisticTensorDictModule` or `ProbabilisticActor` deterministic without touching the global `torch.manual_seed` state. Add a keyword-only `generator` argument to `ProbabilisticTensorDictModule.__init__` so the agent's RNG stream can be isolated from the environment's.

Accept a `torch.Generator`, an `int` shorthand for `torch.Generator().manual_seed(int)`, or a `NestedKey` (`str` or `tuple`) that fetches the generator from the input tensordict at every call. For the tensordict-key form, support both `torch.Generator` values and scalar `int` / `Tensor` stream keys, and write a fresh `next_seed` back to the same key.

Apply the generator to both `sample` and `rsample` paths, including discrete distributions, while leaving the global RNG state unchanged. The generator state should advance only when sampling occurs; MODE/DETERMINISTIC paths must leave it untouched. Preserve the existing behavior when `generator=None`, and raise `TypeError` for unsupported generator values.

Forward the `generator` kwarg through TorchRL so `ProbabilisticActor(..., generator=...)` works. Add the `generator` kwarg to `SafeProbabilisticModule.__init__`, forward it to `ProbabilisticTensorDictModule`, and document the option on `SafeProbabilisticModule` and `ProbabilisticActor`.
