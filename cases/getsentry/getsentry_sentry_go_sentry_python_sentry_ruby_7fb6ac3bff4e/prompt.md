Handle HTTP 413 responses for oversized envelopes.

Handle the `RequestEntityTooLarge` (413) response from Relay and log a specific message so that users can understand envelope size rejections. Previously, oversized envelopes returned HTTP 400 from Relay. Now that Relay returns 413, the SDK can distinguish size-related rejections from other errors.

When downstream envelope size limits are exceeded, log a specific error/debug/warning message. For 413 responses, use the message `HTTP 413: Envelope dropped due to exceeded size limit` where applicable, including the response body if available.

Record client reports with reason `send_error` for each dropped item so Sentry can track data loss. Do not retry on 413; the data is definitively too large.
