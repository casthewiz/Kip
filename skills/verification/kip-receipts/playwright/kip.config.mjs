// Kip's Playwright config for desk-testing any site across viewports.
// No imports, so it loads from anywhere; specs resolve @playwright/test from
// ~/.kip/node_modules (see setup.sh). desktest.py sets the env vars below,
// including KIP_VIEWPORTS: {name: Playwright `use` block} from Kip's config.
const level = process.env.KIP_LEVEL || 'full';
const viewports = JSON.parse(process.env.KIP_VIEWPORTS || '{}');

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
  projects: Object.entries(viewports).map(([name, use]) => ({ name, use })),
};
