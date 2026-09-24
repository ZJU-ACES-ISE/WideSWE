All well-known types should have `metadata.generation` and `status.observedGeneration`. HPA doesn't have that.

Generate `metadata.generation` and `status.observedGeneration` fields in HorizontalPodAutoscaler resources.

A follow-up to consider is whether we should add `ObservedGeneration` to `HorizontalPodAutoscalerCondition` and populate that, consistent with `metav1.Condition`.

HorizontalPodAutoscaler conditions allow optionally including the `observedGeneration` at the time the condition was recorded.

Doing to VPA what Jordan suggested we do to HPA. This also moves us closer to the standardised conditions.

VPA now stores a `observedGeneration` in both status and condition fields.

## Required behavior

* Add the `HPAGeneration` feature gate as Beta and enabled by default for Kubernetes 1.37. When enabled, an HPA starts with `metadata.generation` 1 and increments it when its spec changes; when disabled, the strategy must not initialize or increment generation.
* When `HPAGeneration` is enabled, the HPA controller sets `status.observedGeneration` to the HPA's current `metadata.generation`.
* `HorizontalPodAutoscalerCondition.observedGeneration` is an optional `int64` value. Nil and non-negative values are valid, while negative values must be rejected during status validation.
* VPA status exposes optional `observedGeneration` and the recommender records the VPA generation it processed. VPA conditions also expose a non-negative `observedGeneration`.
* Keep these fields consistent across API types, conversions, serialization, generated schemas/apply configurations, and VPA CRDs.
