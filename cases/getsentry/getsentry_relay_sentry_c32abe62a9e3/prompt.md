Extend performance profiles to V2 spans.

This makes performance profiles able to also match V2 spans by turning the browser name conditions into disjunctions checking two different fields.

This is required to enable performance score calculation for V2 spans.

Calculate performance scores for V2 spans.

Make performance score calculation access `Attributes` in addition to `Measurements` so it can be applied to V2 spans.

The conditions for performance profiles need to be adjusted so that they match the browser name in either `event.contexts.browser.name` (the status quo, for legacy events/spans) or `span.attributes.browser.name.value` (for V2 spans).
