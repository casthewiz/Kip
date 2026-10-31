# Codex (host)

Codex (the CLI and the desktop app) drives a browser through its bundled
`browser` plugin: JavaScript run in the Node REPL tool (`mcp__node_repl__js`),
against a Playwright-like client. Read the plugin's `control-in-app-browser`
skill once per session; it bootstraps the runtime:

```js
const { setupBrowserRuntime } = await import("<plugin root>/scripts/browser-client.mjs");
const agent = await setupBrowserRuntime();
const browser = await agent.browsers.get("iab");  // or "extension" for the user's Chrome
const tab = await browser.tabs.new();
```

The in-app browser (`iab`) and Chrome (`extension`) keep separate sign-ins;
the first time GitHub asks, the user signs in there.

| Operation | Call |
|---|---|
| `open(url)` | `await tab.goto(url)` |
| `find(selector)` | `tab.playwright.getByRole(role, {name})`, or `tab.playwright.locator(selector)` |
| `set_files(selector, path)` | The file chooser, below |
| `eval(js)` | `await tab.playwright.evaluate(() => …)` |
| `screenshot()` | `await tab.screenshot({})` |

`set_files` through the file chooser. This tool needs the chooser open, so
clicking the input is right here, the exception [CONTRACT.md](CONTRACT.md)
allows; the native picker never shows:

```js
const chooser = tab.playwright.waitForEvent("filechooser", { timeoutMs: 10000 });
await tab.playwright.locator("<SELECTOR>").click();
await (await chooser).setFiles(["<ABSOLUTE PATH>"]);
```

Start waiting before the click. An input with `hidden` (GitHub's comment
box) can't be clicked: `eval` `hidden = false` on it first. There's no
`locator.setInputFiles`; uploads go through the chooser only.

Limits: in Chrome, the chooser fails until the user turns on "Allow access to
file URLs" for the ChatGPT extension (`chrome://extensions` → Details); say
so and stop. If the chooser won't open, use `eval` instead (see
[CONTRACT.md](CONTRACT.md#set_files-by-eval)), for files up to about 1 MB.

Last verified: 2026-10-30, read from Codex's bundled browser plugin
(26.915.31945: `docs/api.json`, `docs/file-uploads.md`, the Chrome upload
troubleshooting). Not yet run in Codex: the first upload to a PR settles
whether clicking the unhidden input opens the chooser; update this line with
what happened.
