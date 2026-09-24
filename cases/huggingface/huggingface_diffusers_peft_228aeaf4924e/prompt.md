`exclude_modules` won't consider the module names in `target_modules`.

The problem is that if `target_modules` includes a module name where a substring from `exclude_modules` is present, the module from `target_modules` becomes invalid for PEFT. For example, if `target_modules` has `single_transformer_blocks.0.proj_out` and `exclude_modules` has `proj_out`, loading adapter weights produces unexpected keys:

```text
Loading adapter weights from state_dict led to unexpected keys found in the model: single_transformer_blocks.0.proj_out.lora_A.default_0.weight, single_transformer_blocks.0.proj_out.lora_B.default_0.weight, ...
```

The adapter checkpoint should load without unexpected keys even when a full target module name contains a substring listed in `exclude_modules`. The adapter layers represented by the checkpoint, including their expected keys and shapes, should remain available after loading.

Reproducer:

```python
from diffusers import DiffusionPipeline
import torch

pipeline = DiffusionPipeline.from_pretrained(
    "black-forest-labs/FLUX.1-dev", torch_dtype=torch.bfloat16
).to("cuda")
pipe_kwargs = {
    "prompt": "{trigger_word} A cat holding a sign that says hello world",
    "height": 1024,
    "width": 1024,
    "guidance_scale": 3.5,
    "num_inference_steps": 28,
    "max_sequence_length": 512,
}
pipeline.load_lora_weights("glif/l0w-r3z")
image = pipeline(**pipe_kwargs).images[0]
```

The shared adapter-injection contract must allow `inject_adapter_in_model()` to receive an optional `state_dict` and use its keys as the reference for which adapter layers to target; the values are not used to populate model weights. Without a `state_dict`, existing behavior must remain unchanged.

This contract must work across supported non-prompt-learning adapter types and model families, including irregular layer-specific targets, and remain compatible with `low_cpu_mem_usage`. If the PEFT config and `state_dict` identify different target modules, the mismatch must produce a warning while injection follows the layers represented by the `state_dict`. A config using `target_parameters` must be rejected for `state_dict`-based injection because the keys cannot distinguish parameter targets from module targets.
