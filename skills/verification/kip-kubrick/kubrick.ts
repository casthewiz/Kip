// Kubrick's capture helpers for Playwright specs. Import by absolute path:
//   import { shot } from '<HOME>/.kip/skills/kip-kubrick/kubrick.ts';
// Take shots after the assertions that prove the page is ready, never instead of them.
import { test, type Page } from '@playwright/test';

// Saves <name>.png in the test's output folder, under that name whether the test
// later passes or fails, with CSS animations and transitions finished (Playwright
// already waits for web fonts). `full` (default) captures everything that scrolls,
// including an app that scrolls inside a container instead of the document.
export async function shot(page: Page, name: string, { full = true } = {}) {
  const path = test.info().outputPath(`${name}.png`);
  const size = page.viewportSize()!;
  // messy-bun: grows the viewport by the deepest container's hidden height, so it
  // needs a layout that stretches with the viewport (100vh, flex) and misses nested scrollers.
  const hidden = full ? await page.evaluate(() => Math.max(0, ...[...document.querySelectorAll('*')]
    .filter((e) => /auto|scroll/.test(getComputedStyle(e).overflowY))
    .map((e) => e.scrollHeight - e.clientHeight))) : 0;
  try {
    if (hidden) await page.setViewportSize({ width: size.width, height: size.height + hidden });
    await page.screenshot({ path, fullPage: full, animations: 'disabled' });
  } finally {
    if (hidden) await page.setViewportSize(size);
  }
  return path;
}
