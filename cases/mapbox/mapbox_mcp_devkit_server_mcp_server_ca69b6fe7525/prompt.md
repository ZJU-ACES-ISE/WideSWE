Fix blank MCP Apps map preview panels in Claude Desktop.

Claude Desktop applies a strict `frame-src 'self' blob: data:` CSP to all MCP Apps panels. The GeoJSON Preview, Style Preview, and Style Comparison tools previously rendered content inside inner `<iframe>` elements pointing to external URLs such as `geojson.io`, `api.mapbox.com`, and `agent.mapbox.com`, which are blocked and leave panels blank.

These panels should display map previews in MCP Apps hosts without relying on blocked external iframes. GeoJSON Preview should show GeoJSON data on a map with fitted bounds and a graceful fallback when a token cannot be generated. Style Preview should show the user's style and a readable style name. Style Comparison should show two synced maps with a draggable reveal slider and style names.

All panels should support fullscreen behavior, an open-in-browser action, the MCP Apps initialization handshake, and compatibility with Claude Desktop, VS Code, and Goose.

Keep the existing browser-link and MCP-UI result compatibility for the preview tools. In particular, GeoJSON Preview should return a `https://geojson.io/next/?data=` URL with the GeoJSON encoded in the query parameter together with its MCP-UI resource.

Apply the same MCP Apps compatibility to static map image previews. The static map tool should return the generated Mapbox Static Images API URL directly as text instead of fetching and base64-encoding the image. The URL must preserve the requested style, map position, dimensions, pixel density, access token, and supported overlays. In MCP Apps-capable operation, return the URL together with the interactive UI resource; when MCP-UI is disabled, return only the URL. Existing default-style and overlay behavior must remain compatible.
