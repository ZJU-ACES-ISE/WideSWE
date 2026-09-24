We've decided to rename the default model and default inference endpoint for ELSER running EIS:

- Rename the model from `elser-v2` to `elser_model_2`.
- Rename the default inference endpoint from `.elser-v2-elastic` to `.elser-2-elastic`.

Multiple other places depend on these new names, so this should be treated as a bug fix.

The default ELSER sparse-embedding configuration and the advertised default model information must expose these renamed identifiers consistently.

Consumers that identify ELSER EIS by its default inference endpoint must recognize `.elser-2-elastic`. When that endpoint is selected for product-document search, the requested products must continue to resolve to their individual index names.
