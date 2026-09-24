Add `paddle.compat.pad` with behavior compatible with `torch.nn.functional.pad`, and add the matching PaConvert conversion support.

Support the PyTorch-style signature `input`, `pad`, `mode`, and `value`, including `constant`, `reflect`, `replicate`, and `circular` padding behavior. Padding should start from the last dimension; `pad` may be a Tensor, list, or tuple, and an empty padding specification should leave the input unchanged. Non-constant padding should support 2D through 5D inputs and at most the last three dimensions, while constant padding should support compatible one- and multi-dimensional padding. Preserve behavior in dynamic and static execution and through gradient computation.

Keep `paddle.compat.pad` strict to the compatible signature and reject Paddle-only keywords such as `x`, `name`, `data_format`, and `pad_from_left_axis`. Add unit tests for the compatible Paddle API.

Update PaConvert conversion rules and tests for `torch.nn.functional.pad` so converted code uses the compatible Paddle API.
