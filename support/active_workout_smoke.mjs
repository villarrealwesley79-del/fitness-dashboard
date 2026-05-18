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

function assertEqual(actual, expected, label) {
  if (String(actual) !== String(expected)) {
    throw new Error(`${label}: expected ${expected}, got ${actual}`);
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
  await page.route('**/api/workout/swap', async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'success',
        recommendation: {
          id: 'smoke-fewer-set-swap',
          focus: 'Smoke Swap',
          exercises: [{
            name: 'Smoke Replacement',
            equipment: 'machine',
            target_sets: 1,
            target_reps: 5,
            target_weight: 10,
          }],
          cardio: null,
        },
      }),
    });
  });
  await page.goto(`${BASE_URL}/?smoke=${Date.now()}`, { waitUntil: 'networkidle' });
  await page.locator('#btn-start-workout, #btn-start-workout-2').first().click();
  await page.locator('#modal-active:not([hidden])').waitFor();

  const cardsBeforeRemove = await page.locator('#active-workout-body .active-ex:not(.active-cardio)').count();
  if (cardsBeforeRemove < 2) {
    throw new Error(`expected at least two exercise cards before remove check, got ${cardsBeforeRemove}`);
  }
  const removeCard = page.locator('#active-workout-body .active-ex:not(.active-cardio)').nth(1);
  const removedExercise = (await removeCard.locator('h4').innerText()).trim();
  await removeCard.locator('.active-remove-btn').click();
  await page.locator('#active-workout-body .active-ex:not(.active-cardio)').nth(cardsBeforeRemove - 2).waitFor();
  const cardsAfterRemove = await page.locator('#active-workout-body .active-ex:not(.active-cardio)').count();
  assertEqual(cardsAfterRemove, cardsBeforeRemove - 1, 'exercise card count after remove');
  const remainingNames = await page.locator('#active-workout-body .active-ex:not(.active-cardio) h4').allTextContents();
  if (remainingNames.map((value) => value.trim()).includes(removedExercise)) {
    throw new Error(`removed exercise still visible: ${removedExercise}`);
  }

  const firstCard = page.locator('#active-workout-body .active-ex:not(.active-cardio)').first();
  const oldExercise = (await firstCard.locator('h4').innerText()).trim();
  const oldSetCount = await firstCard.locator('.set-row').count();
  if (oldSetCount < 2) {
    throw new Error(`expected at least two source set rows, got ${oldSetCount}`);
  }
  for (let idx = 0; idx < oldSetCount; idx += 1) {
    const row = firstCard.locator('.set-row').nth(idx);
    await row.locator('input[data-field="weight"]').fill(String(123 + idx));
    await row.locator('input[data-field="reps"]').fill(String(8 + idx));
    await row.locator('input[data-field="done"]').setChecked(true);
    await row.locator('input[data-field="notes"]').fill(`preserve smoke note ${idx + 1}`);
  }

  await firstCard.locator('.active-swap-btn').click();
  await page.locator('#modal-swap:not([hidden])').waitFor();
  await page.locator('#swap-alternatives button.swap-row:not(.current)').first().click();
  await page.waitForFunction(() => document.querySelector('#modal-swap')?.hidden === true);

  const newExercise = (await firstCard.locator('h4').innerText()).trim();
  const newSetCount = await firstCard.locator('.set-row').count();

  if (newExercise === oldExercise) {
    throw new Error(`exercise did not change from ${oldExercise}`);
  }
  assertEqual(newSetCount, oldSetCount, 'all prior set rows preserved after fewer-set active swap');

  const preservedSets = [];
  for (let idx = 0; idx < oldSetCount; idx += 1) {
    const row = firstCard.locator('.set-row').nth(idx);
    const weight = await row.locator('input[data-field="weight"]').inputValue();
    const reps = await row.locator('input[data-field="reps"]').inputValue();
    const done = await row.locator('input[data-field="done"]').isChecked();
    const notes = await row.locator('input[data-field="notes"]').inputValue();
    assertEqual(weight, String(123 + idx), `weight preserved after active swap row ${idx + 1}`);
    assertEqual(reps, String(8 + idx), `reps preserved after active swap row ${idx + 1}`);
    assertEqual(done, true, `done checkbox preserved after active swap row ${idx + 1}`);
    assertEqual(notes, `preserve smoke note ${idx + 1}`, `set note preserved after active swap row ${idx + 1}`);
    preservedSets.push({ weight, reps, done, notes });
  }

  console.log(JSON.stringify({
    ok: true,
    oldExercise,
    newExercise,
    oldSetCount,
    newSetCount,
    preservedSets,
    removedExercise,
    cardsBeforeRemove,
    cardsAfterRemove,
  }, null, 2));
} finally {
  await browser.close();
}
