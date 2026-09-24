Move OpenAPI model naming from a reflection based approach to a declarative approach.

`OpenAPIModelName()` receiver functions to access OpenAPI model names of API types should be generated into files such as `zz_generated.model_name.go`. This allows API authors to declare the desired OpenAPI model packages when the model name derived from the go package path is not desirable. Expose this through `--output-model-name-file`: when specified, generate the named accessor file and use the generated names for OpenAPI schema references; when empty, generate no accessor file and continue inferring names from Go types.

Support `+k8s:openapi-model-package`, for example `+k8s:openapi-model-package=io.k8s.api.core.v1`, and generate receiver functions such as `func (in Pod) OpenAPIModelName() string` that return `io.k8s.api.core.v1.Pod`.

Migrate the Kubernetes API to use this generator while keeping all OpenAPI model names the same.
