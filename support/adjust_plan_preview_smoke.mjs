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

const adjustedRecommendation = {
  id: 'adjust-preview-smoke',
  focus: 'Recovery Strength',
  estimated_minutes: 32,
  exercises: [
    {
      name: 'Machine Chest Press',
      muscle: 'chest',
      equipment: 'machine',
      target_sets: 2,
      target_reps: 8,
      target_weight: 80,
      rpe_target: 6,
    },
    {
      name: 'Seated Row',
      muscle: 'back',
      equipment: 'machine',
      target_sets: 2,
      target_reps: 10,
      target_weight: 90,
      rpe_target: 6,
    },
  ],
  cardio: null,
};

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
  const seenAdjustRequests = [];
  page.on('pageerror', (err) => {
    console.error(`PAGE_ERROR: ${err.message}`);
  });
  await page.route('**/api/workout/adjust', async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    seenAdjustRequests.push(route.request().url());
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'success',
        summary: 'Reduced volume for low energy and time constraints.',
        applied_notes: ['Reduced to two machine exercises', 'Kept intensity at RPE 6'],
        meta: { model_version: 'smoke-model', elapsed_ms: 12 },
        recommendation: adjustedRecommendation,
      }),
    });
  });

  await page.goto(`${BASE_URL}/?adjustPreviewSmoke=${Date.now()}`, { waitUntil: 'networkidle' });
  await page.locator('#btn-adjust-plan, #btn-adjust-plan-2').first().click();
  await page.locator('#modal-adjust:not([hidden])').waitFor();
  await page.locator('#adjust-constraint').fill('low energy and short on time');
  await page.locator('#btn-adjust-submit').click();

  try {
    await page.locator('#adjust-plan-preview:not([hidden])').waitFor({ timeout: 45000 });
  } catch (err) {
    const stateText = await page.locator('#adjust-state').innerText().catch(() => '<missing>');
    const resultHidden = await page.locator('#adjust-result').getAttribute('hidden').catch(() => '<missing>');
    const previewHtml = await page.locator('#adjust-plan-preview').innerHTML().catch(() => '<missing>');
    throw new Error(`preview did not appear; requests=${JSON.stringify(seenAdjustRequests)} state=${JSON.stringify(stateText)} resultHidden=${resultHidden} preview=${JSON.stringify(previewHtml)}`);
  }
  await page.locator('#adjust-plan-preview').getByText('Updated workout plan').waitFor();
  await page.locator('#adjust-plan-preview').getByText('Recovery Strength').waitFor();
  await page.locator('#adjust-plan-preview').getByText('Machine Chest Press').waitFor();

  await page.locator('#btn-adjust-start-workout').click();
  await page.locator('#modal-active:not([hidden])').waitFor();
  await page.locator('#active-workout-title').getByText('Recovery Strength').waitFor();
  await page.locator('#active-workout-body').getByText('Machine Chest Press').waitFor();

  console.log(JSON.stringify({
    ok: true,
    previewTitle: await page.locator('#adjust-plan-preview .adjust-preview-title').innerText().catch(() => 'hidden-after-start'),
    activeTitle: await page.locator('#active-workout-title').innerText(),
    firstExercise: await page.locator('#active-workout-body .active-ex h4').first().innerText(),
  }, null, 2));
} finally {
  await browser.close();
}
