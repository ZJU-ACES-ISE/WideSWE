Improve MCP integration with mcp-tunnel handshaking and graceful shutdown.

The server cannot reliably detect abnormal closing connection and having stale connection.

Add tunnel handshaking that sends a `handshake` message with `projectRoot` and `devServerUrl` when connected.

Define WebSocket close codes for server-forced shutdowns. Reconnect by default after an ordinary disconnection, but abort the connection and do not reconnect when the server closes it with code `4003`.

Add `McpServer` graceful shutdown in the Expo CLI integration so it closes during process shutdown and explicit closure cleans up the registered shutdown handling. Pass `projectRoot` and `devServerUrl` for handshaking.
