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

function apiSetEquipment(cookie, value) {
  const status = execFileSync('curl', [
    '-sS',
    '-o',
    '/tmp/fitness-dashboard-equipment-smoke-restore.json',
    '-w',
    '%{http_code}',
    '-H',
    `Cookie: session=${cookie}`,
    '-H',
    'Content-Type: application/json',
    '-X',
    'PUT',
    `${BASE_URL}/api/settings/equipment`,
    '-d',
    JSON.stringify({ equipment_preference: value }),
  ], {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  }).trim();
  if (status !== '200') throw new Error(`restore equipment returned ${status}`);
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
let originalPreference = 'machines_only';
let targetPreference = 'all';

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
  let dashboardRequests = 0;
  page.on('request', (req) => {
    if (req.url().includes('/api/dashboard')) dashboardRequests += 1;
  });
  await page.goto(`${BASE_URL}/?equipment_smoke=${Date.now()}`, { waitUntil: 'networkidle' });
  await page.locator('.tab-btn[data-tab="tab-settings"]').click();
  const select = page.locator('#settings-equipment');
  await select.waitFor();
  await page.waitForFunction(() => {
    const el = document.querySelector('#settings-equipment');
    return Boolean(el && el.value && el.options.length);
  });
  originalPreference = await select.inputValue();
  targetPreference = originalPreference === 'all' ? 'machines_only' : 'all';

  const before = dashboardRequests;
  const refreshedDashboard = page.waitForResponse((res) => (
    res.url().includes('/api/dashboard') &&
    res.request().method() === 'GET' &&
    res.status() >= 200 &&
    res.status() < 300
  ));
  await select.selectOption(targetPreference);
  await page.evaluate((value) => {
    const el = document.querySelector('#settings-equipment');
    el.value = value;
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }, targetPreference);
  await refreshedDashboard;
  const after = dashboardRequests;
  await page.locator('.tab-btn[data-tab="tab-workout"]').click();
  await page.locator('#btn-start-workout-2').click();
  await page.locator('#modal-active:not([hidden])').waitFor();

  console.log(JSON.stringify({
    ok: true,
    originalPreference,
    targetPreference,
    dashboardFetchesBefore: before,
    dashboardFetchesAfter: after,
    activeWorkoutOpened: true,
  }, null, 2));
} finally {
  try {
    apiSetEquipment(cookie, ['machines_only', 'machines_and_cables', 'all'].includes(originalPreference) ? originalPreference : 'machines_only');
  } finally {
    await browser.close();
  }
}
