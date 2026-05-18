import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

const BASE_URL = process.env.BASE_URL || 'http://127.0.0.1:5050';
const cwd = new URL('..', import.meta.url).pathname;

function makeSessionCookie() {
  const script = `
import contextlib
import io
with contextlib.redirect_stdout(io.StringIO()):
    from app import app
with app.test_client() as c:
    with c.session_transaction() as sess:
        sess['_user_id'] = '1'
        sess['_fresh'] = True
    session_name = app.config.get('SESSION_COOKIE_NAME', 'session')
    for cookie in getattr(c, '_cookies', {}).values():
        if getattr(cookie, 'key', '') == session_name:
            print(cookie.value)
            break
`;
  return execFileSync('venv/bin/python', ['-c', script], {
    cwd,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  }).trim();
}

async function launchBrowser() {
  try {
    return await chromium.launch({ channel: 'chrome', headless: true });
  } catch {
    return await chromium.launch({ headless: true });
  }
}

async function visibleSummaryText(page) {
  return (await page.locator('#history-workout-list .w-summary').allTextContents()).join(' | ');
}

const cookie = makeSessionCookie();
const browser = await launchBrowser();
try {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    ignoreHTTPSErrors: true,
  });
  await context.addCookies([{
    name: 'session',
    value: cookie,
    domain: '127.0.0.1',
    path: '/',
    httpOnly: true,
    sameSite: 'Lax',
  }]);

  const page = await context.newPage();
  await page.route('**/api/history-all', async (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      workouts: [{
        id: 'lifted-filter-smoke',
        date: '2099-04-27',
        duration_minutes: 45,
        exercises: [{ machine: 'Smoke Row', sets: [{ weight_lbs: 50, reps: 10 }] }],
        total_sets: 1,
        total_volume: 500,
      }],
      cardio: [],
      recovery: [],
      personal_records: {},
    }),
  }));
  await page.route('**/api/apple-health/workouts*', async (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      workouts: [
        { date: '2099-04-26', activity_type: 'Walk', duration_minutes: 12, total_energy_kcal: 40 },
        { date: '2099-04-25', activity_type: 'Basketball', duration_minutes: 36, total_energy_kcal: 260 },
      ],
    }),
  }));

  await page.goto(`${BASE_URL}/?smoke=${Date.now()}`, { waitUntil: 'networkidle' });
  await page.locator('.tab-btn[data-tab="tab-history"]').click();
  await page.locator('#history-type-filter').getByText('Lifted').waitFor();
  await page.locator('#history-type-filter').getByText('Walk').waitFor();
  await page.locator('#history-type-filter').getByText('Basketball').waitFor();

  await page.locator('#history-type-filter button[data-filter="lifted"]').click();
  let summaries = await visibleSummaryText(page);
  if (!summaries.includes('Smoke Row') || summaries.includes('Walk') || summaries.includes('Basketball')) {
    throw new Error(`lifted filter failed: ${summaries}`);
  }

  await page.locator('#history-type-filter button[data-filter="walk"]').click();
  summaries = await visibleSummaryText(page);
  if (!summaries.includes('Walk') || summaries.includes('Smoke Row') || summaries.includes('Basketball')) {
    throw new Error(`walk filter failed: ${summaries}`);
  }

  await page.locator('#history-type-filter button[data-filter="basketball"]').click();
  summaries = await visibleSummaryText(page);
  if (!summaries.includes('Basketball') || summaries.includes('Smoke Row') || summaries.includes('Walk')) {
    throw new Error(`basketball filter failed: ${summaries}`);
  }

  console.log(JSON.stringify({
    ok: true,
    filters: ['Lifted', 'Walk', 'Basketball'],
  }, null, 2));
} finally {
  await browser.close();
}
