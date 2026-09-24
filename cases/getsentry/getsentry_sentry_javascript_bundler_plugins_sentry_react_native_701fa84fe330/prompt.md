Auto-inject Sentry React Native labels from static JSX text.

Add an opt-in `autoInjectSentryLabel` option to `@sentry/babel-plugin-component-annotate` that automatically extracts static text from JSX children and injects a `sentry-label` attribute on the root element. This enables React Native apps to get meaningful touch breadcrumb labels, for example "Save workout" or "Add to cart", without manual annotation.

The feature walks the JSX tree up to 3 levels deep, collecting text from recognized text components, defaulting to `Text` and `text`, and bails out entirely if any dynamic content is found anywhere in the subtree. It supports nested text components. When the root is a fragment, use the first JSX element child as the injection target; fragments are transparent wrappers and do not consume the text-search depth budget. Labels exceeding 64 characters are truncated with `...`, and `textComponentNames` is a configurable list of component names whose children are treated as text content.

Pass `autoInjectSentryLabel: true` to `@sentry/babel-plugin-component-annotate` by default when `annotateReactComponents` is enabled in the Metro config. Users can opt out with `annotateReactComponents.autoInjectSentryLabel: false`.

The extracted `sentry-label` should improve touch breadcrumb and user interaction span labeling. Buttons like `<Pressable><Text>Save workout</Text></Pressable>` should automatically get labeled "Save workout" in breadcrumbs and transaction names.
