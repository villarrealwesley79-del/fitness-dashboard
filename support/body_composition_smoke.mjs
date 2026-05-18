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
  await page.route('**/api/body-history', async (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      status: 'success',
      trend: 'stable',
      history: [
        { date: '2099-04-27', weight_lbs: 185, body_fat_pct: 20 },
        { date: '2099-02-01', weight_lbs: 190, body_fat_pct: 22 },
      ],
    }),
  }));

  await page.goto(`${BASE_URL}/?smoke=${Date.now()}`, { waitUntil: 'networkidle' });
  await page.locator('.tab-btn[data-tab="tab-body"]').click();
  await page.locator('.card-label', { hasText: 'COMPOSITION' }).waitFor();
  await page.locator('#measurements-grid').getByText('Lean Mass').waitFor();
  await page.locator('#measurements-grid').getByText('148.0 lb').waitFor();
  await page.locator('#measurements-grid').getByText('Fat Mass').waitFor();
  await page.locator('#measurements-grid').getByText('37.0 lb').waitFor();
  await page.locator('#measurements-grid').getByText('-5.0 lb').waitFor();
  await page.locator('#measurements-grid').getByText('-2.0%').waitFor();

  console.log(JSON.stringify({
    ok: true,
    leanMass: '148.0 lb',
    fatMass: '37.0 lb',
    weight90d: '-5.0 lb',
    bodyFat90d: '-2.0%',
  }, null, 2));
} finally {
  await browser.close();
}
