# Cursor (host)

Cursor's agent drives its built-in browser with the `browser_*` tools
(the bundled cursor-browser-automation extension). The browser keeps its
own sign-ins: the first time a site asks, the user signs in there.

| Operation | Tool call |
|---|---|
| `open(url)` | `browser_navigate` |
| `find(selector)` | `browser_snapshot` (refs by role and name), or `eval` with `document.querySelector` |
| `set_files(selector, path)` | Three `browser_cdp` calls, below |
| `eval(js)` | `browser_cdp` with method `Runtime.evaluate`, params `{"expression": JS, "returnByValue": true, "awaitPromise": true}`; the value is at `result.value` |
| `screenshot()` | `browser_take_screenshot` |

`set_files` with `browser_cdp`:

1. `DOM.getDocument`, params `{"depth": 0}`: note `root.nodeId`.
2. `DOM.querySelector`, params `{"nodeId": <root>, "selector": "<SELECTOR>"}`:
   note `nodeId`.
3. `DOM.setFileInputFiles`, params `{"nodeId": <input>, "files": ["<ABSOLUTE PATH>"]}`.

The input fires `change` as if a person picked the file. If `browser_cdp`
refuses `DOM.setFileInputFiles`, use `eval` instead (see
[CONTRACT.md](CONTRACT.md#set_files-by-eval)).

Limits: `browser_cdp` refuses `Input.*`, cookie, storage, permission,
download, target, and browser-wide methods; `browser_fill` and
`browser_type` refuse file inputs. Node ids go stale when the page
re-renders, so run the three calls back to back.

Last verified: 2026-10-19, read from Cursor's bundled extension (tool names,
`browser_cdp`'s schema and refusals). Not yet run in Cursor: the first
upload there settles whether `DOM.setFileInputFiles` is allowed; update this
line with what happened.
