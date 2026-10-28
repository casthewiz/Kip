---
name: kip-kubrick
description: >
  Captures screenshots and screen recordings of a website with Playwright the
  same way every time, as receipts evidence: a named shot taken after the
  assertions that prove the page is ready, animations finished, full height
  even when the app scrolls inside a container, one per configured viewport;
  recordings at the viewport's own size, finished before they're collected,
  and converted to an MP4 GitHub plays inline. Works inside a desk-test spec
  (`shot()`) or with no spec at all (`kubrick.py shot|record CLAIM URL`). Use
  whenever a claim needs a screenshot or video of a site, and whenever the user
  says "screenshot", "screen recording", "record a video", "capture the page",
  "show me what it looks like", or "kubrick". Do NOT use for screenshots of the
  agent's own browser pane taken to look at a page (that's the host's browser
  tool), or for native apps and simulators.
license: MIT
---

# Kubrick

Every capture comes out the same way: same name, same size, same moment, so
two runs of the same page can be compared and a reviewer sees what the check
proved. Kubrick makes captures; [kip-receipts](../kip-receipts/SKILL.md)
records and publishes them.

Playwright runs from `~/.kip` on Kip's pinned Node: run
`bash ~/.kip/skills/kip-receipts/playwright/setup.sh` once, after the user
agrees to the download.

```bash
K=~/.kip/skills/kip-kubrick
R=~/.kip/skills/kip-receipts/receipt.py
```

## Steps

1. **Pick the path.** The claim is about behavior (a click, a form, a
   layout) → a desk-test spec with `shot()`, step 2. The claim only needs to
   show a page as it loads (a deploy, a before/after) → no spec, step 3.
2. **In a spec**, assert first, then shoot:

   ```ts
   import { shot } from 'kip-kubrick/kubrick';   // desktest.py puts it on NODE_PATH
   await expect(page.getByText('Check your inbox')).toBeVisible();
   await shot(page, 'confirmation');              // <test output>/confirmation.png
   ```

   Run it with `desktest.py` (see kip-receipts' frontend provider): every
   configured viewport is its own evidence row with its shots collected.
   `shot(page, name, { full: false })` captures just the viewport.
3. **Without a spec**, per configured viewport:

   ```bash
   python3 $K/kubrick.py shot c4 https://staging.example.com --viewports mobile,desktop
   python3 $K/kubrick.py record c4 https://staging.example.com --viewports desktop
   ```

   `shot` loads the page and takes one full `shot()`. `record` records the
   page loading and scrolling to the bottom, at the viewport's size.
4. **Video for GitHub**: convert first, then upload with
   [kip-receipts-github](../kip-receipts/kip-receipts-github/SKILL.md).

   ```bash
   # H.264, ≤1280 wide, <10 MB; OUT where the browser tool can read it (the session's scratchpad)
   python3 $K/kubrick.py mp4 "$(python3 $R path)/files/<video>.webm" <scratchpad>/c4.mp4
   python3 $R attach c4 <scratchpad>/c4.mp4
   ```

5. **Publish the latest capture per viewport**, not every rerun: an ultra
   run makes dozens of files.

## Rules

- **Wait on the page, not the clock.** `expect(...).toBeVisible()` on the
  thing the claim is about, then `shot()`. A `waitForTimeout` is only for
  what has no DOM signal (a chart animating in with JavaScript), and says
  so in a comment.
- **Name every shot.** Playwright's own end-state shot is `test-finished-1.png`
  on a pass and `test-failed-1.png` on a failure; a `shot()` name is the same
  either way, so look for it.
- **Never `page.screenshot` directly** in a spec: it skips the animation
  freeze and the scroll-container growth.
- **Measure in one `page.evaluate`** on a page that refreshes itself, so the
  DOM can't be rebuilt between reads.
- **Overflow inside a panel** doesn't show in `document.scrollWidth`; check
  the panel's own `scrollWidth` when the claim is about it.
- **Sign-in**: use the project's own test helpers (a seeded session, stubbed
  APIs) or a token the user gives you. Never type credentials into a page.
- **Viewports come from Kip's config** (`evidence.frontend.viewports`), not a
  size picked per run.

## Limits

- `shot()` grows the viewport by the deepest scrolling container's hidden
  height, so the layout has to stretch with the viewport (`100vh`, flex);
  nested scrollers are missed.
- Animations driven by JavaScript (`requestAnimationFrame`, chart libraries)
  aren't frozen; only CSS animations and transitions are.
- `record` scrolls the document; an app that scrolls inside a container
  records its first screen.

## Files

| File | What |
|---|---|
| [kubrick.ts](kubrick.ts) | `shot(page, name, { full })` for specs |
| [kubrick.py](kubrick.py) | `shot`, `record` (through `desktest.py`), `mp4` |
| [shot.spec.ts](shot.spec.ts), [record.spec.ts](record.spec.ts) | The specs `kubrick.py shot` and `record` run |
| [kubrick.spec.ts](kubrick.spec.ts) | `shot()`'s self-check in a real browser |
