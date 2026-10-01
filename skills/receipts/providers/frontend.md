# frontend provider

## Handles
Claims about what a user sees or does: a component renders, an interaction
works, a layout holds, a page is accessible, no console errors.

## Requires
1. A way to serve the app: a dev-server command in `package.json` scripts,
   the project's launch config, or an already-running URL.
2. A way to drive a browser: an agent browser tool, or Playwright / Cypress
   already installed in the project.

If 1 is unmet, fall through to generic (component tests). If only 2 is
unmet, fall through to generic and note that nothing was seen in a browser.

## lite
Typecheck and build. Run existing component tests for the touched
components.

## full
Serve the app and drive the real page:
- Navigate to the page the claim is about.
- Perform the interaction the claim describes (click, type, submit).
- Assert on the resulting DOM or accessibility tree: the text, element, or
  state the claim promises. Prefer reading the page structure over
  eyeballing a screenshot.
- Read the browser console; any new error refutes "works".

## ultra
Everything in full, plus:
- Falsifiability: revert the change, reload, confirm the assertion fails,
  restore.
- Mobile width (375px) and desktop; check nothing overflows or disappears.
- Keyboard-only path through the interaction; an accessibility scan (axe or
  the browser's audit) on the page, with no new violations.
- Empty, loading, and error states of the touched UI, not just the happy
  path.
- Light and dark theme, if the app has both.

## Observation
URL, the steps taken, the assertion and what the page actually contained,
console error count, and a screenshot path when the claim is visual.
