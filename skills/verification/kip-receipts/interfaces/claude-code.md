# Claude Code (host)

Claude Code and the Claude desktop app reach a browser two ways. Prefer
Claude in Chrome: it can set file inputs directly.

## Claude in Chrome

The user's own Chrome, through the extension (tools `mcp__claude-in-chrome__*`).
If they're deferred, load them in one `ToolSearch`:
`select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__find,mcp__claude-in-chrome__read_page,mcp__claude-in-chrome__file_upload,mcp__claude-in-chrome__javascript_tool,mcp__claude-in-chrome__computer`.

| Operation | Tool call |
|---|---|
| `open(url)` | `tabs_context_mcp` (once), then `navigate` |
| `find(selector)` | `find` (by role or label; returns a `ref`), or `read_page` |
| `set_files(selector, path)` | `file_upload` with the input's `ref` and the absolute path |
| `eval(js)` | `javascript_tool` |
| `screenshot()` | `computer` with action `screenshot` |

Limits: `file_upload` takes at most 10 MB per call and reads only this
session's folders (working directory, scratchpad); copy other files there
first.

## Built-in browser pane

The desktop app's own browser (tools `mcp__Claude_Browser__*`): `navigate`,
`find`, `javascript_tool`, `computer`. It has no file tool, so `set_files`
goes through `eval` (see [CONTRACT.md](CONTRACT.md#set_files-by-eval)), for
files up to about 1 MB. Its sign-ins are separate from Chrome's.

Last verified: 2026-10-19, Claude in Chrome on casthewiz/Kip#10: `file_upload`
put a PNG in GitHub's comment box and `javascript_tool` read back the URL.
The built-in pane's `eval` route hasn't been run yet.
