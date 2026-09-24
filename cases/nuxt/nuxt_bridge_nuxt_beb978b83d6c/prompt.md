Cookie default value not set if `useCookie` only invoked in CSR

### Reproduction

On startup you'll see that only the `xx-test-universal-cookie` cookie gets written. The 'xx-test-csr-cookie' one never does, you can reload the page many times and it still wont happen.

### Describe the bug

It appears that if a `useCookie` invocation w/ a default value set (e.g. `useCookie('xxx', {default: () => 'test'})`) only happens on the client-side (e.g. if wrapped in process.client or if it happens in a client-only plugin), the default value never gets written to the cookie - the cookie doesn't get defined.

### Additional context

What should happen is the cookie default value should always be set on `useCookie` invocation irregardless of whether it happens in SSR or CSR.

Also ensure the initial cookie value is written in the browser if it has expired or the default cookie value is being used.

This client-side default-value behavior should also hold when `useCookie` is configured with `watch: false`, `watch: true`, or `readonly: true`.
