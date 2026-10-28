// Self-check for kubrick.ts against a real browser:
//   NODE_PATH=~/.kip/node_modules node ~/.kip/node_modules/@playwright/test/cli.js test kubrick.spec.ts
import { readFileSync } from 'node:fs';
import { test, expect } from '@playwright/test';
import { shot } from './kubrick';

const pngHeight = (path: string) => readFileSync(path).readUInt32BE(20);

test.use({ viewport: { width: 400, height: 300 } });

test('full shot includes content that scrolls inside a container', async ({ page }) => {
  await page.setContent(`<body style="margin:0"><main style="height:100vh;overflow:auto">
    <div style="height:1500px;background:linear-gradient(red,blue)">tall</div></main></body>`);
  expect(pngHeight(await shot(page, 'container'))).toBeGreaterThanOrEqual(1500);
  expect(page.viewportSize()).toEqual({ width: 400, height: 300 });
  expect(pngHeight(await shot(page, 'viewport', { full: false }))).toBe(300);
});

test('animated page shoots the same twice', async ({ page }) => {
  await page.setContent(`<style>@keyframes s{to{transform:rotate(360deg)}}</style>
    <div style="width:100px;height:100px;background:red;animation:s 1s linear infinite"></div>`);
  const a = readFileSync(await shot(page, 'a'));
  await page.waitForTimeout(300);
  expect(readFileSync(await shot(page, 'b')).equals(a)).toBe(true);
});

test.describe(() => {
  test.fail(); // the test fails after the shot; the shot keeps its name
  test('shot keeps its name when the test fails', async ({ page }) => {
    await page.setContent('<h1>hi</h1>');
    const path = await shot(page, 'named');
    expect(path.endsWith('/named.png')).toBe(true);
    expect(readFileSync(path).length).toBeGreaterThan(0);
    expect(1).toBe(2);
  });
});
