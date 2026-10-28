// Record BASE_URL loading and scrolling top to bottom, no spec needed: `kubrick.py record CLAIM URL`.
// Records its own context so the video is the viewport's size and finished (the
// context closed) before the test ends, whatever the config's video setting.
import { test } from '@playwright/test';

test.use({ screenshot: 'off' });

test('record', async ({ browser, viewport, deviceScaleFactor, isMobile, hasTouch }) => {
  const ctx = await browser.newContext({
    viewport, deviceScaleFactor, isMobile, hasTouch,
    recordVideo: { dir: test.info().outputPath('raw'), size: viewport! },
  });
  const page = await ctx.newPage();
  await page.goto(process.env.BASE_URL!, { waitUntil: 'load' });
  await page.waitForTimeout(500);
  // messy-bun: scrolls the document only; an app that scrolls inside a container records its first screen.
  for (let y = 0; y < await page.evaluate(() => document.documentElement.scrollHeight); y += viewport!.height / 2) {
    await page.evaluate((top) => window.scrollTo({ top, behavior: 'smooth' }), y);
    await page.waitForTimeout(400);
  }
  await ctx.close();
  await page.video()!.saveAs(test.info().outputPath('page.webm'));
  await page.video()!.delete(); // the raw copy would be collected as a second video
});
