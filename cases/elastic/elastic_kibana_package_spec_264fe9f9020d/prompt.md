Add manifest-driven variable groups and Fleet UI support for selecting them.

Add a new `var_groups` field to the package manifest schema that defines mutually exclusive groups of variables, controls variable visibility based on the selected option, stores the selection in the policy for backend processing, and uses `additionalProperties: true` on options to allow feature-specific extensions. Support `var_groups` at both package and data-stream levels from package specification format `3.6.0`.

A variable group defines `name`, `title`, `selector_title`, optional `description` and `required`, and one or more `options`. Each option defines `name`, `title`, optional `description`, its `vars`, and optional `hide_in_deployment_modes` values of `default` or `agentless`. Policy-template inputs may use `hide_in_var_group_options` to hide named options for a group.

Validate that group names are unique, option names are unique within a group, and every referenced variable exists in the applicable package, policy-template, input, or stream variables. Variables controlled by a group must not set `required: true` themselves: when the group is required, requirement is inferred for variables in the selected option; when it is optional, the group variables remain optional.

Implement `var_groups` recognition and the `VarGroupSelector` component. Variable groups allow package authors to define mutually exclusive sets of variables, enabling users to choose between different configuration options. Show only variables for the selected option, preserve saved selections, and store `var_group_selections` at both package and stream levels in Fleet package policies.

When there is no saved selection, select the first option visible for the current deployment mode and input. Respect `hide_in_deployment_modes` and `hide_in_var_group_options`, and do not show the selector when only one option remains visible. Required validation and required indicators apply only to variables in the selected option; variables outside the selected option are hidden and skipped, while variables not controlled by any group keep their existing behavior.
