# Interfaces

Kip splits work that runs through a browser into two parts:

- An **intent** says what to get done, without naming a surface: "attach
  this run's files to the PR". It lives in the skill or provider that needs
  it (like [kip-receipts-github](../kip-receipts-github/SKILL.md)) and is
  written only in the browser operations below.
- An **interface** says how one surface does it, and lives here, one file
  per surface. An agent host (where the agent runs) supplies the operations:
  [claude-code](claude-code.md), [cursor](cursor.md), [codex](codex.md),
  [playwright-mcp](playwright-mcp.md).
  A site (what the agent works on) supplies its URLs, selectors, and results:
  [github](github.md).

To carry out an intent, read the host interface for the surface you're
running on and the site interface the intent names, then do each step with
the host's tools. A surface that's missing here is added once and reused by
every intent.

## Browser operations

| Operation | Does |
|---|---|
| `open(url)` | Load a page in the agent's browser, signed in as the user |
| `find(selector)` | Locate an element, by role or label first, then the site's selector |
| `set_files(selector, path)` | Put a local file into an `<input type=file>` without opening the picker |
| `eval(js)` | Run JavaScript in the page and return its JSON result |
| `screenshot()` | Capture what the page shows |

Rules for every surface:

- **Never click a file input**: it opens a native picker the agent can't
  use (unless the interface says its tool needs the chooser open).
- **Never type credentials.** A sign-in page means stop and ask the user to
  sign in in that browser; the browser keeps the session afterwards.
- **Paths are absolute** and must be readable by the browser's tool; some
  only read the session's folders, so copy files there first.

## `set_files` by `eval`

A host whose tools can run JavaScript but can't set a file input can still
do it, for files up to about 1 MB (the bytes travel in the tool call):
base64 the file (`base64 -i FILE`), then `eval`:

```js
const b = atob("<BASE64>"), u = new Uint8Array(b.length);
for (let i = 0; i < b.length; i++) u[i] = b.charCodeAt(i);
const dt = new DataTransfer();
dt.items.add(new File([u], "<NAME>", {type: "<MIME TYPE>"}));
const input = document.querySelector("<SELECTOR>");
input.files = dt.files;
input.dispatchEvent(new Event("change", {bubbles: true}));
```

## Adding a surface

Write `<surface>.md`, named like the host in Kip's config (`kip.py config`).
Never `claude.md` or `agents.md`: hosts load those as instructions, and
macOS ignores case. A host maps each operation to its tool calls, with
limits and how to load the tools. A site gives the URLs, selectors, and
results its intents rely on. End with a **Last verified** line (date, what
ran, where), and update it whenever an intent runs end to end there.
