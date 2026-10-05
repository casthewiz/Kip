// Starter spec for desktest.py. Copy it into the run's specs/ folder, one
// spec per claim, so the spec is part of the evidence:
//   cp example.spec.ts "$(python3 receipt.py path)/specs/c1.spec.ts"
// The site under test comes from BASE_URL, so use relative paths.
import { test, expect } from '@playwright/test';

test('signup shows a confirmation', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });

  await page.goto('/signup');
  await page.getByLabel('Email').fill('test@example.com');
  await page.getByRole('button', { name: 'Sign up' }).click();

  // The behavior the claim promises.
  await expect(page.getByText('Check your inbox')).toBeVisible();

  // Holds at every viewport: no horizontal overflow, no console errors.
  // Compare against the emulated viewport, not window.innerWidth: mobile
  // emulation zooms out to fit wide content, which hides the overflow.
  const { width } = page.viewportSize()!;
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  expect(errors).toEqual([]);
});
