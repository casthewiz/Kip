// Screenshot BASE_URL as it loads, no spec needed: `kubrick.py shot CLAIM URL`.
import { test } from '@playwright/test';
import { shot } from './kubrick';

test.use({ screenshot: 'off' }); // shot() takes the one that counts

test('shot', async ({ page }) => {
  await page.goto(process.env.BASE_URL!, { waitUntil: 'load' });
  await shot(page, 'page');
});
