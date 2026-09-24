`dart-symbol-map` error: unexpected argument `--url` found.

### CLI Version

3.1.0

### CLI Command

`sentry-cli --url https://sentry.xxx.com --auth-token <redacted> dart-symbol-map upload --org sentry --project xxx-app <mapping.json> <symbols> --log-level debug`

### Expected Results

Successfully uploaded.

### Actual Results

Upload failed.

### Logs

`error: unexpected argument '--url' found`

`Usage: sentry-cli [OPTIONS] <COMMAND>`
