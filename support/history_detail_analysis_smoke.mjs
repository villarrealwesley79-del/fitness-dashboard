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
  const workout = {
    id: 'smoke-history-notes',
    date: '2099-04-27',
    session_type: 'full_body',
    duration_minutes: 47,
    source: 'manual',
    total_sets: 3,
    total_volume: 3495,
    notes: 'Overall note: low energy but completed the work.',
    cardio: {
      completed: true,
      activity_type: 'Bike',
      duration_minutes: 12,
      notes: 'Cardio note: kept it easy after legs.',
      recommendation: { type: 'Bike', duration_minutes: 12 },
    },
    exercises: [
      {
        machine: 'Smoke Leg Press',
        muscle_group: 'quads',
        sets: [
          { set_number: 1, weight_lbs: 95, reps: 12, rpe: 6, notes: 'Set note: machine felt sticky.' },
          { set_number: 2, weight_lbs: 100, reps: 10, rpe: 7, notes: '' },
        ],
      },
      {
        machine: 'Smoke Row',
        muscle_group: 'back',
        sets: [
          { set_number: 1, weight_lbs: 85, reps: 15, rpe: 6, notes: 'Set note: right shoulder felt fine.' },
        ],
      },
    ],
  };

  await page.route('**/api/history-all', async (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      workouts: [workout],
      cardio: [],
      recovery: [],
      personal_records: {},
    }),
  }));

  await page.route('**/api/workout/analyze', async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'ok',
        workout: {
          id: workout.id,
          date: workout.date,
          session_type: workout.session_type,
          total_sets: workout.total_sets,
          total_volume: workout.total_volume,
          duration_minutes: workout.duration_minutes,
        },
        analysis: {
          summary: 'Smoke analysis used the notes.',
          wins: ['Smoke Row note was considered.'],
          concerns: ['Smoke Leg Press note was considered.'],
          comparison: 'Smoke comparison.',
          next_session_cue: 'Smoke cue.',
        },
        context_used: {
          recent_session_count: 0,
          readiness_available: false,
          set_note_count: 2,
          workout_notes_present: true,
          cardio_notes_present: true,
        },
        meta: { model_version: 'smoke-model', elapsed_ms: 11 },
        cache_hit: false,
      }),
    });
  });

  await page.goto(`${BASE_URL}/?smoke=${Date.now()}`, { waitUntil: 'networkidle' });
  await page.locator('.tab-btn[data-tab="tab-history"]').click();
  await page.locator('#history-workout-list .w-row').first().click();
  await page.locator('#modal-workout-detail:not([hidden])').waitFor();
  await page.locator('#workout-detail-body').getByText('Set note: machine felt sticky.').waitFor();
  await page.locator('#workout-detail-body').getByText('Overall note: low energy').waitFor();
  await page.locator('#workout-detail-body').getByText('Cardio note: kept it easy').waitFor();

  await page.locator('#modal-workout-detail [data-close-modal]').click();
  await page.locator('#history-workout-list .ex-analyze-btn').first().click();
  await page.locator('#modal-analyze:not([hidden])').waitFor();
  await page.locator('#analyze-notes-context').getByText('2 set notes, workout note, cardio note').waitFor();

  console.log(JSON.stringify({
    ok: true,
    detailOpened: true,
    setNotesVisible: true,
    analysisNotesContextVisible: true,
  }, null, 2));
} finally {
  await browser.close();
}
