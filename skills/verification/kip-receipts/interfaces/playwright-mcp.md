# Playwright MCP (host)

Any agent with Microsoft's Playwright MCP server (`@playwright/mcp`)
connected. Its browser has its own sign-ins.

| Operation | Tool call |
|---|---|
| `open(url)` | `browser_navigate` |
| `find(selector)` | `browser_snapshot` (refs by role and name) |
| `set_files(selector, path)` | `browser_click` the file input (this server expects the chooser open), then `browser_file_upload` with `paths: ["<ABSOLUTE PATH>"]` |
| `eval(js)` | `browser_evaluate` with `function: "() => <JS>"` |
| `screenshot()` | `browser_take_screenshot` |

Last verified: never run in Kip; written from the server's tool list.
Update this line after the first intent runs here.
