### Describe the bug

Since SvelteKit v2.21.3, when `inlineStyleThreshold` is set to a non-zero value, some CSS styles are not being applied correctly.

### Severity

Serious, but I can work around it.

### Additional Information

Setting `inlineStyleThreshold: 0` resolves the issue.

When client stylesheets are inlined, actual CSS `url(...)` references should resolve to the correct asset path according to `paths.relative`, `paths.assets`, and the configured base path. Preserve quoting, whitespace, query strings, fragments, escaped quotes or parentheses, multiple URLs, and case-insensitive `url` spelling. Do not rewrite URL-like text inside strings or comments, or URLs that are already absolute, use a protocol or data scheme, or do not correspond to the relevant Vite/static asset category.

CSS declaration values containing escaped characters must remain unchanged when parsed, so the URL text required by this workflow is preserved.
