Remove support for warnings reports from the generic Jenkins MCP server plugin and provide the MCP tool for warnings in the Warnings NG plugin.

The warnings report support was originally implemented as “Support for warnings report” and then reverted “with the intention to move it into this plugin.” The generic MCP server must no longer expose `getWarnings` as a built-in tool; it remains the extension host for the tool provided by Warnings NG.

The tool should retrieve warnings from static analysis tools associated with a Jenkins build. Keep the MCP tool name `getWarnings`; inputs include `jobFullName`, optional `buildNumber` (use the last build when omitted), and optional `checkId` (return all checks when omitted). Return warnings grouped by check ID. Each warning entry contains exactly `category`, `message`, `type`, `severity`, `fileName`, and `line`.

If the job does not exist, the selected build has no warnings, or `checkId` does not match a check, return a successful no-results response rather than an error.

The dependency on the MCP server should be optional, so it should not increase the API surface for people who did not install the MCP server plugin.
