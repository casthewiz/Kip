// Kip's Playwright config for desk-testing any site across viewports.
// No imports, so it loads from anywhere; specs resolve @playwright/test from
// ~/.kip/node_modules (see setup.sh). desktest.py sets the env vars below.
const level = process.env.KIP_LEVEL || 'full';
const chromium = { browserName: 'chromium' };

export default {
  testDir: process.env.KIP_SPECS || '.',
  outputDir: process.env.KIP_OUT || 'test-results',
  reporter: [['list']],
  retries: 0, // a pass that needed a retry isn't evidence
  use: {
    baseURL: process.env.BASE_URL,
    screenshot: 'on',
    video: level === 'ultra' ? 'on' : 'off',
    trace: level === 'ultra' ? 'on' : 'off',
  },
  projects: [
    { name: 'mobile', use: { ...chromium, viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true } },
    { name: 'tablet', use: { ...chromium, viewport: { width: 820, height: 1180 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true } },
    { name: 'desktop', use: { ...chromium, viewport: { width: 1440, height: 900 } } },
  ],
};
