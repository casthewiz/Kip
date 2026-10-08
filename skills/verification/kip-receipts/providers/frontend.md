# frontend provider

## Handles
Claims about what a user sees or does on a website: a page renders, an
interaction works, a layout holds at every viewport, a page is accessible,
no console errors. The site can be anything reachable by URL: a local dev
server, a preview deploy, staging, prod. It doesn't matter who built it.

## Requires
1. A URL for the site under test. For local code, the project's dev-server
   command (`package.json` scripts, launch config) started first.
2. Playwright: Kip's install in `~/.kip` (`sh playwright/setup.sh` once,
   after the user agrees to the download), or the project's own Playwright
   if the claim is about its existing e2e suite.

If 1 is unmet, fall through to generic (component tests). If 2 is unmet and
the user declines the install, fall back to the `browser` surface: an agent
browser tool with `observe` and attached screenshots, and say the evidence
is observed, not asserted.

## lite
Typecheck and build. Run existing component tests for the touched
components. Or, for a remote site, one `desktest.py` run at `desktop` only.

## full
Write a spec for the claim in the run's `specs/` folder (start from
[`playwright/example.spec.ts`](../playwright/example.spec.ts)) and desk-test
it across all three viewports:

```bash
P=~/.kip/skills/kip-receipts/playwright
cp $P/example.spec.ts "$(python3 $R path)/specs/c1.spec.ts"   # then edit it
python3 $P/desktest.py c1 http://localhost:3000 "$(python3 $R path)/specs/c1.spec.ts"
```

Each configured viewport (by default mobile 390×844, tablet 820×1180,
desktop 1440×900; a user or project config can change them under
`evidence.frontend.viewports`) becomes its own evidence row, so a layout that breaks only on mobile shows up as
exactly that. The spec should:
- Navigate with relative paths (`BASE_URL` comes from the command).
- Perform the interaction the claim describes.
- Assert the outcome through roles, labels, and text (`getByRole`,
  `getByLabel`, `getByText`), not CSS selectors or screenshots.
- Fail on console errors and horizontal overflow.

## ultra
Everything in full, plus:
- Falsifiability: `desktest.py --red` against a baseline that lacks the
  behavior (the base branch's dev server, prod when the change is on
  staging). Scope it with `--viewports` to where the baseline is actually
  broken: a mobile-only layout bug passes on desktop, so `--red` there
  would correctly report "check can't fail".
- Keyboard-only path through the interaction, and an accessibility scan
  (`@axe-core/playwright` if installed) with no new violations.
- Empty, loading, and error states of the touched UI, not just the happy
  path.
- Light and dark theme, if the app has both (`page.emulateMedia`).

## Observation
The exact command per viewport (recorded by `receipt.py run`), the
Playwright list output, and the screenshots. Use `observe` only for checks
done in an agent browser tool, and attach the screenshot.

## Files
Collected automatically by `desktest.py`: an end-state screenshot per test
per viewport at every level, plus a video and a Playwright trace (`.zip`,
open with `npx playwright show-trace`) at ultra.
