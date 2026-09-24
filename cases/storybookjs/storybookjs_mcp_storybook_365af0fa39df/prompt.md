Add testing toolset.

This introduces a testing toolset to `addon-mcp`. The toolset includes a `run-story-tests` tool that, when called, uses the existing `addon-vitest` configuration to run tests and report results back to the agent. If `addon-a11y` is configured, a11y violations will be reported to the agent. This can be disabled by an `a11y: false` input to the tool, based on the user's instructions to the agent.

The `run-story-tests` tool accepts an optional `stories` array for focused runs; omit it to run all available stories. Each selected story requires `exportName` and `absoluteStoryPath`, with optional `explicitStoryName`, `props`, and `globals`. Concurrent calls should run in sequence, and inputs that match no stories should return a clear error.

Add a new programmatic trigger to `addon-vitest`, which allows external actors like `addon-mcp` to trigger a test run and get results back via events on the channel. Expose `TRIGGER_TEST_RUN_REQUEST`, `TRIGGER_TEST_RUN_RESPONSE`, `TriggerTestRunRequestPayload`, `TriggerTestRunResponsePayload`, and `TestRunResult` through the addon-vitest constants API. Requests identify the caller and may select story IDs or override test configuration; responses correlate by request ID and report a completed, error, or cancelled result.

The test triggering flow must work without opening the manager UI first. Get the index directly from `StoryIndexGenerator` on the server and subscribe to changes so that when tests are triggered, the index is already available and up-to-date. Focused runs should report only the selected stories, while retaining child test results that belong to a selected parent story.
