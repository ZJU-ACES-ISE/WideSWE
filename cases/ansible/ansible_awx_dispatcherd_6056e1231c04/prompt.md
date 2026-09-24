Right now, it looks like logs will respect the `dynamic_level_filter`, and this is a bad thing and was never intended. Doing this means talking to Postgres, which has no async version, so it will likely create a new synchronous connection in asyncio code.

The configured log level should be applied when the dispatcherd process starts without requiring asynchronous logging code to consult Postgres. Runtime log-level changes must continue to reach the running process and take effect in memory. The cross-process control contract uses `set_log_level` with a requested `level`.

The `set_log_level` control operation must accept standard named logging levels case-insensitively as well as valid integer levels. Invalid types or unknown levels must return an error without changing the active logger level; successful requests should report the previous and resulting levels.

The operation must be available through the control CLI with a required `level` argument, and each control command must receive only the arguments defined for that command.
