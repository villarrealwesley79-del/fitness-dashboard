/* =======================================================
   AI Coach Feed — Analytical Dashboard (consolidated)
   One boot, one tab loader, real API endpoints, SVG charts.
   ======================================================= */

(function () {
    'use strict';

    // --- state ---------------------------------------------------
    const state = {
        currentTab: 'tab-dashboard',
        dashboard: null,
        vitals: null,
        oura: null,
        ouraSleep: null,
        ouraTrends: null,
        reco: null,
        insights: null,
        history: null,
        body: null,
        settings: null,
        analytics: null,
        muscleFatigue: null,
        nextWorkout: null,
        exercises: null,
        whoopStatus: null,
        whoopUi: {
            syncInFlight: false,
            disconnectInFlight: false,
            lastError: '',
        },
        ranges: { history: 30, stats: 30 },
        historyTypeFilter: 'all',
        activeWorkout: null,
        adjustedWorkout: null,
        swapContext: null,
    };

    // FIT-129: generation counters drop sentinel writes from stale fetches.
    // dashboardRenderGen bumps once per renderDashboard() (closes the
    // inter-render race). dashboardSentinelGen[key] bumps once per
    // renderDashboard() AND once per retry click for that chip (closes the
    // intra-render retry race — an older same-render fetch landing after a
    // retry success can no longer flip its sentinel back on).
    let dashboardRenderGen = 0;
    let modalOpenSeq = 0;
    let selectedGoalToRestoreFocus = null;
    const dashboardSentinelGen = { ouraError: 0, recoError: 0, ouraSleepError: 0 };
    let nextWorkoutRenderGen = 0;

    // --- helpers -------------------------------------------------
    const $ = (id) => document.getElementById(id);
    const qs = (sel, root = document) => root.querySelector(sel);
    const qsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));
    const today = () => {
        // Local-time YYYY-MM-DD. toISOString() uses UTC which flips to
        // tomorrow after evening UTC rollover in CDT/CST.
        const d = new Date();
        const y = d.getFullYear();
        const m = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        return `${y}-${m}-${day}`;
    };
    const fmtInt = (n) => (n == null || Number.isNaN(n)) ? '--' : Math.round(n).toLocaleString();
    const fmtKilo = (n) => {
        if (n == null || Number.isNaN(n)) return '--';
        if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
        if (n >= 1000) return (n / 1000).toFixed(n >= 10000 ? 0 : 1) + 'K';
        return Math.round(n).toString();
    };
    const fmtDur = (min) => {
        if (min == null || Number.isNaN(min)) return '--';
        const h = Math.floor(min / 60);
        const m = Math.round(min % 60);
        if (h === 0) return `${m}m`;
        if (m === 0) return `${h}h`;
        return `${h}h ${m}m`;
    };
    const fmtDecimal = (n, d = 1) => (n == null || Number.isNaN(n)) ? '--' : Number(n).toFixed(d);
    const fmtDate = (iso) => {
        if (!iso) return '—';
        // Parse "YYYY-MM-DD" as LOCAL midnight, not UTC midnight. Otherwise
        // CDT/CST users see the calendar day shifted back by one.
        const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso));
        const d = m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(iso);
        return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    };
    const parseServerDateTime = (value) => {
        if (!value) return null;
        const raw = String(value).trim();
        const d = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(raw)
            ? new Date(raw.replace(' ', 'T') + 'Z')
            : new Date(raw);
        return Number.isNaN(d.getTime()) ? null : d;
    };
    const fmtDateTime = (value) => {
        const d = parseServerDateTime(value);
        if (!d) return '—';
        return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
    };

    // FIT-128: dashboard fetchers pass timeoutMs so a hung endpoint
    // surfaces the per-card retry chip instead of sitting silent forever.
    const DASHBOARD_FETCH_TIMEOUT_MS = 30000;
    const CSRF_HEADER_NAME = 'X-Requested-With';
    const CSRF_HEADER_VALUE = 'XMLHttpRequest';
    const MODAL_FOCUS_SELECTOR = [
        '[autofocus]',
        '.modal-close:not([disabled])',
        '[data-close-modal]:not([disabled])',
        'button:not([disabled])',
        '[href]',
        'iframe',
        'input:not([disabled]):not([type="hidden"])',
        'select:not([disabled])',
        'textarea:not([disabled])',
        '[tabindex]:not([tabindex="-1"])',
    ].join(',');

    async function api(path, opts = {}) {
        const { timeoutMs, ...fetchOpts } = opts;
        let timer = null;
        if (timeoutMs && !fetchOpts.signal) {
            const controller = new AbortController();
            timer = setTimeout(() => controller.abort(), timeoutMs);
            fetchOpts.signal = controller.signal;
        }
        let res;
        const headers = { 'Accept': 'application/json', ...(fetchOpts.headers || {}), [CSRF_HEADER_NAME]: CSRF_HEADER_VALUE };
        try {
            res = await fetch(path, {
                ...fetchOpts,
                credentials: 'same-origin',
                headers,
            });
        } finally {
            if (timer) clearTimeout(timer);
        }
        if (res.status === 401) {
            let reloadRequired = false;
            try {
                const body = await res.clone().json();
                reloadRequired = Boolean(body && (body.reload === true || body.error === 'reload_required'));
            } catch {
                reloadRequired = false;
            }
            if (reloadRequired) {
                if (activeWorkoutHasProgress()) {
                    toast('Update ready after workout. Refresh when finished.', 'warn');
                    throw new Error('reload required after workout');
                }
                window.location.reload();
                throw new Error('reload required');
            }
            window.location.href = '/login?next=' + encodeURIComponent(location.pathname);
            throw new Error('unauthorized');
        }
        if (!res.ok) {
            const text = await res.text().catch(() => '');
            throw new Error(`${res.status} ${path}: ${text.slice(0, 120)}`);
        }
        const ct = res.headers.get('content-type') || '';
        return ct.includes('application/json') ? res.json() : res.text();
    }

    function toast(msg, variant = 'ok') {
        const host = $('toast-host');
        if (!host) return;
        const el = document.createElement('div');
        el.className = `toast ${variant}`;
        el.textContent = msg;
        host.appendChild(el);
        setTimeout(() => el.remove(), 2400);
    }

    function toastUndo(msg, onUndo, durationMs = 10000, onTap = null) {
        const host = $('toast-host');
        if (!host) return null;
        const el = document.createElement('div');
        el.className = 'toast toast-undo';

        // FIT-124: Inspect surface is a real <button> when tappable, a
        // passive <span> otherwise. Both share .toast-undo-text styling
        // so the pill looks identical across the two modes. Using a real
        // <button> avoids nesting <button> inside a role=button container
        // (PR #109's shape — flagged in Codex audit) and gives us native
        // Enter / Space handling for free.
        const tappable = typeof onTap === 'function';
        let inspectEl;
        if (tappable) {
            inspectEl = document.createElement('button');
            inspectEl.type = 'button';
            inspectEl.className = 'toast-undo-text toast-undo-inspect';
            inspectEl.setAttribute('aria-label', `${msg}. Tap to inspect.`);
            el.classList.add('toast-undo--tap');
        } else {
            inspectEl = document.createElement('span');
            inspectEl.className = 'toast-undo-text';
        }
        inspectEl.textContent = msg;

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'toast-undo-btn';
        btn.textContent = 'Undo';

        el.appendChild(inspectEl);
        el.appendChild(btn);
        host.appendChild(el);

        let dismissed = false;
        const dismiss = () => {
            if (dismissed) return;
            dismissed = true;
            el.remove();
            clearTimeout(timer);
        };
        const timer = setTimeout(dismiss, durationMs);

        btn.addEventListener('click', () => {
            dismiss();
            try { onUndo && onUndo(); } catch (e) { console.error(e); }
        });

        if (tappable) {
            const fire = () => {
                dismiss();
                try { onTap(); } catch (e) { console.error(e); }
            };
            inspectEl.addEventListener('click', fire);
            // FIT-124: container click delegate covers the pill's padding
            // and the gap between Inspect and Undo. Filter by target so a
            // bubbled click from either child button never double-fires.
            el.addEventListener('click', (ev) => {
                if (ev.target === el) fire();
            });
        }
        return dismiss;
    }

    // FIT-139: surface backend-recorded food-log refresh events as a passive
    // toast notice. The server is the only source of truth — no client-side
    // row diffing — and acknowledged events are dismissed via the ack API so
    // they stop appearing on subsequent polls.
    const foodLogRefreshNoticeState = {
        seen: new Set(),
        fetching: false,
    };

    function foodLogRefreshDayLabel(date) {
        if (!date) return '';
        const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(date));
        if (m) {
            const target = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
            const today = new Date();
            today.setHours(0, 0, 0, 0);
            const targetMid = new Date(target.getFullYear(), target.getMonth(), target.getDate());
            const diffDays = Math.round((targetMid - today) / (1000 * 60 * 60 * 24));
            if (diffDays === 0) return "today's";
            if (diffDays === -1) return "yesterday's";
        }
        return `${fmtDate(date)}'s`;
    }

    function foodLogRefreshNoticeCopy(event) {
        const item = (event && event.item_name) ? String(event.item_name).trim() : 'meal';
        const dayLabel = foodLogRefreshDayLabel(event && event.date);
        const title = dayLabel
            ? `Updated ${dayLabel} ${item} nutrition`
            : `Updated ${item} nutrition`;
        const sourceLabel = (event && event.source) ? String(event.source).trim() : 'verified source';
        const detail = `Verified source: ${sourceLabel}`;
        return { title, detail };
    }

    function showFoodLogRefreshNotice(event) {
        const host = $('toast-host');
        if (!host || !event || !event.id) return;
        const el = document.createElement('div');
        el.className = 'toast food-log-refresh-toast';
        el.setAttribute('role', 'status');
        el.setAttribute('aria-live', 'polite');

        const body = document.createElement('div');
        body.className = 'food-log-refresh-toast-body';

        const { title, detail } = foodLogRefreshNoticeCopy(event);
        const titleEl = document.createElement('div');
        titleEl.className = 'food-log-refresh-toast-title';
        titleEl.textContent = title;
        body.appendChild(titleEl);

        const detailEl = document.createElement('div');
        detailEl.className = 'food-log-refresh-toast-detail';
        detailEl.textContent = detail;
        body.appendChild(detailEl);

        const dismiss = document.createElement('button');
        dismiss.type = 'button';
        dismiss.className = 'food-log-refresh-toast-dismiss';
        dismiss.setAttribute('aria-label', 'Dismiss refresh notice');
        dismiss.textContent = 'Dismiss';

        let dismissed = false;
        dismiss.addEventListener('click', async () => {
            if (dismissed) return;
            dismissed = true;
            dismiss.disabled = true;
            try {
                await api(`/api/food-log-refresh-events/${encodeURIComponent(event.id)}/ack`, { method: 'POST' });
                el.remove();
            } catch (err) {
                dismissed = false;
                dismiss.disabled = false;
                foodLogRefreshNoticeState.seen.delete(event.id);
                console.warn('food-log refresh ack failed:', err);
            }
        });

        el.appendChild(body);
        el.appendChild(dismiss);
        host.appendChild(el);
    }

    async function fetchFoodLogRefreshNotices() {
        if (foodLogRefreshNoticeState.fetching) return;
        foodLogRefreshNoticeState.fetching = true;
        try {
            const payload = await api('/api/food-log-refresh-events?unacknowledged=true&limit=10');
            const events = (payload && payload.events) || [];
            for (const event of events) {
                if (!event || !event.id) continue;
                if (foodLogRefreshNoticeState.seen.has(event.id)) continue;
                foodLogRefreshNoticeState.seen.add(event.id);
                showFoodLogRefreshNotice(event);
            }
        } finally {
            foodLogRefreshNoticeState.fetching = false;
        }
    }

    // --- FIT-137: nutrition-driven workout-adaptation confirmation ----
    // Read/display side of the FIT-136 seam. Mirrors the FIT-139 passive-notice
    // contract: poll the backend event feed, ack on dismiss, no client-side
    // row diffing. A confirmation renders ONLY when FIT-136 reports an applied
    // change to *today's* plan. No-change / low-confidence events arrive with
    // `silent: true` and render nothing; next-day effects surface solely as
    // #nw-why reasoning when tomorrow's plan opens, never as a toast here. The
    // full adaptation audit log is backend-only — this code never fetches or
    // renders it, only the projected event's user-visible reason + signals.
    const workoutAdaptationNoticeState = {
        seen: new Set(),
        fetching: false,
    };

    function workoutAdaptationIsRenderable(event) {
        // Applied-change gate. Silent (no-change / low-confidence) events and
        // next-day effects render nothing; only an applied change to today's
        // plan produces a confirmation.
        if (!event || !event.id) return false;
        if (event.silent) return false;
        if (event.status !== 'applied') return false;
        if (event.change_type === 'none') return false;
        if (event.applies_to !== 'today') return false;
        return true;
    }

    function workoutAdaptationSignalLabels(event) {
        const signals = (event && event.nutrition_context && event.nutrition_context.signals) || [];
        return signals
            .map((s) => (s && s.label ? String(s.label).trim() : ''))
            .filter(Boolean);
    }

    function workoutAdaptationRemainingPlanRows(plan) {
        const exercises = (plan && plan.exercises) || [];
        if (!exercises.length) {
            return '<div class="workout-adaptation-plan-empty">Recovery focus — no remaining strength sets.</div>';
        }
        return exercises.slice(0, 8).map((ex) => {
            const name = ex && ex.name ? String(ex.name) : 'exercise';
            const sets = ex && ex.target_sets != null ? String(ex.target_sets) : '—';
            const reps = ex && ex.target_reps != null ? ` × ${escapeHtml(String(ex.target_reps))} reps` : '';
            return `<div class="workout-adaptation-plan-row"><span class="workout-adaptation-plan-name">${escapeHtml(name)}</span><span class="workout-adaptation-plan-target">${escapeHtml(sets)} sets${reps}</span></div>`;
        }).join('');
    }

    function applyWorkoutAdaptationToActiveWorkout(event) {
        // AC: when a workout is active, fold the server-adapted remaining plan
        // into the active workout via the FIT-179 identity-merge so completed
        // sets survive. The backend has already patched its recommendation, so
        // the freshest next-workout fetch carries the adapted remaining work.
        if (!state.activeWorkout) return;
        if (!(event.active_workout && event.active_workout.updated_live)) return;
        getNextWorkout(true).then((nw) => {
            if (!nw || !state.activeWorkout) return;
            const previous = Array.isArray(state.activeWorkout.exercises)
                ? state.activeWorkout.exercises
                : [];
            applyAdjustedRecommendationToActiveWorkout(nw, previous);
            renderActiveWorkout();
        }).catch((err) => console.warn('active workout adaptation merge failed:', err));
    }

    function showWorkoutAdaptationNotice(event) {
        const host = $('workout-adaptation-host');
        if (!host || !workoutAdaptationIsRenderable(event)) return;
        const reason = (event.reason && String(event.reason).trim())
            || 'Adjusted your remaining workout based on saved nutrition.';
        const labels = workoutAdaptationSignalLabels(event);
        const chips = labels.length
            ? `<div class="workout-adaptation-chips">${labels.map((l) => `<span class="workout-adaptation-chip">${escapeHtml(l)}</span>`).join('')}</div>`
            : '';
        const planRows = workoutAdaptationRemainingPlanRows(event.after_remaining_plan);

        const card = document.createElement('div');
        card.className = 'card workout-adaptation-card';
        card.setAttribute('role', 'status');
        card.setAttribute('aria-live', 'polite');

        const head = document.createElement('div');
        head.className = 'workout-adaptation-head';
        const kicker = document.createElement('span');
        kicker.className = 'workout-adaptation-kicker';
        kicker.textContent = 'Workout updated';
        const dismiss = document.createElement('button');
        dismiss.type = 'button';
        dismiss.className = 'workout-adaptation-dismiss';
        dismiss.setAttribute('aria-label', 'Dismiss workout update');
        dismiss.textContent = 'Dismiss';
        head.appendChild(kicker);
        head.appendChild(dismiss);

        const reasonEl = document.createElement('div');
        reasonEl.className = 'workout-adaptation-reason';
        reasonEl.textContent = reason;

        // AC: per-meal/item specifics live behind a collapsed native <details>.
        // Only the user-visible neutral signals + the updated remaining plan are
        // shown — never the audit log.
        const details = document.createElement('details');
        details.className = 'workout-adaptation-details';
        details.innerHTML = `
            <summary class="workout-adaptation-summary">View details</summary>
            ${chips}
            <div class="workout-adaptation-plan-kicker">Updated remaining plan</div>
            <div class="workout-adaptation-plan">${planRows}</div>
        `;

        card.appendChild(head);
        card.appendChild(reasonEl);
        card.appendChild(details);

        let dismissed = false;
        dismiss.addEventListener('click', async () => {
            if (dismissed) return;
            dismissed = true;
            dismiss.disabled = true;
            try {
                await api(`/api/workout-adaptation-events/${encodeURIComponent(event.id)}/ack`, { method: 'POST' });
                card.remove();
                if (!host.children.length) host.hidden = true;
            } catch (err) {
                // Keep the existing card and leave the event in `seen` so a
                // later poll does NOT append a duplicate; just re-enable the
                // button so the user can retry the ack on this same card.
                dismissed = false;
                dismiss.disabled = false;
                console.warn('workout adaptation ack failed:', err);
            }
        });

        host.hidden = false;
        host.appendChild(card);
        applyWorkoutAdaptationToActiveWorkout(event);
    }

    async function fetchWorkoutAdaptationNotices() {
        if (workoutAdaptationNoticeState.fetching) return;
        workoutAdaptationNoticeState.fetching = true;
        try {
            const payload = await api('/api/workout-adaptation-events?unacknowledged=true&limit=10');
            const events = (payload && payload.events) || [];
            for (const event of events) {
                if (!event || !event.id) continue;
                if (workoutAdaptationNoticeState.seen.has(event.id)) continue;
                workoutAdaptationNoticeState.seen.add(event.id);
                // Silent (no-change / low-confidence) and next-day events are
                // intentionally swallowed — marked seen but never rendered.
                if (!workoutAdaptationIsRenderable(event)) continue;
                showWorkoutAdaptationNotice(event);
            }
        } finally {
            workoutAdaptationNoticeState.fetching = false;
        }
    }

    function newWorkoutId(recommendationId) {
        const suffix = Math.random().toString(36).slice(2, 8);
        return `w-${recommendationId || Date.now()}-${suffix}`;
    }

    function setActiveWorkoutStatus(message, variant = '') {
        const el = $('active-workout-status');
        if (!el) return;
        el.hidden = !message;
        el.textContent = message || '';
        el.className = `active-workout-status ${variant}`.trim();
    }

    function workoutSaveErrorMessage(err) {
        if (typeof navigator !== 'undefined' && navigator && navigator.onLine === false) {
            return 'Offline: workout is still open. Reconnect and tap Complete Workout again.';
        }
        const raw = String((err && err.message) || err || '');
        const jsonStart = raw.indexOf('{');
        if (jsonStart >= 0) {
            try {
                const parsed = JSON.parse(raw.slice(jsonStart));
                const serverMsg = parsed && parsed.error && parsed.error.message;
                if (serverMsg) return `Validation failed: ${serverMsg}. Fix the highlighted workout data and try again.`;
            } catch {}
        }
        if (/Failed to fetch|NetworkError|Load failed/i.test(raw)) {
            return 'Connection failed: workout is still open. Check the server or network and retry.';
        }
        return 'Save failed: workout is still open. Review the set values and try again.';
    }

    function apiErrorMessage(err, fallback) {
        const raw = String((err && err.message) || err || '');
        const jsonStart = raw.indexOf('{');
        if (jsonStart >= 0) {
            try {
                const parsed = JSON.parse(raw.slice(jsonStart));
                const serverMsg = parsed && parsed.error && parsed.error.message;
                if (serverMsg) return serverMsg;
            } catch {}
        }
        return fallback;
    }

    // --- SVG chart helpers ---------------------------------------
    const SVG_NS = 'http://www.w3.org/2000/svg';
    function svg(attrs, children = []) {
        const el = document.createElementNS(SVG_NS, 'svg');
        Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
        children.forEach((c) => el.appendChild(c));
        return el;
    }
    function ns(tag, attrs = {}, children = []) {
        const el = document.createElementNS(SVG_NS, tag);
        Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
        if (typeof children === 'string') el.textContent = children;
        else children.forEach((c) => el.appendChild(c));
        return el;
    }

    function sparkline(container, values, opts = {}) {
        if (!container) return;
        container.innerHTML = '';
        const clean = (values || []).map(Number).filter((v) => Number.isFinite(v));
        if (clean.length < 2) {
            container.innerHTML = '<div class="empty" style="padding:6px;font-size:11px">—</div>';
            return;
        }
        const w = opts.width || 300;
        const h = opts.height || 32;
        const pad = 2;
        const min = Math.min(...clean);
        const max = Math.max(...clean);
        const range = (max - min) || 1;
        const stepX = (w - pad * 2) / (clean.length - 1);
        const color = opts.color || '#60a5fa';
        const pts = clean.map((v, i) => {
            const x = pad + i * stepX;
            const y = pad + (h - pad * 2) * (1 - (v - min) / range);
            return [x, y];
        });
        const pathD = pts.map((p, i) => (i === 0 ? 'M' : 'L') + p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' ');
        const areaD = pathD + ` L ${pts[pts.length - 1][0].toFixed(1)},${h - pad} L ${pts[0][0].toFixed(1)},${h - pad} Z`;

        const gradId = 'g-' + Math.random().toString(36).slice(2, 8);
        const root = svg({ viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: 'none' });
        const defs = ns('defs', {}, [
            ns('linearGradient', { id: gradId, x1: 0, y1: 0, x2: 0, y2: 1 }, [
                ns('stop', { offset: '0%', 'stop-color': color, 'stop-opacity': '0.35' }),
                ns('stop', { offset: '100%', 'stop-color': color, 'stop-opacity': '0' }),
            ]),
        ]);
        root.appendChild(defs);
        root.appendChild(ns('path', { d: areaD, fill: `url(#${gradId})`, stroke: 'none' }));
        root.appendChild(ns('path', { d: pathD, fill: 'none', stroke: color, 'stroke-width': '1.8', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
        const last = pts[pts.length - 1];
        root.appendChild(ns('circle', { cx: last[0], cy: last[1], r: '2.4', fill: color }));
        container.appendChild(root);
    }

    function lineChart(container, points, opts = {}) {
        if (!container) return;
        container.innerHTML = '';
        const pts = (points || []).filter((p) => p && p.value != null && Number.isFinite(Number(p.value)));
        if (pts.length < 2) {
            container.innerHTML = '<div class="empty">Not enough data yet.</div>';
            return;
        }
        const w = 600;
        const h = 160;
        const padL = 28, padR = 10, padT = 10, padB = 22;
        const ys = pts.map((p) => Number(p.value));
        const min = Math.min(...ys);
        const max = Math.max(...ys);
        const range = (max - min) || 1;
        const emptyMaxY = Number(opts.emptyMaxY) > 0 ? Number(opts.emptyMaxY) : 100;
        const useEmptyDomain = opts.nonNegativeY && max <= 0;
        const minPad = useEmptyDomain ? 0 : (opts.nonNegativeY ? Math.max(0, min - range * 0.12) : min - range * 0.12);
        const maxPad = useEmptyDomain ? emptyMaxY : max + range * 0.12;
        const trueRange = maxPad - minPad || 1;
        const plotW = w - padL - padR;
        const plotH = h - padT - padB;
        const xFor = (i) => padL + (pts.length === 1 ? plotW / 2 : (i / (pts.length - 1)) * plotW);
        const yFor = (v) => padT + plotH * (1 - (v - minPad) / trueRange);

        const color = opts.color || '#60a5fa';
        const gradId = 'lg-' + Math.random().toString(36).slice(2, 8);
        const root = svg({ viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: 'none' });
        const defs = ns('defs', {}, [
            ns('linearGradient', { id: gradId, x1: 0, y1: 0, x2: 0, y2: 1 }, [
                ns('stop', { offset: '0%', 'stop-color': color, 'stop-opacity': '0.35' }),
                ns('stop', { offset: '100%', 'stop-color': color, 'stop-opacity': '0' }),
            ]),
        ]);
        root.appendChild(defs);

        // Horizontal gridlines (3 lines)
        for (let i = 0; i < 3; i++) {
            const yv = minPad + (trueRange * (i + 1)) / 4;
            const yy = yFor(yv);
            root.appendChild(ns('line', { x1: padL, x2: w - padR, y1: yy, y2: yy, stroke: '#1e293b', 'stroke-width': '1', 'stroke-dasharray': '2 3' }));
        }

        const pathD = pts.map((p, i) => (i === 0 ? 'M' : 'L') + xFor(i).toFixed(1) + ',' + yFor(Number(p.value)).toFixed(1)).join(' ');
        const areaD = pathD + ` L ${xFor(pts.length - 1).toFixed(1)},${h - padB} L ${xFor(0).toFixed(1)},${h - padB} Z`;
        root.appendChild(ns('path', { d: areaD, fill: `url(#${gradId})`, stroke: 'none' }));
        root.appendChild(ns('path', { d: pathD, fill: 'none', stroke: color, 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));

        // Y-axis labels (min / max)
        root.appendChild(ns('text', { x: 6, y: yFor(maxPad) + 4, fill: '#64748b', 'font-size': '10', 'font-weight': '600' }, String(Math.round(maxPad * 10) / 10)));
        root.appendChild(ns('text', { x: 6, y: yFor(minPad) + 4, fill: '#64748b', 'font-size': '10', 'font-weight': '600' }, String(Math.round(minPad * 10) / 10)));

        // X-axis labels (first, middle, last)
        const labelPts = [0, Math.floor(pts.length / 2), pts.length - 1];
        labelPts.forEach((i) => {
            if (!pts[i] || !pts[i].label) return;
            const align = i === 0 ? 'start' : i === pts.length - 1 ? 'end' : 'middle';
            root.appendChild(ns('text', {
                x: xFor(i), y: h - 6,
                fill: '#64748b', 'font-size': '10', 'font-weight': '600',
                'text-anchor': align,
            }, pts[i].label));
        });

        // End dot
        root.appendChild(ns('circle', { cx: xFor(pts.length - 1), cy: yFor(Number(pts[pts.length - 1].value)), r: '3.2', fill: color }));
        container.appendChild(root);
    }

    function barChart(container, bars, opts = {}) {
        if (!container) return;
        container.innerHTML = '';
        const data = (bars || []).filter(b => b && b.value != null);
        if (!data.length) { container.innerHTML = '<div class="empty">No data.</div>'; return; }
        const w = 600;
        const h = 160;
        const padL = 10, padR = 10, padT = 10, padB = 22;
        const plotW = w - padL - padR;
        const plotH = h - padT - padB;
        const max = Math.max(...data.map(b => Number(b.value))) || 1;
        const barW = Math.max(4, Math.min(20, (plotW / data.length) * 0.7));
        const gap = (plotW - barW * data.length) / Math.max(1, data.length - 1);
        const color = opts.color || '#60a5fa';

        const root = svg({ viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: 'none' });
        data.forEach((b, i) => {
            const x = padL + i * (barW + gap);
            const bh = Math.max(2, (plotH * Number(b.value)) / max);
            const y = padT + plotH - bh;
            root.appendChild(ns('rect', { x: x.toFixed(1), y: y.toFixed(1), width: barW.toFixed(1), height: bh.toFixed(1), rx: '2', fill: color, opacity: i === data.length - 1 ? 1 : 0.75 }));
        });

        const labelIdx = [0, Math.floor(data.length / 2), data.length - 1];
        labelIdx.forEach((i) => {
            const b = data[i];
            if (!b || !b.label) return;
            const x = padL + i * (barW + gap) + barW / 2;
            const align = i === 0 ? 'start' : i === data.length - 1 ? 'end' : 'middle';
            root.appendChild(ns('text', { x, y: h - 6, fill: '#64748b', 'font-size': '10', 'font-weight': '600', 'text-anchor': align }, b.label));
        });
        container.appendChild(root);
    }

    // FIT-112: shared chart-takeaway plumbing. setChartTakeaway populates
    // a sibling <div class="chart-takeaway"> below the named chart and
    // toggles its visibility based on whether the derived copy is empty.
    // Empty text → element hidden entirely. The optional `empty` flag
    // dims the line + uses italic for the "Needs more data" affordance.
    function setChartTakeaway(id, text, opts = {}) {
        const el = $(id);
        if (!el) return;
        const t = (text || '').trim();
        el.textContent = t;
        el.hidden = !t;
        if (opts.empty) el.classList.add('empty');
        else el.classList.remove('empty');
    }

    function deriveWeightTakeaway(points) {
        if (!points || points.length < 2) {
            return { text: 'Needs 2+ entries to summarize.', empty: true };
        }
        const first = points[0].value;
        const last = points[points.length - 1].value;
        const delta = last - first;
        const dir = Math.abs(delta) < 0.5 ? 'holding steady'
            : (delta < 0 ? 'trending down' : 'trending up');
        const days = points.length;
        const word = Math.abs(delta) < 0.05 ? 'Flat'
            : `${delta < 0 ? 'Down' : 'Up'} ${Math.abs(delta).toFixed(1)} lb`;
        return { text: `${word} over the last ${days} entries · ${dir}.` };
    }

    function deriveBodyFatTakeaway(points) {
        if (!points || points.length < 2) {
            return { text: 'Needs 2+ entries to summarize.', empty: true };
        }
        const first = points[0].value;
        const last = points[points.length - 1].value;
        const delta = last - first;
        const dir = Math.abs(delta) < 0.3 ? 'holding steady'
            : (delta < 0 ? 'trending down' : 'trending up');
        const days = points.length;
        const word = Math.abs(delta) < 0.05 ? 'Flat'
            : `${delta < 0 ? 'Down' : 'Up'} ${Math.abs(delta).toFixed(1)}%`;
        return { text: `${word} over the last ${days} entries · ${dir}.` };
    }

    // FIT-112 (Codex audit): take the range in days directly rather than
    // deriving it from bucket boundaries. Each bucket's end is
    // `T23:59:59` (inclusive), so `(end - start) / 86400000 + 1` was
    // overcounting by ~1 day per bucket and halving the per-week rate.
    function deriveHistoryFreqTakeaway(buckets, totalWorkouts, days) {
        if (!buckets || !buckets.length || !totalWorkouts) {
            return { text: 'No workouts in range.', empty: true };
        }
        const weeks = Math.max((Number(days) || 1) / 7, 1 / 7);
        const perWeek = totalWorkouts / weeks;
        return { text: `${totalWorkouts} workouts in range · ${perWeek.toFixed(1)} per week.` };
    }

    function deriveHistoryVolumeTakeaway(buckets) {
        if (!buckets || !buckets.length) {
            return { text: 'No workouts in range.', empty: true };
        }
        const total = buckets.reduce((s, b) => s + (Number(b.volume) || 0), 0);
        if (total <= 0) {
            return { text: 'No volume recorded in range.', empty: true };
        }
        const half = Math.floor(buckets.length / 2);
        if (half < 1) {
            return { text: `${fmtKilo(total)} lbs total · need a longer range for trend.` };
        }
        const firstHalf = buckets.slice(0, half).reduce((s, b) => s + (Number(b.volume) || 0), 0);
        const secondHalf = buckets.slice(buckets.length - half).reduce((s, b) => s + (Number(b.volume) || 0), 0);
        if (firstHalf <= 0) {
            return { text: `${fmtKilo(total)} lbs total · ramping up.` };
        }
        const pct = Math.round(((secondHalf - firstHalf) / firstHalf) * 100);
        if (Math.abs(pct) < 5) {
            return { text: `${fmtKilo(total)} lbs total · holding steady.` };
        }
        const dir = pct > 0 ? 'up' : 'down';
        return { text: `${fmtKilo(total)} lbs total · volume ${dir} ${Math.abs(pct)}% vs the prior period.` };
    }

    function deriveReadiness7dTakeaway(points) {
        if (!points || !points.length) {
            return { text: 'No readiness data yet.', empty: true };
        }
        const vals = points.map((p) => Number(p.value)).filter((v) => Number.isFinite(v));
        if (!vals.length) {
            return { text: 'No readiness data yet.', empty: true };
        }
        const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
        const last = vals[vals.length - 1];
        const dev = Math.max(...vals) - Math.min(...vals);
        const stability = dev <= 10 ? 'steady' : (last >= avg ? 'recovering' : 'dipping');
        return { text: `7-day average ${Math.round(avg)} · ${stability}.` };
    }

    function donutChart(container, slices, opts = {}) {
        if (!container) return;
        container.innerHTML = '';
        const total = slices.reduce((a, s) => a + Number(s.value || 0), 0);
        if (!slices.length || total <= 0) { container.innerHTML = '<div class="empty">No data yet.</div>'; return; }
        const size = opts.size || 180;
        const r = size / 2 - 6;
        const inner = r * 0.6;
        const root = svg({ viewBox: `0 0 ${size} ${size}`, width: size, height: size });
        let ang = -Math.PI / 2;
        slices.forEach((s) => {
            const frac = Number(s.value) / total;
            const next = ang + frac * Math.PI * 2;
            const large = frac > 0.5 ? 1 : 0;
            const cx = size / 2, cy = size / 2;
            const x1 = cx + r * Math.cos(ang), y1 = cy + r * Math.sin(ang);
            const x2 = cx + r * Math.cos(next), y2 = cy + r * Math.sin(next);
            const x3 = cx + inner * Math.cos(next), y3 = cy + inner * Math.sin(next);
            const x4 = cx + inner * Math.cos(ang), y4 = cy + inner * Math.sin(ang);
            const d = [
                `M ${x1.toFixed(2)} ${y1.toFixed(2)}`,
                `A ${r} ${r} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`,
                `L ${x3.toFixed(2)} ${y3.toFixed(2)}`,
                `A ${inner} ${inner} 0 ${large} 0 ${x4.toFixed(2)} ${y4.toFixed(2)}`,
                'Z',
            ].join(' ');
            root.appendChild(ns('path', { d, fill: s.color || '#60a5fa' }));
            ang = next;
        });
        root.appendChild(ns('text', { x: size / 2, y: size / 2 + 4, 'text-anchor': 'middle', fill: '#f1f5f9', 'font-size': '20', 'font-weight': '800' }, fmtKilo(total)));
        root.appendChild(ns('text', { x: size / 2, y: size / 2 + 20, 'text-anchor': 'middle', fill: '#94a3b8', 'font-size': '10', 'font-weight': '700' }, opts.subtitle || 'TOTAL'));
        container.appendChild(root);
    }

    function gaugeChart(container, value, opts = {}) {
        if (!container) return;
        container.innerHTML = '';
        const size = 120;
        const pct = Math.max(0, Math.min(100, Number(value) || 0));
        const stroke = 10;
        const r = size / 2 - stroke - 2;
        const cx = size / 2, cy = size / 2;
        const circumference = 2 * Math.PI * r;
        const dash = (pct / 100) * circumference;
        const color = pct >= 75 ? '#22c55e' : pct >= 55 ? '#f59e0b' : '#ef4444';

        const root = svg({ viewBox: `0 0 ${size} ${size}` });
        root.appendChild(ns('circle', { cx, cy, r, fill: 'none', stroke: '#1e293b', 'stroke-width': stroke }));
        root.appendChild(ns('circle', {
            cx, cy, r,
            fill: 'none',
            stroke: color,
            'stroke-width': stroke,
            'stroke-linecap': 'round',
            'stroke-dasharray': `${dash} ${circumference - dash}`,
            transform: `rotate(-90 ${cx} ${cy})`,
        }));
        container.appendChild(root);

        const overlay = document.createElement('div');
        overlay.className = 'readiness-gauge-value';
        overlay.innerHTML = `<div class="val">${Math.round(pct)}<span class="pct">%</span></div><div class="label">${opts.label || ''}</div>`;
        container.appendChild(overlay);
    }

    // --- greeting ------------------------------------------------
    function renderGreeting() {
        const h = new Date().getHours();
        const part = h < 12 ? 'morning' : h < 17 ? 'afternoon' : 'evening';
        const sub = $('greeting-sub');
        if ($('greeting-title')) $('greeting-title').textContent = `Good ${part}.`;
        if (sub) sub.textContent = `Here's your analysis for ${new Date().toLocaleDateString('en-US', { weekday: 'long' })}.`;
        if ($('vitals-date')) $('vitals-date').textContent = new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
        if ($('body-date')) $('body-date').textContent = new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
        if ($('log-date-display')) $('log-date-display').textContent = new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    }

    // --- tab switching -------------------------------------------
    function switchTab(tabId) {
        state.currentTab = tabId;
        qsa('.tab-content').forEach((el) => {
            const active = el.id === tabId;
            el.classList.toggle('active', active);
            el.setAttribute('aria-hidden', active ? 'false' : 'true');
        });
        qsa('.tab-btn').forEach((b) => {
            const active = b.getAttribute('data-tab') === tabId;
            b.classList.toggle('active', active);
            b.setAttribute('aria-selected', active ? 'true' : 'false');
            b.tabIndex = active ? 0 : -1;
        });
        loadTab(tabId);
        window.scrollTo({ top: 0, behavior: 'instant' });
    }

    function handleTabKeydown(e) {
        const key = e.key;
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(key)) return;
        const tabs = qsa('.tab-btn');
        if (!tabs.length) return;
        const current = tabs.indexOf(e.currentTarget);
        if (current < 0) return;
        let next = current;
        if (key === 'ArrowLeft') next = (current - 1 + tabs.length) % tabs.length;
        else if (key === 'ArrowRight') next = (current + 1) % tabs.length;
        else if (key === 'Home') next = 0;
        else if (key === 'End') next = tabs.length - 1;
        e.preventDefault();
        tabs[next].focus();
        switchTab(tabs[next].getAttribute('data-tab'));
    }

    function focusOpenModal(modal) {
        if (!modal || modal.hidden || !modal.isConnected) return;
        if (!modal.__fit192OpenedAt) modal.__fit192OpenedAt = ++modalOpenSeq;
        if (!modal.__fit192ReturnFocus && document.activeElement && !modal.contains(document.activeElement)) {
            modal.__fit192ReturnFocus = document.activeElement;
        }
        if (modal.contains(document.activeElement)) return;
        const target = qs(MODAL_FOCUS_SELECTOR, modal);
        if (target && typeof target.focus === 'function') {
            target.focus({ preventScroll: true });
        }
    }

    function restoreModalFocus(modal) {
        const target = modal && modal.__fit192ReturnFocus;
        if (modal) modal.__fit192ReturnFocus = null;
        if (target && target.isConnected && !target.closest('[hidden]') && typeof target.focus === 'function') {
            target.focus({ preventScroll: true });
        }
    }

    function closeModal(modal) {
        if (!modal || modal.id === 'modal-active') return;
        if (typeof modal.__fit192Close === 'function') {
            modal.__fit192Close();
        } else {
            modal.hidden = true;
        }
        restoreModalFocus(modal);
    }

    function getTopmostOpenModal() {
        return qsa('.modal')
            .filter((modal) => modal.id !== 'modal-active' && !modal.hidden && modal.isConnected)
            .sort((a, b) => (a.__fit192OpenedAt || 0) - (b.__fit192OpenedAt || 0))
            .pop() || null;
    }

    function getTopmostModalForFocus() {
        return qsa('.modal')
            .filter((modal) => !modal.hidden && modal.isConnected)
            .sort((a, b) => (a.__fit192OpenedAt || 0) - (b.__fit192OpenedAt || 0))
            .pop() || null;
    }

    function handleModalEscape(e) {
        if (e.key !== 'Escape') return;
        const modal = getTopmostOpenModal();
        if (!modal) return;
        e.preventDefault();
        closeModal(modal);
    }

    function restoreFocusInsideModal(modal) {
        if (!modal || modal.contains(document.activeElement)) return;
        const focusable = getModalFocusableElements(modal);
        if (focusable.length) focusable[0].focus({ preventScroll: true });
    }

    function bindModalIframeFocusGuards(modal) {
        qsa('iframe', modal).forEach((frame) => {
            if (frame.__fit238FocusGuardBound) return;
            frame.__fit238FocusGuardBound = true;
            frame.addEventListener('blur', () => {
                window.setTimeout(() => restoreFocusInsideModal(modal), 0);
            });
        });
    }

    function getModalFocusableElements(modal) {
        bindModalIframeFocusGuards(modal);
        return qsa(MODAL_FOCUS_SELECTOR, modal).filter((el) => {
            if (el.disabled || el.hidden || el.getAttribute('aria-hidden') === 'true') return false;
            if (el.closest('[hidden]')) return false;
            return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
        });
    }

    function handleModalTabKeydown(e) {
        if (e.key !== 'Tab') return;
        const modal = getTopmostModalForFocus();
        if (!modal) return;
        const focusable = getModalFocusableElements(modal);
        if (!focusable.length) {
            e.preventDefault();
            return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        const active = document.activeElement;
        if (!modal.contains(active)) {
            e.preventDefault();
            first.focus({ preventScroll: true });
            return;
        }
        if (e.shiftKey && active === first) {
            e.preventDefault();
            last.focus({ preventScroll: true });
        } else if (!e.shiftKey && active === last) {
            e.preventDefault();
            first.focus({ preventScroll: true });
        }
    }

    function handleModalFocusin(e) {
        const modal = getTopmostModalForFocus();
        if (!modal || modal.contains(e.target)) return;
        restoreFocusInsideModal(modal);
    }

    function handleModalFocusout() {
        window.setTimeout(() => {
            const modal = getTopmostModalForFocus();
            restoreFocusInsideModal(modal);
        }, 0);
    }

    function handleModalWindowFocus() {
        window.setTimeout(() => {
            const modal = getTopmostModalForFocus();
            restoreFocusInsideModal(modal);
        }, 0);
    }

    function collectOpenModals(node, seen) {
        if (!node || node.nodeType !== 1) return;
        if (node.classList.contains('modal') && !node.hidden) seen.add(node);
        qsa('.modal', node).forEach((modal) => {
            if (!modal.hidden) seen.add(modal);
        });
    }

    function watchModalFocus() {
        qsa('.modal').forEach(focusOpenModal);
    document.addEventListener('keydown', handleModalEscape);
    document.addEventListener('keydown', handleModalTabKeydown);
    document.addEventListener('focusin', handleModalFocusin);
    document.addEventListener('focusout', handleModalFocusout);
    window.addEventListener('focus', handleModalWindowFocus);
        if (!('MutationObserver' in window)) return;
        const observer = new MutationObserver((records) => {
            const opened = new Set();
            records.forEach((record) => {
                if (record.type === 'attributes' && record.attributeName === 'hidden') {
                    const modal = record.target;
                    if (!modal.classList || !modal.classList.contains('modal')) return;
                    if (!modal.hidden) opened.add(modal);
                    else {
                        modal.__fit192ReturnFocus = null;
                        modal.__fit192OpenedAt = 0;
                    }
                } else if (record.type === 'childList') {
                    record.addedNodes.forEach((node) => collectOpenModals(node, opened));
                }
            });
            opened.forEach(focusOpenModal);
        });
        observer.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['hidden'] });
    }

    async function loadTab(tabId) {
        try {
            switch (tabId) {
                case 'tab-dashboard': await renderDashboard(); break;
                case 'tab-vitals': await renderVitals(); break;
                case 'tab-workout': await renderNextWorkout(); break;
                case 'tab-log': await prepareLog(); break;
                case 'tab-history': await renderHistory(); break;
                case 'tab-body': await renderBody(); break;
                case 'tab-stats': await renderStats(); break;
                case 'tab-settings': await renderSettings(); break;
            }
        } catch (e) {
            console.error('loadTab', tabId, e);
            toast(`Load failed: ${tabId.replace('tab-', '')}`, 'err');
        }
    }

    // --- loaders (cached) ----------------------------------------
    async function getDashboard(force = false) {
        if (!force && state.dashboard) return state.dashboard;
        state.dashboard = await api('/api/dashboard', { timeoutMs: DASHBOARD_FETCH_TIMEOUT_MS });
        if (state.dashboard && state.dashboard.next_workout) state.nextWorkout = state.dashboard.next_workout;
        return state.dashboard;
    }
    async function getNextWorkout(force = false) {
        if (!force && state.nextWorkout) return state.nextWorkout;
        const payload = await api('/api/next-workout', { timeoutMs: DASHBOARD_FETCH_TIMEOUT_MS });
        state.nextWorkout = payload && payload.next_workout ? payload.next_workout : null;
        return state.nextWorkout;
    }
    async function getVitals(force = false) {
        if (!force && state.vitals) return state.vitals;
        state.vitals = await api('/api/vitals');
        return state.vitals;
    }
    async function getOuraStatus(force = false, refreshApi = false) {
        if (!force && !refreshApi && state.oura) return state.oura;
        // FIT-129: sentinel ownership moved to renderDashboard's settle helper
        // so a stale fetch from an older render/retry can no longer flip
        // state.ouraError back on. Swallow contract preserved (returns null on
        // failure) for non-dashboard callers (renderVitals, renderSettings).
        // timeoutMs makes a hung endpoint reject after 30s instead of leaving
        // the chip silent forever.
        try { state.oura = await api('/api/oura/status' + (refreshApi ? '?refresh=true' : ''), { timeoutMs: DASHBOARD_FETCH_TIMEOUT_MS }); }
        catch { state.oura = null; }
        return state.oura;
    }
    async function getOuraSleep(force = false) {
        if (!force && state.ouraSleep) return state.ouraSleep;
        try { state.ouraSleep = await api('/api/oura/sleep-summary', { timeoutMs: DASHBOARD_FETCH_TIMEOUT_MS }); }
        catch { state.ouraSleep = null; }
        return state.ouraSleep;
    }
    async function getWhoopStatus(force = false) {
        if (!force && state.whoopStatus) return state.whoopStatus;
        try { state.whoopStatus = await api('/api/whoop/status', { timeoutMs: DASHBOARD_FETCH_TIMEOUT_MS }); }
        catch { state.whoopStatus = null; }
        return state.whoopStatus;
    }
    async function getOuraTrends(force = false) {
        if (!force && state.ouraTrends) return state.ouraTrends;
        try { state.ouraTrends = await api('/api/oura/trends'); }
        catch { state.ouraTrends = null; }
        return state.ouraTrends;
    }
    async function getReco(force = false) {
        if (!force && state.reco) return state.reco;
        try { state.reco = await api('/api/recommendation/smart', { timeoutMs: DASHBOARD_FETCH_TIMEOUT_MS }); }
        catch { state.reco = null; }
        return state.reco;
    }
    async function getInsights(force = false) {
        if (!force && state.insights) return state.insights;
        try { state.insights = await api('/api/insights'); }
        catch { state.insights = null; }
        return state.insights;
    }
    async function getHistory(force = false) {
        if (!force && state.history) return state.history;
        state.history = await api('/api/history-all');
        return state.history;
    }
    async function getAppleHealthWorkouts(days = 90, force = false) {
        const key = `aw_${days}`;
        if (!force && state[key]) return state[key];
        try {
            const r = await api(`/api/apple-health/workouts?days=${days}`);
            state[key] = r && Array.isArray(r.workouts) ? r.workouts : [];
        } catch { state[key] = []; }
        return state[key];
    }
    async function getBody(force = false) {
        if (!force && state.body) return state.body;
        state.body = await api('/api/body-history');
        return state.body;
    }
    async function getSettings(force = false) {
        if (!force && state.settings) return state.settings;
        state.settings = await api('/api/settings');
        return state.settings;
    }
    async function getAnalytics(force = false) {
        if (!force && state.analytics) return state.analytics;
        try { state.analytics = await api('/api/analytics/advanced'); }
        catch { state.analytics = null; }
        return state.analytics;
    }
    async function getMuscleFatigue(force = false) {
        if (!force && state.muscleFatigue) return state.muscleFatigue;
        try { state.muscleFatigue = await api('/api/muscle-fatigue'); }
        catch { state.muscleFatigue = null; }
        return state.muscleFatigue;
    }
    async function getExercises(force = false) {
        if (!force && state.exercises) return state.exercises;
        try {
            const r = await api('/api/exercises');
            state.exercises = r && r.exercises ? r.exercises : Array.isArray(r) ? r : [];
        } catch { state.exercises = []; }
        return state.exercises;
    }

    function invalidateCaches() {
        state.dashboard = state.vitals = state.oura = state.ouraSleep = null;
        state.ouraTrends = state.reco = state.insights = state.history = null;
        state.body = state.settings = state.analytics = state.muscleFatigue = state.nextWorkout = null;
    }

    // --- Freshness chip rendering (FIT-2) -------------------------
    const RECO_CONF_LABEL = { high: '92%', medium: '78%', low: '62%' };

    function humanizeMuscle(s) {
        return String(s || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()).trim();
    }

    function formatFoodChip(food, ago) {
        // Four states per FIT-2 spec: no food logged, pending estimate review,
        // accepted food, over/under target. (pending_review is a backend stub
        // that FIT-6 will flip once the photo → AI estimate flow exists.)
        if (food && food.pending_review) {
            return { cls: 'warn', label: 'Food · pending review' };
        }
        if (!food || food.status === 'missing' || (food.target_state === 'none')) {
            return { cls: 'warn', label: 'No food logged today' };
        }
        if (food.target_state === 'over') {
            return { cls: 'stale', label: 'Food · over target' };
        }
        if (food.target_state === 'under') {
            return { cls: 'warn', label: 'Food · under target' };
        }
        // on_track / fresh accepted log
        return { cls: 'ok', label: 'Food · on track' };
    }

    function formatOuraChip(oura, ago) {
        if (!oura || oura.status === 'unknown' || oura.status == null) {
            return { cls: 'unknown', label: 'Oura · —' };
        }
        if (oura.status === 'missing') {
            return { cls: 'stale', label: 'Oura · no data' };
        }
        // Real Oura state: combine source (cached/live) + relative last-data-point age
        const sourceLabel = oura.source === 'live' ? 'live' : 'cached';
        const ageLabel = ago(oura.last_data_point) || 'today';
        const label = 'Oura · ' + sourceLabel + ' · ' + ageLabel;
        if (oura.status === 'fresh')  return { cls: 'ok',    label };
        if (oura.status === 'aging')  return { cls: 'warn',  label };
        if (oura.status === 'stale')  return { cls: 'stale', label };
        return { cls: 'unknown', label };
    }

    function formatAppleChip(apple, ago) {
        if (!apple || apple.status === 'unknown' || apple.status == null) {
            return { cls: 'unknown', label: 'Apple · —', title: 'Apple Health freshness' };
        }
        if (apple.status === 'missing') {
            return { cls: 'stale', label: 'Apple · no data', title: 'Apple Health freshness · no data received' };
        }
        // FIT-113: collapse the two-signal label into a single short chip so it
        // stays on one line at 390/375 widths. The dual-signal detail
        // (sync attempt vs data point — useful when sync just ran but the
        // watch isn't writing) moves to the chip's `title` attribute so
        // users can hover / long-press for the full story without
        // crowding the reco card.
        const syncedAgo = ago(apple.last_sync_attempt);
        const dataAgo = ago(apple.last_data_point);
        const primary = dataAgo || syncedAgo;
        const label = primary ? 'Apple · ' + primary : 'Apple · —';
        const title = (syncedAgo && dataAgo && syncedAgo !== dataAgo)
            ? `Apple Health · synced ${syncedAgo} · data ${dataAgo}`
            : 'Apple Health freshness';
        if (apple.status === 'fresh')  return { cls: 'ok',    label, title };
        if (apple.status === 'aging')  return { cls: 'warn',  label, title };
        if (apple.status === 'stale')  return { cls: 'stale', label, title };
        return { cls: 'unknown', label, title };
    }

    // Required WHOOP UI states: connected, disconnected, syncing, fresh,
    // aging, stale, missing/no-data, pending score, unscorable,
    // calibrating, reauth required, CSV only, source conflict, error.
    const WHOOP_UI_STATES = {
        connected: 'connected',
        disconnected: 'disconnected',
        syncing: 'syncing',
        fresh: 'fresh',
        aging: 'aging',
        stale: 'stale',
        missing: 'missing',
        pending_score: 'pending_score',
        unscorable: 'unscorable',
        calibrating: 'calibrating',
        reauth_required: 'reauth_required',
        csv_only: 'csv_only',
        source_conflict: 'source_conflict',
        error: 'error',
    };

    function normalizeSourceKey(value) {
        const raw = String(value == null ? '' : value).trim().toLowerCase();
        if (!raw) return '';
        if (raw === 'apple' || raw === 'apple health' || raw === 'apple-health') return 'apple_health';
        if (raw === 'whoop' || raw === 'official_whoop') return 'whoop';
        if (raw === 'oura' || raw === 'oura ring') return 'oura';
        if (raw === 'noop' || raw === 'noop import') return 'noop';
        if (raw === 'food' || raw === 'food log' || raw === 'nutrition') return 'food';
        return raw.replace(/\s+/g, '_');
    }

    function sourceDisplayName(value) {
        const key = normalizeSourceKey(value);
        if (key === 'whoop') return 'WHOOP';
        if (key === 'oura') return 'Oura';
        if (key === 'apple_health') return 'Apple Health';
        if (key === 'noop') return 'Noop import';
        if (key === 'food') return 'Food log';
        if (!key) return 'Unknown source';
        return key.replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
    }

    function normalizeWhoopStateToken(value) {
        const raw = String(value == null ? '' : value)
            .trim()
            .toLowerCase()
            .replace(/\s+/g, '_')
            .replace(/-/g, '_');
        if (!raw) return '';
        if (raw === 'no_data' || raw === 'missing/no_data' || raw === 'missing/no-data' || raw === 'no-data') return WHOOP_UI_STATES.missing;
        if (raw === 'pending' || raw === 'pending_score') return WHOOP_UI_STATES.pending_score;
        if (raw === 'reauth' || raw === 'reauth_required') return WHOOP_UI_STATES.reauth_required;
        if (raw === 'missing_config') return 'missing_config';
        if (raw === 'csv' || raw === 'csv_only') return WHOOP_UI_STATES.csv_only;
        if (raw === 'source_conflict' || raw === 'conflict') return WHOOP_UI_STATES.source_conflict;
        return raw;
    }

    function normalizeSourceConflicts(conflicts) {
        if (!Array.isArray(conflicts)) return [];
        return conflicts.map(function (entry) {
            if (typeof entry === 'string') {
                return { providers: [], message: entry.trim() };
            }
            const providers = [
                entry && entry.provider,
                entry && entry.provider_a,
                entry && entry.provider_b,
                entry && entry.left,
                entry && entry.right,
            ]
                .filter(Boolean)
                .map(normalizeSourceKey);
            const message = entry && (entry.message || entry.summary || entry.reason || entry.detail || '');
            return {
                providers,
                message: String(message || '').trim(),
            };
        });
    }

    function recommendationSourceConflictNode(payload) {
        const node = payload && payload.recommendation_sources && payload.recommendation_sources.source_conflict;
        if (!node || node.has_conflict !== true) return null;
        return {
            providers: [node.whoop_band ? 'whoop' : null, node.oura_band ? 'oura' : null].filter(Boolean),
            message: node.explanation || 'Wearable sources disagree, so the recommendation stays conservative.',
        };
    }

    function collectSourceConflicts(dash, reco) {
        const conflicts = []
            .concat((dash && dash.source_conflicts) || [])
            .concat((reco && reco.source_conflicts) || []);
        const dashConflict = recommendationSourceConflictNode(dash);
        const recoConflict = recommendationSourceConflictNode(reco);
        if (dashConflict) conflicts.push(dashConflict);
        if (recoConflict) conflicts.push(recoConflict);
        return normalizeSourceConflicts(conflicts);
    }

    function conflictTouchesProvider(conflict, providerKey) {
        const key = normalizeSourceKey(providerKey);
        if (!conflict) return false;
        return Array.isArray(conflict.providers) && conflict.providers.indexOf(key) >= 0;
    }

    function firstWhoopConflict(conflicts) {
        return normalizeSourceConflicts(conflicts).find(function (conflict) {
            return conflictTouchesProvider(conflict, 'whoop');
        }) || null;
    }

    function resolveWhoopUiState(whoop, conflicts) {
        const status = normalizeWhoopStateToken(
            whoop && (
                whoop.ui_state
                || whoop.status
                || whoop.connection_status
                || whoop.freshness_status
                || whoop.score_state
                || whoop.mode
            )
        );
        if (whoop && (whoop.error || status === WHOOP_UI_STATES.error)) return WHOOP_UI_STATES.error;
        if (whoop && (whoop.syncing || status === WHOOP_UI_STATES.syncing)) return WHOOP_UI_STATES.syncing;
        if (whoop && (whoop.reauth_required || status === WHOOP_UI_STATES.reauth_required)) return WHOOP_UI_STATES.reauth_required;
        if (firstWhoopConflict(conflicts) || (whoop && (whoop.source_conflict || status === WHOOP_UI_STATES.source_conflict))) {
            return WHOOP_UI_STATES.source_conflict;
        }
        if (whoop && (whoop.calibrating || status === WHOOP_UI_STATES.calibrating)) return WHOOP_UI_STATES.calibrating;
        if (whoop && (whoop.pending_score || status === WHOOP_UI_STATES.pending_score)) return WHOOP_UI_STATES.pending_score;
        if (whoop && (whoop.unscorable || status === WHOOP_UI_STATES.unscorable)) return WHOOP_UI_STATES.unscorable;
        if (whoop && (whoop.csv_only || normalizeWhoopStateToken(whoop.source_kind) === WHOOP_UI_STATES.csv_only || status === WHOOP_UI_STATES.csv_only)) {
            return WHOOP_UI_STATES.csv_only;
        }
        if (status === WHOOP_UI_STATES.fresh || status === WHOOP_UI_STATES.aging || status === WHOOP_UI_STATES.stale) return status;
        if (status === WHOOP_UI_STATES.missing || whoop && (whoop.no_data || whoop.has_data === false)) return WHOOP_UI_STATES.missing;
        if (status === WHOOP_UI_STATES.disconnected || status === 'missing_config' || whoop && whoop.connected === false) return WHOOP_UI_STATES.disconnected;
        if (whoop && (whoop.connected || whoop.connected_at || whoop.last_sync_at || whoop.last_successful_sync_at)) return WHOOP_UI_STATES.connected;
        return WHOOP_UI_STATES.disconnected;
    }

    function mergeWhoopFreshnessNode(freshnessNode, whoopStatus, conflicts) {
        const merged = Object.assign({}, freshnessNode || {}, whoopStatus || {});
        if (freshnessNode && freshnessNode.status) merged.status = freshnessNode.status;
        if (freshnessNode && freshnessNode.score_state) merged.score_state = freshnessNode.score_state;
        if (freshnessNode && Object.prototype.hasOwnProperty.call(freshnessNode, 'last_data_point')) merged.last_data_point = freshnessNode.last_data_point;
        const conflict = firstWhoopConflict(conflicts);
        if (conflict && conflict.message && !merged.conflict_message) merged.conflict_message = conflict.message;
        merged.ui_state = resolveWhoopUiState(merged, conflicts);
        if (!merged.status) merged.status = merged.ui_state;
        return merged;
    }

    function formatWhoopChip(whoop, ago) {
        const uiState = resolveWhoopUiState(whoop, whoop && whoop.conflicts);
        const dataAgo = ago((whoop && (whoop.last_data_point || whoop.local_date)) || null);
        const syncAgo = ago((whoop && (whoop.last_successful_sync_at || whoop.last_sync_at)) || null);
        const ageDetail = dataAgo || syncAgo || '';
        if (uiState === WHOOP_UI_STATES.syncing) {
            return { cls: 'warn', label: 'WHOOP · syncing', title: 'WHOOP manual sync in progress' };
        }
        if (uiState === WHOOP_UI_STATES.fresh) {
            return { cls: 'ok', label: 'WHOOP · fresh', title: ageDetail ? `WHOOP freshness · ${ageDetail}` : 'WHOOP freshness · fresh' };
        }
        if (uiState === WHOOP_UI_STATES.aging) {
            return { cls: 'warn', label: 'WHOOP · aging', title: ageDetail ? `WHOOP freshness · ${ageDetail}` : 'WHOOP freshness · aging' };
        }
        if (uiState === WHOOP_UI_STATES.stale) {
            return { cls: 'stale', label: 'WHOOP · stale', title: ageDetail ? `WHOOP freshness · ${ageDetail}` : 'WHOOP freshness · stale' };
        }
        if (uiState === WHOOP_UI_STATES.pending_score) {
            return { cls: 'warn', label: 'WHOOP · pending score', title: 'Latest WHOOP day is pending score' };
        }
        if (uiState === WHOOP_UI_STATES.unscorable) {
            return { cls: 'warn', label: 'WHOOP · unscorable', title: 'Latest WHOOP day cannot be scored yet' };
        }
        if (uiState === WHOOP_UI_STATES.calibrating) {
            return { cls: 'warn', label: 'WHOOP · calibrating', title: 'WHOOP is still calibrating' };
        }
        if (uiState === WHOOP_UI_STATES.reauth_required) {
            return { cls: 'stale', label: 'WHOOP · reauth required', title: 'WHOOP needs to be reconnected before the next sync' };
        }
        if (uiState === WHOOP_UI_STATES.csv_only) {
            return { cls: 'warn', label: 'WHOOP · CSV only', title: 'WHOOP data is available only from CSV import right now' };
        }
        if (uiState === WHOOP_UI_STATES.source_conflict) {
            return { cls: 'stale', label: 'WHOOP · source conflict', title: whoop && whoop.conflict_message ? whoop.conflict_message : 'WHOOP and another wearable disagree' };
        }
        if (uiState === WHOOP_UI_STATES.error) {
            return { cls: 'stale', label: 'WHOOP · error', title: whoop && whoop.error ? String(whoop.error) : 'WHOOP status error' };
        }
        if (uiState === WHOOP_UI_STATES.missing) {
            return { cls: 'stale', label: 'WHOOP · no data', title: 'WHOOP is connected, but no scored data is available yet' };
        }
        if (uiState === WHOOP_UI_STATES.connected) {
            return { cls: 'ok', label: 'WHOOP · connected', title: syncAgo ? `WHOOP connected · last sync ${syncAgo}` : 'WHOOP connected' };
        }
        return { cls: 'unknown', label: 'WHOOP · disconnected', title: 'WHOOP is not connected' };
    }

    function normalizeRecommendationSources(entries) {
        if (entries && !Array.isArray(entries) && typeof entries === 'object') {
            const normalizedEntries = [];
            if (entries.whoop) {
                const whoop = entries.whoop;
                const detailParts = []
                    .concat(whoop.explanations || [])
                    .concat((whoop.applied_modifiers || []).map(function (item) { return `modifier: ${item}`; }));
                normalizedEntries.push({
                    key: 'whoop',
                    label: 'WHOOP',
                    role: whoop.display_only ? 'display only' : 'modifier',
                    detail: detailParts.join(' · ') || 'WHOOP recovery context is available.',
                });
            }
            if (entries.load_source) {
                normalizedEntries.push({
                    key: entries.load_source,
                    label: sourceDisplayName(entries.load_source),
                    role: 'load source',
                    detail: `${sourceDisplayName(entries.load_source)} remains the training-load source.`,
                });
            }
            entries = normalizedEntries;
        }
        if (!Array.isArray(entries)) return [];
        return entries.map(function (entry) {
            if (typeof entry === 'string') {
                const key = normalizeSourceKey(entry);
                return { key, label: sourceDisplayName(key), role: '', detail: '' };
            }
            const key = normalizeSourceKey(entry && (entry.provider || entry.source || entry.key || entry.name));
            const role = String(entry && (entry.role || entry.used_for || entry.kind || '') || '').trim();
            const detail = String(entry && (entry.detail || entry.reason || entry.summary || '') || '').trim();
            return {
                key,
                label: sourceDisplayName(key),
                role,
                detail,
            };
        }).filter(function (entry) { return entry.key; });
    }

    function normalizeWearableSources(entries) {
        if (!Array.isArray(entries)) return [];
        return entries.map(function (entry) {
            if (typeof entry === 'string') {
                const key = normalizeSourceKey(entry);
                return { key, label: sourceDisplayName(key), state: '', detail: '' };
            }
            const key = normalizeSourceKey(entry && (entry.provider || entry.source || entry.key || entry.name));
            const stateText = String(entry && (entry.status || entry.freshness || entry.state || '') || '').trim();
            const detail = String(entry && (entry.detail || entry.summary || entry.reason || '') || '').trim();
            return {
                key,
                label: sourceDisplayName(key),
                state: stateText,
                detail,
            };
        }).filter(function (entry) { return entry.key; });
    }

    const DASHBOARD_FRESHNESS_SLOTS = [
        { id: 'reco-fresh-whoop', key: 'whoop',        render: formatWhoopChip },
        { id: 'reco-fresh-oura',  key: 'oura',         render: formatOuraChip  },
        { id: 'reco-fresh-apple', key: 'apple_health', render: formatAppleChip },
        { id: 'reco-fresh-food',  key: 'food',         render: formatFoodChip  },
    ];
    const SETTINGS_FRESHNESS_SLOTS = [
        { id: 'whoop-connect-state', key: 'whoop',        render: formatWhoopChip },
        { id: 'oura-connect-state',  key: 'oura',         render: formatOuraChip  },
        { id: 'apple-connect-state', key: 'apple_health', render: formatAppleChip },
    ];

    function renderFreshnessChips(freshness, slots) {
        slots = slots || DASHBOARD_FRESHNESS_SLOTS;
        const ago = (window.__dashHelpers && window.__dashHelpers.ago) || function (s) { return s || ''; };
        slots.forEach(function (slot) {
            const el = $(slot.id);
            if (!el) return;
            const node = freshness ? freshness[slot.key] : null;
            const { cls, label, title } = slot.render(node, ago);
            el.classList.remove('ok', 'warn', 'stale', 'unknown');
            el.classList.add(cls);
            el.textContent = label;
            // FIT-113: formatters can return an optional `title` to push
            // long-form detail into the chip's tooltip instead of the
            // visible label. Existing `title` is preserved when the
            // formatter omits one (e.g. Oura, Food still return {cls,label}).
            if (typeof title === 'string') el.setAttribute('title', title);
        });
    }

    // FIT-111: populate the four Settings group header chips. Reads the
    // same freshness block the integration chips use + the existing
    // notification / coach state chips. No new endpoints; pure DOM
    // derivation from data already on the page after renderFreshnessChips
    // has run. Safe to call even if some chips are missing.
    function applyGroupChip(el, cls, label) {
        if (!el) return;
        el.classList.remove('ok', 'warn', 'stale', 'unknown');
        el.classList.add(cls || 'unknown');
        el.textContent = label;
    }

    function _readChipState(chip) {
        if (!chip) return { cls: 'unknown', text: '' };
        const cls = ['ok', 'warn', 'stale', 'unknown'].find((c) => chip.classList.contains(c)) || 'unknown';
        const text = (chip.textContent || '').trim();
        return { cls, text: text === '—' ? '' : text };
    }

    // FIT-111: derive every group chip from the LIVE inner-chip state in
    // the DOM. No `freshness` parameter — that way the same helper stays
    // accurate after async renderers (renderAiCoachHealth /
    // renderPushSection / enablePush / disablePush / sendPushTest)
    // update the inner chips. Each of those callers calls this at the
    // tail of their work; safe to call any time after the initial
    // settings render.
    function renderSettingsGroupSummaries() {
        // Data sources: count fresh vs stale by inspecting the live
        // SETTINGS_FRESHNESS_SLOTS chips (already populated by
        // renderFreshnessChips, which runs before this helper).
        let fresh = 0, stale = 0;
        SETTINGS_FRESHNESS_SLOTS.forEach((slot) => {
            const chip = $(slot.id);
            if (!chip) return;
            if (chip.classList.contains('ok')) fresh++;
            else if (chip.classList.contains('warn') || chip.classList.contains('stale')) stale++;
        });
        const total = fresh + stale;
        applyGroupChip($('settings-group-summary-data-sources'),
            stale ? 'warn' : (fresh ? 'ok' : 'unknown'),
            total ? `${fresh} fresh · ${stale} stale` : '—');

        // Notifications: mirror the push-state-chip exactly.
        const push = _readChipState($('push-state-chip'));
        applyGroupChip($('settings-group-summary-notifications'),
            push.cls,
            push.text || 'Not configured');

        // Coaching setup: mirror the AI primary chip (the only one with
        // a live freshness signal in this group).
        const ai = _readChipState($('ai-primary-state'));
        applyGroupChip($('settings-group-summary-coaching'),
            ai.cls,
            ai.text ? `AI ${ai.text.toLowerCase()}` : 'Configured');

        // Maintenance: mirror the last-backup chip.
        const backup = _readChipState($('last-backup'));
        applyGroupChip($('settings-group-summary-maintenance'),
            backup.cls,
            backup.text ? `Backup ${backup.text}` : 'No recent backup');
    }

    function buildRecommendationSourcesSummary(sources) {
        if (!sources.length) return 'Recommendation source detail is not available yet.';
        return 'Using ' + sources.map(function (entry) {
            return entry.role ? `${entry.label} for ${entry.role}` : entry.label;
        }).join(', ') + '.';
    }

    function buildWearableSignalsSummary(wearables) {
        if (!wearables.length) return 'Wearable freshness has not loaded yet.';
        return wearables.map(function (entry) {
            return entry.state ? `${entry.label} · ${String(entry.state).replace(/_/g, ' ')}` : entry.label;
        }).join(' · ');
    }

    function renderSourceList(hostId, items, emptyText, detailFormatter) {
        const host = $(hostId);
        if (!host) return;
        host.innerHTML = '';
        if (!items.length) {
            const empty = document.createElement('div');
            empty.className = 'reco-source-list-empty';
            empty.textContent = emptyText;
            host.appendChild(empty);
            return;
        }
        items.forEach(function (item) {
            const row = document.createElement('div');
            row.className = 'reco-source-item';

            const head = document.createElement('div');
            head.className = 'reco-source-item-head';

            const name = document.createElement('span');
            name.className = 'reco-source-item-name';
            name.textContent = item.label || sourceDisplayName(item.key);
            head.appendChild(name);

            const tag = document.createElement('span');
            tag.className = 'state-chip state-chip-sm';
            const tone = normalizeWhoopStateToken(item.state || item.role || item.key);
            if (tone === WHOOP_UI_STATES.fresh || tone === WHOOP_UI_STATES.connected) tag.classList.add('ok');
            else if (tone === WHOOP_UI_STATES.aging || tone === WHOOP_UI_STATES.pending_score || tone === WHOOP_UI_STATES.unscorable || tone === WHOOP_UI_STATES.calibrating || tone === WHOOP_UI_STATES.csv_only || tone === 'load') tag.classList.add('warn');
            else if (tone === WHOOP_UI_STATES.stale || tone === WHOOP_UI_STATES.reauth_required || tone === WHOOP_UI_STATES.source_conflict || tone === WHOOP_UI_STATES.error || tone === WHOOP_UI_STATES.missing) tag.classList.add('stale');
            else tag.classList.add('unknown');
            tag.textContent = String(item.role || item.state || 'available').replace(/_/g, ' ');
            head.appendChild(tag);

            row.appendChild(head);

            const detail = document.createElement('div');
            detail.className = 'reco-source-item-detail';
            detail.textContent = detailFormatter ? detailFormatter(item) : (item.detail || 'No extra detail');
            row.appendChild(detail);
            host.appendChild(row);
        });
    }

    function renderRecommendationSourceSummary(dash, reco, freshness) {
        const recommendationSources = normalizeRecommendationSources(
            (dash && dash.recommendation_sources) || (reco && reco.recommendation_sources) || []
        );
        let wearableSources = normalizeWearableSources((dash && dash.wearable_sources) || []);
        if (!wearableSources.length && freshness) {
            wearableSources = ['whoop', 'oura', 'apple_health']
                .filter(function (key) { return freshness[key]; })
                .map(function (key) {
                    const node = freshness[key];
                    return {
                        key,
                        label: sourceDisplayName(key),
                        state: key === 'whoop' ? resolveWhoopUiState(node, collectSourceConflicts(dash, reco)) : (node && node.status) || '',
                        detail: node && (node.detail || node.summary || node.last_data_point || ''),
                    };
                });
        }
        const conflicts = collectSourceConflicts(dash, reco);
        const summary = $('reco-sources-summary');
        const note = $('reco-sources-note');
        const conflictEl = $('reco-source-conflict');
        const openBtn = $('btn-reco-sources');
        const drawerSummary = $('reco-sources-drawer-summary');

        if (summary) summary.textContent = buildRecommendationSourcesSummary(recommendationSources);
        if (note) note.textContent = buildWearableSignalsSummary(wearableSources);
        if (drawerSummary) drawerSummary.textContent = summary ? summary.textContent : 'Recommendation source detail is not available yet.';
        if (openBtn) openBtn.disabled = !recommendationSources.length && !wearableSources.length && !conflicts.length;

        if (conflictEl) {
            if (conflicts.length) {
                conflictEl.hidden = false;
                conflictEl.textContent = conflicts[0].message || 'Wearable sources disagree, so the recommendation stays conservative.';
            } else {
                conflictEl.hidden = true;
                conflictEl.textContent = '';
            }
        }

        renderSourceList(
            'reco-sources-used',
            recommendationSources,
            'No recommendation source detail yet.',
            function (entry) {
                return entry.detail || (entry.role ? `${entry.label} contributes ${entry.role}.` : `${entry.label} is available to the recommendation.` );
            }
        );
        renderSourceList(
            'reco-sources-signals',
            wearableSources,
            'No wearable source detail yet.',
            function (entry) {
                return entry.detail || (entry.state ? `${entry.label} is currently ${entry.state}.` : `${entry.label} has no current state detail.`);
            }
        );
        renderSourceList(
            'reco-sources-conflicts',
            conflicts.map(function (entry) {
                return {
                    key: 'conflict',
                    label: 'Source conflict',
                    role: 'review',
                    detail: entry.message || 'Recommendation uses the conservative plan until the conflict clears.',
                };
            }),
            'No source conflicts.',
            function (entry) { return entry.detail; }
        );
    }

    function openRecoSourcesModal() {
        renderRecommendationSourceSummary(state.dashboard, state.reco, state.dashboard && state.dashboard.freshness);
        const modal = $('modal-reco-sources');
        const trigger = $('btn-reco-sources');
        if (!modal) return;
        modal.__fit192Close = function () {
            modal.hidden = true;
            if (trigger) trigger.setAttribute('aria-expanded', 'false');
        };
        modal.hidden = false;
        if (trigger) trigger.setAttribute('aria-expanded', 'true');
        focusOpenModal(modal);
    }

    function currentWhoopConnectUrl() {
        const whoop = state.whoopStatus;
        return whoop && (
            whoop.connect_url
            || whoop.authorization_url
            || whoop.authorize_url
            || (whoop.actions && whoop.actions.connect_url)
        ) || '';
    }

    async function connectWhoop() {
        const url = currentWhoopConnectUrl();
        if (url) {
            window.location.assign(url);
            return;
        }
        try {
            const body = await api('/api/whoop/connect/start', { method: 'POST' });
            const nextUrl = body && (body.authorization_url || body.url);
            if (!nextUrl) {
                toast('WHOOP connect flow is not available yet.', 'warn');
                return;
            }
            state.whoopStatus = Object.assign({}, state.whoopStatus || {}, body.connection || {}, { authorization_url: nextUrl });
            window.location.assign(nextUrl);
        } catch (err) {
            toast((err && err.message) || 'WHOOP connect failed.', 'error');
        }
    }

    function setWhoopActionButtons(whoop, uiState) {
        const connectBtn = $('btn-connect-whoop');
        const syncBtn = $('btn-sync-whoop');
        const disconnectBtn = $('btn-disconnect-whoop');
        const connectUrl = currentWhoopConnectUrl();
        const disconnected = uiState === WHOOP_UI_STATES.disconnected;
        const missingConfig = uiState === 'missing_config';
        const reauth = uiState === WHOOP_UI_STATES.reauth_required;
        const busySync = state.whoopUi.syncInFlight || uiState === WHOOP_UI_STATES.syncing;
        const busyDisconnect = state.whoopUi.disconnectInFlight;

        if (connectBtn) {
            connectBtn.hidden = !(disconnected || missingConfig || reauth || connectUrl);
            connectBtn.disabled = busySync || busyDisconnect;
            connectBtn.textContent = reauth ? 'Reconnect' : 'Connect';
        }
        if (syncBtn) {
            syncBtn.hidden = disconnected && !connectUrl;
            syncBtn.disabled = busySync || busyDisconnect || disconnected || reauth;
            syncBtn.textContent = busySync ? 'Syncing…' : 'Sync';
        }
        if (disconnectBtn) {
            disconnectBtn.hidden = disconnected && !connectUrl;
            disconnectBtn.disabled = busySync || busyDisconnect || disconnected;
            disconnectBtn.textContent = busyDisconnect ? 'Disconnecting…' : 'Disconnect';
        }
    }

    function buildFoodGuidanceLine(food) {
        // Returns a sentence explaining whether today's food changed/should change
        // the remaining-day guidance. Always render something (per FIT-1 acceptance
        // criterion: the UI must explain whether food logged today changed guidance).
        if (!food) {
            return 'Log food to see remaining macros for today.';
        }
        if (food.pending_review) {
            return 'Food estimates pending review — accept or correct to update macro guidance.';
        }
        const cal = food.calories || 0;
        const calT = food.calories_target || 0;
        const pro = food.protein_g || 0;
        const proT = food.protein_target_g || 0;
        if (food.target_state === 'none' || cal === 0) {
            return 'No food logged yet. Log meals for personalized macro guidance.';
        }
        if (food.target_state === 'over') {
            return `Over calorie target (${cal}/${calT} cal) — ease intensity if you trained heavy.`;
        }
        if (food.target_state === 'under') {
            const remCal = Math.max(0, calT - cal);
            const remPro = Math.max(0, Math.round(proT - pro));
            return `${cal}/${calT} cal · ${pro}/${proT}g protein logged · ${remCal} cal and ${remPro}g protein remaining.`;
        }
        // on_track
        return `${cal}/${calT} cal · ${pro}/${proT}g protein logged — on track.`;
    }

    // --- Macro status card (FIT-23) ------------------------------
    // Status threshold for each macro: under <80%, on-track 80-110%, over >110%.
    function macroStatusClass(pct) {
        if (pct > 110) return 'macro-status-over';
        if (pct >= 80) return 'macro-status-ok';
        return 'macro-status-under';
    }

    function renderMacroRow(key, consumed, target, pct) {
        const consumedEl = $(`macro-${key}-consumed`);
        const targetEl = $(`macro-${key}-target`);
        const fill = $(`macro-${key}-fill`);
        if (consumedEl) consumedEl.textContent = fmtInt(consumed);
        if (targetEl) targetEl.textContent = fmtInt(target);
        if (fill) {
            fill.style.width = Math.min(Math.max(pct || 0, 0), 100) + '%';
            fill.className = 'macro-progress-fill ' + macroStatusClass(pct || 0);
        }
    }

    function renderMacroCard(n) {
        const card = $('macro-card');
        const body = $('macro-body');
        const empty = $('macro-empty');
        const sub = $('macro-card-sub');
        if (!card || !body || !empty) return;
        const entries = n && n.entries_count ? Number(n.entries_count) : 0;
        const coaching = n && n.coaching_context ? n.coaching_context : null;
        const pendingReview = coaching && Number(coaching.pending_review_count) || 0;
        if (!n || entries <= 0) {
            body.hidden = true;
            empty.hidden = false;
            if (sub) sub.textContent = pendingReview ? `${pendingReview} pending review` : 'no entries';
            renderFoodContext(n);
            return;
        }
        empty.hidden = true;
        body.hidden = false;
        if (sub) sub.textContent = entries + ' ' + (entries === 1 ? 'entry' : 'entries');
        renderMacroRow('cal',     n.calories,  n.calories_target,  n.calories_pct);
        renderMacroRow('protein', n.protein_g, n.protein_target_g, n.protein_pct);
        renderMacroRow('carbs',   n.carbs_g,   n.carbs_target_g,   n.carbs_pct);
        renderMacroRow('fat',     n.fat_g,     n.fat_target_g,     n.fat_pct);
        const sodiumEl = $('macro-sodium-total');
        if (sodiumEl) sodiumEl.textContent = fmtInt(n.sodium_mg);
        renderFoodContext(n);
    }

    function renderFoodContext(n) {
        const chipsHost = $('food-context-chips');
        const nextDayHost = $('food-context-nextday');
        if (!chipsHost || !nextDayHost) return;
        const coaching = n && n.coaching_context ? n.coaching_context : n;
        if (!coaching || !Array.isArray(coaching.warnings)) {
            chipsHost.innerHTML = '';
            chipsHost.hidden = true;
            nextDayHost.hidden = true;
            nextDayHost.textContent = '';
            return;
        }
        const legacyRemaining = coaching.remaining || {};
        const remaining = {
            calories: Number.isFinite(Number(n && n.calories_remaining))
                ? Number(n.calories_remaining)
                : legacyRemaining.calories,
            protein_g: Number.isFinite(Number(n && n.protein_gap_g))
                ? Number(n.protein_gap_g)
                : legacyRemaining.protein_g,
        };
        const pendingReview = Number(coaching.pending_review_count) || 0;
        const chipBuilders = {
            calories_over_target: () => ({
                tone: 'warn',
                text: Number.isFinite(remaining.calories) && remaining.calories < 0
                    ? `${fmtInt(Math.abs(remaining.calories))} kcal over target`
                    : 'Over calorie target',
            }),
            calories_remaining: () => ({
                tone: 'info',
                text: Number.isFinite(remaining.calories) && remaining.calories > 0
                    ? `${fmtInt(remaining.calories)} kcal remaining`
                    : 'Calories remaining',
            }),
            protein_gap: () => ({
                tone: 'info',
                text: Number.isFinite(remaining.protein_g) && remaining.protein_g > 0
                    ? `${fmtDecimal(remaining.protein_g)} g protein gap`
                    : 'Protein gap',
            }),
            under_fueled_hard_workout: () => ({
                tone: 'warn',
                text: 'Under-fueled for today’s hard training',
            }),
            food_pending_review: () => ({
                tone: 'pending',
                text: pendingReview === 1 ? '1 estimate pending review' : `${pendingReview || ''} estimates pending review`.trim(),
            }),
        };
        const chips = [];
        coaching.warnings.forEach((w) => {
            const build = chipBuilders[w && w.code];
            if (!build) return;
            const chip = build();
            if (!chip || !chip.text) return;
            chips.push(`<span class="food-context-chip food-context-chip-${chip.tone}">${escapeHtml(chip.text)}</span>`);
        });
        chipsHost.innerHTML = chips.join('');
        chipsHost.hidden = chips.length === 0;

        const nextDay = coaching.next_day_context || {};
        const noteParts = [];
        if (nextDay.high_sodium) noteParts.push('high sodium');
        if (nextDay.late_meal) noteParts.push('late meal');
        if (noteParts.length) {
            const joined = noteParts.join(' · ');
            nextDayHost.textContent = `Interpretation context: ${joined} — may shift tomorrow’s readiness reading.`;
            nextDayHost.hidden = false;
        } else {
            nextDayHost.textContent = '';
            nextDayHost.hidden = true;
        }
    }

    async function refreshMacroCard() {
        state.dashboard = null;
        const dash = await getDashboard(true);
        renderMacroCard(dash && dash.nutrition_today);
        fetchFoodLogRefreshNotices().catch((err) => console.warn('food-log refresh notices failed:', err));
        fetchWorkoutAdaptationNotices().catch((err) => console.warn('workout adaptation notices failed:', err));
    }

    // --- Dashboard render ----------------------------------------
    // FIT-125: render each card as its API resolves instead of awaiting
    // Promise.all on the slowest call. Cards stay in their HTML placeholder
    // ("--", "Loading…", etc.) until their data lands, and each paint reads
    // from state so it tolerates the other endpoints not having returned yet.
    async function renderDashboard() {
        // FIT-129: capture both gens at the top so stale fetches landing
        // after a newer render OR after a retry click can be dropped before
        // they touch the sentinels. dashboardRenderGen guards against
        // overlapping renderDashboard() calls; sentinelGens guard against an
        // older same-render fetch flipping a sentinel after a retry click
        // has already superseded it.
        const gen = ++dashboardRenderGen;
        const sentinelGens = {
            ouraError:      ++dashboardSentinelGen.ouraError,
            recoError:      ++dashboardSentinelGen.recoError,
            ouraSleepError: ++dashboardSentinelGen.ouraSleepError,
        };

        // Paint once with whatever's already in cache. Per-field guards inside
        // paintDashboardFromState (added in FIT-127) keep the cold-open empty
        // state from injecting misleading defaults — the painters skip cards
        // whose backing data is fully absent, so the HTML placeholders
        // ('--', '—', empty gauge <div>) stay in place until real data lands.
        //
        // FIT-128: reset per-card error sentinels at the top of each render so
        // a card that recovered since the last navigation isn't stuck in
        // failure state. Settle (below) re-sets them on rejection.
        state.ouraError = false;
        state.recoError = false;
        state.ouraSleepError = false;

        paintDashboardFromState();

        const repaint = () => {
            if (gen !== dashboardRenderGen) return;
            try { paintDashboardFromState(); } catch (e) { console.warn('dashboard repaint failed:', e); }
        };
        // FIT-129: gen-guarded sentinel write. Bails if either a newer
        // renderDashboard() has started OR a retry click on this chip has
        // bumped the per-sentinel gen, so a stale fetcher can't re-surface
        // the chip after the user has moved on.
        const settle = (ok, sentinel) => {
            if (gen !== dashboardRenderGen) return;
            if (sentinelGens[sentinel] !== dashboardSentinelGen[sentinel]) return;
            if (!ok) state[sentinel] = true;
            repaint();
        };

        // FIT-128/129: getDashboard rejects on failure (unlike the other
        // three, which swallow and return null). Both endpoints feed the AI
        // Recommendation card, so both map to recoError.
        const dashP  = getDashboard().then(() => settle(true,      'recoError'),       () => settle(false, 'recoError'));
        const ouraP  = getOuraStatus().then(v => settle(v != null, 'ouraError'));
        const recoP  = getReco().then(v       => settle(v != null, 'recoError'));
        const sleepP = getOuraSleep().then(v  => settle(v != null, 'ouraSleepError'));

        // Independent trend + history charts paint as soon as their own data lands.
        getOuraTrends().then(paintReadinessTrendChart, () => paintReadinessTrendChart(null));
        if (state.history) paintVolumeChart(state.history);
        else getHistory().then(paintVolumeChart, () => paintVolumeChart(null));

        // Awaited last so callers (e.g. settings flow at line ~4187) still know when
        // the four primary cards have all attempted to load.
        await Promise.allSettled([dashP, ouraP, recoP, sleepP]);
    }

    function paintDashboardFromState() {
        const dash = state.dashboard;
        const oura = state.oura;
        const reco = state.reco;
        const sleep = state.ouraSleep;

        // FIT-127: per-field guards. paintDashboardFromState runs after every
        // .then(repaint) in renderDashboard AND after invalidateCaches() nulls
        // state slices on sync/log actions. To keep misleading defaults off
        // the cold-open placeholders AND to clear stale data from a prior
        // session when a refetch is slow or fails, each card writes its HTML
        // placeholder back when its backing data is fully absent — never
        // skipping silently (which would otherwise leave the prior session's
        // ring/text on screen as stale guidance).
        const ouraReadiness = oura && oura.readiness != null ? oura.readiness : null;
        const dashReadiness = dash && dash.recomp_command && dash.recomp_command.readiness != null ? dash.recomp_command.readiness : null;
        const readiness = ouraReadiness != null ? ouraReadiness : dashReadiness;

        if (readiness != null) {
            gaugeChart($('readiness-gauge-svg'), readiness, { label: readiness >= 75 ? 'Very Good' : readiness >= 55 ? 'Good' : 'Low' });
        } else {
            // Clear the SVG so a prior session's ring/value doesn't linger.
            // gaugeChart is the only writer to this container.
            const gaugeEl = $('readiness-gauge-svg');
            if (gaugeEl) gaugeEl.innerHTML = '';
        }

        if ($('dash-hrv')) $('dash-hrv').textContent = oura && oura.hrv != null ? `${oura.hrv} ms` : '--';
        if ($('dash-rhr')) $('dash-rhr').textContent = oura && oura.resting_hr != null ? `${oura.resting_hr} bpm` : '--';
        if ($('dash-sleep')) $('dash-sleep').textContent = oura && oura.sleep_duration_min != null ? fmtDur(oura.sleep_duration_min) : '--';

        // Recommendation card — FIT-1 brief + FIT-2 honest freshness
        const nw = dash && dash.next_workout ? dash.next_workout : null;
        const freshness = (reco && reco.freshness) || (dash && dash.freshness) || null;
        const sourceConflicts = collectSourceConflicts(dash, reco);
        const freshnessWithWhoop = Object.assign({}, freshness || {});
        const whoopFreshness = mergeWhoopFreshnessNode(
            freshnessWithWhoop.whoop,
            (state.whoopStatus && typeof state.whoopStatus === 'object') ? state.whoopStatus : null,
            sourceConflicts
        );
        freshnessWithWhoop.whoop = whoopFreshness;
        const wearableStatuses = [
            whoopFreshness && resolveWhoopUiState(whoopFreshness, sourceConflicts),
            freshnessWithWhoop.oura && freshnessWithWhoop.oura.status,
            freshnessWithWhoop.apple_health && freshnessWithWhoop.apple_health.status,
        ].filter(Boolean);
        const wearableStale = wearableStatuses.indexOf('stale') >= 0;
        const wearableAllMissing = wearableStatuses.length > 0 && wearableStatuses.every(function (s) { return s === 'missing'; });
        const wearableDegraded = wearableStale || wearableAllMissing;

        // Title — swap to lower-confidence variant when wearable signal is gone
        let recoTitle;
        if (wearableAllMissing) {
            recoTitle = 'Rest day — no recent wearable data';
        } else if (wearableStale) {
            const lastDayIso = freshness && freshness.oura && freshness.oura.last_data_point;
            let daysOld = null;
            if (lastDayIso) {
                const t = new Date(lastDayIso).getTime();
                if (!isNaN(t)) daysOld = Math.max(2, Math.floor((Date.now() - t) / 86400000));
            }
            recoTitle = daysOld
                ? `Generic recommendation — wearable data is ${daysOld} days old`
                : 'Generic recommendation — wearable data is stale';
        } else {
            recoTitle = (reco && reco.suggested_workout) || (nw && (nw.focus || nw.goal_name)) || null;
        }
        if ($('reco-title')) {
            if (recoTitle) {
                $('reco-title').textContent = recoTitle.replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
            } else {
                // Reset to HTML placeholder so a prior session's title doesn't linger.
                $('reco-title').textContent = '—';
            }
        }

        // Intensity / time / RPE chips
        const focusLabel = nw ? (nw.focus || nw.goal_name || '') : '';
        const intensityWord = reco && reco.recommendation
            ? (reco.recommendation === 'intensity' ? 'High'
                : reco.recommendation === 'moderate' ? 'Moderate'
                : reco.recommendation === 'recovery' ? 'Low'
                : reco.recommendation)
            : null;
        if ($('reco-intensity')) {
            const intensityText = [focusLabel.replace(/_/g, ' '), intensityWord].filter(Boolean).join(' · ');
            if (intensityText) {
                $('reco-intensity').textContent = intensityText;
            } else {
                // Reset to HTML placeholder so a prior session's chip doesn't linger.
                $('reco-intensity').textContent = '—';
            }
        }
        const timeMin = nw && nw.estimated_minutes;
        if ($('reco-time')) {
            if (timeMin) { $('reco-time').textContent = `${timeMin} min`; $('reco-time').hidden = false; }
            else { $('reco-time').hidden = true; }
        }
        const rpeTarget = nw && nw.goal && nw.goal.rpe_target;
        if ($('reco-rpe')) {
            if (rpeTarget) { $('reco-rpe').textContent = `RPE ${rpeTarget}`; $('reco-rpe').hidden = false; }
            else { $('reco-rpe').hidden = true; }
        }

        // FIT-88: last-session badge — one-liner showing how the completion
        // shaped the next recommendation. Hidden when no recent completion.
        const lastEl = $('reco-last-session');
        if (lastEl) {
            while (lastEl.firstChild) lastEl.removeChild(lastEl.firstChild);
            const last = reco && reco.last_completed;
            const hoursAgo = last && (last.hours_ago != null ? last.hours_ago : null);
            if (last && hoursAgo != null) {
                const label = document.createElement('span');
                label.className = 'reco-last-session-label';
                label.textContent = 'Last session';
                lastEl.appendChild(label);
                const muscles = (Array.isArray(last.muscles_trained) ? last.muscles_trained : [])
                    .slice(0, 3)
                    .map((entry) => humanizeMuscle(entry && entry.muscle))
                    .filter(Boolean)
                    .join(', ');
                const detail = document.createElement('span');
                const hoursLabel = hoursAgo < 1
                    ? 'just now'
                    : `${hoursAgo < 10 ? hoursAgo.toFixed(1) : Math.round(hoursAgo)}h ago`;
                detail.textContent = muscles ? `${hoursLabel} — ${muscles}` : hoursLabel;
                lastEl.appendChild(detail);
                if (last.overall_fatigue != null) {
                    const fatigue = document.createElement('span');
                    fatigue.className = 'reco-last-session-fatigue';
                    // Leading space inside textContent so screen readers and
                    // copy/paste don't read "Shoulders· fatigue 9/10"; CSS
                    // margin still handles visual gap.
                    fatigue.textContent = ` · fatigue ${last.overall_fatigue}/10`;
                    lastEl.appendChild(fatigue);
                }
                lastEl.hidden = false;
            } else {
                lastEl.hidden = true;
            }
        }

        // Avoid list — surface existing avoid_muscles as chips (0-3 max).
        // Build via DOM + textContent (not innerHTML) so user-supplied soreness
        // muscle names cannot inject HTML/JS into the dashboard card.
        // FIT-88: muscles that appear in `recently_trained` (came from a recent
        // completion, not a soreness log) render as amber `chip-recent` to
        // distinguish recovery-buffer from actual soreness.
        const avoidEl = $('reco-avoid');
        if (avoidEl) {
            const avoidRaw = (reco && reco.avoid_muscles) || [];
            const avoid = (Array.isArray(avoidRaw) ? avoidRaw : []).slice(0, 3);
            const recentRaw = (reco && reco.recently_trained) || [];
            const recentSet = new Set(
                (Array.isArray(recentRaw) ? recentRaw : [])
                    .map((entry) => entry && entry.muscle)
                    .filter(Boolean)
            );
            while (avoidEl.firstChild) avoidEl.removeChild(avoidEl.firstChild);
            if (avoid.length === 0) {
                avoidEl.hidden = true;
            } else {
                const label = document.createElement('span');
                label.className = 'reco-avoid-label';
                label.textContent = 'Avoid';
                avoidEl.appendChild(label);
                avoid.forEach(function (m) {
                    const chip = document.createElement('span');
                    // Amber for muscles that came from a recent completion;
                    // red for muscles flagged via a soreness log. A muscle in
                    // both buckets renders amber because the post-completion
                    // signal is fresher and actionable ("recover before
                    // reloading").
                    const isRecent = recentSet.has(m);
                    chip.className = isRecent ? 'chip chip-recent' : 'chip chip-avoid';
                    chip.textContent = humanizeMuscle(m);
                    if (isRecent) {
                        chip.title = 'Trained recently — recovery buffer';
                    }
                    avoidEl.appendChild(chip);
                });
                avoidEl.hidden = false;
            }
        }

        // Reason / "why" — wearable reasoning + explicit food guidance (FIT-1 AC)
        const whyEl = $('reco-why');
        if (whyEl) {
            let whyText = null;
            if (wearableAllMissing) {
                whyText = 'No recent wearable data — showing a conservative default. Sync Oura or Apple Health for a personalized recommendation.';
            } else if (wearableStale) {
                whyText = ((reco && reco.reasoning) ? reco.reasoning + '. ' : '') + 'Confidence is lowered because wearable data is stale.';
            } else if (reco && reco.reasoning) {
                whyText = reco.reasoning;
            }
            if (whyText) {
                // Append food guidance line so the brief always explains how
                // today's food changed (or could change) remaining-day guidance.
                const foodLine = buildFoodGuidanceLine(freshness && freshness.food);
                if (foodLine) whyText = whyText.replace(/\.\s*$/, '') + '. ' + foodLine;
                whyEl.textContent = whyText;
                whyEl.classList.toggle('lower-confidence', wearableDegraded);
            } else {
                // Reset to HTML placeholder so a prior session's reasoning doesn't linger.
                whyEl.textContent = 'Analyzing your readiness, sleep, and training load…';
                whyEl.classList.remove('lower-confidence');
            }
        }

        // Confidence — server-driven bucket → label; legacy ladder as fallback.
        // Ladder is gated on readiness != null so cold-open doesn't paint the
        // worst-bucket '45%' over the '--%' HTML placeholder.
        const confLabel = (reco && reco.confidence_level && RECO_CONF_LABEL[reco.confidence_level])
            || (readiness != null ? (readiness >= 80 ? '92%' : readiness >= 65 ? '78%' : readiness >= 50 ? '62%' : '45%') : null);
        if ($('reco-confidence-pct')) {
            if (confLabel) {
                $('reco-confidence-pct').textContent = confLabel;
            } else {
                // Reset to '--%' HTML placeholder so a prior session's % doesn't linger.
                $('reco-confidence-pct').textContent = '--%';
            }
        }

        // Freshness chips (always render — null freshness shows "unknown" state)
        renderFreshnessChips(freshnessWithWhoop);
        renderRecommendationSourceSummary(dash, reco, freshnessWithWhoop);

        // Macro status card (FIT-23)
        renderMacroCard(dash && dash.nutrition_today);

        // Today at a glance
        if ($('glance-steps')) $('glance-steps').textContent = fmtInt(oura && oura.steps);
        if ($('glance-steps-goal')) $('glance-steps-goal').textContent = oura && oura.steps != null ? `${Math.round((oura.steps / 10000) * 100)}% of 10,000` : 'Pending sync';
        if ($('glance-cal')) $('glance-cal').textContent = fmtInt(oura && oura.active_calories);
        if ($('glance-cal-goal')) $('glance-cal-goal').textContent = oura && oura.active_calories != null ? `${Math.round((oura.active_calories / 800) * 100)}% of 800` : '';
        if ($('glance-sleep')) $('glance-sleep').textContent = oura && oura.sleep_duration_min != null ? fmtDur(oura.sleep_duration_min) : '--';
        if ($('glance-sleep-quality')) {
            const score = oura && oura.sleep_score;
            $('glance-sleep-quality').textContent = score != null ? (score >= 85 ? 'Excellent' : score >= 70 ? 'Good' : 'Fair') : '—';
        }
        const bs = (dash && dash.body_stats) || {};
        if ($('glance-weight')) $('glance-weight').textContent = bs.latest_weight != null ? `${fmtDecimal(bs.latest_weight, 1)} lb` : '--';
        if ($('glance-weight-delta')) {
            const ch = bs.weight_change_30d;
            if (ch == null) $('glance-weight-delta').textContent = bs.trend || '—';
            else {
                $('glance-weight-delta').textContent = (ch > 0 ? '↑' : ch < 0 ? '↓' : '·') + ' ' + Math.abs(ch).toFixed(1) + ' lb';
            }
        }

        // Insight card — only paint when reco has resolved. On a cold open,
        // or when state.reco is nulled by getReco's catch path or by a post-
        // invalidate refetch that's slow or fails, reset to the HTML
        // placeholders so a prior session's insight doesn't linger as stale
        // guidance.
        if (reco) {
            const recoFactors = reco.readiness_factors;
            let insightTitle = 'Recovery is on track';
            let insightBody = reco.reasoning || 'Keep your sleep consistent and you\'ll stay ready.';
            if (recoFactors) {
                if (recoFactors.sleep_debt && recoFactors.sleep_debt.status === 'severe') {
                    insightTitle = 'Sleep debt is high';
                    insightBody = recoFactors.sleep_debt.message;
                } else if (recoFactors.acwr && recoFactors.acwr.risk === 'detraining') {
                    insightTitle = 'Training load is low';
                    insightBody = recoFactors.acwr.message;
                }
            }
            if ($('insight-title')) $('insight-title').textContent = insightTitle;
            if ($('insight-body')) $('insight-body').textContent = insightBody;
        } else {
            if ($('insight-title')) $('insight-title').textContent = 'Gathering data…';
            if ($('insight-body')) $('insight-body').textContent = '';
        }

        // Sparkline: sleep scores from Oura trend
        const sleepSeries = (sleep && sleep.trend_data ? sleep.trend_data : []).map((d) => d.score);
        sparkline($('insight-sparkline'), sleepSeries, { color: '#22d3ee', height: 32 });

        // FIT-128/129: per-card retry chips. Each chip surfaces when its
        // endpoint rejected/timed out (sentinel set by renderDashboard's
        // settle helper). Click re-fetches with force=true under a per-
        // sentinel gen guard so a stale older fetch can't re-show the chip
        // after a successful retry, and a newer renderDashboard wins over an
        // in-flight retry. retryFn must throw on failure (paintRetryChip
        // catches and writes state[sentinelKey] = true under guard); the
        // null-as-failure normalization below adapts the swallow-on-failure
        // fetchers to that contract.
        paintRetryChip('readiness-retry', state.ouraError, 'ouraError', async () => {
            if (await getOuraStatus(true) == null) throw new Error('readiness retry failed');
        });
        paintRetryChip('reco-retry', state.recoError, 'recoError', async () => {
            // The AI Recommendation chip covers BOTH dashboard and reco
            // because the card chrome is fed by both endpoints. getDashboard
            // throws on failure (unlike the other fetchers); getReco swallows
            // and returns null. Either failing surfaces the chip.
            let dashOk = true;
            try { await getDashboard(true); } catch { dashOk = false; }
            const reco = await getReco(true);
            if (!dashOk || reco == null) throw new Error('reco retry failed');
        });
        paintRetryChip('insight-retry', state.ouraSleepError, 'ouraSleepError', async () => {
            if (await getOuraSleep(true) == null) throw new Error('insight retry failed');
        });
    }

    function paintRetryChip(elementId, isErrored, sentinelKey, retryFn) {
        const chip = $(elementId);
        if (!chip) return;
        chip.hidden = !isErrored;
        if (!isErrored) return;
        chip.onclick = async () => {
            // FIT-129: capture render-gen and bump this chip's sentinel-gen
            // BEFORE retryFn runs. The bump invalidates any older same-render
            // in-flight fetch for the same sentinel, so it can no longer
            // re-show the chip after the retry succeeds. Both gens are
            // checked at the top of finally, before any DOM/state mutation,
            // so a stale retry can't re-enable the chip or repaint either.
            const clickGen = dashboardRenderGen;
            const clickSentinelGen = ++dashboardSentinelGen[sentinelKey];
            chip.disabled = true;
            chip.hidden = true;
            let failed = false;
            try { await retryFn(); } catch { failed = true; }
            finally {
                if (clickGen !== dashboardRenderGen) return;
                if (clickSentinelGen !== dashboardSentinelGen[sentinelKey]) return;
                chip.disabled = false;
                state[sentinelKey] = failed;
                paintDashboardFromState();
            }
        };
    }

    function paintReadinessTrendChart(trends) {
        const series = trends && trends.series ? trends.series : [];
        const readinessPts = series.map((s) => ({ value: s.readiness_score, label: fmtDate(s.day) })).filter(p => p.value != null);
        lineChart($('chart-readiness-7d'), readinessPts, { color: '#22c55e' });
        if ($('readiness-7d-avg')) {
            const vals = readinessPts.map(p => p.value);
            const avg = vals.length ? Math.round(vals.reduce((a, b) => a + b, 0) / vals.length) : null;
            $('readiness-7d-avg').textContent = avg != null ? `avg ${avg}` : '—';
        }
        // FIT-112: takeaway below the readiness chart.
        const rdy = deriveReadiness7dTakeaway(readinessPts);
        setChartTakeaway('chart-readiness-7d-takeaway', rdy.text, { empty: rdy.empty });
    }

    function paintVolumeChart(history) {
        const hist = (history && history.workouts) || [];
        const weekVolumes = computeWeeklyVolumes(hist, 4);
        barChart($('chart-volume-4w'), weekVolumes.map(w => ({ value: w.volume, label: w.label })), { color: '#a78bfa' });
        if ($('volume-4w-sub')) {
            const total = weekVolumes.reduce((a, w) => a + w.volume, 0);
            $('volume-4w-sub').textContent = total ? `${fmtKilo(total)} lbs` : '—';
        }
    }

    function computeWeeklyVolumes(workouts, weeks = 4) {
        const now = new Date();
        const buckets = [];
        for (let i = weeks - 1; i >= 0; i--) {
            const end = new Date(now);
            end.setDate(end.getDate() - 7 * i);
            const start = new Date(end);
            start.setDate(end.getDate() - 6);
            buckets.push({ start, end, volume: 0, label: fmtDate(end.toISOString().slice(0, 10)) });
        }
        workouts.forEach((w) => {
            if (!w.date) return;
            const wd = new Date(w.date + 'T00:00:00');
            for (const b of buckets) {
                if (wd >= b.start && wd <= b.end) { b.volume += Number(w.total_volume || 0); break; }
            }
        });
        return buckets;
    }

    // --- Vitals --------------------------------------------------
    async function renderVitals() {
        const [vit, oura, sleep, body] = await Promise.all([getVitals(), getOuraStatus(), getOuraSleep(), getBody()]);

        // RHR
        const rhr = (oura && oura.resting_hr) || (vit && vit.heart_rate && vit.heart_rate.resting_bpm);
        $('v-rhr').textContent = rhr != null ? Math.round(rhr) : '--';
        // HRV
        const hrv = oura && oura.hrv;
        $('v-hrv').textContent = hrv != null ? Math.round(hrv) : '--';
        // HR zone (static approximation)
        if (rhr != null) {
            const zone = rhr < 58 ? 'Zone 2' : rhr < 68 ? 'Zone 2' : 'Zone 3';
            $('v-hr-zone').textContent = zone;
            $('v-hr-zone-sub').textContent = '';
        } else {
            $('v-hr-zone').textContent = '--';
            $('v-hr-zone-sub').textContent = '';
        }
        // Body temp
        const tempDev = oura && oura.temperature_deviation;
        $('v-temp').textContent = tempDev != null ? (98.6 + Number(tempDev)).toFixed(1) : '--';
        $('v-temp-delta').textContent = tempDev != null ? `${tempDev >= 0 ? '+' : ''}${Number(tempDev).toFixed(2)}°F` : '';

        // Activity
        const steps = oura && oura.steps;
        $('v-steps').textContent = fmtInt(steps);
        $('v-steps-goal').textContent = steps != null ? `${Math.round((steps / 10000) * 100)}% of 10,000` : 'of 10,000';
        const activeCal = oura && oura.active_calories;
        $('v-active-cal').textContent = fmtInt(activeCal);
        $('v-active-cal-goal').textContent = activeCal != null ? `${Math.round((activeCal / 800) * 100)}% of 800` : 'of 800 kcal';
        const totalCal = oura && oura.active_calories ? Math.round(oura.active_calories + 1600) : null;
        $('v-total-cal').textContent = fmtInt(totalCal);
        $('v-total-cal-goal').textContent = 'Est. target 2,300';
        const activityScore = oura && oura.activity_score;
        $('v-active-min').textContent = activityScore != null ? Math.round(activityScore * 0.9) : '--';
        $('v-active-min-goal').textContent = 'of 80 min';

        // Sparks from trends
        const trends = await getOuraTrends();
        const series = trends && trends.series ? trends.series : [];
        sparkline($('spark-steps'), series.map((s) => s.steps), { color: '#22c55e' });
        sparkline($('spark-active-min'), series.map((s) => s.activity_score), { color: '#fbbf24' });
        sparkline($('spark-sleep'), series.map((s) => (s.sleep_duration_min || 0) / 60), { color: '#a78bfa' });

        // Sleep details
        const last = sleep && sleep.last_night;
        if (last) {
            $('v-sleep-dur').textContent = fmtDur(last.total_sleep_min);
            $('v-sleep-dur-sub').textContent = `${Math.round(last.rem_sleep_min)}m REM · ${Math.round(last.deep_sleep_min)}m Deep`;
        } else {
            $('v-sleep-dur').textContent = '--';
            $('v-sleep-dur-sub').textContent = '—';
        }
        $('v-sleep-score').textContent = oura && oura.sleep_score != null ? oura.sleep_score : '--';
        const wa = sleep && sleep.week_average;
        $('v-sleep-score-sub').textContent = wa && wa.score ? `avg ${wa.score} · 7d` : '';

        // Body
        const latest = body && body.history && body.history[0];
        if (latest) {
            $('v-weight').textContent = latest.weight_lbs != null ? Number(latest.weight_lbs).toFixed(1) : '--';
            $('v-bf').textContent = latest.body_fat_pct != null ? Number(latest.body_fat_pct).toFixed(1) : '--';
        }
        const wTrend = body && body.history ? body.history : [];
        // Compare latest vs 7 days ago
        const sevenAgo = wTrend.find((h, i) => {
            if (!wTrend[0] || !h.date) return false;
            const days = Math.round((new Date(wTrend[0].date) - new Date(h.date)) / 86400000);
            return days >= 7;
        });
        if (latest && sevenAgo && latest.weight_lbs != null && sevenAgo.weight_lbs != null) {
            const d = Number(latest.weight_lbs) - Number(sevenAgo.weight_lbs);
            $('v-weight-delta').textContent = `${d >= 0 ? '↑' : '↓'} ${Math.abs(d).toFixed(1)} lb (7d)`;
            $('v-weight-delta').className = 'metric-delta ' + (d < 0 ? 'pos' : 'neg');
        } else { $('v-weight-delta').textContent = ''; }
        if (latest && sevenAgo && latest.body_fat_pct != null && sevenAgo.body_fat_pct != null) {
            const d = Number(latest.body_fat_pct) - Number(sevenAgo.body_fat_pct);
            $('v-bf-delta').textContent = `${d >= 0 ? '↑' : '↓'} ${Math.abs(d).toFixed(1)}% (7d)`;
            $('v-bf-delta').className = 'metric-delta ' + (d < 0 ? 'pos' : 'neg');
        } else { $('v-bf-delta').textContent = ''; }

        // RHR/HRV deltas (7d from trends)
        const rhrSeries = series.map(s => s.resting_hr).filter(v => v != null);
        if (rhrSeries.length >= 2 && rhr != null) {
            const avg = rhrSeries.slice(0, -1).reduce((a, b) => a + b, 0) / Math.max(1, rhrSeries.length - 1);
            const d = rhr - avg;
            $('v-rhr-delta').textContent = `${d >= 0 ? '↑' : '↓'} ${Math.abs(d).toFixed(1)} vs 7d`;
            $('v-rhr-delta').className = 'metric-delta ' + (d < 0 ? 'pos' : 'neg');
        }
        const hrvSeries = series.map(s => s.hrv).filter(v => v != null);
        if (hrvSeries.length >= 2 && hrv != null) {
            const avg = hrvSeries.slice(0, -1).reduce((a, b) => a + b, 0) / Math.max(1, hrvSeries.length - 1);
            const d = hrv - avg;
            $('v-hrv-delta').textContent = `${d >= 0 ? '↑' : '↓'} ${Math.abs(d).toFixed(1)} vs 7d`;
            $('v-hrv-delta').className = 'metric-delta ' + (d >= 0 ? 'pos' : 'neg');
        }
    }

    // --- Next Workout --------------------------------------------
    async function renderNextWorkout() {
        const gen = ++nextWorkoutRenderGen;
        let nw = null;
        try {
            nw = await getNextWorkout(true);
        } catch (err) {
            console.warn('next workout load failed', err);
            nw = state.nextWorkout || (state.dashboard && state.dashboard.next_workout);
        }
        if (!nw) {
            $('nw-title').textContent = 'Rest Day';
            $('nw-sub').textContent = 'Take recovery seriously today.';
            $('nw-exercise-list').innerHTML = '<div class="empty">No exercises scheduled.</div>';
            return;
        }
        const focus = (nw.focus || nw.goal_name || 'Workout').replace(/_/g, ' ');
        const title = focus.replace(/\b\w/g, (c) => c.toUpperCase());
        $('nw-title').textContent = title;
        $('nw-sub').textContent = nw.goal_name || 'Moderate Intensity';
        $('nw-duration').textContent = (nw.estimated_minutes || nw.available_time || '—') + ' min';
        const renderRpe = (st) => {
            const goalRpe = (st && st.goal_details && st.goal_details.rpe_target) || null;
            const exRpes = (nw.exercises || []).map((e) => Number(e.rpe_target)).filter((v) => Number.isFinite(v));
            const avgExRpe = exRpes.length ? Math.round((exRpes.reduce((a, b) => a + b, 0) / exRpes.length) * 10) / 10 : null;
            const rpeTarget = goalRpe || avgExRpe;
            $('nw-rpe').textContent = rpeTarget ? `RPE ${rpeTarget}` : 'RPE —';
        };
        const renderWhy = (reco) => {
            const why = reco && reco.reasoning ? reco.reasoning : 'Your readiness is high and your plan optimizes strength while managing fatigue.';
            $('nw-why').textContent = why;
        };
        renderRpe(null);
        renderWhy(null);

        getReco().then((reco) => {
            if (gen !== nextWorkoutRenderGen || state.currentTab !== 'tab-workout') return;
            renderWhy(reco);
        }).catch(() => {});
        getSettings().then((st) => {
            if (gen !== nextWorkoutRenderGen || state.currentTab !== 'tab-workout') return;
            renderRpe(st);
        }).catch((err) => {
            console.warn('settings unavailable for next workout render', err);
        });

        const list = $('nw-exercise-list');
        list.innerHTML = '';
        (nw.exercises || []).forEach((ex, i) => {
            const card = document.createElement('div');
            card.className = 'ex-card';
            const muscle = (ex.muscle || ex.muscle_group || '').toString().toLowerCase().trim();
            const tagClass = muscle ? 'tag-' + muscle.replace(/\s+/g, '-') : '';
            const sets = ex.target_sets || ex.sets || 3;
            const reps = ex.target_reps || ex.reps || (ex.rep_range ? ex.rep_range.join('–') : 10);
            const w = ex.target_weight != null ? ex.target_weight : ex.target_weight_lbs;
            const weightStr = w != null && Number(w) > 0 ? ` · ${Math.round(w)} lb` : '';
            const rpe = ex.rpe_target || ex.rpe;
            const rationale = ex.rationale || ex.reason || '';
            const loadHint = exerciseLoadHint(ex);
            const exerciseName = ex.exercise || ex.name || ex.machine || '—';
            card.innerHTML = `
                <div class="ex-row-1">
                    <div class="ex-num">${i + 1}</div>
                    <div class="ex-name">${escapeHtml(exerciseName)}</div>
                    ${muscle ? `<span class="ex-tag ${tagClass}">${escapeHtml(muscle)}</span>` : ''}
                    <button class="ex-swap-btn" type="button" data-ex-idx="${i}" data-ex-muscle="${escapeHtml(muscle)}" data-ex-name="${escapeHtml(exerciseName)}" title="Swap this exercise" aria-label="Swap ${escapeHtml(exerciseName)}">⇄</button>
                </div>
                <div class="ex-row-2">
                    <span class="ex-sets">${sets} × ${reps}${weightStr}</span>
                    ${rpe ? `<span class="ex-rpe">RPE ${rpe}</span>` : ''}
                    ${ex.rest_label ? `<span class="ex-rpe">Rest ${escapeHtml(ex.rest_label)}</span>` : ''}
                </div>
                ${renderLoadHintHtml(loadHint, 'ex-load-hint')}
                ${rationale ? `<div class="ex-why">${escapeHtml(rationale)}</div>` : ''}
            `;
            card.querySelector('.ex-swap-btn').addEventListener('click', () => openSwap(i, muscle, exerciseName));
            list.appendChild(card);
        });
        if (!nw.exercises || !nw.exercises.length) list.innerHTML = '<div class="empty">No exercises planned — rest day.</div>';

        const card = $('nw-cardio-card');
        const c = nw.cardio;
        if (c && (c.type || c.machine)) {
            card.hidden = false;
            $('nw-cardio-title').textContent = c.type || c.machine || 'Cardio';
            const bits = [];
            if (c.duration_minutes != null) bits.push(`${c.duration_minutes} min`);
            if (c.zone) bits.push(c.zone);
            if (c.heart_rate_range) bits.push(c.heart_rate_range);
            else if (c.target_hr) bits.push(`${c.target_hr} bpm`);
            $('nw-cardio-meta').textContent = bits.join(' · ') || (c.intensity || '');
            // FIT-109: surface FIT-96's rotation/intensity rationale.
            // Prefer the server-provided `reason` (set by
            // _choose_dynamic_cardio_recommendation). Fall back to
            // zone + zone_description so the user still gets context
            // when an older payload arrives without `reason`.
            const cardioWhy = $('nw-cardio-why');
            if (cardioWhy) {
                const whyText = cardioWhyText(c);
                cardioWhy.textContent = whyText;
                cardioWhy.hidden = !whyText;
            }
        } else {
            card.hidden = true;
        }
    }

    // FIT-109: shared helper used by both Next Workout card and the
    // active-workout cardio block so the "why" copy stays consistent.
    // Prefer the zone + zone_description derived line (FIT-96's
    // rotation/intensity already encodes recovery vs moderate vs
    // intensity into `zone_description`), then fall back to the
    // server-provided `reason` text, then the description alone.
    function cardioWhyText(c) {
        if (!c) return '';
        if (c.zone && c.zone_description) {
            return `${c.zone} · ${c.zone_description}`;
        }
        const reason = (c.reason || '').trim();
        if (reason) return reason;
        return c.zone_description || '';
    }

    function workoutTitle(nw) {
        const focus = (nw && (nw.focus || nw.goal_name) || 'Workout').replace(/_/g, ' ');
        return focus.replace(/\b\w/g, (c) => c.toUpperCase());
    }

    function exerciseTargetText(ex) {
        const sets = ex.target_sets || ex.sets || 3;
        const reps = ex.target_reps || ex.reps || (ex.rep_range ? ex.rep_range.join('–') : 10);
        const w = ex.target_weight != null ? ex.target_weight : ex.target_weight_lbs;
        const bits = [`${sets} × ${reps}`];
        if (w != null && Number(w) > 0) bits.push(`${Math.round(w)} lb`);
        if (ex.rpe_target || ex.rpe) bits.push(`RPE ${ex.rpe_target || ex.rpe}`);
        return bits.join(' · ');
    }

    // FIT-105: returns either null (no hint, e.g. direct-history exercises)
    // or a rich object the renderer can decorate with a confidence chip
    // and a source-detail suffix. The wrapper preserves Codex's FIT-103
    // backend contract: `inference.message` is canonical when present;
    // we also pass through `confidence` and `load_source_detail` so the
    // user can spot LOW-confidence estimates at a glance.
    function exerciseLoadHint(ex) {
        const inference = ex && ex.load_inference;
        if (inference && inference.message) {
            return {
                text: inference.message,
                confidence: inference.confidence || null,
                sourceExercise: inference.source_exercise || null,
                detail: ex.load_source_detail || null,
            };
        }
        if (ex && ex.load_source === 'similar_history' && ex.load_source_detail) {
            return {
                text: 'Estimated from similar exercise history; adjust after first set.',
                confidence: null,
                sourceExercise: null,
                detail: ex.load_source_detail,
            };
        }
        return null;
    }

    // FIT-105: render the rich hint object as inline HTML with an
    // accessible confidence chip + optional source-detail suffix. Returns
    // empty string when `hint` is null so call sites can interpolate
    // unconditionally.
    function renderLoadHintHtml(hint, baseClass) {
        if (!hint) return '';
        const conf = (hint.confidence || '').toLowerCase();
        const confChip = conf === 'low' || conf === 'medium' || conf === 'high' || conf === 'med'
            ? renderLoadConfChip(conf)
            : '';
        // Detail (e.g. "Lateral Raise: 80% peak") is more useful than the
        // raw scaling string; trim the noisy internal `similar_history:`
        // prefix the backend emits in `detail`.
        const cleanDetail = hint.detail && !hint.detail.startsWith('similar_history:')
            ? ` · ${escapeHtml(hint.detail)}`
            : '';
        return `
            <div class="ex-why ${baseClass}" role="note" aria-label="Load estimate">
                <span class="load-hint-text">${escapeHtml(hint.text)}${cleanDetail}</span>
                ${confChip}
            </div>
        `;
    }

    function renderLoadConfChip(conf) {
        const label = conf === 'low' ? 'LOW'
            : (conf === 'medium' || conf === 'med') ? 'MED'
            : 'HIGH';
        const cls = conf === 'low' ? 'load-conf-low'
            : (conf === 'medium' || conf === 'med') ? 'load-conf-med'
            : 'load-conf-high';
        const sr = conf === 'low' ? 'low confidence — verify first set'
            : (conf === 'medium' || conf === 'med') ? 'medium confidence'
            : 'high confidence';
        return `<span class="load-conf-chip ${cls}" aria-label="${sr}">${label}</span>`;
    }

    function cardioTargetText(cardio) {
        if (!cardio || !(cardio.type || cardio.machine)) return '';
        const bits = [cardio.type || cardio.machine || 'Cardio'];
        if (cardio.duration_minutes != null) bits.push(`${cardio.duration_minutes} min`);
        if (cardio.zone) bits.push(cardio.zone);
        if (cardio.heart_rate_range) bits.push(cardio.heart_rate_range);
        else if (cardio.target_hr) bits.push(`${cardio.target_hr} bpm`);
        return bits.join(' · ');
    }

    function renderAdjustedPlanPreview(nw) {
        const host = $('adjust-plan-preview');
        if (!host) return;
        if (!nw) {
            host.hidden = true;
            host.innerHTML = '';
            return;
        }
        const exercises = (nw.exercises || []).slice(0, 8);
        const duration = nw.estimated_minutes || nw.available_time;
        const cardioText = cardioTargetText(nw.cardio);
        host.innerHTML = `
            <div class="adjust-preview-head">
                <div>
                    <div class="adjust-preview-kicker">Updated workout plan</div>
                    <div class="adjust-preview-title">${escapeHtml(workoutTitle(nw))}</div>
                </div>
                ${duration ? `<div class="adjust-preview-duration">${escapeHtml(String(duration))} min</div>` : ''}
            </div>
            <div class="adjust-preview-list">
                ${exercises.length ? exercises.map((ex, i) => `
                    <div class="adjust-preview-row">
                        <span class="adjust-preview-num">${i + 1}</span>
                        <span class="adjust-preview-name">${escapeHtml(exerciseName(ex))}</span>
                        <span class="adjust-preview-target">${escapeHtml(exerciseTargetText(ex))}</span>
                    </div>
                `).join('') : '<div class="empty">No exercises planned — rest day.</div>'}
            </div>
            ${cardioText ? `<div class="adjust-preview-cardio">${escapeHtml(cardioText)}</div>` : ''}
            <div class="adjust-preview-actions">
                <button id="btn-adjust-view-plan" class="btn btn-ghost" type="button">View Full Plan</button>
                <button id="btn-adjust-start-workout" class="btn btn-primary" type="button">Start Workout</button>
            </div>
        `;
        host.hidden = false;
        const viewBtn = $('btn-adjust-view-plan');
        const startBtn = $('btn-adjust-start-workout');
        if (viewBtn) viewBtn.addEventListener('click', viewAdjustedPlan);
        if (startBtn) startBtn.addEventListener('click', startAdjustedWorkout);
    }

    // --- Log -----------------------------------------------------
    async function prepareLog() {
        const dateInputs = ['log-date', 'cardio-date', 'recovery-date'];
        dateInputs.forEach((id) => { if ($(id) && !$(id).value) $(id).value = today(); });

        const select = $('log-exercise');
        if (select && select.options.length <= 1) {
            const list = await getExercises();
            list.forEach((ex) => {
                const opt = document.createElement('option');
                const name = typeof ex === 'string' ? ex : (ex.name || ex.exercise || ex.machine);
                opt.value = name; opt.textContent = name;
                select.appendChild(opt);
            });
        }

        // today's summary
        const hist = await getHistory();
        const workouts = hist && hist.workouts ? hist.workouts : [];
        const todaysW = workouts.filter((w) => w.date === today());
        const vol = todaysW.reduce((a, w) => a + Number(w.total_volume || 0), 0);
        const setsCount = todaysW.reduce((a, w) => a + Number(w.total_sets || 0), 0);
        const exCount = todaysW.reduce((a, w) => a + ((w.exercises || []).length), 0);
        const dur = todaysW.reduce((a, w) => a + Number(w.duration_minutes || 0), 0);
        $('today-volume').textContent = vol ? fmtKilo(vol) + ' lbs' : '—';
        $('today-sets').textContent = setsCount || '—';
        $('today-exercises').textContent = exCount || '—';
        $('today-duration').textContent = dur ? `${dur} min` : '—';
    }

    // --- History -------------------------------------------------
    async function renderHistory() {
        // FIT-115: write the empty-state KPI surfaces ("0", "Last N days")
        // before any data fetching or chart math runs. The original code
        // only set these AFTER the data pipeline, so any thrown error left
        // the static "--" placeholders in place — which read as
        // "never loaded" rather than "no data".
        const days = state.ranges.history;
        $('history-count').textContent = '0';
        $('history-freq-sub').textContent = `Last ${days} days`;
        $('history-total-volume').textContent = fmtKilo(0);
        $('history-vol-sub').textContent = `Last ${days} days · lifting only`;

        let hist, aw;
        try {
            [hist, aw] = await Promise.all([
                getHistory(),
                getAppleHealthWorkouts(Math.max(days, 30)),
            ]);
        } catch (e) {
            console.error('renderHistory: history fetch failed', e);
            $('chart-history-freq').innerHTML = '<div class="empty">Could not load history. Tap a range chip to retry.</div>';
            $('chart-history-volume').innerHTML = '<div class="empty">Could not load history. Tap a range chip to retry.</div>';
            setChartTakeaway('chart-history-freq-takeaway', '', {});
            setChartTakeaway('chart-history-volume-takeaway', '', {});
            $('history-top-exercises').innerHTML = '<div class="empty">No exercises in range.</div>';
            $('history-workout-list').innerHTML = '';
            return;
        }

        // FIT-115: defend against malformed responses (e.g. a backend that
        // surfaces an empty object on a soft error). Without this guard,
        // a non-array `workouts` would crash `.map`/`.filter` below and
        // wipe the chart surfaces.
        const allLifts = Array.isArray(hist && hist.workouts) ? hist.workouts : [];
        const cutoff = new Date();
        cutoff.setDate(cutoff.getDate() - days);

        const lifts = allLifts
            .map((w, i) => ({ ...w, source: 'lifted', _origIndex: i }))
            .filter((w) => w.date && new Date(w.date + 'T00:00:00') >= cutoff);
        const watch = (Array.isArray(aw) ? aw : [])
            .filter((w) => w.date && new Date(w.date + 'T00:00:00') >= cutoff)
            .map((w) => ({ ...w, source: 'watch' }));

        // Exclude Apple Watch's own "Traditional Strength Training" entries on
        // days the user already logged a lift — same session, different source.
        const liftDates = new Set(lifts.map((w) => w.date));
        const watchFiltered = watch.filter((w) => {
            const t = (w.activity_type || w.activity || '').toLowerCase();
            const looksLikeLift = t.includes('strength') || t.includes('weight') || t.includes('functional');
            return !(looksLikeLift && liftDates.has(w.date));
        });

        const workouts = lifts.length ? lifts : [];

        const totalVol = lifts.reduce((a, w) => a + Number(w.total_volume || 0), 0);
        const totalSessions = lifts.length + watchFiltered.length;
        $('history-count').textContent = totalSessions || '0';
        $('history-freq-sub').textContent = lifts.length && watchFiltered.length
            ? `Last ${days} days · ${lifts.length} lifted + ${watchFiltered.length} from Watch`
            : `Last ${days} days`;
        $('history-total-volume').textContent = fmtKilo(totalVol);
        $('history-vol-sub').textContent = `Last ${days} days · lifting only`;

        // frequency bars by day — count both lifted and watch sessions
        const buckets = buildDailyBuckets(days);
        [...lifts, ...watchFiltered].forEach((w) => {
            if (!w.date) return;
            const b = buckets.find((bb) => bb.iso === w.date);
            if (b) b.count += 1;
        });
        const barBuckets = groupIntoBuckets(buckets, Math.min(days, 30));

        // volume over time — aggregate before drawing so the line chart sees
        // the populated bucket values.
        workouts.forEach((w) => {
            if (!w.date) return;
            const dt = new Date(w.date + 'T00:00:00');
            const idx = barBuckets.findIndex((b) => dt >= b.start && dt <= b.end);
            if (idx >= 0) barBuckets[idx].volume = (barBuckets[idx].volume || 0) + Number(w.total_volume || 0);
        });

        // FIT-115: render each chart independently so a failure in one
        // doesn't leave the other blank.
        try {
            barChart($('chart-history-freq'), barBuckets.map((b) => ({ value: b.count, label: b.label })), { color: '#60a5fa' });
        } catch (e) {
            console.error('renderHistory: barChart failed', e);
            $('chart-history-freq').innerHTML = '<div class="empty">Chart unavailable.</div>';
        }
        try {
            lineChart($('chart-history-volume'), barBuckets.map((b) => ({ value: b.volume || 0, label: b.label })), { color: '#a78bfa', nonNegativeY: true, emptyMaxY: 100 });
        } catch (e) {
            console.error('renderHistory: lineChart failed', e);
            $('chart-history-volume').innerHTML = '<div class="empty">Chart unavailable.</div>';
        }

        // FIT-112: takeaways below the history charts.
        const totalCount = barBuckets.reduce((s, b) => s + (Number(b.count) || 0), 0);
        const freqTake = deriveHistoryFreqTakeaway(barBuckets, totalCount, days);
        setChartTakeaway('chart-history-freq-takeaway', freqTake.text, { empty: freqTake.empty });
        const volTake = deriveHistoryVolumeTakeaway(barBuckets);
        setChartTakeaway('chart-history-volume-takeaway', volTake.text, { empty: volTake.empty });

        // top exercises
        const topEx = {};
        workouts.forEach((w) => {
            (w.exercises || []).forEach((e) => {
                const name = e.machine || e.exercise || '—';
                topEx[name] = topEx[name] || { volume: 0, sets: 0 };
                (e.sets || []).forEach((s) => {
                    topEx[name].volume += Number(s.weight_lbs || 0) * Number(s.reps || 0);
                    topEx[name].sets += 1;
                });
            });
        });
        const topList = Object.entries(topEx)
            .map(([name, v]) => ({ name, ...v }))
            .sort((a, b) => b.volume - a.volume)
            .slice(0, 5);
        const topHost = $('history-top-exercises');
        topHost.innerHTML = topList.length ? '' : '<div class="empty">No exercises in range.</div>';
        topList.forEach((t) => {
            const row = document.createElement('div');
            row.className = 'top-row';
            row.innerHTML = `<span class="top-name">${escapeHtml(t.name)}</span><span class="top-val">${fmtKilo(t.volume)} lbs</span>`;
            topHost.appendChild(row);
        });

        // recent workouts — interleaved, newest first, with source tag
        const listHost = $('history-workout-list');
        const filterHost = $('history-type-filter');
        listHost.innerHTML = '';
        const merged = [...lifts, ...watchFiltered].sort((a, b) => (b.date || '').localeCompare(a.date || ''));
        if (!merged.length) {
            // FIT-14: explain WHY the range is empty and offer a path
            // forward — most users opening History on a quiet week want
            // to know it's not broken.
            listHost.innerHTML = `
                <div class="empty">
                    <div class="empty-title">No workouts in this range</div>
                    <div class="empty-hint">Log a workout from the Next or Log tabs and it will appear here.</div>
                </div>
            `;
            return;
        }

        renderHistoryTypeFilter(merged, filterHost);
        const visible = merged.filter((w) => historyFilterKey(w) === state.historyTypeFilter || state.historyTypeFilter === 'all');
        if (!visible.length) {
            const label = state.historyTypeFilter === 'lifted' ? 'Lifted' : state.historyTypeFilter;
            // FIT-14: filtered-empty state distinguishes itself from the
            // truly-empty case and offers a one-tap Clear-filter CTA.
            listHost.innerHTML = `
                <div class="empty">
                    <div class="empty-title">No ${escapeHtml(label)} workouts in this range</div>
                    <div class="empty-hint">Other types may exist — try clearing the filter.</div>
                    <button type="button" class="btn btn-ghost btn-sm" id="btn-history-clear-filter">Show all</button>
                </div>
            `;
            const clearBtn = $('btn-history-clear-filter');
            if (clearBtn) {
                clearBtn.addEventListener('click', () => {
                    state.historyTypeFilter = 'all';
                    renderHistory();
                });
            }
            return;
        }

        visible.slice(0, 40).forEach((w) => {
            const row = document.createElement('div');
            row.className = 'w-row';
            row.tabIndex = 0;
            if (w.source === 'watch') {
                const title = w.activity_type || w.activity || 'Workout';
                const mins = w.duration_minutes || w.duration_min || 0;
                const kcal = w.total_energy_kcal || w.energy_kcal || 0;
                const hr = w.avg_heart_rate ? `${Math.round(w.avg_heart_rate)} bpm` : '';
                const meta = [mins ? `${Math.round(mins)} min` : null, kcal ? `${Math.round(kcal)} kcal` : null, hr].filter(Boolean).join(' · ');
                row.innerHTML = `
                    <div class="w-date">${fmtDate(w.date)}</div>
                    <div>
                        <div class="w-summary"><span class="src-tag src-watch">WATCH</span>${escapeHtml(title)}</div>
                        <div class="w-meta">${escapeHtml(meta) || '—'}</div>
                    </div>
                        <div class="w-volume watch-volume">${mins ? Math.round(mins) + ' min' : ''}</div>
                `;
                row.addEventListener('click', () => openWorkoutDetail(w));
                row.addEventListener('keydown', (ev) => {
                    if (ev.key === 'Enter' || ev.key === ' ') {
                        ev.preventDefault();
                        openWorkoutDetail(w);
                    }
                });
            } else {
                const exNames = (w.exercises || []).map((e) => e.machine || e.exercise).filter(Boolean).slice(0, 3).join(', ');
                row.innerHTML = `
                    <div class="w-date">${fmtDate(w.date)}</div>
                    <div>
                        <div class="w-summary"><span class="src-tag src-lifted">LIFTED</span>${escapeHtml(exNames || '—')}</div>
                        <div class="w-meta">${w.total_sets || 0} sets · ${w.duration_minutes || 0} min</div>
                    </div>
                    <div class="w-analyze-wrap">
                        <div class="w-volume">${fmtKilo(w.total_volume)} lbs</div>
                        <button class="ex-analyze-btn" type="button" data-analyze-id="${escapeHtml(w.id || '')}" data-analyze-date="${escapeHtml(w.date || '')}" title="Analyze this workout" aria-label="Analyze workout from ${escapeHtml(w.date || '')}">↗</button>
                    </div>
                `;
                const btn = row.querySelector('.ex-analyze-btn');
                if (btn) {
                    btn.addEventListener('click', (ev) => {
                        ev.stopPropagation();
                        const req = btn.dataset.analyzeId
                            ? { workout_id: btn.dataset.analyzeId }
                            : { workout_date: btn.dataset.analyzeDate };
                        openAnalyzeModal(req, `Analysis · ${fmtDate(btn.dataset.analyzeDate)}`);
                    });
                }
                row.addEventListener('click', () => openWorkoutDetail(w));
                row.addEventListener('keydown', (ev) => {
                    if (ev.key === 'Enter' || ev.key === ' ') {
                        ev.preventDefault();
                        openWorkoutDetail(w);
                    }
                });
            }
            listHost.appendChild(row);
        });
    }

    function historyFilterKey(w) {
        if (!w) return 'other';
        if (w.source === 'lifted') return 'lifted';
        return (w.activity_type || w.activity || 'Other').toString().trim().toLowerCase() || 'other';
    }

    function historyFilterLabel(key) {
        if (key === 'all') return 'All';
        if (key === 'lifted') return 'Lifted';
        return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
    }

    function renderHistoryTypeFilter(items, host) {
        if (!host) return;
        const counts = new Map();
        items.forEach((w) => {
            const key = historyFilterKey(w);
            counts.set(key, (counts.get(key) || 0) + 1);
        });
        const keys = ['all'];
        if (counts.has('lifted')) keys.push('lifted');
        Array.from(counts.keys())
            .filter((key) => key !== 'lifted')
            .sort((a, b) => a.localeCompare(b))
            .forEach((key) => keys.push(key));
        if (state.historyTypeFilter !== 'all' && !counts.has(state.historyTypeFilter)) {
            state.historyTypeFilter = 'all';
        }
        host.innerHTML = '';
        keys.forEach((key) => {
            const btn = document.createElement('button');
            btn.className = 'history-filter-chip' + (state.historyTypeFilter === key ? ' active' : '');
            btn.type = 'button';
            btn.dataset.filter = key;
            const count = key === 'all' ? items.length : counts.get(key);
            btn.innerHTML = `${escapeHtml(historyFilterLabel(key))}<span>${count || 0}</span>`;
            btn.addEventListener('click', () => {
                state.historyTypeFilter = key;
                renderHistory();
            });
            host.appendChild(btn);
        });
    }

    function setVolume(set) {
        return Number(set && set.weight_lbs || 0) * Number(set && set.reps || 0);
    }

    function workoutExerciseName(ex) {
        return ex && (ex.machine || ex.exercise || ex.name) || 'Exercise';
    }

    function openWorkoutDetail(item) {
        const modal = $('modal-workout-detail');
        const body = $('workout-detail-body');
        const title = $('workout-detail-title');
        const foot = $('workout-detail-foot');
        const deleteBtn = $('btn-delete-workout');
        if (!modal || !body || !title || !item) return;

        if (foot && deleteBtn) {
            const deletable = item.source === 'lifted' && Number.isInteger(item._origIndex);
            foot.hidden = !deletable;
            const fresh = deleteBtn.cloneNode(true);
            deleteBtn.parentNode.replaceChild(fresh, deleteBtn);
            if (deletable) {
                fresh.addEventListener('click', () => openDeleteConfirm(item));
            }
        }

        if (item.source === 'watch') {
            const activity = item.activity_type || item.activity || 'Workout';
            const mins = item.duration_minutes || item.duration_min || 0;
            const kcal = item.total_energy_kcal || item.energy_kcal || 0;
            const hr = item.avg_heart_rate ? `${Math.round(item.avg_heart_rate)} bpm` : '';
            title.textContent = `${activity} · ${fmtDate(item.date)}`;
            body.innerHTML = `
                <div class="workout-detail-kpis">
                    <div><span>${Math.round(mins || 0)}</span><label>minutes</label></div>
                    <div><span>${Math.round(kcal || 0)}</span><label>kcal</label></div>
                    <div><span>${escapeHtml(hr || '—')}</span><label>avg HR</label></div>
                </div>
                ${item.notes ? `<div class="workout-detail-section"><div class="analyze-label">NOTES</div><div class="workout-note">${escapeHtml(item.notes)}</div></div>` : ''}
            `;
            modal.hidden = false;
            return;
        }

        const exercises = item.exercises || [];
        const totalSets = item.total_sets || exercises.reduce((sum, ex) => sum + ((ex.sets || []).length), 0);
        const totalVolume = item.total_volume || exercises.reduce((sum, ex) => sum + (ex.sets || []).reduce((s, set) => s + setVolume(set), 0), 0);
        title.textContent = `Workout · ${fmtDate(item.date)}`;

        // FIT-14: per-exercise source labelling from the workout's
        // adherence record. Exercises in ``adherence.added`` were
        // added freehand; in ``adherence.modified`` were performed
        // with different weight/reps than the plan called for;
        // everything else is treated as planned. Skipped exercises
        // (in ``adherence.skipped``) get their own section below
        // since they have no sets to render.
        const adherence = item.adherence || {};
        const addedSet = new Set((adherence.added || []).map((n) => String(n).toLowerCase()));
        const modifiedSet = new Set((adherence.modified || []).map((m) =>
            String((m && (m.exercise || m.machine || m.name)) || m).toLowerCase()
        ));
        const skipped = (adherence.skipped || []).filter(Boolean);

        const _sourceBadge = (label, cls) =>
            `<span class="exercise-source-tag exercise-source-${cls}">${escapeHtml(label)}</span>`;
        const _exerciseSourceLabel = (name) => {
            const lc = String(name || '').toLowerCase();
            if (addedSet.has(lc)) return _sourceBadge('Added', 'added');
            if (modifiedSet.has(lc)) return _sourceBadge('Modified', 'modified');
            return _sourceBadge('Planned', 'planned');
        };

        const exerciseHtml = exercises.map((ex) => {
            const sets = ex.sets || [];
            const rows = sets.map((set) => `
                <div class="workout-set-row">
                    <span>${escapeHtml(set.set_number || '')}</span>
                    <span>${escapeHtml(set.weight_lbs ?? 0)} lb</span>
                    <span>${escapeHtml(set.reps ?? 0)} reps</span>
                    <span>${set.rpe ? `RPE ${escapeHtml(set.rpe)}` : 'RPE —'}</span>
                    ${set.notes ? `<div class="workout-set-note">${escapeHtml(set.notes)}</div>` : ''}
                </div>
            `).join('');
            const exVolume = sets.reduce((sum, set) => sum + setVolume(set), 0);
            const exName = workoutExerciseName(ex);
            return `
                <div class="workout-detail-ex">
                    <div class="workout-detail-ex-head">
                        <h4>${escapeHtml(exName)} ${_exerciseSourceLabel(exName)}</h4>
                        <span>${sets.length} sets · ${fmtKilo(exVolume)} lbs</span>
                    </div>
                    ${rows || '<div class="empty">No sets logged.</div>'}
                </div>
            `;
        }).join('');

        // FIT-14: skipped exercises — show what the plan called for
        // that the user didn't do, so adherence stays honest.
        const skippedHtml = skipped.length ? `
            <div class="workout-detail-section">
                <div class="analyze-label">SKIPPED</div>
                <div class="workout-detail-skipped">
                    ${skipped.map((name) =>
                        `<span class="skipped-exercise">${escapeHtml(name)}</span>`
                    ).join('')}
                </div>
            </div>
        ` : '';

        // FIT-14: adherence summary line — single-glance "X planned ·
        // Y added · Z skipped" so the user can scan the day's discipline
        // without reading every exercise. ``followed`` is a precomputed
        // boolean from the backend. ``planned`` is the residual after
        // backing out added AND modified exercises — otherwise a
        // modified exercise would be counted in both buckets.
        const plannedCount = exercises.filter((ex) => {
            const n = String(workoutExerciseName(ex) || '').toLowerCase();
            return !addedSet.has(n) && !modifiedSet.has(n);
        }).length;
        const adherenceParts = [];
        adherenceParts.push(`${plannedCount} planned`);
        if (addedSet.size) adherenceParts.push(`${addedSet.size} added`);
        if (skipped.length) adherenceParts.push(`${skipped.length} skipped`);
        if (modifiedSet.size) adherenceParts.push(`${modifiedSet.size} modified`);
        const adherenceHtml = (plannedCount + addedSet.size + skipped.length + modifiedSet.size) ? `
            <div class="workout-detail-adherence">
                <span class="adherence-label">Adherence</span>
                <span class="adherence-summary">${escapeHtml(adherenceParts.join(' · '))}</span>
                ${adherence.followed === false ? '<span class="adherence-flag">Off-plan</span>' : ''}
            </div>
        ` : '';

        const cardio = item.cardio || null;
        const cardioHtml = cardio ? `
            <div class="workout-detail-section">
                <div class="analyze-label">CARDIO</div>
                <div class="workout-note">
                    ${cardio.completed ? 'Completed' : 'Planned'} · ${escapeHtml(cardio.activity_type || (cardio.recommendation || {}).type || 'Cardio')} · ${escapeHtml(cardio.duration_minutes || (cardio.recommendation || {}).duration_minutes || 0)} min
                    ${cardio.notes ? `<div class="workout-set-note">${escapeHtml(cardio.notes)}</div>` : ''}
                </div>
            </div>
        ` : '';

        // FIT-14: in-modal Analyze button so the user doesn't have to
        // close the detail to find the row's analyze affordance. Reuses
        // the existing openAnalyzeModal which is read-only and does NOT
        // mutate LAST_WORKOUT_RECOMMENDATION (verified by FIT-14 tests).
        const analyzeBtnHtml = `
            <div class="workout-detail-actions">
                <button type="button" class="btn btn-ghost btn-sm" id="btn-analyze-detail"
                    data-analyze-id="${escapeHtml(item.id || '')}"
                    data-analyze-date="${escapeHtml(item.date || '')}">
                    Analyze workout
                </button>
            </div>
        `;

        body.innerHTML = `
            <div class="workout-detail-kpis">
                <div><span>${fmtInt(totalSets)}</span><label>sets</label></div>
                <div><span>${fmtKilo(totalVolume)}</span><label>lbs</label></div>
                <div><span>${fmtInt(item.duration_minutes || 0)}</span><label>minutes</label></div>
            </div>
            ${adherenceHtml}
            ${analyzeBtnHtml}
            ${item.notes ? `<div class="workout-detail-section"><div class="analyze-label">WORKOUT NOTES</div><div class="workout-note">${escapeHtml(item.notes)}</div></div>` : ''}
            <div class="workout-detail-section">
                <div class="analyze-label">EXERCISES</div>
                ${exerciseHtml || '<div class="empty">No exercises logged.</div>'}
            </div>
            ${skippedHtml}
            ${cardioHtml}
        `;

        // Wire the Analyze button. Build the same request shape the
        // history-row analyze button uses (id preferred, date fallback).
        const analyzeBtn = $('btn-analyze-detail');
        if (analyzeBtn) {
            analyzeBtn.addEventListener('click', () => {
                const id = analyzeBtn.dataset.analyzeId;
                const date = analyzeBtn.dataset.analyzeDate;
                const request = id ? { workout_id: id }
                    : date ? { workout_date: date }
                    : { latest: true };
                modal.hidden = true;
                openAnalyzeModal(request, `Analyze · ${fmtDate(item.date)}`);
            });
        }

        modal.hidden = false;
    }

    function workoutSummaryLabel(w) {
        const exNames = (w.exercises || [])
            .map((e) => e.machine || e.exercise)
            .filter(Boolean)
            .slice(0, 3)
            .join(', ');
        const parts = [];
        if (exNames) parts.push(exNames);
        if (w.total_volume) parts.push(`${fmtKilo(w.total_volume)} lbs`);
        const suffix = parts.length ? ` (${parts.join(' · ')})` : '';
        return `Workout from ${fmtDate(w.date)}${suffix}`;
    }

    function openDeleteConfirm(workout) {
        const modal = $('modal-delete-confirm');
        const text = $('delete-confirm-text');
        const btn = $('btn-confirm-delete');
        if (!modal || !text || !btn) return;
        text.textContent = `Delete ${workoutSummaryLabel(workout)}? This will recompute history.`;
        const fresh = btn.cloneNode(true);
        btn.parentNode.replaceChild(fresh, btn);
        fresh.addEventListener('click', () => performDeleteWorkout(workout, fresh));
        modal.hidden = false;
    }

    async function performDeleteWorkout(workout, button) {
        if (!workout || !Number.isInteger(workout._origIndex)) return;
        const confirmModal = $('modal-delete-confirm');
        if (button) {
            button.disabled = true;
            button.textContent = 'Deleting…';
        }
        try {
            const res = await api('/api/delete-history', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ type: 'workout', index: workout._origIndex }),
            });
            const deletedEntry = res && res.deleted;
            const detailModal = $('modal-workout-detail');
            if (detailModal) detailModal.hidden = true;
            if (confirmModal) confirmModal.hidden = true;
            state.history = null;
            await renderHistory();
            if (deletedEntry) {
                toastUndo('Workout deleted', () => restoreDeletedWorkout(deletedEntry));
            } else {
                toast('Workout deleted', 'ok');
            }
        } catch (err) {
            toast('Delete failed', 'err');
            console.error(err);
        } finally {
            if (button) {
                button.disabled = false;
                button.textContent = 'Delete';
            }
        }
    }

    async function restoreDeletedWorkout(entry) {
        try {
            await api('/api/restore-history', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ type: 'workout', entry }),
            });
            state.history = null;
            await renderHistory();
            toast('Workout restored', 'ok');
        } catch (err) {
            toast('Restore failed', 'err');
            console.error(err);
        }
    }

    function buildDailyBuckets(days) {
        // Build local-date buckets (not UTC) so evening-hour rendering doesn't
        // shift the History bars forward by a day in CDT/CST.
        const buckets = [];
        for (let i = days - 1; i >= 0; i--) {
            const d = new Date();
            d.setDate(d.getDate() - i);
            const y = d.getFullYear();
            const m = String(d.getMonth() + 1).padStart(2, '0');
            const day = String(d.getDate()).padStart(2, '0');
            buckets.push({ iso: `${y}-${m}-${day}`, count: 0, volume: 0 });
        }
        return buckets;
    }
    function groupIntoBuckets(dailyBuckets, targetBuckets) {
        if (dailyBuckets.length <= targetBuckets) {
            return dailyBuckets.map((b) => ({ start: new Date(b.iso + 'T00:00:00'), end: new Date(b.iso + 'T23:59:59'), count: b.count, volume: b.volume, label: fmtDate(b.iso) }));
        }
        const binSize = Math.ceil(dailyBuckets.length / targetBuckets);
        const out = [];
        for (let i = 0; i < dailyBuckets.length; i += binSize) {
            const slice = dailyBuckets.slice(i, i + binSize);
            if (!slice.length) continue;
            out.push({
                start: new Date(slice[0].iso + 'T00:00:00'),
                end: new Date(slice[slice.length - 1].iso + 'T23:59:59'),
                count: slice.reduce((a, s) => a + s.count, 0),
                volume: slice.reduce((a, s) => a + (s.volume || 0), 0),
                label: fmtDate(slice[slice.length - 1].iso),
            });
        }
        return out;
    }

    // --- Body ----------------------------------------------------
    async function renderBody() {
        const body = await getBody();
        const all = (body && body.history) || [];
        const latest = all[0] || {};
        $('body-weight').textContent = latest.weight_lbs != null ? Number(latest.weight_lbs).toFixed(1) : '--';
        $('body-bf').textContent = latest.body_fat_pct != null ? Number(latest.body_fat_pct).toFixed(1) : '--';

        const cutoff = new Date(); cutoff.setDate(cutoff.getDate() - 90);
        const recent = all.filter((h) => h.date && new Date(h.date + 'T00:00:00') >= cutoff)
            .slice().reverse();
        const wPts = recent.filter(h => h.weight_lbs != null).map((h) => ({ value: Number(h.weight_lbs), label: fmtDate(h.date) }));
        const bfPts = recent.filter(h => h.body_fat_pct != null).map((h) => ({ value: Number(h.body_fat_pct), label: fmtDate(h.date) }));
        lineChart($('chart-weight'), wPts, { color: '#60a5fa' });
        lineChart($('chart-bf'), bfPts, { color: '#a78bfa' });
        // FIT-112: takeaways below the body trend charts.
        const wTake = deriveWeightTakeaway(wPts);
        setChartTakeaway('chart-weight-takeaway', wTake.text, { empty: wTake.empty });
        const bfTake = deriveBodyFatTakeaway(bfPts);
        setChartTakeaway('chart-bf-takeaway', bfTake.text, { empty: bfTake.empty });

        if (wPts.length >= 2) {
            const d = wPts[wPts.length - 1].value - wPts[0].value;
            $('body-weight-delta').textContent = `${d >= 0 ? '↑' : '↓'} ${Math.abs(d).toFixed(1)} lb (30d)`;
            $('body-weight-delta').className = 'metric-delta ' + (d < 0 ? 'pos' : 'neg');
        }
        if (bfPts.length >= 2) {
            const d = bfPts[bfPts.length - 1].value - bfPts[0].value;
            $('body-bf-delta').textContent = `${d >= 0 ? '↑' : '↓'} ${Math.abs(d).toFixed(1)}% (30d)`;
            $('body-bf-delta').className = 'metric-delta ' + (d < 0 ? 'pos' : 'neg');
        }

        const grid = $('measurements-grid');
        grid.innerHTML = '';
        const latestWeight = latest.weight_lbs != null ? Number(latest.weight_lbs) : null;
        const latestBf = latest.body_fat_pct != null ? Number(latest.body_fat_pct) : null;
        const oldest = recent.find((h) => h.weight_lbs != null || h.body_fat_pct != null) || {};
        const oldestWeight = oldest.weight_lbs != null ? Number(oldest.weight_lbs) : null;
        const oldestBf = oldest.body_fat_pct != null ? Number(oldest.body_fat_pct) : null;
        const fatMass = latestWeight != null && latestBf != null ? latestWeight * latestBf / 100 : null;
        const leanMass = latestWeight != null && fatMass != null ? latestWeight - fatMass : null;
        const composition = [
            {
                label: 'Lean Mass',
                value: leanMass != null ? `${leanMass.toFixed(1)} lb` : '—',
                sub: latestWeight != null && latestBf != null ? 'estimated from body fat' : 'needs weight + body fat',
            },
            {
                label: 'Fat Mass',
                value: fatMass != null ? `${fatMass.toFixed(1)} lb` : '—',
                sub: latestWeight != null && latestBf != null ? `${latestBf.toFixed(1)}% of body weight` : 'needs body fat %',
            },
            {
                label: 'Weight 90D',
                value: latestWeight != null && oldestWeight != null ? `${latestWeight - oldestWeight >= 0 ? '+' : '-'}${Math.abs(latestWeight - oldestWeight).toFixed(1)} lb` : '—',
                sub: oldest.date ? `since ${fmtDate(oldest.date)}` : 'needs more entries',
            },
            {
                label: 'Body Fat 90D',
                value: latestBf != null && oldestBf != null ? `${latestBf - oldestBf >= 0 ? '+' : '-'}${Math.abs(latestBf - oldestBf).toFixed(1)}%` : '—',
                sub: oldest.date ? `since ${fmtDate(oldest.date)}` : 'needs more entries',
            },
        ];
        composition.forEach((m) => {
            const row = document.createElement('div');
            row.className = 'm-row';
            row.innerHTML = `<div><span class="m-label">${escapeHtml(m.label)}</span>${m.sub ? `<span class="m-sub">${escapeHtml(m.sub)}</span>` : ''}</div><span class="m-val">${escapeHtml(m.value)}</span>`;
            grid.appendChild(row);
        });
        if (latest && latest.date) $('measurements-date').textContent = fmtDate(latest.date);

        // FIT-13: nutrition-history-driven interpretation + recomp +
        // 14-day nutrition trend. Each runs independently and is
        // tolerant of endpoint failure — the existing body charts
        // above must keep rendering even when nutrition history
        // is unavailable.
        renderBodyInterpretationAndNutritionTrend();
        renderBodyRecompTargetProgress();
    }

    // FIT-13: layered interpretation of recent scale variance using
    // accepted food context. Surfaces high-sodium + late-meal flags and
    // confidence/correction reliability — never says "fat gain" or
    // "fat loss"; the user reads the data, the UI is non-prescriptive.
    async function renderBodyInterpretationAndNutritionTrend() {
        const card = $('body-interpretation-card');
        const list = $('body-interpretation-notes');
        const nutCard = $('body-nutrition-card');
        const nutRows = $('body-nutrition-rows');
        const nutSub = $('body-nutrition-sub');
        if (!card || !list) return;

        let history = null;
        try {
            const payload = await api('/api/nutrition-history');
            history = (payload && payload.history) || [];
        } catch {
            card.hidden = true;
            if (nutCard) nutCard.hidden = true;
            return;
        }
        if (!history || !history.length) {
            card.hidden = true;
            if (nutCard) nutCard.hidden = true;
            return;
        }

        // --- Interpretation notes ---------------------------------
        // Each note is composed from the same accepted-entries view
        // the macro card uses, so the body view can't claim something
        // the food log doesn't actually show.
        const notes = [];
        const recent3 = history.slice(-3); // last 3 days
        const recent7 = history.slice(-7); // last 7 days

        const highSodiumDays = recent3.filter((d) => d.high_sodium).map((d) => d.date);
        if (highSodiumDays.length) {
            const label = highSodiumDays.length === 1
                ? `Sodium was high on ${fmtDate(highSodiumDays[0])}.`
                : `Sodium was high on ${highSodiumDays.length} of the last 3 days.`;
            notes.push(`${label} High-sodium days can temporarily inflate scale weight from water retention; this isn't fat gain.`);
        }
        const lateMealDays = recent3.filter((d) => d.late_meal).map((d) => d.date);
        if (lateMealDays.length) {
            notes.push(`Late meals on ${lateMealDays.length} of the last 3 days. Eating late can shift the next-morning weigh-in.`);
        }
        const estimatedCount7 = recent7.reduce((s, d) => s + (d.estimated_count || 0), 0);
        const correctedCount7 = recent7.reduce((s, d) => s + (d.corrected_count || 0), 0);
        const manualCount7 = recent7.reduce((s, d) => s + (d.manual_count || 0), 0);
        const totalAccepted7 = estimatedCount7 + correctedCount7 + manualCount7;
        if (totalAccepted7 > 0) {
            const firmPct = Math.round(100 * (correctedCount7 + manualCount7) / totalAccepted7);
            if (firmPct < 50) {
                notes.push(`Most of the last 7 days' nutrition is AI-estimated rather than user-corrected. Trends are directional, not exact.`);
            }
        }
        const pendingCount = recent3.reduce((s, d) => s + (d.pending_count || 0), 0);
        if (pendingCount > 0) {
            notes.push(`${pendingCount} pending estimate${pendingCount === 1 ? '' : 's'} in the last 3 days — accept or discard them to make the trend more accurate.`);
        }

        if (notes.length) {
            list.innerHTML = notes.map((n) =>
                `<li class="body-interpretation-note">${escapeHtml(n)}</li>`
            ).join('');
            card.hidden = false;
        } else {
            list.innerHTML = '';
            card.hidden = true;
        }

        // --- Nutrition trend table (14 days, newest first) --------
        if (!nutCard || !nutRows) return;
        const dataDays = history.filter((d) => (d.entries_count || 0) > 0).reverse();
        if (!dataDays.length) {
            nutCard.hidden = true;
            return;
        }
        nutCard.hidden = false;
        if (nutSub) {
            nutSub.textContent = `${dataDays.length} day${dataDays.length === 1 ? '' : 's'} logged`;
        }
        nutRows.innerHTML = dataDays.slice(0, 14).map((d) => {
            const calPct = d.calories_pct;
            const proPct = d.protein_pct;
            const calClass = calPct == null ? 'pct-unknown'
                : (calPct >= 110 ? 'pct-over' : calPct >= 90 ? 'pct-ok' : 'pct-under');
            const proClass = proPct == null ? 'pct-unknown'
                : (proPct >= 90 ? 'pct-ok' : 'pct-under');
            // Reliability badge: distinguish estimated-only days from
            // ones the user corrected / manually entered.
            const totalAccepted = (d.estimated_count || 0)
                + (d.corrected_count || 0) + (d.manual_count || 0);
            let reliability;
            if (!totalAccepted) {
                reliability = '<span class="trend-reliability trend-rel-none">no data</span>';
            } else if ((d.corrected_count || 0) + (d.manual_count || 0) >= totalAccepted / 2) {
                reliability = '<span class="trend-reliability trend-rel-firm">corrected</span>';
            } else {
                reliability = '<span class="trend-reliability trend-rel-estimated">estimated</span>';
            }
            const ctxFlags = [];
            if (d.high_sodium) ctxFlags.push('high sodium');
            if (d.late_meal) ctxFlags.push('late meal');
            const ctxText = ctxFlags.length
                ? `<span class="trend-context">${escapeHtml(ctxFlags.join(' · '))}</span>`
                : '';
            // FIT-93: each row is an expandable summary; tap to fetch and
            // render that day's individual food_log entries inline. The
            // `data-date` attribute drives the on-click fetch.
            const dateAttr = escapeHtml(d.date);
            return `
                <details class="body-nutrition-row body-nutrition-row-expandable" data-date="${dateAttr}">
                    <summary class="body-nutrition-row-summary">
                        <span class="trend-date">${escapeHtml(fmtDate(d.date))}</span>
                        <span class="trend-cal ${calClass}">${d.calories || 0}<small>${calPct != null ? ` (${calPct}%)` : ''}</small></span>
                        <span class="trend-protein ${proClass}">${d.protein_g || 0}g<small>${proPct != null ? ` (${proPct}%)` : ''}</small></span>
                        <span class="trend-sodium">${(d.sodium_mg || 0).toLocaleString()}mg</span>
                        ${reliability}
                        ${ctxText}
                    </summary>
                    <div class="body-nutrition-row-meals" data-loaded="0">
                        <div class="body-nutrition-row-loading">Tap to load meals…</div>
                    </div>
                </details>
            `;
        }).join('');

        // FIT-93: lazy-load meals on first expand. Avoid re-fetching if
        // the row was opened before. On failure we render an explicit
        // Retry button instead of a "tap row to retry" hint — toggling
        // the row first closes it, which would be a confusing two-tap
        // recovery flow.
        function loadDayMeals(row, slot, date) {
            slot.setAttribute('data-loaded', '1');
            slot.innerHTML = '<div class="body-nutrition-row-loading">Loading meals…</div>';
            api(`/api/food-logs/by-date/${encodeURIComponent(date)}`)
                .then((payload) => renderFoodLogMealList(slot, (payload && payload.entries) || []))
                .catch(() => {
                    slot.setAttribute('data-loaded', '0');
                    slot.innerHTML = '';
                    const msg = document.createElement('div');
                    msg.className = 'body-nutrition-row-loading';
                    msg.textContent = 'Couldn\'t load meals.';
                    slot.appendChild(msg);
                    const retry = document.createElement('button');
                    retry.type = 'button';
                    retry.className = 'body-nutrition-row-retry';
                    retry.textContent = 'Retry';
                    retry.addEventListener('click', (ev) => {
                        ev.preventDefault();
                        ev.stopPropagation();
                        loadDayMeals(row, slot, date);
                    });
                    slot.appendChild(retry);
                });
        }
        nutRows.querySelectorAll('details.body-nutrition-row-expandable').forEach((row) => {
            row.addEventListener('toggle', () => {
                if (!row.open) return;
                const date = row.getAttribute('data-date');
                const slot = row.querySelector('.body-nutrition-row-meals');
                if (!date || !slot || slot.getAttribute('data-loaded') === '1') return;
                loadDayMeals(row, slot, date);
            });
        });

        fetchFoodLogRefreshNotices().catch((err) => console.warn('food-log refresh notices failed:', err));
        fetchWorkoutAdaptationNotices().catch((err) => console.warn('workout adaptation notices failed:', err));
    }

    function renderFoodLogMealList(container, entries) {
        if (!entries.length) {
            container.innerHTML = '<div class="body-nutrition-row-empty">No individual meals recorded for this day.</div>';
            return;
        }
        container.innerHTML = entries.map((e, idx) => {
            const name = (e.item_name || e.portion_description || 'Meal').trim();
            const time = (e.logged_at || '').slice(11, 16) || '';
            const cal = e.calories != null ? `${Math.round(e.calories)} kcal` : '';
            const macros = [
                e.protein_g != null ? `${Math.round(e.protein_g)}P` : '',
                e.carbs_g != null ? `${Math.round(e.carbs_g)}C` : '',
                e.fat_g != null ? `${Math.round(e.fat_g)}F` : '',
            ].filter(Boolean).join('/');
            // Distinguish estimated vs corrected/manual so the user sees
            // where to focus correction effort. `correction_state` is the
            // canonical signal (FIT-61); fall back to source heuristic.
            const state = (e.correction_state || '').toLowerCase();
            let stateLabel = '';
            if (state === 'corrected' || state === 'accepted_manual') {
                stateLabel = '<span class="meal-state meal-state-corrected">corrected</span>';
            } else if (state === 'pending_review') {
                stateLabel = '<span class="meal-state meal-state-pending">pending</span>';
            } else if (state === 'accepted') {
                stateLabel = '<span class="meal-state meal-state-estimated">estimated</span>';
            }
            // FIT-97: tap-target index lets the click handler look up the
            // original entry by index rather than re-parsing the DOM, so
            // user-supplied strings never need round-tripping.
            // FIT-97 a11y: per-row accessible name so screen readers
            // distinguish entries (e.g. "View meal details: Burger at 12:30").
            const ariaParts = ['View meal details:', name];
            if (time) ariaParts.push('at', time);
            const ariaLabel = escapeHtml(ariaParts.filter(Boolean).join(' '));
            return `
                <button class="body-nutrition-meal body-nutrition-meal-tap" type="button" data-meal-idx="${idx}" aria-label="${ariaLabel}">
                    <div class="body-nutrition-meal-head">
                        ${time ? `<span class="meal-time">${escapeHtml(time)}</span>` : ''}
                        <span class="meal-name">${escapeHtml(name)}</span>
                        ${stateLabel}
                    </div>
                    <div class="body-nutrition-meal-macros">
                        ${cal ? `<span>${escapeHtml(cal)}</span>` : ''}
                        ${macros ? `<span>${escapeHtml(macros)}</span>` : ''}
                    </div>
                </button>
            `;
        }).join('');

        // FIT-97: wire the click → modal flow. Entries are referenced by
        // index from the bound `entries` array so the closure keeps the
        // canonical data (including client_id) regardless of DOM state.
        container.querySelectorAll('[data-meal-idx]').forEach((btn) => {
            btn.addEventListener('click', () => {
                const idx = parseInt(btn.getAttribute('data-meal-idx'), 10);
                const entry = entries[idx];
                if (entry) openMealDetailModal(entry, container);
            });
        });
    }

    // FIT-97: populate and show the meal-detail modal for a food_log entry.
    // Reads the bounded projection returned by /api/food-logs/by-date so
    // the same field set as `renderFoodLogMealList`.
    function openMealDetailModal(entry, listContainer) {
        const modal = $('modal-meal-detail');
        if (!modal) return;

        const itemName = (entry.item_name || entry.portion_description || 'Meal').trim();
        const portion = (entry.portion_description || '').trim() || '—';
        const loggedAt = entry.logged_at ? entry.logged_at.replace('T', ' ') : '—';
        const source = (entry.source || '—').toString();
        const conf = entry.confidence != null
            ? `${Math.round(Number(entry.confidence) * 100)}%`
            : '—';
        // SQLite roundtrip turns Python True into integer 1, so we can't
        // use strict-equality here. Treat any truthy as Yes, explicit 0/
        // false as No, null/undefined as unknown.
        const fromImage = entry.from_image == null
            ? '—'
            : (entry.from_image ? 'Yes' : 'No');

        $('meal-detail-title').textContent = itemName;
        $('meal-detail-item').textContent = itemName;
        $('meal-detail-portion').textContent = portion;
        $('meal-detail-time').textContent = loggedAt;
        $('meal-detail-source').textContent = source;
        $('meal-detail-confidence').textContent = conf;
        $('meal-detail-from-image').textContent = fromImage;
        $('meal-detail-cal').textContent = entry.calories != null ? `${Math.round(entry.calories)} kcal` : '—';
        $('meal-detail-pro').textContent = entry.protein_g != null ? `${Math.round(entry.protein_g)} g` : '—';
        $('meal-detail-carb').textContent = entry.carbs_g != null ? `${Math.round(entry.carbs_g)} g` : '—';
        $('meal-detail-fat').textContent = entry.fat_g != null ? `${Math.round(entry.fat_g)} g` : '—';
        $('meal-detail-sodium').textContent = entry.sodium_mg != null ? `${Math.round(entry.sodium_mg)} mg` : '—';

        // FIT-97: surface the FIT-90 stub-vision caveat explicitly when
        // the user is looking at a photo-derived entry that was generated
        // by the canned `stub_vision_estimate`. Otherwise the inspect
        // view would silently look authoritative.
        const stubNotice = $('meal-detail-stub-notice');
        // FIT-100: track whether the caveat applies so setMealDetailMode
        // can restore it after Cancel returns from edit to view.
        const stubApplies = !!(source && source.toLowerCase().startsWith('stub_vision'));
        if (stubNotice) {
            stubNotice.dataset.applies = stubApplies ? '1' : '0';
            stubNotice.hidden = !stubApplies;
        }
        // FIT-97 AC2: show the photo retention note when this entry was
        // logged from a photo. The raw image is never persisted (FIT-9),
        // so the note tells the user the photo isn't retrievable and only
        // the extracted estimate is kept. Hidden for text-only entries
        // since there's no photo to talk about.
        const retentionNote = $('meal-detail-retention-note');
        const retentionApplies = !!entry.from_image;
        if (retentionNote) {
            retentionNote.dataset.applies = retentionApplies ? '1' : '0';
            retentionNote.hidden = !retentionApplies;
        }

        // FIT-97: wire Delete to the existing DELETE endpoint. On success,
        // remove the row from the inline list, close the modal, and let
        // the user know via toast.
        const deleteBtn = $('btn-meal-detail-delete');
        if (deleteBtn) {
            // Replace any prior handler — re-binding cleanly avoids stale
            // client_ids from previous opens.
            const fresh = deleteBtn.cloneNode(true);
            deleteBtn.parentNode.replaceChild(fresh, deleteBtn);
            fresh.disabled = !entry.client_id;
            fresh.addEventListener('click', async () => {
                if (!entry.client_id) return;
                fresh.disabled = true;
                try {
                    await api(`/api/meal-intake/${encodeURIComponent(entry.client_id)}`, { method: 'DELETE' });
                    modal.hidden = true;
                    toast('Meal deleted', 'ok');
                    // Force the trend card to re-fetch so the row updates
                    // immediately without a manual reload.
                    renderBodyInterpretationAndNutritionTrend();
                    // FIT-107: notify the food-log sheet (if open) so it
                    // can refresh its sections after a delete.
                    document.dispatchEvent(new CustomEvent('fit107:meal-deleted', {
                        detail: { client_id: entry.client_id },
                    }));
                } catch (err) {
                    console.error(err);
                    toast(apiErrorMessage(err, 'Delete failed'), 'err');
                    fresh.disabled = false;
                }
            });
        }

        // FIT-100: edit/correct flow. Default to view mode each open so a
        // previous edit session doesn't leak.
        setMealDetailMode('view');
        const editBtn = $('btn-meal-detail-edit');
        if (editBtn) {
            const fresh = editBtn.cloneNode(true);
            editBtn.parentNode.replaceChild(fresh, editBtn);
            // Correct only makes sense when the entry has a client_id —
            // /api/add-nutrition upserts by client_id. Disable otherwise.
            fresh.disabled = !entry.client_id;
            fresh.addEventListener('click', () => {
                prefillMealEditForm(entry);
                setMealDetailMode('edit');
            });
        }
        const cancelBtn = $('btn-meal-edit-cancel');
        if (cancelBtn) {
            const fresh = cancelBtn.cloneNode(true);
            cancelBtn.parentNode.replaceChild(fresh, cancelBtn);
            fresh.addEventListener('click', () => setMealDetailMode('view'));
        }
        const saveBtn = $('btn-meal-edit-save');
        if (saveBtn) {
            const fresh = saveBtn.cloneNode(true);
            saveBtn.parentNode.replaceChild(fresh, saveBtn);
            // Reset disabled — cloneNode preserves the attribute, so a prior
            // save attempt that left it disabled would persist across opens.
            fresh.disabled = false;
            fresh.addEventListener('click', () => saveMealCorrection(entry, modal, fresh));
        }

        modal.hidden = false;
    }

    function setMealDetailMode(mode) {
        const view = $('meal-detail-view');
        const edit = $('meal-detail-edit');
        const footView = $('meal-detail-foot-view');
        const footEdit = $('meal-detail-foot-edit');
        const stubNotice = $('meal-detail-stub-notice');
        const retentionNote = $('meal-detail-retention-note');
        const errBox = $('meal-detail-edit-error');
        const editing = mode === 'edit';
        if (view) view.hidden = editing;
        if (edit) edit.hidden = !editing;
        if (footView) footView.hidden = editing;
        if (footEdit) footEdit.hidden = !editing;
        // Notices hide while editing (keep focus on the form) but get
        // restored on the way back to view mode for entries where they
        // still apply. `data-applies` is set when the modal opens.
        const restoreNotice = (el) => {
            if (!el) return;
            if (editing) el.hidden = true;
            else el.hidden = el.dataset.applies !== '1';
        };
        restoreNotice(stubNotice);
        restoreNotice(retentionNote);
        if (errBox) { errBox.hidden = true; errBox.textContent = ''; }
    }

    function prefillMealEditForm(entry) {
        const fields = [
            ['meal-edit-item', entry.item_name || ''],
            ['meal-edit-portion', entry.portion_description || ''],
            ['meal-edit-cal', entry.calories != null ? entry.calories : ''],
            ['meal-edit-pro', entry.protein_g != null ? entry.protein_g : ''],
            ['meal-edit-carb', entry.carbs_g != null ? entry.carbs_g : ''],
            ['meal-edit-fat', entry.fat_g != null ? entry.fat_g : ''],
            ['meal-edit-sodium', entry.sodium_mg != null ? entry.sodium_mg : ''],
        ];
        fields.forEach(([id, val]) => {
            const el = $(id);
            if (el) el.value = val;
        });
    }

    async function saveMealCorrection(entry, modal, saveBtn) {
        const errBox = $('meal-detail-edit-error');
        const showError = (msg) => {
            if (!errBox) return;
            errBox.textContent = msg;
            errBox.hidden = false;
        };
        if (errBox) { errBox.hidden = true; errBox.textContent = ''; }
        if (!entry.client_id) {
            showError('This entry has no client_id and can\'t be corrected here.');
            return;
        }
        const itemName = ($('meal-edit-item').value || '').trim();
        if (!itemName) {
            showError('Item name is required.');
            return;
        }
        const parseNumOrNull = (id, allowNull = true) => {
            const raw = ($(id).value || '').trim();
            if (raw === '') return allowNull ? null : NaN;
            const n = Number(raw);
            if (!Number.isFinite(n) || n < 0) return NaN;
            return n;
        };
        const calories = parseNumOrNull('meal-edit-cal', false);
        const protein = parseNumOrNull('meal-edit-pro', false);
        const carbs = parseNumOrNull('meal-edit-carb');
        const fat = parseNumOrNull('meal-edit-fat');
        const sodium = parseNumOrNull('meal-edit-sodium');
        if ([calories, protein, carbs, fat, sodium].some((v) => Number.isNaN(v))) {
            showError('Calories and macros must be non-negative numbers.');
            return;
        }
        saveBtn.disabled = true;
        // Anchor the correction to the original meal date. Prefer the
        // explicit `date` field on the row (always present for food_log
        // entries) and fall back to `logged_at` if needed; never let the
        // server default to today, which would silently move a corrected
        // legacy entry forward in time.
        const originalDate = entry.date
            || (entry.logged_at ? entry.logged_at.slice(0, 10) : undefined);
        const payload = {
            client_id: entry.client_id,
            date: originalDate,
            logged_at: entry.logged_at || undefined,
            source: entry.source || undefined,
            // Mark the correction explicitly so /api/adherence and history
            // surfaces distinguish corrected entries from estimated ones.
            correction_state: 'corrected',
            item_name: itemName,
            portion_description: ($('meal-edit-portion').value || '').trim() || undefined,
            calories: Math.round(calories),
            protein_g: protein,
            carbs_g: carbs,
            fat_g: fat,
            sodium_mg: sodium != null ? Math.round(sodium) : null,
        };
        try {
            await api('/api/add-nutrition', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            modal.hidden = true;
            toast('Correction saved', 'ok');
            renderBodyInterpretationAndNutritionTrend();
        } catch (err) {
            console.error(err);
            showError(apiErrorMessage(err, 'Save failed'));
            saveBtn.disabled = false;
        }
    }

    // ===================== FIT-107: View food log sheet =====================
    // Opens #modal-food-log from the dashboard macro card and populates
    // Today / Yesterday / Recent-14d sections from existing endpoints.
    // No backend changes — reuses /api/nutrition-history and
    // /api/food-logs/by-date/<YYYY-MM-DD>. Each meal row hands off to
    // openMealDetailModal so the existing FIT-97 inspect/delete flow works.
    let foodLogSheetListenerBound = false;

    function foodLogYmd(date) {
        const y = date.getFullYear();
        const m = String(date.getMonth() + 1).padStart(2, '0');
        const d = String(date.getDate()).padStart(2, '0');
        return `${y}-${m}-${d}`;
    }

    function renderFoodLogSectionTotals(el, day) {
        if (!el) return;
        if (!day || !day.entries_count) {
            el.hidden = true;
            el.textContent = '';
            return;
        }
        const parts = [];
        if (day.calories != null) {
            const tgt = day.calories_target ? ` / ${Math.round(day.calories_target)}` : '';
            parts.push(`${Math.round(day.calories)}${tgt} kcal`);
        }
        if (day.protein_g != null) {
            const tgt = day.protein_target_g ? ` / ${Math.round(day.protein_target_g)}` : '';
            parts.push(`${Math.round(day.protein_g)}${tgt}P`);
        }
        if (day.entries_count != null) {
            parts.push(`${day.entries_count} ${day.entries_count === 1 ? 'entry' : 'entries'}`);
        }
        el.textContent = parts.join(' · ');
        el.hidden = false;
    }

    function renderFoodLogRecent14d(history, todayStr, yesterdayStr) {
        const host = $('food-log-recent-rows');
        if (!host) return;
        const rows = (history || [])
            .filter((d) => d && d.date && d.date !== todayStr && d.date !== yesterdayStr)
            .filter((d) => Number(d.entries_count || 0) > 0);
        if (!rows.length) {
            host.innerHTML = '<div class="food-log-section-empty">No meals logged in the last 14 days.</div>';
            return;
        }
        host.innerHTML = rows.map((d) => {
            const calClass = d.calories_pct == null ? 'pct-unknown'
                : (d.calories_pct > 110 ? 'pct-over' : (d.calories_pct < 80 ? 'pct-under' : 'pct-ok'));
            const proClass = d.protein_pct == null ? 'pct-unknown'
                : (d.protein_pct >= 90 ? 'pct-ok' : 'pct-under');
            const calPct = d.calories_pct != null ? ` (${d.calories_pct}%)` : '';
            const proPct = d.protein_pct != null ? ` (${d.protein_pct}%)` : '';
            const reliability = d.entries_count
                ? `<span class="trend-reliability">${d.corrected_count || 0}c·${d.estimated_count || 0}e</span>`
                : '<span class="trend-reliability">—</span>';
            const ctxFlags = [];
            if (d.high_sodium) ctxFlags.push('high sodium');
            if (d.late_meal) ctxFlags.push('late meal');
            const ctxText = ctxFlags.length
                ? `<span class="trend-context">${escapeHtml(ctxFlags.join(' · '))}</span>`
                : '';
            const dateAttr = escapeHtml(d.date);
            return `
                <details class="body-nutrition-row body-nutrition-row-expandable" data-date="${dateAttr}">
                    <summary class="body-nutrition-row-summary">
                        <span class="trend-date">${escapeHtml(fmtDate(d.date))}</span>
                        <span class="trend-cal ${calClass}">${d.calories || 0}<small>${calPct}</small></span>
                        <span class="trend-protein ${proClass}">${d.protein_g || 0}g<small>${proPct}</small></span>
                        <span class="trend-sodium">${(d.sodium_mg || 0).toLocaleString()}mg</span>
                        ${reliability}
                        ${ctxText}
                    </summary>
                    <div class="body-nutrition-row-meals" data-loaded="0">
                        <div class="body-nutrition-row-loading">Tap to load meals…</div>
                    </div>
                </details>
            `;
        }).join('');

        host.querySelectorAll('details.body-nutrition-row-expandable').forEach((row) => {
            row.addEventListener('toggle', () => {
                if (!row.open) return;
                const date = row.getAttribute('data-date');
                const slot = row.querySelector('.body-nutrition-row-meals');
                if (!date || !slot || slot.getAttribute('data-loaded') === '1') return;
                loadFoodLogDayMeals(slot, date);
            });
        });
    }

    function loadFoodLogDayMeals(slot, date) {
        slot.setAttribute('data-loaded', '1');
        slot.innerHTML = '<div class="body-nutrition-row-loading">Loading meals…</div>';
        api(`/api/food-logs/by-date/${encodeURIComponent(date)}`)
            .then((payload) => renderFoodLogMealList(slot, (payload && payload.entries) || []))
            .catch(() => {
                slot.setAttribute('data-loaded', '0');
                slot.innerHTML = '';
                const msg = document.createElement('div');
                msg.className = 'body-nutrition-row-loading';
                msg.textContent = "Couldn't load meals.";
                slot.appendChild(msg);
                const retry = document.createElement('button');
                retry.type = 'button';
                retry.className = 'body-nutrition-row-retry';
                retry.textContent = 'Retry';
                retry.addEventListener('click', (ev) => {
                    ev.preventDefault();
                    ev.stopPropagation();
                    loadFoodLogDayMeals(slot, date);
                });
                slot.appendChild(retry);
            });
    }

    function renderFoodLogSectionLoading(slot) {
        if (slot) slot.innerHTML = '<div class="food-log-section-loading">Loading…</div>';
    }

    function renderFoodLogSectionError(slot, retryFn) {
        if (!slot) return;
        slot.innerHTML = '';
        const msg = document.createElement('div');
        msg.className = 'food-log-section-loading';
        msg.textContent = "Couldn't load meals.";
        slot.appendChild(msg);
        if (retryFn) {
            const retry = document.createElement('button');
            retry.type = 'button';
            retry.className = 'food-log-section-retry';
            retry.textContent = 'Retry';
            retry.addEventListener('click', retryFn);
            slot.appendChild(retry);
        }
    }

    async function loadFoodLogDaySection(date, slotId, totalsEl, dayMeta) {
        const slot = $(slotId);
        if (!slot) return 0;
        renderFoodLogSectionLoading(slot);
        try {
            const payload = await api(`/api/food-logs/by-date/${encodeURIComponent(date)}`);
            const entries = (payload && payload.entries) || [];
            if (!entries.length) {
                slot.innerHTML = '<div class="food-log-section-empty">No meals logged.</div>';
                renderFoodLogSectionTotals(totalsEl, dayMeta);
                return 0;
            }
            renderFoodLogMealList(slot, entries);
            renderFoodLogSectionTotals(totalsEl, dayMeta);
            return entries.length;
        } catch (err) {
            renderFoodLogSectionError(slot, () => loadFoodLogDaySection(date, slotId, totalsEl, dayMeta));
            return 0;
        }
    }

    async function openFoodLogSheet() {
        const modal = $('modal-food-log');
        if (!modal) return;
        // Reset UI to loading state before showing so reopening doesn't
        // flash stale content from a previous open.
        const todayMeals = $('food-log-today-meals');
        const yesterdayMeals = $('food-log-yesterday-meals');
        const recentRows = $('food-log-recent-rows');
        const empty = $('food-log-empty');
        if (empty) empty.hidden = true;
        if (todayMeals) todayMeals.innerHTML = '<div class="food-log-section-loading">Loading…</div>';
        if (yesterdayMeals) yesterdayMeals.innerHTML = '<div class="food-log-section-loading">Loading…</div>';
        if (recentRows) recentRows.innerHTML = '<div class="food-log-section-loading">Loading…</div>';
        modal.hidden = false;

        // FIT-107: refresh on delete from the inner meal-detail modal.
        // Bind once on first open — listener checks visibility at fire time.
        if (!foodLogSheetListenerBound) {
            document.addEventListener('fit107:meal-deleted', () => {
                const m = $('modal-food-log');
                if (m && !m.hidden) openFoodLogSheet();
            });
            foodLogSheetListenerBound = true;
        }

        // FIT-107 (Codex audit): derive today/yesterday from the
        // server-keyed nutrition-history response rather than client local
        // time. /api/nutrition-history orders days oldest→newest with
        // today as the last entry, so the trailing two entries are the
        // canonical server-day keys. Avoids timezone-drift bugs around
        // midnight where the browser and server disagree on "today".
        let history = [];
        try {
            const hist = await api('/api/nutrition-history');
            history = (hist && hist.history) || [];
        } catch (err) {
            console.error('FIT-107: nutrition-history failed', err);
        }

        // Fall back to client-local only if the server gave us nothing
        // (e.g. empty history). In that case the by-date lookups will
        // return empty entries either way.
        const todayStr = (history.length && history[history.length - 1].date)
            || foodLogYmd(new Date());
        const yesterdayStr = (history.length >= 2 && history[history.length - 2].date)
            || foodLogYmd(new Date(Date.now() - 86400000));

        const todayMeta = history.find((d) => d && d.date === todayStr) || null;
        const yesterdayMeta = history.find((d) => d && d.date === yesterdayStr) || null;

        const [todayCount, yesterdayCount] = await Promise.all([
            loadFoodLogDaySection(todayStr, 'food-log-today-meals', $('food-log-today-totals'), todayMeta),
            loadFoodLogDaySection(yesterdayStr, 'food-log-yesterday-meals', $('food-log-yesterday-totals'), yesterdayMeta),
        ]);

        renderFoodLogRecent14d(history, todayStr, yesterdayStr);

        const recentTotal = (history || [])
            .filter((d) => d && d.date !== todayStr && d.date !== yesterdayStr)
            .reduce((sum, d) => sum + Number(d.entries_count || 0), 0);
        if (empty) empty.hidden = (todayCount + yesterdayCount + recentTotal) > 0;
    }

    async function renderBodyRecompTargetProgress() {
        const card = $('body-recomp-card');
        const rows = $('body-recomp-rows');
        if (!card || !rows) return;
        let recomp = null;
        try {
            recomp = await api('/api/body-recomp');
        } catch {
            card.hidden = true;
            return;
        }
        const summary = recomp && recomp.summary;
        const latest = summary && summary.latest;
        if (!summary || !latest) {
            card.hidden = true;
            return;
        }
        const targetWeight = summary.target_weight_lbs;
        const targetBf = summary.target_body_fat_pct;
        const currentWeight = latest.weight_lbs;
        const currentBf = latest.body_fat_pct;
        const eta = summary.eta_weeks;

        const items = [];
        if (targetWeight != null && currentWeight != null) {
            const diff = Number(currentWeight) - Number(targetWeight);
            const direction = diff > 0 ? 'above' : diff < 0 ? 'below' : 'at';
            items.push({
                label: 'Target weight',
                value: `${Number(targetWeight).toFixed(1)} lb`,
                sub: `${Math.abs(diff).toFixed(1)} lb ${direction} target`,
            });
        }
        if (targetBf != null && currentBf != null) {
            const diff = Number(currentBf) - Number(targetBf);
            const direction = diff > 0 ? 'above' : diff < 0 ? 'below' : 'at';
            items.push({
                label: 'Target body fat',
                value: `${Number(targetBf).toFixed(1)}%`,
                sub: `${Math.abs(diff).toFixed(1)}% ${direction} target`,
            });
        }
        // ETA can be negative when the current weight trend is moving
        // AWAY from the target (e.g., target 175lb, currently 180lb,
        // weight is still going up). Rendering "-3.4 weeks" is
        // nonsense — surface honest copy instead so the body view
        // doesn't lie about reachability.
        if (eta != null && Number.isFinite(Number(eta))) {
            const etaNum = Number(eta);
            if (etaNum < 0) {
                items.push({
                    label: 'ETA at current pace',
                    value: 'Not on track',
                    sub: 'current trend is moving away from target',
                });
            } else {
                items.push({
                    label: 'ETA at current pace',
                    value: `${etaNum.toFixed(1)} weeks`,
                    sub: 'based on 14-day weight velocity',
                });
            }
        }
        if (!items.length) {
            card.hidden = true;
            return;
        }
        rows.innerHTML = items.map((m) =>
            `<div class="m-row"><div><span class="m-label">${escapeHtml(m.label)}</span><span class="m-sub">${escapeHtml(m.sub)}</span></div><span class="m-val">${escapeHtml(m.value)}</span></div>`
        ).join('');
        card.hidden = false;
    }

    // --- Stats ---------------------------------------------------
    const MUSCLE_GROUPS = [
        { label: 'Upper body', muscles: ['chest', 'back', 'shoulders', 'biceps', 'triceps'] },
        { label: 'Lower body', muscles: ['quads', 'hamstrings', 'glutes', 'adductors', 'calves'] },
        { label: 'Core',       muscles: ['core'] },
    ];

    async function renderMuscleRecovery() {
        const data = await getMuscleFatigue();
        const host = $('muscle-recovery-groups');
        const empty = $('muscle-recovery-empty');
        const subEl = $('muscle-recovery-sub');
        if (!host || !empty) return;
        if (!data || !Object.keys(data).length) {
            empty.hidden = false;
            host.hidden = true;
            if (subEl) subEl.textContent = '—';
            return;
        }
        empty.hidden = true;
        host.hidden = false;
        host.innerHTML = '';
        MUSCLE_GROUPS.forEach(({ label, muscles }) => {
            const visible = muscles.filter((m) => data[m]);
            if (!visible.length) return;
            const block = document.createElement('div');
            block.className = 'mr-group';
            const head = document.createElement('div');
            head.className = 'mr-group-label';
            head.textContent = label;
            block.appendChild(head);
            const grid = document.createElement('div');
            grid.className = 'mr-grid';
            visible.forEach((m) => {
                const info = data[m] || {};
                const level = info.fatigue_level || 'recovered';
                const lastTrained = info.last_trained ? fmtDate(info.last_trained) : 'no record';
                const sets = info.weekly_sets || 0;
                const readiness = info.readiness != null ? info.readiness : '—';
                const cell = document.createElement('button');
                cell.type = 'button';
                cell.className = `mr-cell mr-${level}`;
                cell.setAttribute('aria-label', `${capitalize(m)}: ${level}, readiness ${readiness} of 10`);
                cell.innerHTML = `
                    <span class="mr-cell-row">
                        <span class="mr-name">${escapeHtml(capitalize(m))}</span>
                        <span class="mr-readiness">${escapeHtml(String(readiness))}<span class="mr-readiness-max">/10</span></span>
                    </span>
                    <span class="mr-meta">${sets} sets · ${escapeHtml(lastTrained)}</span>
                `;
                const tipLines = [
                    `Recovery: ${level}`,
                    `Readiness: ${readiness}/10`,
                    `Weekly sets: ${sets}`,
                    `Last trained: ${lastTrained}`,
                ];
                if (info.recent_soreness_note) tipLines.push(`Note: ${info.recent_soreness_note}`);
                if (info.recommendation) tipLines.push(info.recommendation);
                cell.title = tipLines.join('\n');
                cell.addEventListener('click', () => toggleMuscleRecoveryDetail(cell, m, info));
                grid.appendChild(cell);
            });
            block.appendChild(grid);
            host.appendChild(block);
        });

        const counts = { recovered: 0, mild: 0, moderate: 0, high: 0, severe: 0 };
        let total = 0;
        Object.values(data).forEach((d) => {
            const lvl = d && d.fatigue_level;
            if (lvl && counts[lvl] != null) { counts[lvl] += 1; total += 1; }
        });
        if (subEl) {
            if (!total) {
                subEl.textContent = '—';
            } else {
                const fresh = counts.recovered + counts.mild;
                const sore = counts.high + counts.severe;
                subEl.textContent = sore
                    ? `${fresh}/${total} fresh · ${sore} sore`
                    : `${fresh}/${total} fresh`;
            }
        }
    }

    function toggleMuscleRecoveryDetail(cell, muscle, info) {
        const existing = cell.parentNode.querySelector('.mr-detail');
        const wasOpen = existing && existing.dataset.muscle === muscle;
        if (existing) existing.remove();
        cell.parentNode.querySelectorAll('.mr-cell.active').forEach((c) => c.classList.remove('active'));
        if (wasOpen) return;
        cell.classList.add('active');
        const detail = document.createElement('div');
        detail.className = 'mr-detail';
        detail.dataset.muscle = muscle;
        const lastTrained = info.last_trained ? fmtDate(info.last_trained) : 'no record';
        const lines = [
            { label: 'Recovery', value: info.fatigue_level || 'recovered' },
            { label: 'Readiness', value: `${info.readiness ?? '—'}/10` },
            { label: 'Weekly sets', value: String(info.weekly_sets || 0) },
            { label: 'Last trained', value: lastTrained },
        ];
        if (info.recent_soreness_note) lines.push({ label: 'Soreness note', value: info.recent_soreness_note });
        if (info.recommendation) lines.push({ label: 'Coach', value: info.recommendation });
        detail.innerHTML = lines.map((l) => `<div class="mr-detail-row"><span class="mr-detail-k">${escapeHtml(l.label)}</span><span class="mr-detail-v">${escapeHtml(l.value)}</span></div>`).join('');
        cell.parentNode.appendChild(detail);
    }

    async function renderStats() {
        const hist = await getHistory();
        const all = (hist && hist.workouts) || [];
        const days = state.ranges.stats;
        const cutoff = new Date(); cutoff.setDate(cutoff.getDate() - days);
        const workouts = all.filter((w) => w.date && new Date(w.date + 'T00:00:00') >= cutoff);

        // Prior period for deltas
        const priorCutoffEnd = cutoff;
        const priorCutoffStart = new Date(cutoff); priorCutoffStart.setDate(priorCutoffStart.getDate() - days);
        const priorWorkouts = all.filter((w) => w.date && new Date(w.date + 'T00:00:00') >= priorCutoffStart && new Date(w.date + 'T00:00:00') < priorCutoffEnd);

        const totalVol = workouts.reduce((a, w) => a + Number(w.total_volume || 0), 0);
        const priorVol = priorWorkouts.reduce((a, w) => a + Number(w.total_volume || 0), 0);
        const totalSets = workouts.reduce((a, w) => a + Number(w.total_sets || 0), 0);
        const priorSets = priorWorkouts.reduce((a, w) => a + Number(w.total_sets || 0), 0);
        const totalTime = workouts.reduce((a, w) => a + Number(w.duration_minutes || 0), 0);
        const priorTime = priorWorkouts.reduce((a, w) => a + Number(w.duration_minutes || 0), 0);
        const avgVol = workouts.length ? totalVol / workouts.length : 0;
        const rpeList = [];
        workouts.forEach((w) => (w.exercises || []).forEach((e) => (e.sets || []).forEach((s) => {
            if (s.rpe != null) rpeList.push(Number(s.rpe));
        })));
        const avgRpe = rpeList.length ? rpeList.reduce((a, b) => a + b, 0) / rpeList.length : 0;

        $('stats-workouts').textContent = workouts.length;
        $('stats-workouts-delta').innerHTML = renderDelta(workouts.length - priorWorkouts.length, 'vs prior');
        $('stats-volume').textContent = fmtKilo(totalVol);
        $('stats-volume-delta').innerHTML = renderDelta(pctDelta(totalVol, priorVol), 'vs prior', true);
        $('stats-avg-vol').textContent = fmtKilo(avgVol);
        $('stats-avg-vol-delta').textContent = '';
        $('stats-rpe').textContent = avgRpe ? avgRpe.toFixed(1) : '--';
        $('stats-rpe-sub').textContent = rpeList.length ? `${rpeList.length} sets` : '';
        $('stats-sets').textContent = totalSets;
        $('stats-sets-delta').innerHTML = renderDelta(totalSets - priorSets, 'vs prior');
        $('stats-time').textContent = fmtDur(totalTime);
        $('stats-time-delta').innerHTML = renderDelta(totalTime - priorTime, 'min', false);

        await renderMuscleRecovery();

        // Volume by muscle
        const muscles = {};
        workouts.forEach((w) => (w.exercises || []).forEach((e) => {
            const mg = (e.muscle_group || 'other').toLowerCase();
            const vol = (e.sets || []).reduce((a, s) => a + Number(s.weight_lbs || 0) * Number(s.reps || 0), 0);
            muscles[mg] = (muscles[mg] || 0) + vol;
        }));
        const muscleColors = { back: '#22d3ee', legs: '#a78bfa', quads: '#a78bfa', hamstrings: '#c084fc', glutes: '#e879f9', chest: '#fb923c', shoulders: '#22c55e', biceps: '#f472b6', triceps: '#f472b6', arms: '#f472b6', core: '#fbbf24', other: '#64748b' };
        const slices = Object.entries(muscles)
            .sort((a, b) => b[1] - a[1])
            .map(([k, v]) => ({ label: k, value: v, color: muscleColors[k] || '#64748b' }));
        donutChart($('chart-muscle-donut'), slices, { subtitle: 'VOLUME' });
        const legend = $('muscle-legend');
        legend.innerHTML = '';
        const totalMuscle = slices.reduce((a, s) => a + s.value, 0) || 1;
        slices.slice(0, 8).forEach((s) => {
            const row = document.createElement('div');
            row.className = 'legend-row';
            const pct = ((s.value / totalMuscle) * 100).toFixed(0);
            row.innerHTML = `<span class="legend-dot" style="background:${s.color}"></span><span>${escapeHtml(capitalize(s.label))}</span><span class="legend-val">${pct}%</span>`;
            legend.appendChild(row);
        });

        // Insights list
        const insights = await getInsights();
        const list = $('insights-list');
        list.innerHTML = '';
        const items = Array.isArray(insights && insights.insights) ? insights.insights : [];
        if (!items.length) {
            list.innerHTML = '<div class="empty">Log a few workouts to start tracking progress.</div>';
        } else {
            items.forEach((ins) => {
                const card = document.createElement('div');
                card.className = 'in-card';
                const kind = (ins.type || 'info').toLowerCase();
                const map = { success: 'pos', warning: 'warn', danger: 'neg', info: 'info' };
                const iconClass = map[kind] || 'info';
                const iconChar = kind === 'success' ? '↑' : kind === 'warning' ? '!' : kind === 'danger' ? '▲' : 'i';
                card.innerHTML = `
                    <div class="in-icon ${iconClass}">${iconChar}</div>
                    <div>
                        <div class="in-title">${escapeHtml(ins.title || '—')}</div>
                        <div class="in-detail">${escapeHtml(ins.detail || '')}</div>
                    </div>
                `;
                list.appendChild(card);
            });
        }
    }

    function pctDelta(cur, prev) {
        if (!prev) return cur ? 100 : 0;
        return ((cur - prev) / prev) * 100;
    }
    function renderDelta(d, suffix = '', isPct = false) {
        if (d == null || Number.isNaN(d) || d === 0) return '<span class="metric-delta mute">— '+suffix+'</span>';
        const cls = d > 0 ? 'pos' : 'neg';
        const sym = d > 0 ? '↑' : '↓';
        const val = Math.abs(d);
        const out = isPct ? val.toFixed(0) + '%' : (Number.isInteger(val) ? val : val.toFixed(1));
        return `<span class="metric-delta ${cls}">${sym} ${out} ${suffix}</span>`;
    }
    function capitalize(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : ''; }
    function escapeHtml(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

    // --- Settings ------------------------------------------------
    function selectGoalOption(option) {
        if (!option || !option.dataset || !option.dataset.goal) return;
        selectedGoalToRestoreFocus = option.dataset.goal;
        updateSetting({ training_goal: option.dataset.goal });
    }

    function handleGoalOptionKeydown(e) {
        const key = e.key;
        if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', ' ', 'Enter'].includes(key)) return;
        const options = qsa('[role="radio"]', $('settings-goals')).filter((option) => !option.disabled);
        if (!options.length) return;
        const current = options.indexOf(e.currentTarget);
        if (current < 0) return;
        e.preventDefault();
        if (key === ' ' || key === 'Enter') {
            selectGoalOption(e.currentTarget);
            return;
        }
        let next = current;
        if (key === 'ArrowLeft' || key === 'ArrowUp') next = (current - 1 + options.length) % options.length;
        else if (key === 'ArrowRight' || key === 'ArrowDown') next = (current + 1) % options.length;
        else if (key === 'Home') next = 0;
        else if (key === 'End') next = options.length - 1;
        options[next].focus({ preventScroll: true });
        selectGoalOption(options[next]);
    }

    async function renderSettings() {
        // FIT-16: settings + Oura first. Oura refresh upserts today's
        // row, so the dashboard freshness block (which we use below to
        // drive the integration chips + detail panels) must be read
        // AFTER that upsert lands — parallel fetches race and can show
        // "Cached · stale" right after a successful live refresh.
        const [st, oura, whoop] = await Promise.all([getSettings(), getOuraStatus(true, true), getWhoopStatus(true)]);
        // FIT-16: use the side-effect-free /api/freshness endpoint.
        // /api/dashboard would also work but it regenerates
        // next_workout and writes LAST_WORKOUT_RECOMMENDATION server-
        // side, which would silently overwrite an adjusted/swapped
        // plan whenever the user opens Settings.
        let freshness = null;
        try {
            const fresh = await api('/api/freshness');
            freshness = (fresh && fresh.freshness) || null;
        } catch {
            freshness = null;
        }
        const ouraFreshness = freshness && freshness.oura;
        const appleFreshness = freshness && freshness.apple_health;
        const whoopFreshness = mergeWhoopFreshnessNode(
            freshness && freshness.whoop,
            whoop,
            collectSourceConflicts(state.dashboard, state.reco)
        );
        freshness = Object.assign({}, freshness || {}, { whoop: whoopFreshness });
        const host = $('settings-goals');
        host.innerHTML = '';
        (st.available_goals || []).forEach((g) => {
            const selected = st.training_goal === g.value || st.goal === g.value || (st.goal_details && st.goal_details.name === g.name);
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'goal-opt' + (selected ? ' active' : '');
            btn.dataset.goal = g.value;
            btn.setAttribute('role', 'radio');
            btn.setAttribute('aria-checked', selected ? 'true' : 'false');
            btn.tabIndex = selected ? 0 : -1;
            btn.innerHTML = `
                <div>
                    <div class="goal-title">${escapeHtml(g.name)}</div>
                    <span class="goal-sub">${escapeHtml(g.description)}</span>
                </div>
                <span class="goal-check" aria-hidden="true">✓</span>
            `;
            btn.addEventListener('click', () => selectGoalOption(btn));
            btn.addEventListener('keydown', handleGoalOptionKeydown);
            host.appendChild(btn);
        });
        if (selectedGoalToRestoreFocus) {
            const focusGoal = selectedGoalToRestoreFocus;
            selectedGoalToRestoreFocus = null;
            const optionToFocus = qsa('[role="radio"]', host).find((option) => option.dataset.goal === focusGoal);
            if (optionToFocus) optionToFocus.focus({ preventScroll: true });
        }

        const dobInput = $('settings-date-of-birth');
        dobInput.value = st.date_of_birth || '';
        dobInput.onchange = () => updateSetting({ date_of_birth: dobInput.value || '' });

        const sexSel = $('settings-sex');
        sexSel.innerHTML = '';
        (st.sex_options || []).forEach((option) => {
            const opt = document.createElement('option');
            opt.value = option.value;
            opt.textContent = option.label;
            if (option.value === (st.sex || '')) opt.selected = true;
            sexSel.appendChild(opt);
        });
        sexSel.onchange = () => updateSetting({ sex: sexSel.value || '' });

        const durSel = $('settings-duration');
        durSel.innerHTML = '';
        (st.time_options || []).forEach((t) => {
            const opt = document.createElement('option');
            opt.value = t.value; opt.textContent = t.label;
            if (Number(t.value) === Number(st.available_time_minutes)) opt.selected = true;
            durSel.appendChild(opt);
        });
        durSel.onchange = () => updateSetting({ available_time_minutes: Number(durSel.value) });

        const range = $('settings-sessions');
        range.value = st.sessions_per_week_target || 3;
        $('settings-sessions-val').textContent = range.value;
        range.oninput = () => { $('settings-sessions-val').textContent = range.value; };
        range.onchange = () => updateSetting({ sessions_per_week_target: Number(range.value) });

        const eqSel = $('settings-equipment');
        eqSel.innerHTML = '';
        (st.equipment_options || []).forEach((e) => {
            const opt = document.createElement('option');
            opt.value = e.value; opt.textContent = e.label;
            if (e.value === st.equipment_preference) opt.selected = true;
            eqSel.appendChild(opt);
        });
        eqSel.onchange = () => updateEquipment(eqSel.value);

        // Integration chips: routed through the shared formatters so Settings
        // matches the dashboard reco-card exactly. Detail panels below each
        // chip carry the richer cached/live + sync nuance.
        renderFreshnessChips(freshness, SETTINGS_FRESHNESS_SLOTS);
        renderWhoopFreshnessDetail(whoop, whoopFreshness, collectSourceConflicts(state.dashboard, state.reco));
        renderOuraFreshnessDetail(oura, ouraFreshness);
        // FIT-111: populate the four settings group header chips so the
        // user gets a glance-level signal per section. Reads live chip
        // state from the DOM, so renderAiCoachHealth + renderPushSection
        // also call it after their async updates to keep the headers in
        // lockstep.
        renderSettingsGroupSummaries();

        // Apple Health — prefer the real sync-status endpoint over
        // the file-existence probe, and only claim "connected" when a
        // sync actually landed recently AND the server's freshness
        // block agrees the data isn't stale. The chip must not contradict
        // the detail panel: a recent HAE attempt that inserted only
        // duplicate / old records should NOT read green.
        try {
            const ah = await api('/api/apple-health/sync/status');
            // FIT-16: use ``last_sync`` (insertion ts) for the chip's
            // age calc — not ``last_attempt``, which can be recent
            // even when nothing actually inserted.
            const lastSyncRaw = ah && ah.last_sync;
            const last = parseServerDateTime(lastSyncRaw);
            const ageDays = last ? Math.floor((Date.now() - last.getTime()) / 86400000) : Infinity;
            const ahFreshnessStatus = appleFreshness && appleFreshness.status;
            const dataIsStale = ahFreshnessStatus === 'stale';
            const dataIsMissing = ahFreshnessStatus === 'missing';
            const connected = last && ageDays <= 3 && !dataIsStale && !dataIsMissing;
            const setupConfigured = Boolean(ah && ah.setup_configured);
            const detail = $('apple-last-export');
            const dotOn = connected || dataIsStale || setupConfigured;
            $('apple-int-dot').className = dotOn ? 'int-dot int-dot-on' : 'int-dot';
            if (detail) {
                detail.textContent = last
                    ? `Last export ${fmtDateTime(lastSyncRaw)} · ${ah.total_records || 0} records`
                    : 'No accepted export yet';
            }
            // FIT-16: detail panel — last accepted vs last attempt,
            // data-through (record_date), record-type breakdown, and
            // a stale-warning row driven by the same freshness signal
            // the chip above uses.
            renderAppleHealthFreshnessDetail(ah, appleFreshness);
        } catch {
            $('apple-int-dot').className = 'int-dot';
            const detail = $('apple-last-export');
            if (detail) detail.textContent = 'Export status unavailable';
            renderAppleHealthFreshnessDetail(null, appleFreshness);
        }
        const setupBtn = $('btn-apple-setup');
        if (setupBtn && !setupBtn.dataset.wired) {
            setupBtn.dataset.wired = '1';
            setupBtn.addEventListener('click', openAppleSetup);
        }

        try {
            const w = await api('/api/weather');
            if (w && w.condition) {
                $('weather-state').textContent = `${w.condition} · ${w.temp_f != null ? Math.round(w.temp_f) + '°F' : ''}`;
                $('weather-state').className = 'state-chip ok';
            }
        } catch {}

        // FIT-15: AI Coach health + 24h metrics card.
        renderAiCoachHealth();
        startAiCoachHealthRefresh();

        // FIT-40: Web Push permission flow + alert surfaces.
        renderPushSection();
    }

    async function syncWhoop() {
        if (state.whoopUi.syncInFlight) return;
        state.whoopUi.syncInFlight = true;
        state.whoopUi.lastError = '';
        renderWhoopFreshnessDetail(state.whoopStatus, mergeWhoopFreshnessNode(null, state.whoopStatus, []), []);
        try {
            const body = await api('/api/whoop/sync', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ trigger: 'manual' }),
            });
            if (body && typeof body === 'object') state.whoopStatus = Object.assign({}, state.whoopStatus || {}, body.status || body.whoop_status || body.whoop || {});
            toast('WHOOP sync requested.', 'ok');
        } catch (error) {
            state.whoopUi.lastError = error && error.message ? error.message : 'WHOOP sync failed.';
            toast('WHOOP sync failed.', 'err');
        } finally {
            state.whoopUi.syncInFlight = false;
            state.dashboard = null;
            await renderSettings();
        }
    }

    async function disconnectWhoop() {
        if (state.whoopUi.disconnectInFlight) return;
        state.whoopUi.disconnectInFlight = true;
        state.whoopUi.lastError = '';
        renderWhoopFreshnessDetail(state.whoopStatus, mergeWhoopFreshnessNode(null, state.whoopStatus, []), []);
        try {
            await api('/api/whoop/disconnect', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({}),
            });
            state.whoopStatus = Object.assign({}, state.whoopStatus || {}, {
                connected: false,
                status: WHOOP_UI_STATES.disconnected,
            });
            toast('WHOOP disconnected.', 'ok');
        } catch (error) {
            state.whoopUi.lastError = error && error.message ? error.message : 'WHOOP disconnect failed.';
            toast('WHOOP disconnect failed.', 'err');
        } finally {
            state.whoopUi.disconnectInFlight = false;
            state.dashboard = null;
            await renderSettings();
        }
    }

    // ── FIT-15: AI Coach health + metrics ─────────────────────────
    // Threshold matches FIT-20's planned "Apply Adjustment" warning so
    // both surfaces flip on/off together when the local model is flaky.
    const AI_COACH_FALLBACK_PCT_WARNING = 20.0;
    const AI_COACH_REFRESH_MS = 30000;
    let _aiCoachRefreshTimer = null;

    function startAiCoachHealthRefresh() {
        // Avoid stacking intervals if renderSettings is called repeatedly.
        if (_aiCoachRefreshTimer) clearInterval(_aiCoachRefreshTimer);
        _aiCoachRefreshTimer = setInterval(() => {
            // Pause refresh when the user is on a different tab — saves
            // network and keeps the tab quiet. The next tab activation
            // calls renderSettings -> renderAiCoachHealth anyway.
            if (state.currentTab !== 'tab-settings') return;
            renderAiCoachHealth();
        }, AI_COACH_REFRESH_MS);
    }

    // FIT-166: friendly host names rendered on the AI coach card.
    // The adapter returns generic role names ("primary"/"fallback"), so
    // the UI maps them to the deployment's real hosts. If the adapter
    // ever starts returning a non-generic name, prefer it over the
    // default — this keeps the UI honest if hardware changes.
    const AI_PRIMARY_HOST_DEFAULT = 'ASUS GX10';
    const AI_FALLBACK_HOST_DEFAULT = 'Mac Studio';

    function _aiHostName(check, fallbackLabel) {
        if (!check) return fallbackLabel;
        const raw = (check.name || '').trim();
        const generic = raw === '' || raw === 'primary' || raw === 'fallback';
        return generic ? fallbackLabel : raw;
    }

    function _aiFallbackHostName(fallback) {
        return fallback ? _aiHostName(fallback, AI_FALLBACK_HOST_DEFAULT) : 'No distinct fallback';
    }

    function _setAiCoachUnavailable(message) {
        // Degraded mode: the rest of Settings keeps working. Acceptance
        // criterion: "Metrics refresh periodically without breaking the
        // app if the route is unavailable."
        const primaryState = $('ai-primary-state');
        const fallbackState = $('ai-fallback-state');
        const metricsPct = $('ai-metrics-fallback-pct');
        const metricsDetail = $('ai-metrics-detail');
        const primaryDetail = $('ai-primary-detail');
        const fallbackDetail = $('ai-fallback-detail');
        const warnRow = $('ai-coach-warning-row');
        const primaryDot = $('ai-primary-dot');
        const fallbackDot = $('ai-fallback-dot');
        if (primaryState) { primaryState.textContent = 'Unavailable'; primaryState.className = 'state-chip unknown'; }
        if (fallbackState) { fallbackState.textContent = 'Unavailable'; fallbackState.className = 'state-chip unknown'; }
        if (metricsPct) { metricsPct.textContent = '—'; metricsPct.className = 'state-chip unknown'; }
        if (primaryDetail) primaryDetail.textContent = message || 'Health endpoint unreachable';
        if (fallbackDetail) fallbackDetail.textContent = '';
        if (metricsDetail) metricsDetail.textContent = 'Metrics unavailable';
        if (warnRow) warnRow.hidden = true;
        if (primaryDot) primaryDot.className = 'int-dot';
        if (fallbackDot) fallbackDot.className = 'int-dot';
        // FIT-166: card-level headline degrades to "unavailable" too,
        // matching the existing per-row chips so the card doesn't look
        // half-painted when the health route is unreachable.
        const unavailableHeadline = {
            stateText: 'AI offline',
            hostText: 'health endpoint unreachable',
            chipText: 'Offline',
            chipCls: 'state-chip stale',
            dotCls: 'int-dot',
            detail: message || 'Health endpoint unreachable',
            showSep: true,
        };
        _renderAiCoachHeadline(unavailableHeadline);
        _announceAiCoachHeadline(unavailableHeadline);
    }

    function _renderAiCoachHeadline(parts) {
        const state = $('ai-coach-headline-state');
        const host = $('ai-coach-headline-host');
        const sep = $('ai-coach-headline-sep');
        const chip = $('ai-coach-headline-chip');
        const dot = $('ai-coach-headline-dot');
        const detail = $('ai-coach-headline-detail');
        if (state) state.textContent = parts.stateText;
        if (host) host.textContent = parts.hostText;
        if (sep) sep.hidden = !parts.showSep;
        if (chip) { chip.textContent = parts.chipText; chip.className = parts.chipCls; }
        if (dot) dot.className = parts.dotCls;
        if (detail) detail.textContent = parts.detail || '';
    }

    // FIT-177: Screen-reader announcement for AI Coach headline state
    // changes. _renderAiCoachHeadline stays as the visual DOM paint; the
    // polite live region writes through this helper. The semantic key
    // intentionally excludes `detail` so a copy tweak in the detail line
    // (e.g. swapping the model name) doesn't re-announce an unchanged
    // host-routing state. `undefined` is the first-transition sentinel
    // (vs. `''` which is "we already announced empty"), so the very
    // first render announces. Early return when unchanged keeps the
    // 30-second poll from re-announcing the same state.
    let _lastAiCoachAnnouncementKey;

    function _announceAiCoachHeadline(parts) {
        const key = `${parts.stateText}|${parts.hostText}|${parts.chipText}`;
        if (_lastAiCoachAnnouncementKey === key) return;
        _lastAiCoachAnnouncementKey = key;
        const region = $('ai-coach-headline-announcement');
        if (!region) return;
        const sep = parts.hostText ? ' · ' : '';
        region.textContent = `AI coach: ${parts.stateText}${sep}${parts.hostText}`;
    }

    function _aiPrimaryUnavailableReason(primary, primaryHost) {
        if (!primary || !primary.reachable) return `${primaryHost} unreachable`;
        if (!primary.model_loaded) return `${primaryHost} model not loaded`;
        return `${primaryHost} not serving traffic`;
    }

    function _aiCoachHeadlineFromHealth(health) {
        // Three states the FIT-166 acceptance criteria call out:
        //   1. Primary healthy           → "Ready · <primary host>"        (ok)
        //   2. Primary down, fallback up → "Fallback active · <fallback host>" (warn)
        //   3. Both down / unavailable   → "AI offline"                    (stale)
        const primary = (health && health.primary) || null;
        const fallback = (health && health.fallback) || null;
        const activeRole = (health && health.active_role) || null;
        const primaryHost = _aiHostName(primary, AI_PRIMARY_HOST_DEFAULT);
        const fallbackHost = _aiFallbackHostName(fallback);
        const primaryOk = !!(primary && primary.reachable && primary.model_loaded);
        const fallbackOk = !!(fallback && fallback.reachable && fallback.model_loaded);

        if (primaryOk && activeRole !== 'fallback') {
            return {
                stateText: 'Ready',
                hostText: primaryHost,
                chipText: 'Online',
                chipCls: 'state-chip ok',
                dotCls: 'int-dot int-dot-on',
                detail: `Serving from ${primaryHost}.`,
                showSep: true,
            };
        }
        if (fallbackOk) {
            return {
                stateText: 'Fallback active',
                hostText: fallbackHost,
                chipText: 'Fallback',
                chipCls: 'state-chip warn',
                dotCls: 'int-dot',
                detail: `${_aiPrimaryUnavailableReason(primary, primaryHost)} — ${fallbackHost} is serving traffic.`,
                showSep: true,
            };
        }
        return {
            stateText: 'AI offline',
            hostText: fallback ? `${primaryHost} & ${fallbackHost} unavailable` : `${primaryHost} unavailable`,
            chipText: 'Offline',
            chipCls: 'state-chip stale',
            dotCls: 'int-dot',
            detail: 'No AI host is serving traffic — deterministic plan remains the source of truth.',
            showSep: true,
        };
    }

    function _aiCheckLabel(check) {
        if (!check) return { text: 'Not configured', cls: 'state-chip unknown' };
        if (!check.reachable) return { text: 'Unreachable', cls: 'state-chip stale' };
        if (!check.model_loaded) return { text: 'Model not loaded', cls: 'state-chip warn' };
        return { text: 'Ready', cls: 'state-chip ok' };
    }

    function _renderAiHealthFields(health) {
        const primary = (health && health.primary) || null;
        const fallback = (health && health.fallback) || null;
        const activeRole = (health && health.active_role) || null;

        const primaryState = $('ai-primary-state');
        const fallbackState = $('ai-fallback-state');
        const primaryDetail = $('ai-primary-detail');
        const fallbackDetail = $('ai-fallback-detail');
        const primaryDot = $('ai-primary-dot');
        const fallbackDot = $('ai-fallback-dot');
        // FIT-166: friendly host labels alongside the role tag.
        const primaryHostEl = $('ai-primary-host');
        const fallbackHostEl = $('ai-fallback-host');
        const fallbackRoleEl = $('ai-fallback-role');
        const primaryHost = _aiHostName(primary, AI_PRIMARY_HOST_DEFAULT);
        const fallbackHost = _aiFallbackHostName(fallback);
        if (primaryHostEl) primaryHostEl.textContent = primaryHost;
        if (fallbackHostEl) fallbackHostEl.textContent = fallbackHost;
        if (fallbackRoleEl) fallbackRoleEl.hidden = !fallback;

        const pLabel = _aiCheckLabel(primary);
        const fLabel = fallback ? _aiCheckLabel(fallback) : { text: 'Primary only', cls: 'state-chip unknown' };

        if (primaryState) {
            primaryState.textContent = pLabel.text + (activeRole === 'primary' ? ' · active' : '');
            primaryState.className = pLabel.cls;
        }
        if (fallbackState) {
            fallbackState.textContent = fLabel.text + (activeRole === 'fallback' ? ' · active' : '');
            fallbackState.className = fLabel.cls;
        }
        if (primaryDetail) {
            // Model identity is fine to surface; raw model traces and
            // prompt content are explicitly NOT in the health payload.
            primaryDetail.textContent = (primary && (primary.model || primary.url)) || 'Not configured';
        }
        if (fallbackDetail) {
            fallbackDetail.textContent = fallback
                ? (fallback.model || fallback.url || '')
                : 'Fallback uses the primary route.';
        }
        if (primaryDot) primaryDot.className = 'int-dot' + (primary && primary.reachable && primary.model_loaded ? ' int-dot-on' : '');
        if (fallbackDot) fallbackDot.className = 'int-dot' + (fallback && fallback.reachable && fallback.model_loaded ? ' int-dot-on' : '');

        const headlineParts = _aiCoachHeadlineFromHealth(health);
        _renderAiCoachHeadline(headlineParts);
        _announceAiCoachHeadline(headlineParts);
    }

    function _renderAiMetricsFields(metrics) {
        const metricsPct = $('ai-metrics-fallback-pct');
        const metricsDetail = $('ai-metrics-detail');
        const warnRow = $('ai-coach-warning-row');
        const warnPct = $('ai-coach-warning-pct');

        const total = (metrics && metrics.adjust_requests) || 0;
        const fallbackPct = metrics && typeof metrics.fallback_pct === 'number' ? metrics.fallback_pct : null;
        const cachePct = metrics && typeof metrics.cache_hit_pct === 'number' ? metrics.cache_hit_pct : null;
        const avgLatencyMs = metrics && typeof metrics.avg_latency_ms === 'number' ? metrics.avg_latency_ms : null;

        if (metricsPct) {
            if (total === 0 || fallbackPct === null) {
                metricsPct.textContent = 'No data';
                metricsPct.className = 'state-chip unknown';
            } else {
                metricsPct.textContent = `${fallbackPct.toFixed(1)}% fallback`;
                metricsPct.className = 'state-chip ' + (fallbackPct >= AI_COACH_FALLBACK_PCT_WARNING ? 'stale' : 'ok');
            }
        }
        if (metricsDetail) {
            if (total === 0) {
                metricsDetail.textContent = 'No AI requests in the last 24h';
            } else {
                const parts = [`${total} request${total === 1 ? '' : 's'}`];
                if (cachePct !== null) parts.push(`${cachePct.toFixed(1)}% cache hit`);
                if (avgLatencyMs !== null) parts.push(`${avgLatencyMs}ms avg`);
                metricsDetail.textContent = parts.join(' · ');
            }
        }
        if (warnRow && warnPct) {
            const shouldWarn = fallbackPct !== null && fallbackPct >= AI_COACH_FALLBACK_PCT_WARNING && total > 0;
            warnRow.hidden = !shouldWarn;
            if (shouldWarn) warnPct.textContent = `${fallbackPct.toFixed(1)}%`;
        }
    }

    async function renderAiCoachHealth() {
        // Both calls in parallel; either failing means the UI degrades
        // gracefully without breaking the rest of Settings.
        const healthPromise = api('/api/ai/health').catch(() => null);
        const metricsPromise = api('/api/ai/metrics').catch(() => null);
        const [health, metrics] = await Promise.all([healthPromise, metricsPromise]);
        if (!health && !metrics) {
            _setAiCoachUnavailable('Health & metrics endpoints unreachable');
            return;
        }
        if (health) {
            // Degraded payload when the adapter failed to import:
            // {reachable: false, error: "adapter not loaded"} with no
            // primary/fallback. Treat that the same as a 5xx so the UI
            // doesn't render "Primary: Not configured / Fallback: Same
            // as primary" instead of showing the real error.
            if (health.reachable === false && !health.primary) {
                _setAiCoachUnavailable(health.error || 'AI adapter unavailable');
            } else {
                _renderAiHealthFields(health);
            }
        } else {
            const primaryState = $('ai-primary-state');
            const fallbackState = $('ai-fallback-state');
            if (primaryState) { primaryState.textContent = 'Unavailable'; primaryState.className = 'state-chip unknown'; }
            if (fallbackState) { fallbackState.textContent = 'Unavailable'; fallbackState.className = 'state-chip unknown'; }
            // FIT-166: keep the headline coherent when /api/ai/health
            // is reachable enough to give us a 0-byte / non-JSON
            // response but /api/ai/metrics still succeeded.
            const offlineHeadline = {
                stateText: 'AI offline',
                hostText: 'health endpoint unreachable',
                chipText: 'Offline',
                chipCls: 'state-chip stale',
                dotCls: 'int-dot',
                detail: 'Health endpoint unreachable',
                showSep: true,
            };
            _renderAiCoachHeadline(offlineHeadline);
            _announceAiCoachHeadline(offlineHeadline);
        }
        if (metrics) {
            _renderAiMetricsFields(metrics);
        } else {
            const metricsPct = $('ai-metrics-fallback-pct');
            const metricsDetail = $('ai-metrics-detail');
            const warnRow = $('ai-coach-warning-row');
            if (metricsPct) { metricsPct.textContent = '—'; metricsPct.className = 'state-chip unknown'; }
            if (metricsDetail) metricsDetail.textContent = 'Metrics unavailable';
            // Don't leave a stale "flaky" warning on screen if /api/ai/metrics
            // fails after a previous refresh exposed a high fallback rate.
            if (warnRow) warnRow.hidden = true;
        }
        // FIT-111 (Codex audit): re-derive the settings group chips
        // after the inner AI chips finish updating asynchronously.
        renderSettingsGroupSummaries();
    }

    // ── FIT-40/FIT-92: Web Push permission flow + test delivery ───

    const PUSH_STATE_CHIP = {
        unsupported:      { text: 'Unsupported',      cls: 'unknown' },
        needs_install:    { text: 'Install required', cls: 'warn'    },
        prompt:           { text: 'Off',              cls: ''        },
        granted_active:   { text: 'Subscribed',       cls: 'ok'      },
        granted_inactive: { text: 'Needs setup',      cls: 'warn'    },
        revoked:          { text: 'Revoked',          cls: 'warn'    },
        denied:           { text: 'Blocked',          cls: 'stale'   },
    };

    const PUSH_STATE_DETAIL = {
        unsupported: 'This browser does not support web push notifications.',
        needs_install: 'Install the app to the Home Screen before enabling push on iOS.',
        prompt: 'Low-stakes nudges only: stale wearable data and pending food review.',
        granted_active: 'Subscribed, no scheduled reminders yet. Send a test notification to verify delivery.',
        granted_inactive: 'Permission is granted, but no browser push subscription is active yet.',
        revoked: 'Browser permission was reset. Re-enable to create a fresh subscription, or disable to clean up.',
        denied: 'Notifications are blocked in browser or OS settings.',
    };

    let pushSetupDetailOverride = '';

    function _pushSupported() {
        return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
    }

    function _pushIsStandalone() {
        // Either the PWA display-mode or iOS Safari's legacy `standalone`.
        if (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) return true;
        return Boolean(navigator.standalone);
    }

    function _pushIsIOS() {
        return /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
    }

    async function _pushSubscriptionsFromServer() {
        try {
            const res = await api('/api/push/subscriptions');
            return (res && Array.isArray(res.subscriptions)) ? res.subscriptions : [];
        } catch {
            return [];
        }
    }

    async function _pushDetectState() {
        if (!_pushSupported()) return { name: 'unsupported', subs: [] };
        // iOS only delivers push when installed to Home Screen.
        if (_pushIsIOS() && !_pushIsStandalone()) return { name: 'needs_install', subs: [] };
        const perm = Notification.permission;
        const subs = await _pushSubscriptionsFromServer();
        if (perm === 'denied') return { name: 'denied', subs };
        if (perm === 'default') {
            // If the server still has a subscription record, the user
            // previously enabled and has since revoked permission at the
            // browser/OS level. Distinct from the first-time `prompt` state.
            if (subs.length > 0) return { name: 'revoked', subs };
            return { name: 'prompt', subs: [] };
        }
        // perm === 'granted'
        const endpointHash = await _pushCurrentEndpointHash();
        if (endpointHash && subs.some((sub) => sub && sub.endpoint_hash === endpointHash)) {
            return { name: 'granted_active', subs };
        }
        return { name: 'granted_inactive', subs: [] };
    }

    function _pushApplyChip(stateName) {
        const chip = $('push-state-chip');
        if (!chip) return;
        const info = PUSH_STATE_CHIP[stateName] || PUSH_STATE_CHIP.unsupported;
        chip.classList.remove('ok', 'warn', 'stale', 'unknown');
        chip.textContent = info.text;
        if (info.cls) chip.classList.add(info.cls);
    }

    function _pushApplyButtons(state) {
        const enableBtn = $('btn-push-enable');
        const testBtn = $('btn-push-test');
        const disableBtn = $('btn-push-disable');
        if (!enableBtn || !disableBtn || !testBtn) return;
        const stateName = state.name;
        const subCount = (state.subs && state.subs.length) || 0;
        // Enable is offered when the user can grant permission or re-subscribe.
        // Hidden in `denied` because requestPermission() is silently rejected
        // when the browser already remembers a deny.
        const showEnable = stateName === 'prompt' || stateName === 'granted_inactive' || stateName === 'revoked';
        // Disable is offered when there's something to clean up — an active
        // subscription, a stale server record from a revoked permission, or
        // an orphan server record from a previous Enable that the user later
        // denied at the OS level.
        const showDisable = (
            stateName === 'granted_active'
            || stateName === 'revoked'
            || (stateName === 'denied' && subCount > 0)
        );
        enableBtn.hidden = !showEnable;
        testBtn.hidden = stateName !== 'granted_active' || subCount === 0;
        disableBtn.hidden = !showDisable;
    }

    function _pushApplyDetail(stateName) {
        const detail = $('push-state-detail');
        if (!detail) return;
        detail.textContent = PUSH_STATE_DETAIL[stateName] || PUSH_STATE_DETAIL.unsupported;
    }

    function _pushApplyHintRows(stateName) {
        // Each hint row corresponds to a specific failure mode. Show
        // exactly the ones relevant to the current state.
        const installRow = $('push-install-row');
        const blockedRow = $('push-blocked-row');
        const revokedRow = $('push-revoked-row');
        const vapidRow = $('push-vapid-row');
        if (installRow) installRow.hidden = stateName !== 'needs_install';
        if (blockedRow) blockedRow.hidden = stateName !== 'denied';
        if (revokedRow) revokedRow.hidden = stateName !== 'revoked';
        if (vapidRow) vapidRow.hidden = stateName !== 'granted_inactive';
    }

    function _pushApplyDot(stateName) {
        const dot = $('push-dot');
        if (!dot) return;
        dot.className = stateName === 'granted_active' ? 'int-dot int-dot-on' : 'int-dot';
    }

    async function _pushRenderAlerts() {
        // The alert preview uses /api/push/reminders/preview. It reflects
        // FIT-39's deterministic stale-wearable + pending-food rules and
        // is safe to surface in-app even when push delivery is off.
        const row = $('push-alerts-row');
        const list = $('push-alerts-list');
        if (!row || !list) return;
        let preview;
        try {
            preview = await api('/api/push/reminders/preview');
        } catch {
            row.hidden = true;
            return;
        }
        const alerts = (preview && Array.isArray(preview.alerts)) ? preview.alerts : [];
        if (!alerts.length) {
            row.hidden = true;
            list.textContent = '';
            return;
        }
        row.hidden = false;
        list.textContent = alerts
            .map((a) => `${a.title || a.type || 'Alert'} — ${a.body || ''}`.trim())
            .join(' · ');
    }

    async function renderPushSection() {
        const card = $('push-notifications-card');
        if (!card) return;
        const state = await _pushDetectState();
        _pushApplyChip(state.name);
        _pushApplyButtons(state);
        _pushApplyDetail(state.name);
        if (pushSetupDetailOverride && state.name !== 'granted_active') {
            const detail = $('push-state-detail');
            if (detail) detail.textContent = pushSetupDetailOverride;
        }
        _pushApplyHintRows(state.name);
        _pushApplyDot(state.name);
        _wirePushButtons();
        _pushRenderAlerts();
        // FIT-111 (Codex audit): re-derive the settings group chips
        // after the push chip finishes updating asynchronously. Also
        // catches enablePush / disablePush / sendPushTest which all
        // await renderPushSection on their state changes.
        renderSettingsGroupSummaries();
    }

    function _pushSetTestResult(message, toastVariant = null) {
        const row = $('push-test-row');
        const result = $('push-test-result');
        if (row) row.hidden = false;
        if (result) result.textContent = message;
        if (toastVariant) toast(message, toastVariant);
    }

    function _pushSetSetupResult(message, toastVariant = null) {
        pushSetupDetailOverride = message;
        _pushSetTestResult(message, toastVariant);
    }

    function _pushResponseMessage(body, fallback) {
        if (body && body.error && body.error.message) return body.error.message;
        if (body && typeof body.error === 'string') return body.error;
        if (body && typeof body.message === 'string') return body.message;
        return fallback;
    }

    function _wirePushButtons() {
        const enableBtn = $('btn-push-enable');
        if (enableBtn && !enableBtn.dataset.wired) {
            enableBtn.dataset.wired = '1';
            enableBtn.addEventListener('click', () => { enablePush(); });
        }
        const disableBtn = $('btn-push-disable');
        if (disableBtn && !disableBtn.dataset.wired) {
            disableBtn.dataset.wired = '1';
            disableBtn.addEventListener('click', () => { disablePush(); });
        }
        const testBtn = $('btn-push-test');
        if (testBtn && !testBtn.dataset.wired) {
            testBtn.dataset.wired = '1';
            testBtn.addEventListener('click', () => { sendPushTest(); });
        }
    }

    async function _pushGetVapidKey() {
        try {
            const res = await fetch('/api/push/vapid-public-key', {
                credentials: 'same-origin',
                headers: { 'Accept': 'application/json', [CSRF_HEADER_NAME]: CSRF_HEADER_VALUE },
            });
            const body = await res.json().catch(() => ({}));
            if (res.status === 401 || res.status === 403) {
                return {
                    ok: false,
                    message: 'Sign in to this installed app, then enable notifications again.',
                };
            }
            if (!res.ok) {
                return {
                    ok: false,
                    message: _pushResponseMessage(body, 'Push delivery is not configured: VAPID public key is missing. Configure VAPID keys, then enable notifications again.'),
                };
            }
            const publicKey = body && body.public_key;
            if (!publicKey) {
                return {
                    ok: false,
                    message: 'Push delivery is not configured: VAPID public key is missing. Configure VAPID keys, then enable notifications again.',
                };
            }
            return { ok: true, publicKey };
        } catch (err) {
            console.warn('GET /api/push/vapid-public-key failed:', err);
            return {
                ok: false,
                message: 'Could not check push setup: network or server error.',
            };
        }
    }

    function _pushTimeout(ms, message) {
        return new Promise((_, reject) => {
            window.setTimeout(() => reject(new Error(message)), ms);
        });
    }

    function _pushUrlBase64ToUint8(base64) {
        // Per Web Push spec: VAPID public keys are URL-safe base64.
        const padding = '='.repeat((4 - base64.length % 4) % 4);
        const cleaned = (base64 + padding).replace(/-/g, '+').replace(/_/g, '/');
        const raw = atob(cleaned);
        const out = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
        return out;
    }

    async function _pushEndpointHash(endpoint) {
        if (!endpoint || !window.crypto || !window.crypto.subtle) return null;
        const bytes = new TextEncoder().encode(endpoint);
        const digest = await window.crypto.subtle.digest('SHA-256', bytes);
        return Array.from(new Uint8Array(digest))
            .map((byte) => byte.toString(16).padStart(2, '0'))
            .join('');
    }

    async function _pushCurrentEndpointHash() {
        if (!('serviceWorker' in navigator)) return null;
        const reg = await navigator.serviceWorker.getRegistration();
        const sub = reg && (await reg.pushManager.getSubscription());
        return sub ? _pushEndpointHash(sub.endpoint) : null;
    }

    async function enablePush() {
        // Must run from a user gesture (button click). Notification.requestPermission
        // requires that on most browsers.
        const enableBtn = $('btn-push-enable');
        if (enableBtn) enableBtn.disabled = true;
        pushSetupDetailOverride = '';
        _pushSetTestResult('Enabling notifications...');
        try {
            try {
                if (!_pushSupported()) {
                    _pushSetTestResult('This browser does not support web push notifications.', 'err');
                    return;
                }
                if (_pushIsIOS() && !_pushIsStandalone()) {
                    _pushSetTestResult('Install the app to the Home Screen, reopen it, then enable notifications.', 'err');
                    return;
                }
                const perm = await Notification.requestPermission();
                if (perm !== 'granted') {
                    _pushSetTestResult('Notifications permission was not granted. Re-enable it in browser or OS settings, then try again.', 'err');
                    return;
                }
                const reg = await Promise.race([
                    navigator.serviceWorker.ready,
                    _pushTimeout(10000, 'service worker did not become ready'),
                ]);
                const vapid = await _pushGetVapidKey();
                if (!vapid.ok) {
                    _pushSetSetupResult(vapid.message, 'err');
                    return;
                }
                const opts = { userVisibleOnly: true };
                opts.applicationServerKey = _pushUrlBase64ToUint8(vapid.publicKey);
                let subscription = null;
                try {
                    subscription = await Promise.race([
                        reg.pushManager.subscribe(opts),
                        _pushTimeout(10000, 'push subscription did not complete'),
                    ]);
                } catch (err) {
                    console.warn('pushManager.subscribe failed:', err);
                    _pushSetSetupResult(`Push subscription failed: ${String((err && err.message) || err || 'service worker or push manager rejected the request')}.`, 'err');
                }
                if (subscription) {
                    let serverSaved = false;
                    try {
                        await api('/api/push/subscriptions', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                subscription: subscription.toJSON ? subscription.toJSON() : subscription,
                                permission_state: 'granted',
                                pwa_installed: _pushIsStandalone(),
                            }),
                        });
                        serverSaved = true;
                    } catch (err) {
                        console.warn('POST /api/push/subscriptions failed:', err);
                        _pushSetSetupResult(`Subscription was created locally, but the server could not save it: ${apiErrorMessage(err, 'server save failed')}.`, 'err');
                    }
                    // If the server save failed, tear down the browser-side
                    // subscription so we don't leave a dangling push channel
                    // that the user can't see or disable in this UI.
                    if (!serverSaved) {
                        try { await subscription.unsubscribe(); }
                        catch (err) { console.warn('rollback unsubscribe failed:', err); }
                    } else {
                        pushSetupDetailOverride = '';
                        _pushSetTestResult('Notifications enabled. Send a test notification to verify delivery.', 'ok');
                    }
                }
            } catch (err) {
                // Outer catch covers requestPermission rejection, the
                // serviceWorker.ready promise, VAPID-key decoding, and any
                // other unexpected throw before the inner try blocks.
                console.warn('enablePush failed:', err);
                _pushSetSetupResult(`Notifications could not be enabled: ${String((err && err.message) || err || 'unknown error')}.`, 'err');
            }
        } finally {
            try {
                await renderPushSection();
            } finally {
                if (enableBtn) enableBtn.disabled = false;
            }
        }
    }

    async function disablePush() {
        const disableBtn = $('btn-push-disable');
        if (disableBtn) disableBtn.disabled = true;
        try {
            // Unsubscribe locally first so the browser stops accepting pushes
            // even if the server DELETE fails.
            try {
                if ('serviceWorker' in navigator) {
                    const reg = await navigator.serviceWorker.getRegistration();
                    const sub = reg && (await reg.pushManager.getSubscription());
                    if (sub) await sub.unsubscribe();
                }
            } catch (err) {
                console.warn('pushManager.unsubscribe failed:', err);
            }
            const subs = await _pushSubscriptionsFromServer();
            await Promise.all(subs.map((s) => {
                if (!s || !s.endpoint_hash) return Promise.resolve();
                return api('/api/push/subscriptions/' + encodeURIComponent(s.endpoint_hash), {
                    method: 'DELETE',
                }).catch((err) => { console.warn('DELETE subscription failed:', err); });
            }));
            await renderPushSection();
        } finally {
            if (disableBtn) disableBtn.disabled = false;
        }
    }

    async function sendPushTest() {
        const testBtn = $('btn-push-test');
        if (testBtn) testBtn.disabled = true;
        _pushSetTestResult('Sending test notification...');
        try {
            const state = await _pushDetectState();
            if (state.name !== 'granted_active' || !state.subs || state.subs.length === 0) {
                _pushSetTestResult('Enable notifications first, then send a test notification.', 'err');
                await renderPushSection();
                return;
            }
            const endpointHash = await _pushCurrentEndpointHash();
            if (!endpointHash) {
                _pushSetTestResult('No active subscription on this device. Re-enable notifications here, then send a test.', 'err');
                await renderPushSection();
                return;
            }
            const res = await fetch('/api/push/test', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Accept': 'application/json', 'Content-Type': 'application/json', [CSRF_HEADER_NAME]: CSRF_HEADER_VALUE },
                body: JSON.stringify({ endpoint_hash: endpointHash }),
            });
            const body = await res.json().catch(() => ({}));
            if (res.ok && body.status === 'delivered' && body.delivered !== false) {
                _pushSetTestResult('Delivered. This device should show the test notification now.');
                toast('Test notification sent');
            } else if (res.status === 410 || body.status === 'gone') {
                _pushSetTestResult('Subscription expired. Re-enable notifications to create a fresh one.', 'err');
                await renderPushSection();
            } else {
                const msg = _pushResponseMessage(body, 'Server could not send the test notification.');
                _pushSetTestResult(`Not delivered: ${msg}`, 'err');
            }
        } catch (err) {
            _pushSetTestResult('Not delivered: network or server error.', 'err');
            console.warn('push test failed:', err);
        } finally {
            if (testBtn) testBtn.disabled = false;
        }
    }

    // ── FIT-16: Wearable freshness evidence panels ────────────────
    // Both renderers tolerate null / partial payloads — the Settings
    // page must keep rendering even when an upstream endpoint is down.

    function _fmtAgo(date) {
        if (!date) return null;
        const ageMs = Date.now() - date.getTime();
        if (ageMs < 0) return 'just now';
        const ageHours = Math.floor(ageMs / 3600000);
        if (ageHours < 1) {
            const m = Math.max(1, Math.floor(ageMs / 60000));
            return `${m}m ago`;
        }
        if (ageHours < 48) return `${ageHours}h ago`;
        const days = Math.floor(ageHours / 24);
        return `${days}d ago`;
    }

    // FIT-113: wrap _fmtAgo for ISO-string inputs and expose via the
    // __dashHelpers.ago contract that renderFreshnessChips has always
    // expected but never had wired. Date-only strings (YYYY-MM-DD) get
    // a local-midnight parse so "today" doesn't mislabel as "1d ago"
    // in negative-offset timezones (matches the FIT-115 renderHistory
    // pattern).
    function _agoFromIso(isoStr) {
        if (!isoStr) return null;
        const s = String(isoStr);
        const d = /^\d{4}-\d{2}-\d{2}$/.test(s) ? new Date(s + 'T00:00:00') : new Date(s);
        if (isNaN(d.getTime())) return null;
        return _fmtAgo(d);
    }
    window.__dashHelpers = window.__dashHelpers || {};
    window.__dashHelpers.ago = _agoFromIso;

    function _setDetail(id, text) {
        const el = $(id);
        if (el) el.textContent = text;
    }

    function renderWhoopFreshnessDetail(whoop, freshness, conflicts) {
        const uiState = resolveWhoopUiState(freshness || whoop, conflicts);
        const dot = $('whoop-int-dot');
        if (dot) dot.className = uiState === WHOOP_UI_STATES.disconnected ? 'int-dot' : 'int-dot int-dot-on';

        const lastSyncRaw = whoop && (whoop.last_successful_sync_at || whoop.last_sync_at || whoop.last_sync);
        if ($('whoop-last-sync')) {
            $('whoop-last-sync').textContent = lastSyncRaw
                ? `Last sync ${fmtDateTime(lastSyncRaw)}`
                : uiState === WHOOP_UI_STATES.disconnected
                    ? 'WHOOP is disconnected'
                    : 'No WHOOP sync recorded yet';
        }

        const connectionText = {
            connected: 'Connected',
            disconnected: 'Disconnected',
            syncing: 'Syncing now',
            fresh: 'Connected · fresh',
            aging: 'Connected · aging',
            stale: 'Connected · stale',
            missing: 'Connected · no data',
            pending_score: 'Connected · pending score',
            unscorable: 'Connected · unscorable',
            calibrating: 'Connected · calibrating',
            reauth_required: 'Reconnect required',
            csv_only: 'CSV import only',
            source_conflict: 'Connected · source conflict',
            error: 'Connection error',
        }[uiState] || 'Disconnected';
        _setDetail('whoop-detail-connection', connectionText);

        const dataThrough = (freshness && (freshness.last_data_point || freshness.local_date))
            || (whoop && (whoop.last_data_point || whoop.local_date));
        _setDetail('whoop-detail-data-through', dataThrough ? `${dataThrough} (${uiState.replace(/_/g, ' ')})` : 'No WHOOP data yet');

        const recoveryParts = [];
        const recoveryScore = whoop && (whoop.recovery_score != null ? whoop.recovery_score : whoop.score);
        if (recoveryScore != null) recoveryParts.push(`Recovery ${recoveryScore}`);
        if (whoop && whoop.recovery_band) recoveryParts.push(String(whoop.recovery_band));
        if (whoop && whoop.strain != null) recoveryParts.push(`strain ${whoop.strain}`);
        if (whoop && whoop.sleep_performance_pct != null) recoveryParts.push(`sleep ${whoop.sleep_performance_pct}%`);
        _setDetail('whoop-detail-recovery', recoveryParts.length ? recoveryParts.join(' · ') : 'No scored WHOOP recovery yet');

        let sourceText = 'Official WHOOP API';
        if (uiState === WHOOP_UI_STATES.csv_only) sourceText = 'CSV import only';
        else if (whoop && whoop.source_kind) sourceText = String(whoop.source_kind).replace(/_/g, ' ');
        _setDetail('whoop-detail-source', sourceText);

        let attentionText = 'Ready for recovery guidance.';
        if (uiState === WHOOP_UI_STATES.syncing) attentionText = 'Manual sync is in progress.';
        else if (uiState === WHOOP_UI_STATES.aging) attentionText = 'Data is aging; manual sync may sharpen the recommendation.';
        else if (uiState === WHOOP_UI_STATES.stale) attentionText = 'Data is stale; recommendation confidence is lowered.';
        else if (uiState === WHOOP_UI_STATES.missing) attentionText = 'Connected, but no scored WHOOP data is available yet.';
        else if (uiState === WHOOP_UI_STATES.pending_score) attentionText = 'Latest WHOOP day is still pending score.';
        else if (uiState === WHOOP_UI_STATES.unscorable) attentionText = 'Latest WHOOP day is unscorable, so WHOOP stays display-only.';
        else if (uiState === WHOOP_UI_STATES.calibrating) attentionText = 'WHOOP is calibrating; the recommendation stays conservative.';
        else if (uiState === WHOOP_UI_STATES.reauth_required) attentionText = 'WHOOP needs to be reconnected before the next sync.';
        else if (uiState === WHOOP_UI_STATES.csv_only) attentionText = 'CSV backfill is present, but live WHOOP sync is not active.';
        else if (uiState === WHOOP_UI_STATES.source_conflict) attentionText = (freshness && freshness.conflict_message) || 'WHOOP and another wearable disagree, so the conservative plan wins.';
        else if (uiState === WHOOP_UI_STATES.error) attentionText = String((whoop && whoop.error) || state.whoopUi.lastError || 'WHOOP status is unavailable right now.');
        else if (uiState === WHOOP_UI_STATES.disconnected) attentionText = 'No WHOOP connection detected.';
        _setDetail('whoop-detail-attention', attentionText);

        const conflictRow = $('whoop-conflict-row');
        const conflict = firstWhoopConflict(conflicts);
        if (conflictRow) {
            conflictRow.hidden = !conflict;
            if (conflict) _setDetail('whoop-conflict-text', conflict.message || 'WHOOP and another wearable disagree.');
        }

        setWhoopActionButtons(whoop, uiState);
    }

    function renderOuraFreshnessDetail(oura, freshness) {
        const detail = $('oura-detail');
        if (!detail) return;
        const hasFreshness = freshness && freshness.last_data_point;

        if ((!oura || !oura.source) && !hasFreshness) {
            // Genuinely no data — no today-row AND no historical
            // record_date the server knows about.
            _setDetail('oura-detail-daily', 'Not connected');
            _setDetail('oura-detail-sleep', '—');
            _setDetail('oura-detail-source', '—');
            return;
        }

        // Latest daily: combine date + the headline readiness/HRV pair.
        // Fall back to the freshness block's last_data_point when the
        // today-row is missing — that surfaces honest stale-cache state
        // ("data through 2026-05-15") instead of misreporting as
        // disconnected.
        const dailyParts = [];
        if (oura && oura.date) dailyParts.push(oura.date);
        else if (hasFreshness) dailyParts.push(`through ${freshness.last_data_point}`);
        const dailyStats = [];
        if (oura && oura.readiness != null) dailyStats.push(`readiness ${oura.readiness}`);
        if (oura && oura.hrv != null) dailyStats.push(`HRV ${oura.hrv}`);
        if (oura && oura.resting_hr != null) dailyStats.push(`RHR ${oura.resting_hr}`);
        if (dailyStats.length) dailyParts.push(dailyStats.join(' · '));
        else if (hasFreshness && freshness.status) dailyParts.push(`(${freshness.status})`);
        _setDetail('oura-detail-daily', dailyParts.length ? dailyParts.join(' — ') : 'No daily row');

        // Latest sleep: duration formatted as Xh Ym + score if present.
        // When today's row is missing but freshness reports a historical
        // record_date, surface that fact (with a pointer to Vitals which
        // owns the historical detail view) instead of misleading
        // "No sleep row" copy that ignores the cached evidence.
        const sleepParts = [];
        if (oura && oura.sleep_duration_min != null) {
            const h = Math.floor(oura.sleep_duration_min / 60);
            const m = oura.sleep_duration_min % 60;
            sleepParts.push(`${h}h ${m}m`);
        }
        if (oura && oura.sleep_score != null) sleepParts.push(`score ${oura.sleep_score}`);
        let sleepText;
        if (sleepParts.length) sleepText = sleepParts.join(' · ');
        else if (hasFreshness) sleepText = `Cached through ${freshness.last_data_point} — see Vitals for detail`;
        else sleepText = 'No sleep row';
        _setDetail('oura-detail-sleep', sleepText);

        // Source: distinguish a real API pull from a cached DB read.
        // ``api`` == fresh live fetch; ``db`` == served from local cache.
        // When only the freshness block is available, label as cached
        // and surface the freshness status so the user can tell why
        // today's row is missing.
        let sourceText;
        if (oura && oura.source === 'api') sourceText = 'Live (fresh API pull)';
        else if (oura && oura.source === 'db') sourceText = 'Cached (local SQLite)';
        else if (oura && oura.source) sourceText = oura.source;
        else if (hasFreshness) sourceText = `Cached (${freshness.status})`;
        else sourceText = '—';
        const warnings = [];
        if (oura && oura.warning) warnings.push(oura.warning);
        if (hasFreshness && freshness.status === 'stale') warnings.push('Data is stale (≥48h)');
        if (warnings.length) sourceText += ` · ${warnings.join(' · ')}`;
        _setDetail('oura-detail-source', sourceText);
    }

    function renderAppleHealthFreshnessDetail(ah, freshness) {
        const detail = $('apple-detail');
        if (!detail) return;
        const staleRow = $('apple-detail-stale-row');
        const attemptRow = $('apple-detail-attempt-row');

        if (!ah) {
            _setDetail('apple-detail-last-sync', 'Status endpoint unavailable');
            _setDetail('apple-detail-records', '—');
            _setDetail('apple-detail-data-through', '—');
            if (attemptRow) attemptRow.hidden = true;
            if (staleRow) staleRow.hidden = true;
            return;
        }

        const lastSyncDt = parseServerDateTime(ah.last_sync);
        const lastAttemptDt = parseServerDateTime(ah.last_attempt);

        // Last accepted: the timestamp of the most recent inserted row
        // (insertion event, not the data date). This is when the
        // import landed. The data covered by that import is in
        // ``freshness.apple_health.last_data_point``.
        if (lastSyncDt) {
            const ago = _fmtAgo(lastSyncDt);
            _setDetail('apple-detail-last-sync',
                `${fmtDateTime(ah.last_sync)} · ${ago}`);
        } else {
            _setDetail('apple-detail-last-sync',
                ah.setup_configured ? 'Configured, no accepted export yet' : 'Never');
        }

        // Last attempt: only surface separately when it diverges from
        // last_sync — that's the signal that HAE *tried* but nothing
        // got inserted (auth issue, schema drift, etc.). When they
        // match, the row is redundant.
        if (attemptRow) {
            const sameAsSync = lastAttemptDt && lastSyncDt
                && Math.abs(lastAttemptDt.getTime() - lastSyncDt.getTime()) < 5000;
            if (!lastAttemptDt || sameAsSync) {
                attemptRow.hidden = true;
            } else {
                attemptRow.hidden = false;
                const lastEvent = ah.last_event || {};
                const ago = _fmtAgo(lastAttemptDt);
                const parts = [`${fmtDateTime(ah.last_attempt)} · ${ago}`];
                if (typeof lastEvent.inserted === 'number'
                    || typeof lastEvent.skipped === 'number') {
                    parts.push(`${lastEvent.inserted || 0} inserted, ${lastEvent.skipped || 0} skipped`);
                }
                _setDetail('apple-detail-last-attempt', parts.join(' · '));
            }
        }

        // Data through: the date the most recent record_date covers.
        // Honest signal of how current the data actually is — survives
        // HAE replays of old exports.
        const dataThrough = freshness && freshness.last_data_point;
        _setDetail('apple-detail-data-through',
            dataThrough ? `${dataThrough} (${freshness.status || 'unknown'})` : 'No data points');

        // Records: total + small per-type breakdown so the owner can
        // see whether the categories they care about (workouts, HRV,
        // sleep) are actually landing.
        const total = Number(ah.total_records || 0);
        const byType = ah.by_type || {};
        const recordParts = [`${total.toLocaleString()} total`];
        const interesting = ['workouts', 'steps', 'heart_rate', 'hrv', 'sleep', 'active_energy'];
        const typeSummary = interesting
            .filter((k) => byType[k])
            .map((k) => `${k.replace('_', ' ')} ${Number(byType[k]).toLocaleString()}`)
            .slice(0, 3);
        if (typeSummary.length) recordParts.push(typeSummary.join(' · '));
        _setDetail('apple-detail-records', recordParts.join(' — '));

        // Stale warning: drive off the server's freshness status, NOT
        // ah.last_sync. The server computes stale from MAX(record_date)
        // and the documented _FRESHNESS_STALE_HOURS=48 threshold, so a
        // recent HAE replay of an old export still triggers the alert.
        if (staleRow) {
            const freshnessStatus = freshness && freshness.status;
            if (freshnessStatus === 'stale') {
                staleRow.hidden = false;
                _setDetail('apple-detail-stale-text',
                    dataThrough
                        ? `Data through ${dataThrough} — more than 48h old. Expected daily.`
                        : 'No accepted export in the last 48 hours. Expected daily.');
            } else if (freshnessStatus === 'missing' && ah.setup_configured) {
                staleRow.hidden = false;
                _setDetail('apple-detail-stale-text',
                    'Webhook configured but no Apple Health records have landed yet.');
            } else {
                staleRow.hidden = true;
            }
        }
    }

    async function updateSetting(patch) {
        try {
            await api('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(patch),
            });
            toast('Setting saved');
            state.settings = null; state.dashboard = null; state.nextWorkout = null;
            renderSettings();
        } catch (e) {
            console.error(e); toast('Save failed', 'err');
        }
    }
    async function updateEquipment(value) {
        try {
            await api('/api/settings/equipment', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ equipment_preference: value }),
            });
            toast('Equipment updated');
            state.settings = null;
            state.dashboard = null;
            state.nextWorkout = null;
            state.reco = null;
            state.activeWorkout = null;
            clearActiveWorkoutDraft();
            await Promise.allSettled([renderSettings(), renderDashboard()]);
        } catch (e) { console.error(e); toast('Update failed', 'err'); }
    }

    // --- Logging actions -----------------------------------------
    async function logStrength() {
        const payload = {
            date: $('log-date').value || today(),
            exercise: $('log-exercise').value,
            sets: Number($('log-sets').value || 0),
            reps: Number($('log-reps').value || 0),
            weight: Number($('log-weight').value || 0),
            rpe: Number($('log-rpe').value || 0),
            notes: $('log-notes').value || '',
        };
        if (!payload.exercise) return toast('Pick an exercise', 'err');
        try {
            await api('/api/add-workout', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            toast('Set logged');
            invalidateCaches();
            prepareLog();
        } catch (e) { console.error(e); toast('Log failed', 'err'); }
    }

    async function logCardio() {
        const payload = {
            date: $('cardio-date').value || today(),
            activity_type: $('cardio-type').value,
            duration_minutes: Number($('cardio-duration').value || 0),
            avg_heart_rate: Number($('cardio-hr').value) || null,
            intensity: Number($('cardio-intensity').value || 5),
            notes: $('cardio-notes').value || '',
        };
        try {
            await api('/api/add-cardio', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            toast('Cardio logged');
            invalidateCaches();
        } catch (e) { console.error(e); toast('Log failed', 'err'); }
    }

    async function logRecovery() {
        const payload = {
            date: $('recovery-date').value || today(),
            recovery_type: $('recovery-type').value,
            duration_minutes: Number($('recovery-duration').value || 0),
            temperature: Number($('recovery-temp').value) || null,
            notes: $('recovery-notes').value || '',
        };
        try {
            await api('/api/add-recovery', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            toast('Recovery logged');
            invalidateCaches();
        } catch (e) { console.error(e); toast('Log failed', 'err'); }
    }

    async function logBody() {
        const payload = {
            weight_lbs: Number($('body-log-weight').value) || null,
            body_fat_pct: Number($('body-log-bf').value) || null,
        };
        if (!payload.weight_lbs && !payload.body_fat_pct) return toast('Enter a value', 'err');
        try {
            await api('/api/add-body-measurement', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            toast('Measurement saved');
            state.body = null; state.dashboard = null;
            renderBody();
        } catch (e) { console.error(e); toast('Save failed', 'err'); }
    }

    async function syncOura() {
        toast('Syncing Oura…');
        try {
            await api('/api/oura/sync-sleep', { method: 'POST' });
            invalidateCaches();
            toast('Oura synced');
            loadTab(state.currentTab);
        } catch (e) { console.error(e); toast(apiErrorMessage(e, 'Oura sync failed'), 'err'); }
    }

    function downloadExport() {
        const a = document.createElement('a');
        a.href = '/api/export-backup';
        a.download = `fitness-backup-${today()}.json`;
        document.body.appendChild(a); a.click(); a.remove();
    }
    async function importBackupFile(file) {
        if (!file) return;
        const fd = new FormData();
        fd.append('file', file);
        try {
            await api('/api/import-backup', { method: 'POST', body: fd });
            toast('Backup imported');
            invalidateCaches();
            loadTab(state.currentTab);
        } catch (e) { console.error(e); toast('Import failed', 'err'); }
    }

    // --- Active Workout flow -------------------------------------
    function exerciseName(ex) {
        return ex.exercise || ex.name || ex.machine || 'Exercise';
    }

    function exerciseMuscle(ex) {
        return (ex.muscle || ex.muscle_group || '').toString().toLowerCase().trim();
    }

    function numericInputValue(value) {
        if (value == null || value === '') return '';
        const n = Number(value);
        return Number.isFinite(n) ? String(n) : '';
    }

    function plannedTargetNumber(...values) {
        for (const v of values) {
            if (v == null || v === '') continue;
            const n = Number(v);
            if (Number.isFinite(n)) return n;
        }
        return null;
    }

    function recommendedRepsValue(ex) {
        if (ex.target_reps != null) return numericInputValue(ex.target_reps);
        if (ex.reps != null) return numericInputValue(ex.reps);
        if (Array.isArray(ex.rep_range) && ex.rep_range.length) return numericInputValue(ex.rep_range[0]);
        return '';
    }

    function recommendedWeightValue(ex) {
        if (ex.target_weight != null) return numericInputValue(ex.target_weight);
        if (ex.target_weight_lbs != null) return numericInputValue(ex.target_weight_lbs);
        return '';
    }

    function setCountForExercise(ex) {
        const count = Number(ex.target_sets || ex.sets || 3);
        return Number.isFinite(count) && count > 0 ? Math.round(count) : 3;
    }

    const ACTIVE_WORKOUT_DRAFT_KEY = 'fit168:active-workout-draft:v1';
    const ACTIVE_WORKOUT_DRAFT_VERSION = 1;
    let _activeWorkoutDraftSavePending = false;
    let _mealQueueAuthScopeRetryTimer = null;
    let _mealQueueAuthScopeRetryDelayMs = 5_000;

    function currentActiveWorkoutDraftScope() {
        try {
            return String(_mealQueueAuthScope || '').trim();
        } catch (_) {
            return '';
        }
    }

    function activeWorkoutDraftScopeForWorkout(workout) {
        const workoutScope = String(workout && workout.auth_scope || '').trim();
        return workoutScope || currentActiveWorkoutDraftScope();
    }

    function activeWorkoutDraftIsValid(parsed) {
        const workout = parsed && parsed.workout;
        return Boolean(
            parsed
            && parsed.version === ACTIVE_WORKOUT_DRAFT_VERSION
            && workout
            && typeof workout === 'object'
            && Array.isArray(workout.exercises)
        );
    }

    function loadActiveWorkoutDraft() {
        try {
            const raw = localStorage.getItem(ACTIVE_WORKOUT_DRAFT_KEY);
            if (!raw) return null;
            const parsed = JSON.parse(raw);
            if (!activeWorkoutDraftIsValid(parsed)) return null;
            return parsed;
        } catch (_) {
            return null;
        }
    }

    function syncActiveWorkoutInputsFromDom() {
        const aw = state.activeWorkout;
        const body = $('active-workout-body');
        if (!aw || !body) return;
        qsa('.set-row', body).forEach((row) => {
            const exIdx = Number(row.dataset.ex);
            const setIdx = Number(row.dataset.set);
            const ex = aw.exercises && aw.exercises[exIdx];
            const set = ex && ex.logged_sets && ex.logged_sets[setIdx];
            if (!set) return;
            const next = {
                weight: qs('input[data-field="weight"]', row).value,
                reps: qs('input[data-field="reps"]', row).value,
                done: qs('input[data-field="done"]', row).checked,
                notes: qs('input[data-field="notes"]', row).value,
            };
            if (
                String(set.weight ?? '') !== String(next.weight)
                || String(set.reps ?? '') !== String(next.reps)
                || Boolean(set.done) !== Boolean(next.done)
                || String(set.notes ?? '') !== String(next.notes)
            ) {
                ex.logged_sets[setIdx] = next;
                aw.dirty = true;
            }
        });

        const cardio = aw.cardio;
        const cardioCard = qs('.active-cardio', body);
        if (cardio && cardioCard) {
            const next = {
                completed: qs('input[data-cardio-field="completed"]', cardioCard).checked,
                activity_type: qs('input[data-cardio-field="activity_type"]', cardioCard).value,
                duration_minutes: qs('input[data-cardio-field="duration_minutes"]', cardioCard).value,
                notes: qs('textarea[data-cardio-field="notes"]', cardioCard).value,
            };
            if (
                Boolean(cardio.completed) !== Boolean(next.completed)
                || String(cardio.activity_type ?? '') !== String(next.activity_type)
                || String(cardio.duration_minutes ?? '') !== String(next.duration_minutes)
                || String(cardio.notes ?? '') !== String(next.notes)
            ) {
                Object.assign(cardio, next);
                aw.dirty = true;
            }
        }
    }

    function saveActiveWorkoutDraft({ syncDom = true } = {}) {
        if (syncDom) syncActiveWorkoutInputsFromDom();
        const workout = state.activeWorkout;
        if (!workout) return;
        if (workout.queuedForSyncReview) return;
        const authScope = activeWorkoutDraftScopeForWorkout(workout);
        if (!authScope) {
            _activeWorkoutDraftSavePending = true;
            return;
        }
        workout.auth_scope = authScope;
        try {
            const draft = {
                version: ACTIVE_WORKOUT_DRAFT_VERSION,
                saved_at: new Date().toISOString(),
                auth_scope: authScope,
                workout,
            };
            localStorage.setItem(ACTIVE_WORKOUT_DRAFT_KEY, JSON.stringify(draft));
            _activeWorkoutDraftSavePending = false;
        } catch (_) { /* localStorage may be unavailable; keep in memory for this page. */ }
    }

    function flushPendingActiveWorkoutDraftSave() {
        if (_activeWorkoutDraftSavePending && state.activeWorkout) {
            saveActiveWorkoutDraft();
        }
    }

    function saveActiveWorkoutDraftBeforePageHidden() {
        if (state.activeWorkout && !state.activeWorkout.queuedForSyncReview) saveActiveWorkoutDraft();
    }

    function clearActiveWorkoutDraft() {
        try { localStorage.removeItem(ACTIVE_WORKOUT_DRAFT_KEY); } catch (_) {}
    }

    function restoreActiveWorkoutDraft() {
        if (state.activeWorkout) return false;
        const draft = loadActiveWorkoutDraft();
        if (!draft) return false;
        const draftScope = String(draft.auth_scope || '').trim();
        const currentScope = currentActiveWorkoutDraftScope();
        // Require a live auth-scope match. A persisted scope can be stale after
        // sign-out or account switching, and this draft contains health data.
        if (!draftScope || !currentScope || draftScope !== currentScope) {
            return false;
        }
        state.activeWorkout = draft.workout;
        state.activeWorkout.saveState = {
            message: 'Recovered unsaved workout details from this device.',
            variant: 'warn',
        };
        renderActiveWorkout();
        toast('Recovered unsaved workout details');
        return true;
    }

    function settleActiveWorkoutDraftAfterAuthScope(scopeResult) {
        if (!(scopeResult && scopeResult.ok)) return;
        clearMealQueueAuthScopeRetry();
        restoreActiveWorkoutDraft();
        flushPendingActiveWorkoutDraftSave();
    }

    function clearMealQueueAuthScopeRetry() {
        if (_mealQueueAuthScopeRetryTimer) clearTimeout(_mealQueueAuthScopeRetryTimer);
        _mealQueueAuthScopeRetryTimer = null;
        _mealQueueAuthScopeRetryDelayMs = 5_000;
    }

    function scheduleMealQueueAuthScopeRetry(status) {
        if (status !== 'pending' || _mealQueueAuthScopeRetryTimer) return;
        const delayMs = _mealQueueAuthScopeRetryDelayMs;
        _mealQueueAuthScopeRetryDelayMs = Math.min(_mealQueueAuthScopeRetryDelayMs * 2, 30_000);
        _mealQueueAuthScopeRetryTimer = setTimeout(() => {
            _mealQueueAuthScopeRetryTimer = null;
            refreshMealQueueAuthScope()
                .then((scopeResult) => {
                    settleActiveWorkoutDraftAfterAuthScope(scopeResult);
                    scheduleMealQueueAuthScopeRetry(scopeResult && scopeResult.status);
                })
                .catch((err) => {
                    console.warn('Meal queue auth scope refresh failed:', err);
                    scheduleMealQueueAuthScopeRetry('pending');
                });
        }, delayMs);
    }

    function buildLoggedSets(ex, previousSets) {
        const reps = recommendedRepsValue(ex);
        const weight = recommendedWeightValue(ex);
        const priorCount = Array.isArray(previousSets) ? previousSets.length : 0;
        const rowCount = Math.max(setCountForExercise(ex), priorCount);
        return Array.from({ length: rowCount }, (_, idx) => {
            const prev = previousSets && previousSets[idx];
            return {
                reps: prev && prev.reps !== '' && prev.reps != null ? prev.reps : reps,
                weight: prev && prev.weight !== '' && prev.weight != null ? prev.weight : weight,
                done: prev ? Boolean(prev.done) : false,
                notes: prev && prev.notes != null ? prev.notes : '',
            };
        });
    }

    function buildActiveExercise(ex, previous) {
        const previousName = previous ? exerciseName(previous) : '';
        const nextName = exerciseName(ex);
        const carrySets = previousName && nextName && previousName === nextName;
        return {
            ...ex,
            logged_sets: buildLoggedSets(ex, carrySets ? previous.logged_sets : null),
        };
    }

    function setActiveWorkoutFromRecommendation(nw, previousExercises = []) {
        const existing = state.activeWorkout;
        state.activeWorkout = {
            id: (existing && existing.id) || nw.workout_id || newWorkoutId(nw.id),
            recommendation_id: nw.id || (existing && existing.recommendation_id) || null,
            focus: nw.focus || nw.goal_name || (existing && existing.focus) || 'Workout',
            auth_scope: String(nw.auth_scope || (existing && existing.auth_scope) || currentActiveWorkoutDraftScope() || '').trim(),
            exercises: (nw.exercises || []).map((ex, i) => buildActiveExercise(ex, previousExercises[i])),
            cardio: buildActiveCardio(nw.cardio, existing && existing.cardio),
            saveState: existing && existing.saveState ? existing.saveState : null,
            dirty: Boolean(existing && existing.dirty),
        };
        saveActiveWorkoutDraft({ syncDom: false });
    }

    // FIT-179: merge an adjusted recommendation into an in-progress active
    // workout without losing logged work for exercises that remain in the plan.
    // Match by exercise identity, not array index, so removals/trims cannot
    // copy completed rows onto a different exercise after slots shift.
    function buildAdjustedLoggedSets(newEx, previousSets, previousEx = null) {
        const targetInputValue = (value) => {
            if (value == null || value === '') return '';
            const n = Number(value);
            return Number.isFinite(n) ? String(n) : '';
        };
        const prevList = Array.isArray(previousSets) ? previousSets : [];
        const count = Number(newEx.target_sets || newEx.sets || 3);
        const targetCount = Number.isFinite(count) && count > 0 ? Math.round(count) : 3;
        const targetReps = newEx.target_reps != null
            ? targetInputValue(newEx.target_reps)
            : (newEx.reps != null ? targetInputValue(newEx.reps) : (
                Array.isArray(newEx.rep_range) && newEx.rep_range.length ? targetInputValue(newEx.rep_range[0]) : ''
            ));
        const targetWeight = newEx.target_weight != null
            ? targetInputValue(newEx.target_weight)
            : (newEx.target_weight_lbs != null ? targetInputValue(newEx.target_weight_lbs) : '');
        const previousReps = previousEx ? (
            previousEx.target_reps != null
                ? targetInputValue(previousEx.target_reps)
                : (previousEx.reps != null ? targetInputValue(previousEx.reps) : (
                    Array.isArray(previousEx.rep_range) && previousEx.rep_range.length ? targetInputValue(previousEx.rep_range[0]) : ''
                ))
        ) : '';
        const previousWeight = previousEx ? (
            previousEx.target_weight != null
                ? targetInputValue(previousEx.target_weight)
                : (previousEx.target_weight_lbs != null ? targetInputValue(previousEx.target_weight_lbs) : '')
        ) : '';
        const editedSet = (s) => {
            if (!s) return false;
            if (s.done || (s.notes != null && String(s.notes).trim())) return true;
            if (previousReps && String(s.reps ?? '') !== previousReps) return true;
            if (previousWeight && String(s.weight ?? '') !== previousWeight) return true;
            return false;
        };
        const copySet = (s) => ({
            reps: s && s.reps != null ? s.reps : '',
            weight: s && s.weight != null ? s.weight : '',
            done: Boolean(s && s.done),
            notes: s && s.notes != null ? s.notes : '',
        });
        const freshSet = () => ({ reps: targetReps, weight: targetWeight, done: false, notes: '' });
        const rows = Array.from({ length: targetCount }, (_, idx) => {
            const prev = prevList[idx];
            return editedSet(prev) ? copySet(prev) : freshSet();
        });
        prevList.forEach((prev, idx) => {
            if (idx >= targetCount && prev && prev.done) rows.push(copySet(prev));
        });
        return rows;
    }

    const EXERCISE_IDENTITY_ALIAS_GROUPS = [
        ['Pec Fly', ['Pectoral Fly']],
        ['Chest-Supported Row', ['Chest Supported Row']],
        ['Machine Deltoid Raise', ['Deltoid Raise', 'Rear Delt Raise']],
        ['Back Extension', ['Low Back Extension', 'Lower Back Extension']],
        ['Biceps Curl', ['Hoist Biceps Curl', 'Hoist Roc-It Biceps Curl', 'Nautilus Biceps Curl', 'Nautilus ONE Biceps Curl']],
        ['Overhead Tricep Extension', ['Tricep Extension', 'Triceps Extension', 'Tricep Extensions', 'Triceps Extensions']],
        ['Rotary Torso', ['Torso Rotation', 'Rotary Torso Machine']],
    ];
    const EXERCISE_IDENTITY_ALIASES = new Map();
    function normalizeExerciseIdentityKey(name) {
        return String(name || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
    }
    EXERCISE_IDENTITY_ALIAS_GROUPS.forEach(([canonical, aliases]) => {
        const canonicalKey = normalizeExerciseIdentityKey(canonical);
        EXERCISE_IDENTITY_ALIASES.set(canonicalKey, canonicalKey);
        aliases.forEach((alias) => {
            EXERCISE_IDENTITY_ALIASES.set(normalizeExerciseIdentityKey(alias), canonicalKey);
        });
    });

    function normalizeExerciseIdentity(ex) {
        const key = normalizeExerciseIdentityKey(exerciseName(ex));
        return EXERCISE_IDENTITY_ALIASES.get(key) || key;
    }

    function takePreviousExerciseByIdentity(previousExercises, usedPrevious, nextKey, sameSlot) {
        if (!nextKey) return null;
        if (sameSlot && !usedPrevious.has(sameSlot) && normalizeExerciseIdentity(sameSlot) === nextKey) {
            usedPrevious.add(sameSlot);
            return sameSlot;
        }
        const prev = previousExercises.find((candidate) => {
            return candidate && !usedPrevious.has(candidate) && normalizeExerciseIdentity(candidate) === nextKey;
        });
        if (prev) usedPrevious.add(prev);
        return prev || null;
    }

    function activeLoggedSetHasWork(ex, set) {
        if (!set) return false;
        if (set.done || (set.notes != null && String(set.notes).trim())) return true;

        const targetValue = (value) => {
            if (value == null || value === '') return '';
            const n = Number(value);
            return Number.isFinite(n) ? String(n) : '';
        };
        const plannedReps = ex ? (
            ex.target_reps != null
                ? targetValue(ex.target_reps)
                : (ex.reps != null ? targetValue(ex.reps) : (
                    Array.isArray(ex.rep_range) && ex.rep_range.length ? targetValue(ex.rep_range[0]) : ''
                ))
        ) : '';
        const plannedWeight = ex ? (
            ex.target_weight != null
                ? targetValue(ex.target_weight)
                : (ex.target_weight_lbs != null ? targetValue(ex.target_weight_lbs) : '')
        ) : '';
        const reps = set.reps != null ? String(set.reps) : '';
        const weight = set.weight != null ? String(set.weight) : '';
        if (plannedReps) {
            if (reps && reps !== plannedReps) return true;
        } else if (reps) {
            return true;
        }
        if (plannedWeight) {
            if (weight && weight !== plannedWeight) return true;
        } else if (weight) {
            return true;
        }
        return false;
    }

    function activeExerciseHasLoggedWork(ex) {
        return Boolean(ex && Array.isArray(ex.logged_sets) && ex.logged_sets.some((set) => activeLoggedSetHasWork(ex, set)));
    }

    function applyAdjustedRecommendationToActiveWorkout(nw, previousExercises = [], opts = {}) {
        if (!nw) return;
        const existing = state.activeWorkout || {};
        const usedPrevious = new Set();
        const exercises = (nw.exercises || []).map((ex, i) => {
            const nextKey = normalizeExerciseIdentity(ex);
            const prev = takePreviousExerciseByIdentity(previousExercises, usedPrevious, nextKey, previousExercises[i]);
            if (prev) {
                return {
                    ...ex,
                    logged_sets: buildAdjustedLoggedSets(ex, prev.logged_sets, prev),
                };
            }
            return {
                ...ex,
                logged_sets: buildAdjustedLoggedSets(ex, null),
            };
        });
        previousExercises.forEach((prev) => {
            if (prev && !usedPrevious.has(prev) && activeExerciseHasLoggedWork(prev)) {
                exercises.push({ ...prev });
            }
        });
        state.activeWorkout = {
            id: existing.id || nw.workout_id || newWorkoutId(nw.id),
            recommendation_id: nw.id || existing.recommendation_id || null,
            focus: nw.focus || nw.goal_name || existing.focus || 'Workout',
            auth_scope: String(nw.auth_scope || existing.auth_scope || currentActiveWorkoutDraftScope() || '').trim(),
            exercises,
            cardio: mergeAdjustedActiveCardio(nw.cardio, existing.cardio),
            saveState: existing.saveState || null,
            dirty: opts.preserveDirty === false ? false : Boolean(existing.dirty),
        };
        saveActiveWorkoutDraft({ syncDom: false });
    }

    function hasRecommendedCardio(cardio) {
        return Boolean(cardio && cardio.include_cardio !== false && (cardio.type || cardio.machine || Number(cardio.duration_minutes || 0) > 0));
    }

    function activeCardioHasLoggedWork(cardio) {
        if (!cardio) return false;
        if (cardio.completed || (cardio.notes != null && String(cardio.notes).trim())) return true;
        const recommendation = cardio.recommendation || {};
        const plannedActivity = recommendation.type || recommendation.machine || '';
        const plannedDuration = numericInputValue(recommendation.duration_minutes);
        const activity = cardio.activity_type != null ? String(cardio.activity_type) : '';
        const duration = cardio.duration_minutes != null ? String(cardio.duration_minutes) : '';
        if (plannedActivity && activity && activity !== plannedActivity) return true;
        if (plannedDuration) {
            if (duration && duration !== plannedDuration) return true;
        } else if (duration) {
            return true;
        }
        return false;
    }

    function mergeAdjustedActiveCardio(cardio, previous) {
        const previousWithWork = activeCardioHasLoggedWork(previous) ? previous : null;
        const next = buildActiveCardio(cardio, previousWithWork);
        if (next) return next;
        return previousWithWork;
    }

    function buildActiveCardio(cardio, previous) {
        if (!hasRecommendedCardio(cardio)) return null;
        return {
            recommendation: cardio,
            completed: previous ? Boolean(previous.completed) : false,
            activity_type: previous && previous.activity_type ? previous.activity_type : (cardio.type || cardio.machine || 'Cardio'),
            duration_minutes: previous && previous.duration_minutes !== '' && previous.duration_minutes != null
                ? previous.duration_minutes
                : numericInputValue(cardio.duration_minutes),
            notes: previous && previous.notes != null ? previous.notes : '',
        };
    }

    function updateLoggedSetFromRow(row) {
        const exIdx = Number(row.dataset.ex);
        const setIdx = Number(row.dataset.set);
        const ex = state.activeWorkout && state.activeWorkout.exercises && state.activeWorkout.exercises[exIdx];
        if (!ex || !ex.logged_sets || !ex.logged_sets[setIdx]) return;
        const wasDone = !!ex.logged_sets[setIdx].done;
        ex.logged_sets[setIdx] = {
            weight: qs('input[data-field="weight"]', row).value,
            reps: qs('input[data-field="reps"]', row).value,
            done: qs('input[data-field="done"]', row).checked,
            notes: qs('input[data-field="notes"]', row).value,
        };
        state.activeWorkout.dirty = true;
        saveActiveWorkoutDraft();
        // FIT-108: keep the sticky progress header in sync after every input
        // change. Also re-target the "next incomplete" highlight and, when
        // the user just flipped a set to done, scroll the next incomplete
        // row into view so the focus follows the workout.
        const body = $('active-workout-body');
        if (body) {
            const progress = countActiveWorkoutProgress();
            const header = $('active-workout-progress');
            if (header) header.textContent = formatActiveWorkoutProgress(progress);
            qsa('.set-row.set-row-next', body).forEach((r) => r.classList.remove('set-row-next'));
            if (progress.nextIncomplete) {
                const sel = `.set-row[data-ex="${progress.nextIncomplete.exIdx}"][data-set="${progress.nextIncomplete.setIdx}"]`;
                const next = body.querySelector(sel);
                if (next) {
                    next.classList.add('set-row-next');
                    const justCompleted = !wasDone && ex.logged_sets[setIdx].done;
                    if (justCompleted && next !== row) {
                        next.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    }
                }
            }
        }
    }

    // FIT-108: count completed / total sets across every exercise and find
    // the first incomplete set so the sticky header + next-row highlight
    // can stay accurate as the user logs sets. Returns 0/0 when there is
    // no active workout so the caller can render a neutral state.
    // FIT-108 (Codex audit): track the 1-indexed flat position of the
    // next incomplete row so the "Set N of M" header always agrees with
    // the highlighted .set-row-next, even if the user toggles sets out
    // of order. Pure setsDone-based math would diverge from the highlight
    // (e.g. checking set 3 before set 1 would show "Set 2 of M" while
    // the accent still points at set 1).
    function countActiveWorkoutProgress() {
        const aw = state.activeWorkout;
        const out = {
            setsDone: 0,
            setsTotal: 0,
            exercisesWithIncomplete: 0,
            nextIncomplete: null,
            nextIncompleteFlatIdx: null,
        };
        if (!aw || !Array.isArray(aw.exercises)) return out;
        let flatIdx = 0;
        aw.exercises.forEach((ex, exIdx) => {
            if (!ex || !Array.isArray(ex.logged_sets) || !ex.logged_sets.length) return;
            let incompleteInEx = false;
            ex.logged_sets.forEach((set, setIdx) => {
                out.setsTotal += 1;
                flatIdx += 1;
                if (set && set.done) {
                    out.setsDone += 1;
                } else {
                    if (!incompleteInEx) incompleteInEx = true;
                    if (!out.nextIncomplete) {
                        out.nextIncomplete = { exIdx, setIdx };
                        out.nextIncompleteFlatIdx = flatIdx;
                    }
                }
            });
            if (incompleteInEx) out.exercisesWithIncomplete += 1;
        });
        return out;
    }

    function formatActiveWorkoutProgress(p) {
        if (!p || !p.setsTotal) return 'No sets yet';
        // FIT-108 (Codex audit): derive "Set N" from the next-incomplete
        // row's flat index, not from setsDone, so the header agrees with
        // the highlight even under out-of-order completion.
        if (!p.nextIncomplete) {
            return `All ${p.setsTotal} sets complete`;
        }
        const setN = p.nextIncompleteFlatIdx;
        const exLeft = p.exercisesWithIncomplete;
        return `Set ${setN} of ${p.setsTotal} · ${exLeft} ${exLeft === 1 ? 'exercise' : 'exercises'} left`;
    }

    function updateActiveCardio() {
        const cardio = state.activeWorkout && state.activeWorkout.cardio;
        const card = qs('.active-cardio', $('active-workout-body'));
        if (!cardio || !card) return;
        cardio.completed = qs('input[data-cardio-field="completed"]', card).checked;
        cardio.activity_type = qs('input[data-cardio-field="activity_type"]', card).value;
        cardio.duration_minutes = qs('input[data-cardio-field="duration_minutes"]', card).value;
        cardio.notes = qs('textarea[data-cardio-field="notes"]', card).value;
        state.activeWorkout.dirty = true;
        saveActiveWorkoutDraft();
    }

    function renderActiveWorkout() {
        if (!state.activeWorkout) return;
        $('active-workout-title').textContent = (state.activeWorkout.focus + ' Workout').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
        const body = $('active-workout-body');
        body.innerHTML = '';
        // FIT-108: sticky progress header pinned to the top of the
        // scrollable modal body so the user always sees "Set N of M ·
        // X exercises left" while logging. Updated by
        // updateLoggedSetFromRow on every change.
        const progress = countActiveWorkoutProgress();
        const progressEl = document.createElement('div');
        progressEl.className = 'active-workout-progress';
        progressEl.id = 'active-workout-progress';
        progressEl.textContent = formatActiveWorkoutProgress(progress);
        body.appendChild(progressEl);
        state.activeWorkout.exercises.forEach((ex, i) => {
            const card = document.createElement('div');
            card.className = 'active-ex';
            const sets = setCountForExercise(ex);
            const reps = ex.target_reps || ex.reps || 10;
            const rpe = ex.rpe_target || ex.rpe || '7';
            ex.rpe = rpe; // keep for completeWorkout payload
            const target = `${sets} × ${reps} RPE ${rpe}`;
            let rowsHtml = '';
            ex.logged_sets.forEach((set, sidx) => {
                // FIT-108: flag the first incomplete set across the whole
                // workout so the user's next action is visually obvious.
                const isNext = progress.nextIncomplete
                    && progress.nextIncomplete.exIdx === i
                    && progress.nextIncomplete.setIdx === sidx;
                rowsHtml += `
                    <div class="set-row${isNext ? ' set-row-next' : ''}" data-ex="${i}" data-set="${sidx}">
                        <label>${sidx + 1}</label>
                        <input type="number" placeholder="Weight" data-field="weight" inputmode="decimal" value="${escapeHtml(set.weight)}">
                        <input type="number" placeholder="Reps" data-field="reps" inputmode="numeric" value="${escapeHtml(set.reps)}">
                        <label class="set-done-cell" aria-label="mark set done">
                            <input type="checkbox" data-field="done"${set.done ? ' checked' : ''}>
                        </label>
                        <input class="set-notes" type="text" placeholder="Set notes" data-field="notes" value="${escapeHtml(set.notes)}">
                    </div>
                `;
            });
            const name = exerciseName(ex);
            const muscle = exerciseMuscle(ex);
            const loadHint = exerciseLoadHint(ex);
            card.innerHTML = `
                <div class="active-ex-head">
                    <div class="active-ex-main">
                        <h4>${escapeHtml(name)}</h4>
                        <span class="active-ex-target">${target}</span>
                    </div>
                    <div class="active-ex-actions">
                        <button class="ex-swap-btn active-swap-btn" type="button" title="Swap this exercise" aria-label="Swap ${escapeHtml(name)}">⇄</button>
                        <button class="ex-swap-btn active-remove-btn" type="button" title="Remove this exercise" aria-label="Remove ${escapeHtml(name)}">×</button>
                    </div>
                </div>
                ${renderLoadHintHtml(loadHint, 'active-load-hint')}
                ${rowsHtml}
            `;
            card.querySelector('.active-swap-btn').addEventListener('click', () => openSwap(i, muscle, name, 'active'));
            card.querySelector('.active-remove-btn').addEventListener('click', () => removeActiveExercise(i, name));
            body.appendChild(card);
        });
        if (!state.activeWorkout.exercises.length) {
            body.innerHTML = '<div class="empty active-empty">No exercises left in this workout.</div>';
        }
        if (state.activeWorkout.cardio) {
            const cardio = state.activeWorkout.cardio;
            const rec = cardio.recommendation || {};
            const bits = [
                rec.zone,
                rec.heart_rate_range,
                rec.intensity,
            ].filter(Boolean);
            const whyText = cardioWhyText(rec);
            const card = document.createElement('div');
            card.className = 'active-ex active-cardio';
            card.innerHTML = `
                <div class="active-ex-head">
                    <div class="active-ex-main">
                        <h4>Cardio Follow-Up</h4>
                        <span class="active-ex-target">${escapeHtml(rec.type || cardio.activity_type)} · ${escapeHtml(cardio.duration_minutes || rec.duration_minutes || '')} min</span>
                    </div>
                </div>
                ${bits.length ? `<div class="active-cardio-meta">${escapeHtml(bits.join(' · '))}</div>` : ''}
                ${whyText ? `<div class="cardio-why">${escapeHtml(whyText)}</div>` : ''}
                <label class="active-cardio-check">
                    <input type="checkbox" data-cardio-field="completed"${cardio.completed ? ' checked' : ''}>
                    <span>Completed recommended cardio</span>
                </label>
                <div class="active-cardio-grid">
                    <input type="text" data-cardio-field="activity_type" value="${escapeHtml(cardio.activity_type)}" placeholder="Cardio type">
                    <input type="number" data-cardio-field="duration_minutes" value="${escapeHtml(cardio.duration_minutes)}" placeholder="Minutes" inputmode="numeric">
                </div>
                <textarea data-cardio-field="notes" rows="2" placeholder="Cardio notes">${escapeHtml(cardio.notes)}</textarea>
            `;
            body.appendChild(card);
        }
        qsa('.set-row', body).forEach((row) => {
            qsa('input', row).forEach((input) => {
                input.addEventListener(input.type === 'checkbox' ? 'change' : 'input', () => updateLoggedSetFromRow(row));
            });
        });
        qsa('[data-cardio-field]', body).forEach((input) => {
            input.addEventListener(input.type === 'checkbox' ? 'change' : 'input', updateActiveCardio);
        });
        if (state.activeWorkout.saveState) {
            setActiveWorkoutStatus(state.activeWorkout.saveState.message, state.activeWorkout.saveState.variant);
        } else {
            setActiveWorkoutStatus('');
        }
        const modal = $('modal-active');
        wireActiveWorkoutGuards(modal);
        modal.hidden = false;
    }

    function activeWorkoutHasProgress() {
        const aw = state.activeWorkout;
        if (!aw) return false;
        if (aw.dirty) return true;
        const body = $('active-workout-body');
        if (!body) return false;
        let hasProgress = false;
        qsa('.set-row', body).forEach((row) => {
            const exIdx = Number(row.dataset.ex);
            const setIdx = Number(row.dataset.set);
            const ex = aw.exercises && aw.exercises[exIdx];
            const set = ex && ex.logged_sets && ex.logged_sets[setIdx];
            const weight = qs('input[data-field="weight"]', row).value;
            const reps = qs('input[data-field="reps"]', row).value;
            const done = qs('input[data-field="done"]', row).checked;
            const notes = qs('input[data-field="notes"]', row).value.trim();
            if (done || notes) {
                hasProgress = true;
            }
            if (set && (String(weight) !== String(set.weight) || String(reps) !== String(set.reps))) {
                hasProgress = true;
            }
        });
        const cardio = aw.cardio;
        const cardioCard = qs('.active-cardio', body);
        if (cardio && cardioCard) {
            const completed = qs('input[data-cardio-field="completed"]', cardioCard).checked;
            const activityType = qs('input[data-cardio-field="activity_type"]', cardioCard).value;
            const duration = qs('input[data-cardio-field="duration_minutes"]', cardioCard).value;
            const notes = qs('textarea[data-cardio-field="notes"]', cardioCard).value.trim();
            if (completed || notes || String(activityType) !== String(cardio.activity_type || '') || String(duration) !== String(cardio.duration_minutes || '')) {
                hasProgress = true;
            }
        }
        return hasProgress;
    }

    function cancelActiveWorkout({ requireConfirm = false } = {}) {
        const modal = $('modal-active');
        if (!modal) return;
        if (requireConfirm && activeWorkoutHasProgress() && !window.confirm('Discard this in-progress workout?')) {
            return;
        }
        state.activeWorkout = null;
        clearActiveWorkoutDraft();
        clearAdjustIntent();
        modal.hidden = true;
    }

    function wireActiveWorkoutGuards(modal) {
        if (!modal) return;
        const closeBtn = modal.querySelector('.modal-close');
        if (closeBtn) {
            const fresh = closeBtn.cloneNode(true);
            fresh.removeAttribute('data-close-modal');
            closeBtn.parentNode.replaceChild(fresh, closeBtn);
            fresh.addEventListener('click', () => cancelActiveWorkout({ requireConfirm: true }));
        }
        if (modal.__fit24BackdropHandler) {
            modal.removeEventListener('click', modal.__fit24BackdropHandler, true);
        }
        const handler = (e) => {
            if (e.target === modal) {
                e.stopImmediatePropagation();
            }
        };
        modal.__fit24BackdropHandler = handler;
        modal.addEventListener('click', handler, true);
    }

    function removeActiveExercise(exIdx, name) {
        const aw = state.activeWorkout;
        if (!aw || !Array.isArray(aw.exercises) || exIdx < 0 || exIdx >= aw.exercises.length) return;
        aw.exercises.splice(exIdx, 1);
        aw.dirty = true;
        if (!aw.exercises.length) aw.saveState = null;
        renderActiveWorkout();
        saveActiveWorkoutDraft();
        toast(`Removed ${name}`, 'ok');
    }

    async function startWorkout() {
        let nw = null;
        try {
            nw = await getNextWorkout(true);
        } catch (err) {
            console.warn('start workout next-workout load failed', err);
            nw = state.nextWorkout || (state.dashboard && state.dashboard.next_workout);
            if (!nw) {
                try {
                    const dash = await getDashboard();
                    nw = dash && dash.next_workout;
                } catch (dashErr) {
                    console.warn('start workout dashboard fallback failed', dashErr);
                }
            }
        }
        if (!nw) { toast('No workout planned', 'err'); return; }
        if (!confirmDiscardActiveWorkoutForStart()) return;
        startActiveWorkoutFromRecommendation(nw);
        renderActiveWorkout();
    }

    async function viewAdjustedPlan() {
        if ($('modal-adjust')) $('modal-adjust').hidden = true;
        await switchTab('tab-workout');
    }

    async function startAdjustedWorkout() {
        const nw = state.adjustedWorkout || (state.dashboard && state.dashboard.next_workout);
        if (!nw) { toast('No adjusted workout available', 'err'); return; }
        if (!confirmDiscardActiveWorkoutForStart()) return;
        if ($('modal-adjust')) $('modal-adjust').hidden = true;
        startActiveWorkoutFromRecommendation(nw);
        renderActiveWorkout();
    }

    function confirmDiscardActiveWorkoutForStart() {
        if (!activeWorkoutHasProgress()) return true;
        return window.confirm('You have an in-progress workout. Discard logged sets and restart?');
    }

    function startActiveWorkoutFromRecommendation(nw) {
        state.activeWorkout = null;
        setActiveWorkoutFromRecommendation(nw);
    }

    const SYNC_QUEUE_KEY = 'fit51:sync-queue:v1';
    const WORKOUT_QUEUE_RETRYABLE_STATUSES = new Set(['pending', 'auth_required']);
    let _syncFlushInFlight = false;
    // FIT-145: meal-intake offline queue. Metadata and photo blobs live
    // together in IndexedDB so reloads preserve queued meals without
    // putting raw photo bytes into localStorage.
    const MEAL_QUEUE_DB_NAME = 'fitMealIntakeQueueDB';
    const MEAL_QUEUE_DB_VERSION = 1;
    const MEAL_QUEUE_STORE = 'queued_meals';
    const MEAL_PHOTO_STORE = 'meal_photos';
    const MEAL_QUEUE_RETRYABLE_STATUSES = new Set(['pending', 'auth_required']);
    const MEAL_QUEUE_AUTH_SCOPE_KEY = 'fit145:meal-queue-auth-scope:v1';
    let _mealQueueDbPromise = null;
    let _mealQueueAuthScope = '';
    let _mealSyncFlushInFlight = false;
    const _mealSyncInFlightClientIds = new Set();

    function loadSyncQueue() {
        try {
            const raw = localStorage.getItem(SYNC_QUEUE_KEY);
            const arr = raw ? JSON.parse(raw) : [];
            return Array.isArray(arr) ? arr : [];
        } catch (e) { return []; }
    }

    function saveSyncQueue(queue) {
        try { localStorage.setItem(SYNC_QUEUE_KEY, JSON.stringify(queue || [])); } catch (e) {}
        renderSyncBanner();
    }

    function enqueueOfflineWorkout(payload, initialStatus = 'pending') {
        const queue = loadSyncQueue();
        const clientId = payload.client_workout_id || payload.id || `w-offline-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        const finalPayload = { ...payload, id: clientId, client_workout_id: clientId, offline: true };
        const idx = queue.findIndex((e) => e.client_workout_id === clientId);
        const entry = {
            client_workout_id: clientId,
            queued_at: new Date().toISOString(),
            last_attempt_at: null,
            last_status: initialStatus,
            attempts: 0,
            payload: finalPayload,
            server_response: null,
            reject_reason: null,
        };
        if (idx >= 0) {
            queue[idx] = { ...queue[idx], ...entry, queued_at: queue[idx].queued_at, attempts: queue[idx].attempts };
        } else {
            queue.push(entry);
        }
        saveSyncQueue(queue);
        return clientId;
    }

    function removeQueueEntry(clientId) {
        const queue = loadSyncQueue().filter((e) => e.client_workout_id !== clientId);
        saveSyncQueue(queue);
    }

    function updateQueueEntry(clientId, fields) {
        const queue = loadSyncQueue();
        const idx = queue.findIndex((e) => e.client_workout_id === clientId);
        if (idx < 0) return;
        queue[idx] = { ...queue[idx], ...fields };
        saveSyncQueue(queue);
    }

    async function postCompleteWorkout(payload) {
        // Lower-level than api() so we can read both 2xx and 4xx/5xx bodies and
        // pull out the FIT-37 sync_status from response.error.details.
        const res = await fetch('/api/complete-workout', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', [CSRF_HEADER_NAME]: CSRF_HEADER_VALUE },
            body: JSON.stringify(payload),
        });
        const body = await res.json().catch(() => null);
        if (res.ok) {
            return { ok: true, status: res.status, body, syncStatus: (body && body.sync_status) || 'inserted' };
        }
        const err = body && body.error;
        let syncStatus = err && err.details && err.details.sync_status;
        if (!syncStatus) {
            if (res.status === 401 || res.status === 403) syncStatus = 'auth_required';
            else if (res.status === 409) syncStatus = 'conflicted';
            else if (res.status >= 500) syncStatus = 'pending';
            else syncStatus = 'rejected';
        }
        const reason = (err && err.message) || `Workout sync failed (${res.status}).`;
        return { ok: false, status: res.status, body, syncStatus, reason };
    }

    function annotateWorkoutSyncReason(rawReason, syncStatus) {
        const base = String(rawReason || '').trim();
        if (syncStatus === 'pending') {
            const note = 'Will retry automatically when the app can reach the server.';
            return base ? `${base} ${note}` : note;
        }
        if (syncStatus === 'auth_required') {
            const note = 'Sign in with the account that saved this workout, then retry. The workout remains on this device.';
            return base ? `${base} ${note}` : note;
        }
        if (syncStatus === 'rejected') {
            const note = 'The server did not accept this workout. Review the queue before discarding it.';
            return base ? `${base} ${note}` : note;
        }
        if (syncStatus === 'conflicted') {
            const note = 'Conflict reported by the server. Discard this entry, or retry later if you think it should go through.';
            return base ? `${base} ${note}` : note;
        }
        return base || null;
    }

    async function syncSingleEntry(clientId) {
        const queue = loadSyncQueue();
        const entry = queue.find((e) => e.client_workout_id === clientId);
        if (!entry) return null;
        try {
            const result = await postCompleteWorkout(entry.payload);
            const attemptedAt = new Date().toISOString();
            const attempts = (entry.attempts || 0) + 1;
            if (result.ok && (result.syncStatus === 'inserted' || result.syncStatus === 'already_synced')) {
                removeQueueEntry(clientId);
                invalidateCaches();
                if (state.currentTab === 'tab-history') loadTab('tab-history');
                return { ok: true, status: result.syncStatus };
            }
            updateQueueEntry(clientId, {
                last_status: result.syncStatus || (result.ok ? 'pending' : 'rejected'),
                last_attempt_at: attemptedAt,
                attempts,
                server_response: result.body || null,
                reject_reason: annotateWorkoutSyncReason(result.reason, result.syncStatus || (result.ok ? 'pending' : 'rejected')),
            });
            return { ok: false, status: result.syncStatus };
        } catch (e) {
            updateQueueEntry(clientId, {
                last_status: 'pending',
                last_attempt_at: new Date().toISOString(),
                attempts: (entry.attempts || 0) + 1,
                reject_reason: annotateWorkoutSyncReason((e && e.message) || 'Could not reach the server.', 'pending'),
            });
            return { ok: false, status: 'pending', error: e && e.message };
        }
    }

    async function flushSyncQueue() {
        if (!navigator.onLine || _syncFlushInFlight) return;
        _syncFlushInFlight = true;
        try {
            const ids = loadSyncQueue()
                .filter((e) => WORKOUT_QUEUE_RETRYABLE_STATUSES.has(e.last_status || 'pending'))
                .map((e) => e.client_workout_id);
            for (const id of ids) {
                await syncSingleEntry(id);
            }
            renderSyncQueueModal();
        } finally {
            _syncFlushInFlight = false;
        }
    }

    function mealQueueRequest(request) {
        return new Promise((resolve, reject) => {
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error || new Error('IndexedDB request failed'));
        });
    }

    function mealQueueTxComplete(tx) {
        return new Promise((resolve, reject) => {
            tx.oncomplete = () => resolve();
            tx.onabort = () => reject(tx.error || new Error('IndexedDB transaction aborted'));
            tx.onerror = () => reject(tx.error || new Error('IndexedDB transaction failed'));
        });
    }

    function openMealQueueDb() {
        if (!('indexedDB' in window)) return Promise.reject(new Error('IndexedDB unavailable'));
        if (_mealQueueDbPromise) return _mealQueueDbPromise;
        _mealQueueDbPromise = new Promise((resolve, reject) => {
            const request = indexedDB.open(MEAL_QUEUE_DB_NAME, MEAL_QUEUE_DB_VERSION);
            request.onupgradeneeded = () => {
                const db = request.result;
                if (!db.objectStoreNames.contains(MEAL_QUEUE_STORE)) {
                    db.createObjectStore(MEAL_QUEUE_STORE, { keyPath: 'client_id' });
                }
                if (!db.objectStoreNames.contains(MEAL_PHOTO_STORE)) {
                    const photoStore = db.createObjectStore(MEAL_PHOTO_STORE, { keyPath: 'photo_id' });
                    photoStore.createIndex('client_id', 'client_id', { unique: false });
                }
            };
            request.onsuccess = () => {
                const db = request.result;
                db.onversionchange = () => {
                    db.close();
                    _mealQueueDbPromise = null;
                };
                resolve(db);
            };
            request.onerror = () => {
                _mealQueueDbPromise = null;
                reject(request.error || new Error('Could not open meal queue'));
            };
            request.onblocked = () => {
                _mealQueueDbPromise = null;
                reject(new Error('Meal queue database upgrade blocked'));
            };
        });
        return _mealQueueDbPromise;
    }

    function mealPhotoExtension(type) {
        const normalized = String(type || '').toLowerCase();
        if (normalized.includes('png')) return 'png';
        if (normalized.includes('webp')) return 'webp';
        if (normalized.includes('gif')) return 'gif';
        return 'jpg';
    }

    function persistedMealQueueAuthScope() {
        try {
            return String(localStorage.getItem(MEAL_QUEUE_AUTH_SCOPE_KEY) || '').trim();
        } catch (_) {
            return '';
        }
    }

    function cachedMealQueueAuthScope() {
        return String(_mealQueueAuthScope || persistedMealQueueAuthScope() || '').trim();
    }

    function persistMealQueueAuthScope(scope) {
        const normalized = String(scope || '').trim();
        if (!normalized) return '';
        _mealQueueAuthScope = normalized;
        try {
            localStorage.setItem(MEAL_QUEUE_AUTH_SCOPE_KEY, normalized);
        } catch (_) { /* localStorage may be unavailable; keep in memory for this page. */ }
        return normalized;
    }

    async function fetchCurrentMealQueueAuthScope({ timeoutMs = 0 } = {}) {
        let timer = null;
        try {
            const fetchOpts = {
                credentials: 'same-origin',
                headers: { 'Accept': 'application/json', [CSRF_HEADER_NAME]: CSRF_HEADER_VALUE },
            };
            if (timeoutMs && typeof AbortController !== 'undefined') {
                const controller = new AbortController();
                timer = setTimeout(() => controller.abort(), timeoutMs);
                fetchOpts.signal = controller.signal;
            }
            const res = await fetch('/api/auth/scope', {
                ...fetchOpts,
            });
            if (res.status === 401 || res.status === 403) {
                return {
                    ok: false,
                    status: 'auth_required',
                    reason: 'Sign in with the account that saved this offline meal, then retry.',
                };
            }
            if (!res.ok) {
                return {
                    ok: false,
                    status: 'pending',
                    reason: `Could not verify the current sign-in before syncing this meal (${res.status}).`,
                };
            }
            const body = await res.json().catch(() => null);
            const scope = String(body && body.auth_scope || '').trim();
            if (!scope) {
                return {
                    ok: false,
                    status: 'pending',
                    reason: 'Could not verify the current sign-in before syncing this meal.',
                };
            }
            return { ok: true, scope };
        } catch (e) {
            return {
                ok: false,
                status: 'pending',
                reason: 'Could not verify the current sign-in before syncing this meal.',
            };
        } finally {
            if (timer) clearTimeout(timer);
        }
    }

    async function refreshMealQueueAuthScope(options) {
        const liveScope = await fetchCurrentMealQueueAuthScope(options);
        if (liveScope.ok) {
            persistMealQueueAuthScope(liveScope.scope);
        }
        return liveScope;
    }

    async function mealQueueAuthGate(entry) {
        const queuedScope = String(entry && entry.auth_scope || '').trim();
        if (!queuedScope) {
            return {
                ok: false,
                status: 'auth_required',
                reason: 'This offline meal could not be tied to a signed-in account. It will stay on this device until you discard it.',
            };
        }
        const liveScope = await fetchCurrentMealQueueAuthScope();
        if (!liveScope.ok) {
            return liveScope;
        }
        const currentScope = liveScope.scope;
        if (!currentScope) {
            return {
                ok: false,
                status: 'auth_required',
                reason: 'Sign in with the account that saved this offline meal, then retry. It will stay on this device until then.',
            };
        }
        if (queuedScope !== currentScope) {
            return {
                ok: false,
                status: 'auth_required',
                reason: 'This offline meal was saved under a different sign-in. Sign in to that account to sync it, or discard it.',
            };
        }
        return { ok: true };
    }

    async function listMealQueueEntries() {
        const db = await openMealQueueDb();
        const tx = db.transaction(MEAL_QUEUE_STORE, 'readonly');
        const entries = await mealQueueRequest(tx.objectStore(MEAL_QUEUE_STORE).getAll());
        await mealQueueTxComplete(tx);
        return (Array.isArray(entries) ? entries : []).sort((a, b) => String(a.queued_at || '').localeCompare(String(b.queued_at || '')));
    }

    async function getQueuedMealWithPhotos(clientId) {
        const db = await openMealQueueDb();
        const tx = db.transaction([MEAL_QUEUE_STORE, MEAL_PHOTO_STORE], 'readonly');
        const mealStore = tx.objectStore(MEAL_QUEUE_STORE);
        const photoStore = tx.objectStore(MEAL_PHOTO_STORE);
        const entryPromise = mealQueueRequest(mealStore.get(clientId));
        const photosPromise = mealQueueRequest(photoStore.index('client_id').getAll(clientId));
        const entry = await entryPromise;
        const photos = await photosPromise;
        await mealQueueTxComplete(tx);
        return {
            entry: entry || null,
            photos: (Array.isArray(photos) ? photos : []).sort((a, b) => (a.position || 0) - (b.position || 0)),
        };
    }

    async function enqueueMealIntakeOffline({ textValue, files, clientId, localTime }) {
        const text = String(textValue || '').trim();
        const imageFiles = Array.isArray(files) ? files : [];
        if (!text && imageFiles.length === 0) {
            throw new Error('Type a meal or attach a photo.');
        }
        const authScope = cachedMealQueueAuthScope();
        if (!authScope) {
            throw new Error('Sign in before saving meals offline.');
        }
        const queuedAt = new Date().toISOString();
        const photoRecords = imageFiles.map((file, idx) => {
            const type = file.type || 'image/jpeg';
            const photoId = `${clientId}:photo:${idx + 1}`;
            return {
                photo_id: photoId,
                client_id: clientId,
                position: idx,
                type,
                size: file.size || 0,
                queued_at: queuedAt,
                blob: typeof file.slice === 'function' ? file.slice(0, file.size, type) : file,
            };
        });
        const entry = {
            client_id: clientId,
            auth_scope: authScope,
            queued_at: queuedAt,
            last_attempt_at: null,
            attempts: 0,
            last_status: 'pending',
            text,
            local_timestamp: localTime && localTime.local_timestamp,
            local_date: localTime && localTime.local_date,
            local_iso: localTime && localTime.local_iso,
            image_count: photoRecords.length,
            aggregate_bytes: photoRecords.reduce((total, photo) => total + (photo.size || 0), 0),
            image_metadata: photoRecords.map((photo) => ({
                photo_id: photo.photo_id,
                position: photo.position,
                type: photo.type,
                size: photo.size,
            })),
            photo_ids: photoRecords.map((photo) => photo.photo_id),
            server_response_summary: null,
            reject_reason: null,
        };
        const db = await openMealQueueDb();
        const tx = db.transaction([MEAL_QUEUE_STORE, MEAL_PHOTO_STORE], 'readwrite');
        const mealStore = tx.objectStore(MEAL_QUEUE_STORE);
        const photoStore = tx.objectStore(MEAL_PHOTO_STORE);
        photoRecords.forEach((photo) => photoStore.put(photo));
        mealStore.put(entry);
        await mealQueueTxComplete(tx);
        renderSyncBanner();
        return entry;
    }

    async function removeMealQueueEntry(clientId) {
        const db = await openMealQueueDb();
        const readTx = db.transaction(MEAL_QUEUE_STORE, 'readonly');
        const entry = await mealQueueRequest(readTx.objectStore(MEAL_QUEUE_STORE).get(clientId));
        await mealQueueTxComplete(readTx);
        const photoIds = entry && Array.isArray(entry.photo_ids) ? entry.photo_ids : [];
        const tx = db.transaction([MEAL_QUEUE_STORE, MEAL_PHOTO_STORE], 'readwrite');
        const mealStore = tx.objectStore(MEAL_QUEUE_STORE);
        const photoStore = tx.objectStore(MEAL_PHOTO_STORE);
        photoIds.forEach((photoId) => photoStore.delete(photoId));
        mealStore.delete(clientId);
        await mealQueueTxComplete(tx);
        renderSyncBanner();
    }

    async function cleanupOrphanedMealQueuePhotos() {
        const db = await openMealQueueDb();
        const readTx = db.transaction([MEAL_QUEUE_STORE, MEAL_PHOTO_STORE], 'readonly');
        const mealStore = readTx.objectStore(MEAL_QUEUE_STORE);
        const photoStore = readTx.objectStore(MEAL_PHOTO_STORE);
        const entries = await mealQueueRequest(mealStore.getAll());
        const photos = await mealQueueRequest(photoStore.getAll());
        await mealQueueTxComplete(readTx);
        const clientIds = new Set((entries || []).map((entry) => entry.client_id));
        const tx = db.transaction(MEAL_PHOTO_STORE, 'readwrite');
        const writePhotoStore = tx.objectStore(MEAL_PHOTO_STORE);
        (photos || []).forEach((photo) => {
            if (!clientIds.has(photo.client_id)) writePhotoStore.delete(photo.photo_id);
        });
        await mealQueueTxComplete(tx);
    }

    async function updateMealQueueEntry(clientId, fields) {
        const db = await openMealQueueDb();
        const readTx = db.transaction(MEAL_QUEUE_STORE, 'readonly');
        const entry = await mealQueueRequest(readTx.objectStore(MEAL_QUEUE_STORE).get(clientId));
        await mealQueueTxComplete(readTx);
        if (!entry) {
            return;
        }
        const tx = db.transaction(MEAL_QUEUE_STORE, 'readwrite');
        tx.objectStore(MEAL_QUEUE_STORE).put({ ...entry, ...fields });
        await mealQueueTxComplete(tx);
        renderSyncBanner();
    }

    function queuedMealFormData(entry, photos) {
        const form = new FormData();
        if (entry.text) form.append('text', entry.text);
        (photos || []).forEach((photo, idx) => {
            const extension = mealPhotoExtension(photo.type);
            form.append('images', photo.blob, `meal-${idx + 1}.${extension}`);
        });
        form.append('client_id', entry.client_id);
        if (entry.local_timestamp) form.append('local_timestamp', entry.local_timestamp);
        if (entry.local_date) form.append('local_date', entry.local_date);
        if (entry.local_iso) form.append('local_iso', entry.local_iso);
        return form;
    }

    async function postQueuedMealIntake(entry, photos) {
        const res = await fetch('/api/meal-intake', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json', [CSRF_HEADER_NAME]: CSRF_HEADER_VALUE },
            body: queuedMealFormData(entry, photos),
        });
        const ct = res.headers.get('content-type') || '';
        const body = ct.includes('application/json') ? await res.json().catch(() => null) : null;
        if (res.ok) {
            return { ok: true, status: res.status, body };
        }
        const message = (body && body.error && body.error.message) || `Meal sync failed (${res.status}).`;
        let syncStatus = 'rejected';
        if (res.status === 401 || res.status === 403) {
            syncStatus = 'auth_required';
        } else if (res.status === 409) {
            syncStatus = 'conflicted';
        } else if (res.status >= 500) {
            syncStatus = 'pending';
        }
        return { ok: false, status: res.status, body, syncStatus, reason: message };
    }

    function annotateMealSyncReason(rawReason, syncStatus) {
        const base = String(rawReason || '').trim();
        if (syncStatus === 'pending') {
            // Network / 5xx — retry is possible and happens automatically on reconnect.
            const note = 'Will retry automatically when you’re back online.';
            return base ? `${base} ${note}` : note;
        }
        if (syncStatus === 'auth_required') {
            const note = 'Sign in with the account that saved this offline meal, then retry. The meal and any offline photos remain on this device.';
            return base ? `${base} ${note}` : note;
        }
        if (syncStatus === 'rejected') {
            // 4xx — the server can’t accept this entry; no auto-retry.
            const note = 'The server didn’t accept this meal. Discard it from the queue to remove it.';
            return base ? `${base} ${note}` : note;
        }
        if (syncStatus === 'conflicted') {
            // 409 — operator may need to discard or retry once the duplicate clears.
            const note = 'Conflict reported by the server. Discard this entry, or retry later if you think it should go through.';
            return base ? `${base} ${note}` : note;
        }
        return base || null;
    }

    async function syncSingleMealQueueEntry(clientId) {
        if (_mealSyncInFlightClientIds.has(clientId)) return { ok: false, status: 'pending' };
        _mealSyncInFlightClientIds.add(clientId);
        try {
            const queued = await getQueuedMealWithPhotos(clientId);
            const entry = queued.entry;
            if (!entry) return null;
            const attemptedAt = new Date().toISOString();
            const attempts = (entry.attempts || 0) + 1;
            const authGate = await mealQueueAuthGate(entry);
            if (!authGate.ok) {
                const authStatus = authGate.status || 'auth_required';
                await updateMealQueueEntry(clientId, {
                    last_status: authStatus,
                    last_attempt_at: attemptedAt,
                    attempts,
                    reject_reason: annotateMealSyncReason(authGate.reason, authStatus),
                });
                return { ok: false, status: authStatus };
            }
            try {
                const latestQueued = await getQueuedMealWithPhotos(clientId);
                if (!latestQueued.entry) {
                    return { ok: false, status: 'discarded' };
                }
                const result = await postQueuedMealIntake(latestQueued.entry, latestQueued.photos);
                if (result.ok) {
                    try {
                        await removeMealQueueEntry(clientId);
                    } catch (deleteErr) {
                        await updateMealQueueEntry(clientId, {
                            last_status: 'eviction_failed',
                            last_attempt_at: attemptedAt,
                            attempts,
                            server_response_summary: result.body ? { status: result.body.status || null } : null,
                            reject_reason: 'The server accepted this meal, but the browser couldn’t clear the local copy. Tap Discard to remove the offline entry and its photos.',
                        });
                        return { ok: false, status: 'eviction_failed', error: deleteErr && deleteErr.message };
                    }
                    handleMealIntakeResponse(result.body, {
                        textValue: latestQueued.entry.text || '',
                        clientId,
                        imageFiles: [],
                        fromQueue: true,
                        localTime: {
                            local_timestamp: latestQueued.entry.local_timestamp || null,
                            local_date: latestQueued.entry.local_date || null,
                            local_iso: latestQueued.entry.local_iso || null,
                        },
                    });
                    toast('Queued meal synced');
                    return { ok: true, status: 'synced' };
                }
                await updateMealQueueEntry(clientId, {
                    last_status: result.syncStatus || 'rejected',
                    last_attempt_at: attemptedAt,
                    attempts,
                    server_response_summary: result.body ? { status: result.body.status || null, http_status: result.status } : { http_status: result.status },
                    reject_reason: annotateMealSyncReason(result.reason, result.syncStatus || 'rejected'),
                });
                return { ok: false, status: result.syncStatus || 'rejected' };
            } catch (e) {
                await updateMealQueueEntry(clientId, {
                    last_status: 'pending',
                    last_attempt_at: attemptedAt,
                    attempts,
                    reject_reason: annotateMealSyncReason((e && e.message) || 'Couldn’t reach the server.', 'pending'),
                });
                return { ok: false, status: 'pending', error: e && e.message };
            }
        } finally {
            _mealSyncInFlightClientIds.delete(clientId);
        }
    }

    async function flushMealSyncQueue() {
        if (!navigator.onLine || _mealSyncFlushInFlight) return;
        _mealSyncFlushInFlight = true;
        try {
            const ids = (await listMealQueueEntries())
                .filter((e) => MEAL_QUEUE_RETRYABLE_STATUSES.has(e.last_status || 'pending'))
                .map((e) => e.client_id);
            for (const id of ids) {
                await syncSingleMealQueueEntry(id);
            }
            await renderSyncQueueModal();
        } catch (e) {
            console.warn('Meal sync queue flush failed:', e);
        } finally {
            _mealSyncFlushInFlight = false;
        }
    }

    function renderSyncBanner() {
        const banner = $('sync-banner');
        const textEl = $('sync-banner-text');
        if (!banner || !textEl) return;
        const queue = loadSyncQueue();
        listMealQueueEntries().catch(() => []).then((mealQueue) => {
            const pending = queue.filter((e) => (e.last_status || 'pending') === 'pending').length
                + mealQueue.filter((e) => (e.last_status || 'pending') === 'pending').length;
            const failedStatuses = new Set(['rejected', 'conflicted', 'eviction_failed', 'auth_required']);
            const failed = queue.filter((e) => failedStatuses.has(e.last_status)).length
                + mealQueue.filter((e) => failedStatuses.has(e.last_status)).length;
            const total = queue.length + mealQueue.length;
            if (!total) { banner.hidden = true; return; }
            const parts = [];
            if (pending) parts.push(`${pending} pending`);
            if (failed) parts.push(`${failed} failed`);
            textEl.textContent = parts.length ? parts.join(' · ') : `${total} queued`;
            banner.classList.toggle('has-failed', failed > 0);
            banner.hidden = false;
        });
    }

    function openSyncQueueModal() {
        renderSyncQueueModal();
        const modal = $('modal-sync-queue');
        if (modal) modal.hidden = false;
    }

    async function renderSyncQueueModal() {
        const host = $('sync-queue-list');
        if (!host) return;
        const queue = loadSyncQueue();
        const mealQueue = await listMealQueueEntries().catch(() => []);
        host.innerHTML = '';
        if (!queue.length && !mealQueue.length) {
            host.innerHTML = '<div class="empty">No queued meals or workouts.</div>';
            return;
        }
        const statusLabels = {
            pending: 'Waiting to sync',
            auth_required: 'Sign-in needed',
            conflicted: 'Conflict',
            rejected: 'Needs review',
            eviction_failed: 'Cleanup failed',
            inserted: 'Synced',
            already_synced: 'Synced',
        };
        queue.forEach((entry) => {
            const status = entry.last_status || 'pending';
            const row = document.createElement('div');
            row.className = `sync-row sync-row-${status}`;
            const focusRaw = (entry.payload && entry.payload.session_type) || 'workout';
            const focusLabel = capitalize(String(focusRaw).replace(/_/g, ' '));
            const dateLabel = (entry.payload && entry.payload.date) ? fmtDate(entry.payload.date) : '—';
            const lastAttempt = entry.last_attempt_at ? fmtDateTime(entry.last_attempt_at) : 'never';
            const reasonHtml = entry.reject_reason ? `<div class="sync-row-reason">${escapeHtml(entry.reject_reason)}</div>` : '';
            row.innerHTML = `
                <div class="sync-row-head">
                    <span class="sync-row-title">${escapeHtml(focusLabel)} · ${escapeHtml(dateLabel)}</span>
                    <span class="sync-status-pill sync-status-${status}">${escapeHtml(statusLabels[status] || 'Pending')}</span>
                </div>
                <div class="sync-row-meta">${entry.attempts || 0} attempts · last attempt ${escapeHtml(lastAttempt)}</div>
                ${reasonHtml}
                <div class="sync-row-actions">
                    <button class="btn btn-ghost btn-sm" data-sync-discard="${escapeHtml(entry.client_workout_id)}" type="button">Discard</button>
                    <button class="btn btn-primary btn-sm" data-sync-retry="${escapeHtml(entry.client_workout_id)}" type="button">Retry</button>
                </div>
            `;
            host.appendChild(row);
        });
        // FIT-145: distinct labels so meals don't borrow the workout queue's
        // terse "Rejected"/"Conflict" wording — meal users see why and what
        // to do next.
        const mealStatusLabels = {
            pending: 'Waiting to sync',
            auth_required: 'Sign-in needed',
            conflicted: 'Conflict',
            rejected: 'Can’t accept',
            eviction_failed: 'Cleanup failed',
            inserted: 'Synced',
            already_synced: 'Synced',
        };
        mealQueue.forEach((entry) => {
            const status = entry.last_status || 'pending';
            const row = document.createElement('div');
            row.className = `sync-row sync-row-meal sync-row-${status}`;
            const photoCount = entry.image_count || 0;
            const textValue = entry.text ? entry.text.trim() : '';
            let titleText;
            if (textValue) {
                titleText = textValue.slice(0, 48);
            } else if (photoCount > 1) {
                titleText = `${photoCount}-photo meal`;
            } else if (photoCount === 1) {
                titleText = 'Photo meal';
            } else {
                titleText = 'Meal';
            }
            let typeLabel;
            if (textValue && photoCount) {
                typeLabel = `Text + ${photoCount} photo${photoCount === 1 ? '' : 's'}`;
            } else if (photoCount) {
                typeLabel = `${photoCount} photo${photoCount === 1 ? '' : 's'}`;
            } else {
                typeLabel = 'Text only';
            }
            const queuedAt = entry.queued_at ? fmtDateTime(entry.queued_at) : 'unknown';
            const lastAttempt = entry.last_attempt_at ? fmtDateTime(entry.last_attempt_at) : 'not tried yet';
            const reasonHtml = entry.reject_reason ? `<div class="sync-row-reason">${escapeHtml(entry.reject_reason)}</div>` : '';
            const inFlight = _mealSyncInFlightClientIds.has(entry.client_id);
            if (inFlight) row.classList.add('sync-row-in-flight');
            const syncDisabled = inFlight ? ' disabled aria-disabled="true"' : '';
            row.innerHTML = `
                <div class="sync-row-head">
                    <span class="sync-row-title">Meal · ${escapeHtml(titleText)}</span>
                    <span class="sync-status-pill sync-status-${status}">${escapeHtml(mealStatusLabels[status] || 'Pending')}</span>
                </div>
                <div class="sync-row-meta">${escapeHtml(typeLabel)} · saved on this device ${escapeHtml(queuedAt)} · ${entry.attempts || 0} sync attempt${(entry.attempts || 0) === 1 ? '' : 's'} · last try ${escapeHtml(lastAttempt)}</div>
                ${reasonHtml}
                <div class="sync-row-actions">
                    <button class="btn btn-ghost btn-sm" data-meal-sync-discard="${escapeHtml(entry.client_id)}" type="button"${syncDisabled}>Discard</button>
                    <button class="btn btn-primary btn-sm" data-meal-sync-retry="${escapeHtml(entry.client_id)}" type="button"${syncDisabled}>${inFlight ? 'Syncing...' : 'Retry'}</button>
                </div>
            `;
            host.appendChild(row);
        });
        host.querySelectorAll('[data-sync-retry]').forEach((btn) => {
            btn.addEventListener('click', async () => {
                btn.disabled = true;
                btn.textContent = 'Retrying…';
                const res = await syncSingleEntry(btn.dataset.syncRetry);
                await renderSyncQueueModal();
                if (res && res.ok) toast('Workout synced');
                else if (res) toast(`Sync ${res.status || 'failed'}`, 'err');
            });
        });
        host.querySelectorAll('[data-sync-discard]').forEach((btn) => {
            btn.addEventListener('click', () => {
                const clientId = btn.dataset.syncDiscard;
                const entry = loadSyncQueue().find((e) => e.client_workout_id === clientId);
                const needsConfirm = entry && ['rejected', 'conflicted', 'auth_required'].includes(entry.last_status);
                if (needsConfirm && !window.confirm('Discard this queued workout permanently?')) return;
                removeQueueEntry(clientId);
                renderSyncQueueModal();
                toast('Workout discarded from queue');
            });
        });
        host.querySelectorAll('[data-meal-sync-retry]').forEach((btn) => {
            btn.addEventListener('click', async () => {
                btn.disabled = true;
                btn.textContent = 'Syncing…';
                const row = btn.closest('.sync-row');
                if (row) {
                    row.querySelectorAll('[data-meal-sync-retry], [data-meal-sync-discard]').forEach((rowBtn) => {
                        rowBtn.disabled = true;
                        rowBtn.setAttribute('aria-disabled', 'true');
                    });
                }
                const res = await syncSingleMealQueueEntry(btn.dataset.mealSyncRetry);
                await renderSyncQueueModal();
                if (res && res.ok) {
                    toast('Meal synced.');
                } else if (res) {
                    let msg;
                    if (res.status === 'pending') msg = 'Couldn’t reach the server — saved for another try.';
                    else if (res.status === 'auth_required') msg = 'Sign in with the account that saved this meal, then retry.';
                    else if (res.status === 'rejected') msg = 'Server can’t accept this meal. Discard it from the queue.';
                    else if (res.status === 'conflicted') msg = 'Server reported a conflict — discard or retry later.';
                    else if (res.status === 'eviction_failed') msg = 'Synced, but local photos didn’t clear. Tap Discard to remove them.';
                    else msg = 'Meal sync didn’t complete.';
                    toast(msg, 'err');
                }
            });
        });
        host.querySelectorAll('[data-meal-sync-discard]').forEach((btn) => {
            btn.addEventListener('click', async () => {
                const clientId = btn.dataset.mealSyncDiscard;
                if (_mealSyncInFlightClientIds.has(clientId)) {
                    toast('Meal is syncing — wait for this attempt to finish before discarding.', 'warn');
                    return;
                }
                if (!window.confirm('Discard this offline meal? Any photos saved on this device will be deleted from your browser.')) return;
                try {
                    if (_mealSyncInFlightClientIds.has(clientId)) {
                        toast('Meal is syncing — wait for this attempt to finish before discarding.', 'warn');
                        return;
                    }
                    await removeMealQueueEntry(clientId);
                    await renderSyncQueueModal();
                    toast('Meal removed from the offline queue.');
                } catch (e) {
                    toast('Couldn’t discard queued meal', 'err');
                }
            });
        });
    }

    async function completeWorkout() {
        const aw = state.activeWorkout;
        if (!aw) return;
        setActiveWorkoutStatus('', '');
        const exercises = [];
        aw.exercises.forEach((ex, i) => {
            const rows = qsa(`.set-row[data-ex="${i}"]`, $('active-workout-body'));
            const sets = rows.map((r) => ({
                reps: Number(qs('input[data-field="reps"]', r).value || 0),
                weight_lbs: Number(qs('input[data-field="weight"]', r).value || 0),
                rpe: ex.rpe ? Number(ex.rpe) : null,
                done: qs('input[data-field="done"]', r).checked,
                notes: (qs('input[data-field="notes"]', r).value || '').trim(),
            }));
            const hasCheckedSets = sets.some((s) => s.done);
            const completedSets = sets
                .filter((s) => s.reps > 0 && s.weight_lbs >= 0 && (!hasCheckedSets || s.done))
                .map(({ done, ...s }) => s);
            if (completedSets.length) {
                const plannedTargetWeight = plannedTargetNumber(ex.target_weight, ex.target_weight_lbs);
                const plannedTargetReps = plannedTargetNumber(ex.target_reps, ex.reps);
                const plannedTargetSets = plannedTargetNumber(ex.target_sets, ex.sets);
                const exercisePayload = { machine: exerciseName(ex), muscle_group: ex.muscle_group || ex.muscle, sets: completedSets };
                if (plannedTargetWeight !== null) exercisePayload.planned_target_weight = plannedTargetWeight;
                if (plannedTargetReps !== null) exercisePayload.planned_target_reps = plannedTargetReps;
                if (plannedTargetSets !== null) exercisePayload.planned_target_sets = plannedTargetSets;
                exercises.push(exercisePayload);
            }
        });
        if (!exercises.length) {
            const message = 'Validation failed: log at least one set before completing this workout.';
            aw.saveState = { message, variant: 'err' };
            saveActiveWorkoutDraft();
            setActiveWorkoutStatus(message, 'err');
            toast('Log at least one set', 'err');
            return;
        }
        const btn = $('btn-complete-workout');
        if (btn) {
            btn.disabled = true;
            btn.textContent = 'Saving...';
        }
        aw.saveState = { message: 'Saving workout...', variant: '' };
        setActiveWorkoutStatus(aw.saveState.message, aw.saveState.variant);
        const clientWorkoutId = aw.id || `w-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        aw.id = clientWorkoutId;
        saveActiveWorkoutDraft();
        const completePayload = {
            id: clientWorkoutId,
            client_workout_id: clientWorkoutId,
            date: today(),
            recommendation_id: aw.recommendation_id,
            session_type: aw.focus,
            exercises,
            cardio: aw.cardio ? {
                ...aw.cardio,
                completed: Boolean(aw.cardio.completed),
            } : null,
        };
        const totalSets = exercises.reduce((a, ex) => a + ex.sets.length, 0);
        const totalVolume = exercises.reduce((a, ex) => a + ex.sets.reduce((sa, s) => sa + Number(s.weight_lbs || 0) * Number(s.reps || 0), 0), 0);
        const summaryBase = {
            date: today(),
            session_type: aw.focus,
            exercises_count: exercises.length,
            total_sets: totalSets,
            total_volume: Math.round(totalVolume),
            duration_minutes: aw.duration_minutes || 0,
            cardio_completed: Boolean(aw.cardio && aw.cardio.completed),
        };

        function settleQueued(reasonMsg) {
            enqueueOfflineWorkout(completePayload, 'pending');
            clearActiveWorkoutDraft();
            aw.saveState = { message: reasonMsg, variant: '' };
            setActiveWorkoutStatus(reasonMsg, '');
            $('modal-active').hidden = true;
            state.activeWorkout = null;
            clearAdjustIntent();
            invalidateCaches();
            loadTab(state.currentTab);
            openWorkoutSavedConfirm({ ...summaryBase, queued: true, queued_reason: reasonMsg });
            toast('Saved offline — will sync when back online');
        }

        if (!navigator.onLine) {
            settleQueued('Offline — queued for sync.');
            if (btn) { btn.disabled = false; btn.textContent = 'Complete Workout'; }
            return;
        }

        try {
            const result = await postCompleteWorkout(completePayload);
            if (result.ok && (result.syncStatus === 'inserted' || result.syncStatus === 'already_synced')) {
                aw.saveState = { message: 'Workout saved.', variant: 'ok' };
                setActiveWorkoutStatus(aw.saveState.message, aw.saveState.variant);
                const summary = {
                    ...summaryBase,
                    adherence: (result.body && result.body.adherence) || null,
                    duplicate: Boolean(result.body && result.body.duplicate),
                };
                $('modal-active').hidden = true;
                state.activeWorkout = null;
                clearActiveWorkoutDraft();
                clearAdjustIntent();
                invalidateCaches();
                loadTab(state.currentTab);
                openWorkoutSavedConfirm(summary);
            } else {
                if (result.syncStatus === 'pending') {
                    settleQueued('Server unavailable — queued for another sync attempt.');
                    return;
                }
                // Backend could not save it yet — enqueue so user can retry/discard.
                enqueueOfflineWorkout(completePayload, result.syncStatus || 'rejected');
                updateQueueEntry(clientWorkoutId, {
                    last_status: result.syncStatus || 'rejected',
                    last_attempt_at: new Date().toISOString(),
                    attempts: 1,
                    server_response: result.body || null,
                    reject_reason: annotateWorkoutSyncReason(result.reason, result.syncStatus || 'rejected'),
                });
                let msg;
                if (result.syncStatus === 'conflicted') msg = 'Server reported a conflict — see the sync queue.';
                else if (result.syncStatus === 'auth_required') msg = 'Sign in, then retry the workout from the sync queue.';
                else msg = `Save needs review — ${result.reason || 'see the sync queue'}.`;
                aw.saveState = { message: msg, variant: 'err' };
                aw.queuedForSyncReview = true;
                setActiveWorkoutStatus(msg, 'err');
                clearActiveWorkoutDraft();
                toast(result.syncStatus === 'conflicted' ? 'Sync conflict' : 'Workout queued for review', 'err');
            }
        } catch (e) {
            console.error(e);
            settleQueued('Network error — queued for sync.');
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.textContent = 'Complete Workout';
            }
        }
    }

    function openWorkoutSavedConfirm(summary) {
        const modal = $('modal-workout-saved');
        const titleEl = $('saved-title');
        const subEl = $('saved-sub');
        const statsEl = $('saved-stats');
        const adherenceEl = $('saved-adherence');
        const analyzeBtn = $('btn-saved-analyze');
        const dismissBtn = $('btn-saved-dismiss');
        if (!modal || !titleEl || !subEl || !statsEl || !analyzeBtn || !dismissBtn) return;
        const dateLabel = summary.date ? fmtDate(summary.date) : 'today';
        if (summary.queued) {
            titleEl.textContent = 'Queued for sync.';
        } else {
            titleEl.textContent = summary.duplicate ? 'Already logged.' : 'Logged.';
        }
        const focusLabel = summary.session_type ? capitalize(String(summary.session_type).replace(/_/g, ' ')) : 'Workout';
        subEl.textContent = `${focusLabel} · ${dateLabel}`;
        const queuedNoteEl = $('saved-queued-note');
        if (queuedNoteEl) {
            if (summary.queued) {
                queuedNoteEl.hidden = false;
                queuedNoteEl.textContent = summary.queued_reason || 'Queued — will sync when back online.';
            } else {
                queuedNoteEl.hidden = true;
            }
        }
        analyzeBtn.disabled = !!summary.queued;
        analyzeBtn.title = summary.queued ? 'Available after sync completes' : '';
        statsEl.innerHTML = '';
        const cells = [
            { label: 'Exercises', value: String(summary.exercises_count || 0) },
            { label: 'Sets',      value: String(summary.total_sets || 0) },
            { label: 'Volume',    value: summary.total_volume ? `${fmtKilo(summary.total_volume)} lbs` : '—' },
        ];
        if (summary.duration_minutes) cells.push({ label: 'Minutes', value: String(summary.duration_minutes) });
        cells.forEach((c) => {
            const cell = document.createElement('div');
            cell.className = 'saved-stat';
            cell.innerHTML = `<div class="saved-stat-value">${escapeHtml(c.value)}</div><div class="saved-stat-label">${escapeHtml(c.label)}</div>`;
            statsEl.appendChild(cell);
        });
        const adherence = summary.adherence;
        if (adherence && (Array.isArray(adherence.skipped) && adherence.skipped.length
            || Array.isArray(adherence.added) && adherence.added.length
            || Array.isArray(adherence.modified) && adherence.modified.length)) {
            const lines = [];
            if (adherence.skipped && adherence.skipped.length) lines.push(`Skipped: ${adherence.skipped.join(', ')}`);
            if (adherence.added && adherence.added.length) lines.push(`Added: ${adherence.added.join(', ')}`);
            if (adherence.modified && adherence.modified.length) lines.push(`Adjusted ${adherence.modified.length} ${adherence.modified.length === 1 ? 'exercise' : 'exercises'}`);
            adherenceEl.textContent = lines.join(' · ');
            adherenceEl.hidden = false;
        } else {
            adherenceEl.hidden = true;
        }
        const closeBtn = modal.querySelector('.modal-close');
        const backdropHandler = (e) => {
            if (e.target === modal) {
                e.stopImmediatePropagation();
                closeModal(modal);
            }
        };
        function detachBackdrop() {
            modal.removeEventListener('click', backdropHandler, true);
        }
        function dismissToHistory() {
            detachBackdrop();
            modal.hidden = true;
            switchTab('tab-history');
        }
        modal.__fit192Close = dismissToHistory;
        const freshAnalyze = analyzeBtn.cloneNode(true);
        analyzeBtn.parentNode.replaceChild(freshAnalyze, analyzeBtn);
        freshAnalyze.addEventListener('click', () => {
            detachBackdrop();
            modal.hidden = true;
            openAnalyzeModal({ latest: true }, `Analysis · ${dateLabel}`);
        });
        const freshDismiss = dismissBtn.cloneNode(true);
        dismissBtn.parentNode.replaceChild(freshDismiss, dismissBtn);
        freshDismiss.addEventListener('click', () => closeModal(modal));
        if (closeBtn) {
            const freshClose = closeBtn.cloneNode(true);
            freshClose.removeAttribute('data-close-modal');
            closeBtn.parentNode.replaceChild(freshClose, closeBtn);
            freshClose.addEventListener('click', () => closeModal(modal));
        }
        modal.addEventListener('click', backdropHandler, true);
        modal.hidden = false;
    }

    async function openSwap(exIdx, muscle, currentName, source = 'plan') {
        state.swapContext = { exIdx, muscle, currentName, source };
        const modal = $('modal-swap');
        const host = $('swap-alternatives');
        const title = $('swap-modal-title');
        const sub = $('swap-modal-sub');
        title.textContent = `Swap: ${currentName}`;
        sub.textContent = muscle
            ? `Pick a replacement from the ${muscle} library (equipment-filtered).`
            : 'Pick a replacement exercise.';
        host.innerHTML = '<div class="skeleton">Loading alternatives…</div>';
        // FIT-117: reset the custom-swap input + inline error each open so
        // stale text from a prior swap doesn't bleed into the next session.
        const customInput = $('swap-custom-input');
        const customErr = $('swap-custom-error');
        if (customInput) customInput.value = '';
        if (customErr) { customErr.hidden = true; customErr.textContent = ''; }
        modal.hidden = false;

        if (!muscle) {
            host.innerHTML = '<div class="empty">No muscle group on this exercise — can\'t look up alternatives.</div>';
            return;
        }

        let data;
        try {
            data = await api(`/api/exercises/alternatives/${encodeURIComponent(muscle)}`);
        } catch (e) {
            host.innerHTML = '<div class="empty">Couldn\'t load alternatives.</div>';
            return;
        }
        const alts = (data && data.alternatives) || [];
        if (!alts.length) {
            host.innerHTML = `<div class="empty">No alternatives for ${escapeHtml(muscle)} under your current equipment preference. Change equipment in Settings to see more.</div>`;
            return;
        }

        host.innerHTML = '';
        const currentLower = (currentName || '').toLowerCase();
        alts.forEach((alt) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            const isCurrent = alt.name.toLowerCase() === currentLower;
            btn.className = 'swap-row' + (isCurrent ? ' current' : '');
            const equipClass = alt.equipment === 'machine' ? 'machine' : alt.equipment === 'cable' ? 'cable' : '';
            // FIT-105: include the confidence chip on swap rows too, so
            // the user can tell at swap time whether the alt's estimate
            // is shaky before committing to it.
            let loadHintHtml = '';
            if (alt.load_hint && alt.load_hint.inferred_from) {
                const conf = (alt.load_hint.inference_confidence || '').toLowerCase();
                const chip = (conf === 'low' || conf === 'medium' || conf === 'med' || conf === 'high')
                    ? renderLoadConfChip(conf)
                    : '';
                loadHintHtml = `<span class="swap-load-hint">Est. from ${escapeHtml(alt.load_hint.inferred_from)}${chip ? ' ' + chip : ''}</span>`;
            }
            btn.innerHTML = `
                <span>${escapeHtml(alt.name)}${alt.compound ? ' <span class="swap-current-tag">COMPOUND</span>' : ''}${loadHintHtml}</span>
                ${isCurrent ? '<span class="swap-current-tag">CURRENT</span>' : `<span class="swap-row-equip ${equipClass}">${escapeHtml(alt.equipment || '—')}</span>`}
            `;
            if (!isCurrent) {
                btn.addEventListener('click', () => applySwap(exIdx, alt.name, currentName));
            }
            host.appendChild(btn);
        });
    }

    function _finalizeSwap(resp, oldName, newName) {
        if (resp && resp.recommendation) {
            if (!state.dashboard) state.dashboard = {};
            state.dashboard.next_workout = resp.recommendation;
            state.nextWorkout = resp.recommendation;
        }
        closeModal($('modal-swap'));
        toast(`Swapped ${oldName} → ${newName}`, 'ok');
        if (state.swapContext && state.swapContext.source === 'active' && resp && resp.recommendation) {
            const previous = (state.activeWorkout && state.activeWorkout.exercises) || [];
            setActiveWorkoutFromRecommendation(resp.recommendation, previous);
            renderActiveWorkout();
            saveActiveWorkoutDraft();
        } else {
            renderNextWorkout();
        }
    }

    async function applySwap(exIdx, newName, oldName) {
        const host = $('swap-alternatives');
        host.innerHTML = '<div class="skeleton">Swapping…</div>';
        if (state.swapContext && state.swapContext.source === 'active') {
            saveActiveWorkoutDraft();
        }
        try {
            const resp = await api('/api/workout/swap', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ workout_index: 0, exercise_index: exIdx, new_exercise_name: newName }),
            });
            _finalizeSwap(resp, oldName, newName);
        } catch (e) {
            console.error(e);
            host.innerHTML = `<div class="empty">Swap failed — ${escapeHtml(String(e.message || e))}</div>`;
        }
    }

    // FIT-117: free-text swap. The form-submit handler is wired once in
    // wireEvents() and reads state.swapContext at submit time. The same
    // /api/workout/swap endpoint accepts the typed name — for exercises
    // that are in the library but have no direct history, FIT-103's
    // similar-history inference fills in the starter weight + load_hint
    // chip (rendered by setActiveWorkoutFromRecommendation /
    // renderNextWorkout). Errors stay inline so the alternatives picker
    // remains visible for the user to fall back to.
    async function applyCustomSwap() {
        const ctx = state.swapContext;
        if (!ctx) return;
        const input = $('swap-custom-input');
        const errEl = $('swap-custom-error');
        const submitBtn = $('swap-custom-submit');
        if (!input || !errEl || !submitBtn) return;

        const raw = (input.value || '').trim();
        errEl.hidden = true;
        errEl.textContent = '';

        if (!raw) {
            errEl.textContent = 'Enter an exercise name to swap to.';
            errEl.hidden = false;
            input.focus();
            return;
        }
        if (!/[a-z]/i.test(raw)) {
            errEl.textContent = 'Enter a real exercise name (letters required).';
            errEl.hidden = false;
            input.focus();
            return;
        }

        submitBtn.disabled = true;
        const origLabel = submitBtn.textContent;
        submitBtn.textContent = 'Swapping…';
        if (ctx.source === 'active') {
            saveActiveWorkoutDraft();
        }
        try {
            const resp = await api('/api/workout/swap', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ workout_index: 0, exercise_index: ctx.exIdx, new_exercise_name: raw }),
            });
            _finalizeSwap(resp, ctx.currentName, raw);
            input.value = '';
        } catch (e) {
            console.error('applyCustomSwap', e);
            const msg = String((e && e.message) || e || '').toLowerCase();
            let friendly = `Couldn't swap to "${raw}".`;
            if (msg.includes('unknown exercise')) {
                friendly = `"${raw}" isn't in the exercise library yet — pick one from the list above or check the spelling.`;
            } else if (msg.includes('muscle group')) {
                friendly = `"${raw}" isn't a ${ctx.muscle || 'matching'}-group exercise — pick something from the list above.`;
            } else if (msg.includes('equipment')) {
                friendly = `"${raw}" is blocked by your current equipment preference — change it in Settings or pick from the list above.`;
            }
            errEl.textContent = friendly;
            errEl.hidden = false;
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = origLabel;
        }
    }

    function openAdjust() {
        const modal = $('modal-adjust');
        const textarea = $('adjust-constraint');
        const result = $('adjust-result');
        const stateEl = $('adjust-state');
        const preview = $('adjust-plan-preview');
        const banner = $('adjust-restored-banner');
        if (result) result.hidden = true;
        if (preview) { preview.hidden = true; preview.innerHTML = ''; }
        if (stateEl) { stateEl.textContent = ''; stateEl.className = 'adjust-state'; }
        if (banner) banner.hidden = true;

        const saved = loadAdjustIntent();
        if (saved) {
            if (textarea) textarea.value = saved.constraint || '';
            renderAdjustResult(saved, { restored: true, savedAt: saved.saved_at });
        } else {
            if (textarea) textarea.value = '';
            state.adjustedWorkout = null;
        }
        modal.hidden = false;
        setTimeout(() => textarea && textarea.focus(), 60);
    }

    function discardSavedAdjust() {
        clearAdjustIntent();
        state.adjustedWorkout = null;
        const result = $('adjust-result');
        const banner = $('adjust-restored-banner');
        const stateEl = $('adjust-state');
        const textarea = $('adjust-constraint');
        const preview = $('adjust-plan-preview');
        if (result) result.hidden = true;
        if (banner) banner.hidden = true;
        if (stateEl) { stateEl.textContent = 'Adjustment discarded.'; stateEl.className = 'adjust-state'; }
        if (textarea) textarea.value = '';
        if (preview) { preview.hidden = true; preview.innerHTML = ''; }
        if (state.dashboard && state.dashboard.next_workout) {
            // Trigger a re-render so the user sees the current server-canonical plan.
            state.nextWorkout = state.dashboard.next_workout;
            if (state.currentTab === 'tab-workout') renderNextWorkout();
        }
    }

    async function openAnalyzeModal(request, titleOverride) {
        const modal = $('modal-analyze');
        const titleEl = $('analyze-title');
        const loading = $('analyze-loading');
        const content = $('analyze-content');
        const errEl = $('analyze-error');
        if (titleOverride) titleEl.textContent = titleOverride;
        loading.hidden = false;
        loading.textContent = 'AI coach reviewing your session…';
        content.hidden = true;
        errEl.hidden = true;
        modal.hidden = false;

        try {
            const payload = await api('/api/workout/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(request || { latest: true }),
            });

            if (payload.status === 'fallback' && !payload.analysis) {
                loading.hidden = true;
                errEl.hidden = false;
                errEl.textContent = `AI coach unavailable — ${payload.reason || 'try again later'}.`;
                return;
            }

            const w = payload.workout || {};
            const a = payload.analysis || {};
            titleEl.textContent = w.date ? `Analysis · ${fmtDate(w.date)}` : 'Workout Analysis';

            $('analyze-summary').textContent = a.summary || '—';

            const winsEl = $('analyze-wins');
            const concernsEl = $('analyze-concerns');
            winsEl.innerHTML = '';
            concernsEl.innerHTML = '';
            (a.wins || []).forEach((w) => {
                const li = document.createElement('li');
                li.textContent = w;
                winsEl.appendChild(li);
            });
            (a.concerns || []).forEach((c) => {
                const li = document.createElement('li');
                li.textContent = c;
                concernsEl.appendChild(li);
            });
            $('analyze-wins-section').style.display = (a.wins || []).length ? '' : 'none';
            $('analyze-concerns-section').style.display = (a.concerns || []).length ? '' : 'none';

            $('analyze-comparison').textContent = a.comparison || '—';
            if (a.next_session_cue) {
                $('analyze-cue').textContent = a.next_session_cue;
                $('analyze-cue-section').style.display = '';
            } else {
                $('analyze-cue-section').style.display = 'none';
            }

            const m = payload.meta || {};
            const ctx = payload.context_used || {};
            const noteBits = [];
            if (ctx.set_note_count) noteBits.push(`${ctx.set_note_count} set note${ctx.set_note_count === 1 ? '' : 's'}`);
            if (ctx.workout_notes_present) noteBits.push('workout note');
            if (ctx.cardio_notes_present) noteBits.push('cardio note');
            const notesSection = $('analyze-notes-section');
            const notesContext = $('analyze-notes-context');
            if (notesSection && notesContext) {
                if (noteBits.length) {
                    notesContext.textContent = `AI analysis received ${noteBits.join(', ')} for this session.`;
                    notesSection.hidden = false;
                } else {
                    notesContext.textContent = '';
                    notesSection.hidden = true;
                }
            }
            const contextBits = [];
            if (payload.status === 'fallback') contextBits.push('fallback analysis');
            if (ctx.recent_session_count) contextBits.push(`${ctx.recent_session_count} recent sessions`);
            if (ctx.readiness_available) contextBits.push('readiness');
            if (noteBits.length) contextBits.push('notes reviewed');
            contextBits.push(m.model_version || m.model || 'local model');
            if (m.fallback_reason) contextBits.push(m.fallback_reason);
            if (m.elapsed_ms) contextBits.push(`${m.elapsed_ms}ms`);
            if (payload.cache_hit) contextBits.push('cached');
            $('analyze-meta').textContent = contextBits.join(' · ');

            loading.hidden = true;
            content.hidden = false;
        } catch (e) {
            console.error(e);
            loading.hidden = true;
            errEl.hidden = false;
            errEl.textContent = 'Request failed — please try again.';
        }
    }

    async function openAppleSetup() {
        const urlEl = $('apple-webhook-url');
        const detail = $('apple-sync-detail');
        if (detail) detail.textContent = 'Checking…';

        // Setup URL is split from routine status so Settings doesn't fetch
        // token material unless the setup modal is explicitly opened.
        const appleHealthOrigin = (location.hostname.endsWith('.tail6c6490.ts.net') && location.protocol === 'http:')
            ? `https://${location.host}`
            : location.origin;
        let tokenizedUrl = `${appleHealthOrigin}/api/apple-health/sync`;
        let status = null;
        try {
            status = await api('/api/apple-health/sync/status');
        } catch {}
        try {
            const setup = await api('/api/apple-health/sync/setup-url');
            if (setup && setup.webhook_url) tokenizedUrl = setup.webhook_url;
        } catch {}

        if (urlEl) urlEl.textContent = tokenizedUrl;

        if (detail) {
            if (status && status.last_sync) {
                const lastExport = status.last_attempt || status.last_sync;
                const last = parseServerDateTime(lastExport);
                const days = last ? Math.floor((Date.now() - last.getTime()) / 86400000) : 0;
                detail.textContent = `Last accepted export ${fmtDateTime(lastExport)} · ${days}d ago · ${status.total_records || 0} records`;
            } else if (status && status.setup_configured) {
                detail.textContent = 'Webhook is configured — waiting for Health Auto Export to post data.';
            } else if (status) {
                detail.textContent = 'No syncs yet — Health Auto Export has not posted.';
            } else {
                detail.textContent = 'Sync endpoint not reachable.';
            }
        }
        $('modal-apple').hidden = false;
    }

    const ADJUST_INTENT_KEY = 'fit24:adjust-intent:v1';
    const ADJUST_INTENT_TTL_MS = 24 * 60 * 60 * 1000;
    const ADJUST_KIND_TABLE = {
        changed:   { label: 'Plan updated',     cls: 'adjust-kind-changed',   stateMsg: 'Updated. Review the new plan below or start it now.', stateCls: 'adjust-state ok' },
        unchanged: { label: 'No net change',    cls: 'adjust-kind-unchanged', stateMsg: 'Coach considered the change but kept the plan.',       stateCls: 'adjust-state' },
        refused:   { label: 'Coach left as is', cls: 'adjust-kind-refused',   stateMsg: 'Coach declined to change the plan.',                   stateCls: 'adjust-state' },
    };

    function saveAdjustIntent(constraint, payload) {
        try {
            const entry = {
                saved_at: new Date().toISOString(),
                constraint: typeof constraint === 'string' ? constraint : '',
                result_kind: payload && payload.result_kind ? payload.result_kind : null,
                summary: payload && payload.summary ? payload.summary : '',
                applied_notes: Array.isArray(payload && payload.applied_notes) ? payload.applied_notes : [],
                recommendation: payload && payload.recommendation ? payload.recommendation : null,
                meta: payload && payload.meta ? payload.meta : {},
                cache_hit: !!(payload && payload.cache_hit),
            };
            sessionStorage.setItem(ADJUST_INTENT_KEY, JSON.stringify(entry));
        } catch (e) {
            console.warn('saveAdjustIntent failed', e);
        }
    }

    function loadAdjustIntent() {
        try {
            const raw = sessionStorage.getItem(ADJUST_INTENT_KEY);
            if (!raw) return null;
            const entry = JSON.parse(raw);
            const savedAt = entry && entry.saved_at ? Date.parse(entry.saved_at) : 0;
            if (!savedAt || Date.now() - savedAt > ADJUST_INTENT_TTL_MS) {
                clearAdjustIntent();
                return null;
            }
            return entry;
        } catch (e) {
            return null;
        }
    }

    function clearAdjustIntent() {
        try { sessionStorage.removeItem(ADJUST_INTENT_KEY); } catch (e) {}
    }

    function renderAdjustResult(payload, opts = {}) {
        const stateEl = $('adjust-state');
        const result = $('adjust-result');
        const summaryEl = $('adjust-summary');
        const notesEl = $('adjust-notes');
        const metaEl = $('adjust-meta');
        const restoredBanner = $('adjust-restored-banner');
        if (!stateEl || !result || !summaryEl || !notesEl || !metaEl) return;
        const notes = Array.isArray(payload && payload.applied_notes) ? payload.applied_notes : [];
        const rawKind = (payload && payload.result_kind) || (notes.length ? 'changed' : 'unchanged');
        const kind = ADJUST_KIND_TABLE[rawKind] ? rawKind : 'changed';
        const kindMeta = ADJUST_KIND_TABLE[kind];
        const modelSummary = (payload && payload.summary || '').trim();
        const fallbackSummary = kind === 'changed'
            ? 'Adjustment applied.'
            : kind === 'refused'
                ? 'The coach decided no structural change was warranted.'
                : 'The constraint was within the algorithm\'s envelope; no net change applied.';
        const summaryText = modelSummary || fallbackSummary;
        summaryEl.innerHTML = `<span class="adjust-kind ${kindMeta.cls}">${escapeHtml(kindMeta.label)}</span><span class="adjust-summary-text">${escapeHtml(summaryText)}</span>`;
        if (kind === 'changed' && notes.length) {
            notesEl.innerHTML = '<ul>' + notes.map((n) => `<li>${escapeHtml(n)}</li>`).join('') + '</ul>';
        } else if (kind === 'unchanged') {
            notesEl.innerHTML = '<div class="dim">Safety rails clamped the requested change to zero net effect. The original plan stands.</div>';
        } else if (kind === 'refused') {
            notesEl.innerHTML = '<div class="dim">No edits applied — the model returned an empty intent. See the explanation above.</div>';
        } else {
            notesEl.innerHTML = '';
        }
        const meta = (payload && payload.meta) || {};
        const cacheHit = !!(payload && payload.cache_hit);
        metaEl.textContent = `${meta.model_version || meta.model || 'local model'} · ${meta.elapsed_ms || '?'} ms${cacheHit ? ' · cached' : ''}`;
        result.hidden = false;
        if (restoredBanner) restoredBanner.hidden = !opts.restored;
        if (opts.restored && opts.savedAt) {
            const restoredAtEl = $('adjust-restored-at');
            if (restoredAtEl) {
                const t = new Date(opts.savedAt);
                restoredAtEl.textContent = isNaN(t.getTime()) ? '' : fmtDateTime(t.toISOString());
            }
        }
        stateEl.textContent = kindMeta.stateMsg;
        stateEl.className = kindMeta.stateCls;
        if (payload && payload.recommendation) {
            if (!state.dashboard) state.dashboard = {};
            state.dashboard.next_workout = payload.recommendation;
            state.nextWorkout = payload.recommendation;
            state.adjustedWorkout = payload.recommendation;
            renderAdjustedPlanPreview(payload.recommendation);
            // FIT-179: when the user is mid-workout, fold the adjusted plan
            // into the active workout so logged rows for exercises that remain
            // in the plan survive. Only worth doing when something changed.
            if (state.activeWorkout && kind === 'changed' && !opts.restored) {
                const previous = Array.isArray(state.activeWorkout.exercises)
                    ? state.activeWorkout.exercises
                    : [];
                applyAdjustedRecommendationToActiveWorkout(payload.recommendation, previous);
                renderActiveWorkout();
                if (typeof toast === 'function') toast('Adjusted plan applied.', 'ok');
            }
        }
        if (state.currentTab === 'tab-workout') {
            renderNextWorkout();
        }
    }

    async function submitAdjust() {
        const textarea = $('adjust-constraint');
        const btn = $('btn-adjust-submit');
        const stateEl = $('adjust-state');
        const result = $('adjust-result');
        const summaryEl = $('adjust-summary');
        const notesEl = $('adjust-notes');
        const metaEl = $('adjust-meta');
        const previewEl = $('adjust-plan-preview');
        const constraint = (textarea.value || '').trim();
        if (!constraint) { stateEl.textContent = 'Tell the coach what to adjust.'; stateEl.className = 'adjust-state err'; return; }
        if (constraint.length > 280) { stateEl.textContent = 'Keep it under 280 chars.'; stateEl.className = 'adjust-state err'; return; }

        btn.disabled = true;
        btn.textContent = 'Consulting coach…';
        stateEl.textContent = 'Calling local LM Studio model (usually 2–8s)…';
        stateEl.className = 'adjust-state';
        result.hidden = true;
        if (previewEl) { previewEl.hidden = true; previewEl.innerHTML = ''; }

        let finalBtnLabel = 'Apply Another Adjustment';
        try {
            const payload = await api('/api/workout/adjust', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ constraint }),
            });

            if (payload.status === 'fallback') {
                stateEl.textContent = 'AI coach unavailable — plan unchanged. ' + (payload.reason || '');
                stateEl.className = 'adjust-state err';
                finalBtnLabel = 'Retry';
                return;
            }

            renderAdjustResult(payload, { restored: false });
            saveAdjustIntent(constraint, payload);
        } catch (e) {
            console.error(e);
            stateEl.textContent = 'Request failed — keeping the original plan.';
            stateEl.className = 'adjust-state err';
            finalBtnLabel = 'Retry';
        } finally {
            btn.disabled = false;
            btn.textContent = finalBtnLabel;
        }
    }

    // --- Init ----------------------------------------------------
    function wireEvents() {
        // Tab nav
        qsa('.tab-btn').forEach((btn) => {
            btn.addEventListener('click', () => switchTab(btn.getAttribute('data-tab')));
            btn.addEventListener('keydown', handleTabKeydown);
        });

        // Log segmented
        qsa('.seg-btn').forEach((btn) => {
            btn.addEventListener('click', () => {
                const t = btn.getAttribute('data-log-type');
                qsa('.seg-btn').forEach((b) => b.classList.toggle('active', b === btn));
                qsa('.log-panel').forEach((p) => p.classList.toggle('active', p.id === `panel-${t}`));
            });
        });

        // RPE selector
        qsa('#rpe-row button').forEach((b) => {
            b.addEventListener('click', () => {
                qsa('#rpe-row button').forEach((x) => x.classList.toggle('active', x === b));
                $('log-rpe').value = b.dataset.rpe;
            });
        });

        // Range chips
        qsa('#history-range-chips .chip-btn').forEach((c) => {
            c.addEventListener('click', () => {
                state.ranges.history = Number(c.dataset.range);
                qsa('#history-range-chips .chip-btn').forEach((x) => x.classList.toggle('active', x === c));
                renderHistory();
            });
        });
        qsa('#stats-range-chips .chip-btn').forEach((c) => {
            c.addEventListener('click', () => {
                state.ranges.stats = Number(c.dataset.range);
                qsa('#stats-range-chips .chip-btn').forEach((x) => x.classList.toggle('active', x === c));
                renderStats();
            });
        });

        // Log buttons
        $('btn-log-strength') && $('btn-log-strength').addEventListener('click', logStrength);
        $('btn-log-cardio') && $('btn-log-cardio').addEventListener('click', logCardio);
        $('btn-log-recovery') && $('btn-log-recovery').addEventListener('click', logRecovery);
        $('btn-log-body') && $('btn-log-body').addEventListener('click', logBody);

        // Actions
        $('btn-start-workout') && $('btn-start-workout').addEventListener('click', startWorkout);
        $('btn-start-workout-2') && $('btn-start-workout-2').addEventListener('click', startWorkout);
        $('btn-adjust-plan') && $('btn-adjust-plan').addEventListener('click', openAdjust);
        $('btn-adjust-plan-2') && $('btn-adjust-plan-2').addEventListener('click', openAdjust);
        // FIT-179: Adjust Plan entry point from inside the active workout.
        $('btn-adjust-plan-active') && $('btn-adjust-plan-active').addEventListener('click', openAdjust);
        $('btn-adjust-submit') && $('btn-adjust-submit').addEventListener('click', submitAdjust);
        $('btn-adjust-discard') && $('btn-adjust-discard').addEventListener('click', discardSavedAdjust);
        qsa('.chip-preset').forEach((b) => b.addEventListener('click', () => {
            const ta = $('adjust-constraint');
            if (ta) { ta.value = b.dataset.preset || ''; ta.focus(); }
        }));
        $('btn-complete-workout') && $('btn-complete-workout').addEventListener('click', completeWorkout);
        $('sync-banner') && $('sync-banner').addEventListener('click', openSyncQueueModal);
        $('btn-sync-retry-all') && $('btn-sync-retry-all').addEventListener('click', async () => {
            await Promise.all([flushSyncQueue(), flushMealSyncQueue()]);
            await renderSyncQueueModal();
        });
        $('btn-sync-oura') && $('btn-sync-oura').addEventListener('click', syncOura);
        $('btn-connect-whoop') && $('btn-connect-whoop').addEventListener('click', connectWhoop);
        $('btn-sync-whoop') && $('btn-sync-whoop').addEventListener('click', syncWhoop);
        $('btn-disconnect-whoop') && $('btn-disconnect-whoop').addEventListener('click', disconnectWhoop);
        $('btn-export') && $('btn-export').addEventListener('click', downloadExport);
        $('btn-import') && $('btn-import').addEventListener('click', () => $('import-file').click());
        $('import-file') && $('import-file').addEventListener('change', (e) => importBackupFile(e.target.files && e.target.files[0]));

        // FIT-107: dashboard "View food log" button opens the food-log sheet
        $('btn-view-food-log') && $('btn-view-food-log').addEventListener('click', openFoodLogSheet);
        $('btn-reco-sources') && $('btn-reco-sources').addEventListener('click', openRecoSourcesModal);

        // Close modals
        qsa('[data-close-modal]').forEach((b) => b.addEventListener('click', () => {
            const modal = b.closest('.modal');
            if (modal && modal.id === 'modal-active') return;
            if (modal) closeModal(modal);
        }));
        qsa('.modal').forEach((m) => {
            m.addEventListener('click', (e) => {
                if (m.id === 'modal-active') return;
                if (e.target === m) closeModal(m);
            });
        });
        watchModalFocus();

        // AI status button (top right)
        $('btn-ai-status') && $('btn-ai-status').addEventListener('click', toggleAiPopover);
        document.addEventListener('click', closeAiPopoverOnOutsideClick, true);

        // FIT-117: custom-exercise swap form.
        const swapForm = $('swap-custom-form');
        if (swapForm) {
            swapForm.addEventListener('submit', (e) => {
                e.preventDefault();
                applyCustomSwap();
            });
        }
    }

    // --- AI coach status (header button) -----------------------
    let aiStatusTimer = null;

    async function refreshAiStatus() {
        const dot = $('ai-status-dot');
        if (!dot) return;
        try {
            const h = await api('/api/ai/health');
            if (h.reachable && h.model_loaded) dot.className = 'ai-dot ok';
            else if (h.reachable) dot.className = 'ai-dot warn';
            else dot.className = 'ai-dot err';
        } catch {
            dot.className = 'ai-dot err';
        }
    }

    async function toggleAiPopover() {
        const pop = $('popover-ai');
        if (!pop) return;
        if (!pop.hidden) { pop.hidden = true; return; }

        pop.hidden = false;
        $('pop-reachable').textContent = '…';
        $('pop-model-loaded').textContent = '…';
        $('pop-requests').textContent = '…';
        $('pop-latency').textContent = '…';
        $('pop-cache').textContent = '…';
        $('pop-fallbacks').textContent = '…';
        $('pop-foot').textContent = 'Checking LM Studio…';

        try {
            const [h, m] = await Promise.all([
                api('/api/ai/health'),
                api('/api/ai/metrics?hours=24').catch(() => null),
            ]);
            $('pop-reachable').textContent = h.reachable ? 'Yes' : 'No';
            $('pop-model-loaded').textContent = h.model_loaded ? 'Yes' : h.reachable ? 'No' : '—';
            if (m) {
                $('pop-requests').textContent = m.adjust_requests || 0;
                $('pop-latency').textContent = m.avg_latency_ms ? `${(m.avg_latency_ms / 1000).toFixed(2)}s` : '—';
                $('pop-cache').textContent = m.adjust_requests ? `${m.cache_hit_pct}%` : '—';
                const fb = m.fallbacks || 0;
                $('pop-fallbacks').textContent = fb;
                if (fb > 0 && m.recent) {
                    const lastFallback = m.recent.find((r) => r.outcome === 'fallback');
                    $('pop-foot').textContent = lastFallback
                        ? `Last fallback: ${lastFallback.reason || 'unknown'}`
                        : h.reachable ? 'LM Studio reachable' : 'LM Studio unreachable';
                } else {
                    $('pop-foot').textContent = h.reachable ? 'LM Studio reachable · all calls clean 24h' : 'LM Studio unreachable';
                }
            } else {
                $('pop-foot').textContent = h.reachable ? 'LM Studio reachable · metrics unavailable' : 'LM Studio unreachable';
            }
        } catch (e) {
            $('pop-foot').textContent = 'Status check failed.';
        }
    }

    function closeAiPopoverOnOutsideClick(ev) {
        const pop = $('popover-ai');
        const btn = $('btn-ai-status');
        if (!pop || pop.hidden) return;
        if (pop.contains(ev.target) || (btn && btn.contains(ev.target))) return;
        pop.hidden = true;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // FIT-60 — Universal meal composer (text or photo → AI auto-log).
    // Talks to POST /api/meal-intake (FIT-57 contract).
    // ─────────────────────────────────────────────────────────────────────────
    const MEAL_DRAFT_KEY = 'fit60_meal_draft';
    const MEAL_UNDO_MS = 30_000;
    // FIT-138: per-meal photo cap. Backend mirrors this at
    // _MEAL_INTAKE_MAX_IMAGE_COUNT; both must stay in sync.
    const MEAL_MAX_PHOTOS = 4;
    const MEAL_MAX_IMAGE_BYTES = 6 * 1024 * 1024;
    // FIT-138: aggregate-byte cap mirrored to backend's
    // _MEAL_INTAKE_MAX_AGGREGATE_BYTES. Enforced client-side so the
    // user gets immediate feedback rather than a wasted 413 round-trip.
    const MEAL_MAX_AGGREGATE_BYTES = 18 * 1024 * 1024;
    const mealComposerState = {
        // FIT-138: arrays for multi-photo capture; one submission = one meal.
        imageFiles: [],
        imagePreviewUrls: [],
        // FIT-138: in-composer retry state machine. draftClientId is
        // generated on first submit attempt and reused across transient
        // retries so the server idempotency contract (same client_id ⇒
        // same row) prevents duplicate pending entries. Cleared on
        // success, 4xx, or material draft change (text edit / photo
        // add or remove).
        draftClientId: null,
        lastSubmitFailedTransient: false,
        submitting: false,
        barcodeSubmitting: false,
        barcodeStream: null,
        barcodeDetector: null,
        barcodeScanRaf: null,
        barcodeScanLastAt: 0,
        barcodeScanToken: 0,
        barcodeDraftClientId: null,
        barcodeDraftValue: '',
        barcodeUnavailable: false,
        backendUnavailable: false,
        pending: [],
    };

    function mealComposerEls() {
        return {
            form: $('meal-composer'),
            text: $('meal-composer-text'),
            image: $('meal-composer-image'),
            scan: $('meal-composer-scan'),
            barcodePanel: $('meal-composer-barcode'),
            barcodeVideo: $('meal-composer-barcode-video'),
            barcodeInput: $('meal-composer-barcode-input'),
            barcodeSubmit: $('meal-composer-barcode-submit'),
            barcodeClose: $('meal-composer-barcode-close'),
            barcodeStatus: $('meal-composer-barcode-status'),
            submit: $('meal-composer-submit'),
            thumbs: $('meal-composer-thumbs'),
            retention: $('meal-composer-retention'),
            retry: $('meal-composer-retry'),
            offline: $('meal-composer-offline'),
            error: $('meal-composer-error'),
            status: $('meal-composer-status'),
            pendingList: $('meal-pending-list'),
        };
    }

    function newMealClientId() {
        if (window.crypto && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
        return 'meal-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
    }

    function setMealComposerError(msg) {
        const { error } = mealComposerEls();
        if (!error) return;
        if (!msg) { error.hidden = true; error.textContent = ''; return; }
        error.textContent = msg;
        error.hidden = false;
    }

    function refreshMealSubmitState() {
        const { text, submit } = mealComposerEls();
        if (!submit) return;
        const hasText = text && text.value.trim().length > 0;
        const hasImage = mealComposerState.imageFiles.length > 0;
        const online = typeof navigator === 'undefined' || navigator.onLine !== false;
        const blocked = mealComposerState.submitting || mealComposerState.barcodeSubmitting || mealComposerState.backendUnavailable;
        const enabled = (hasText || hasImage) && !blocked;
        submit.disabled = !enabled;
        const { scan } = mealComposerEls();
        if (scan) scan.disabled = blocked || mealComposerState.barcodeUnavailable;
        // FIT-138: surface multi-photo processing count while in-flight.
        // FIT-145: when offline, the submit path enqueues locally instead
        // of uploading, so reflect that in the loading label.
        if (mealComposerState.submitting || mealComposerState.barcodeSubmitting) {
            if (online) {
                submit.textContent = mealComposerState.barcodeSubmitting
                    ? 'Looking up…'
                    : hasImage
                    ? `Processing ${mealComposerState.imageFiles.length} photo${mealComposerState.imageFiles.length === 1 ? '' : 's'}…`
                    : 'Logging…';
            } else {
                submit.textContent = 'Saving offline…';
            }
        } else {
            submit.textContent = online ? 'Log' : 'Save offline';
        }
    }

    function saveMealDraft() {
        try {
            const { text } = mealComposerEls();
            const draft = { text: text ? text.value : '', has_image: mealComposerState.imageFiles.length > 0 };
            if (draft.text || draft.has_image) localStorage.setItem(MEAL_DRAFT_KEY, JSON.stringify(draft));
            else localStorage.removeItem(MEAL_DRAFT_KEY);
        } catch (_) { /* storage may be unavailable */ }
    }

    function clearMealDraft() {
        try { localStorage.removeItem(MEAL_DRAFT_KEY); } catch (_) {}
    }

    function loadMealDraft() {
        try {
            const raw = localStorage.getItem(MEAL_DRAFT_KEY);
            if (!raw) return;
            const draft = JSON.parse(raw);
            const { text } = mealComposerEls();
            if (text && draft && typeof draft.text === 'string') text.value = draft.text;
        } catch (_) {}
    }

    // FIT-138: revoke all preview URLs, clear array state, reset the
    // file input. Called on submit success, accept/discard cleanup, and
    // backend-unavailable transitions.
    function clearMealComposerImages() {
        const { image } = mealComposerEls();
        mealComposerState.imagePreviewUrls.forEach((url) => {
            try { URL.revokeObjectURL(url); } catch (_) { /* already revoked */ }
        });
        mealComposerState.imageFiles = [];
        mealComposerState.imagePreviewUrls = [];
        if (image) image.value = '';
        renderMealComposerThumbs();
        refreshMealSubmitState();
    }

    // FIT-138: render the multi-photo thumb strip. Each thumb wraps its
    // own × remove button which revokes its preview URL and splices
    // both arrays. Resets draftClientId on material change so the next
    // submit uses a fresh client_id.
    function renderMealComposerThumbs() {
        const { thumbs, retention } = mealComposerEls();
        if (!thumbs) return;
        thumbs.innerHTML = '';
        const count = mealComposerState.imageFiles.length;
        if (count === 0) {
            thumbs.hidden = true;
            if (retention) retention.hidden = true;
            return;
        }
        thumbs.hidden = false;
        if (retention) retention.hidden = false;
        for (let i = 0; i < count; i += 1) {
            const url = mealComposerState.imagePreviewUrls[i];
            const file = mealComposerState.imageFiles[i];
            const thumb = document.createElement('div');
            thumb.className = 'meal-composer-thumb';
            thumb.setAttribute('role', 'listitem');
            const img = document.createElement('img');
            img.src = url;
            img.alt = `Attached photo ${i + 1} of ${count}`;
            const remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'meal-composer-thumb-remove';
            // FIT-219 item 4: distinct, descriptive label per thumb so each
            // remove button is individually identifiable (position + filename)
            // rather than a shared generic "Remove photo N".
            const fileName = (file && file.name) ? ` (${file.name})` : '';
            remove.setAttribute('aria-label', `Remove photo ${i + 1} of ${count}${fileName}`);
            remove.textContent = '✕';
            remove.addEventListener('click', () => removeMealComposerImage(i));
            thumb.appendChild(img);
            thumb.appendChild(remove);
            thumbs.appendChild(thumb);
        }
    }

    function removeMealComposerImage(index) {
        const url = mealComposerState.imagePreviewUrls[index];
        if (url) {
            try { URL.revokeObjectURL(url); } catch (_) {}
        }
        mealComposerState.imageFiles.splice(index, 1);
        mealComposerState.imagePreviewUrls.splice(index, 1);
        // Material draft change: invalidate the in-flight draftClientId
        // so the next submit is a fresh attempt, not a same-id retry.
        mealComposerState.draftClientId = null;
        mealComposerState.lastSubmitFailedTransient = false;
        renderMealComposerThumbs();
        refreshMealSubmitState();
        saveMealDraft();
    }

    // FIT-138: validate and append newly-selected files to the
    // mealComposerState arrays. Enforces per-file mimetype + size, the
    // 4-photo count cap, and the 18 MB aggregate cap. Any rejection
    // raises an error message but does NOT discard already-accepted
    // files.
    function onMealComposerImageSelected(fileList) {
        if (!fileList || fileList.length === 0) return;
        setMealComposerError(null);
        const incoming = Array.from(fileList);
        const accepted = [];
        const remainingSlots = MEAL_MAX_PHOTOS - mealComposerState.imageFiles.length;
        let aggregateBytes = mealComposerState.imageFiles.reduce(
            (sum, f) => sum + (f && f.size ? f.size : 0),
            0,
        );
        let rejectedTooMany = 0;
        let rejectedTooLarge = 0;
        let rejectedWrongType = 0;
        let rejectedAggregate = 0;
        for (const file of incoming) {
            if (accepted.length >= remainingSlots) {
                rejectedTooMany += 1;
                continue;
            }
            if (!file || !/^image\//.test(file.type)) {
                rejectedWrongType += 1;
                continue;
            }
            if (file.size > MEAL_MAX_IMAGE_BYTES) {
                rejectedTooLarge += 1;
                continue;
            }
            if (aggregateBytes + file.size > MEAL_MAX_AGGREGATE_BYTES) {
                rejectedAggregate += 1;
                continue;
            }
            aggregateBytes += file.size;
            accepted.push(file);
        }
        for (const file of accepted) {
            mealComposerState.imageFiles.push(file);
            mealComposerState.imagePreviewUrls.push(URL.createObjectURL(file));
        }
        if (accepted.length > 0) {
            // Adding photos is a material change too.
            mealComposerState.draftClientId = null;
            mealComposerState.lastSubmitFailedTransient = false;
        }
        const errors = [];
        if (rejectedTooMany) {
            errors.push(`Only ${MEAL_MAX_PHOTOS} photos per meal — extra ${rejectedTooMany === 1 ? 'photo' : 'photos'} ignored.`);
        }
        if (rejectedTooLarge) {
            errors.push(`${rejectedTooLarge === 1 ? 'A photo is' : `${rejectedTooLarge} photos are`} over 6 MB — pick smaller ${rejectedTooLarge === 1 ? 'one' : 'ones'}.`);
        }
        if (rejectedWrongType) {
            errors.push(`${rejectedWrongType === 1 ? 'A file' : `${rejectedWrongType} files`} ${rejectedWrongType === 1 ? "isn't" : "aren't"} an image.`);
        }
        if (rejectedAggregate) {
            errors.push(`Photos exceed 18 MB total — pick smaller ones.`);
        }
        if (errors.length) setMealComposerError(errors.join(' '));
        renderMealComposerThumbs();
        refreshMealSubmitState();
        saveMealDraft();
    }

    function setMealBackendUnavailable(message) {
        mealComposerState.backendUnavailable = true;
        const { form, status, submit, text, image, scan } = mealComposerEls();
        if (form) form.classList.add('meal-composer-disabled');
        if (status) {
            status.classList.remove('meal-composer-status--provenance');
            status.hidden = false;
            status.textContent = message || 'Meal intake coming soon — backend not yet enabled.';
        }
        if (submit) submit.disabled = true;
        if (text) text.disabled = true;
        if (image) image.disabled = true;
        if (scan) scan.disabled = true;
    }

    function setMealBackendAvailable() {
        mealComposerState.backendUnavailable = false;
        const { form, status, text, image, scan } = mealComposerEls();
        if (form) form.classList.remove('meal-composer-disabled');
        if (status) {
            status.classList.remove('meal-composer-status--provenance');
            status.hidden = true;
            status.textContent = '';
        }
        if (text) text.disabled = false;
        if (image) image.disabled = false;
        if (scan) scan.disabled = false;
        refreshMealSubmitState();
    }

    function setMealBarcodeUnavailable(message) {
        mealComposerState.barcodeUnavailable = true;
        stopMealBarcodeScanner();
        const { scan, barcodeInput, barcodeSubmit } = mealComposerEls();
        if (scan) scan.disabled = true;
        if (barcodeInput) barcodeInput.disabled = true;
        if (barcodeSubmit) barcodeSubmit.disabled = true;
        setMealBarcodeStatus(message || 'Barcode lookup is not enabled yet. You can still log meals with text or photos.');
    }

    function clearMealComposerStatus(clientId = null) {
        if (mealComposerState.backendUnavailable) return;
        const { status } = mealComposerEls();
        if (!status) return;
        if (clientId && status.dataset.provenanceClientId !== String(clientId)) return;
        status.classList.remove('meal-composer-status--provenance');
        status.hidden = true;
        status.textContent = '';
        delete status.dataset.provenanceClientId;
    }

    function mealEstimateChip(estimate) {
        if (!estimate) return 'Logged.';
        const item = estimate.item_name || 'Meal';
        const portion = estimate.portion_description ? ` (${estimate.portion_description})` : '';
        const parts = [item + portion];
        if (Number.isFinite(Number(estimate.calories))) parts.push(`${Math.round(estimate.calories)} kcal`);
        const macroBits = [];
        if (Number.isFinite(Number(estimate.protein_g))) macroBits.push(`${Math.round(estimate.protein_g)}P`);
        if (Number.isFinite(Number(estimate.carbs_g))) macroBits.push(`${Math.round(estimate.carbs_g)}C`);
        if (Number.isFinite(Number(estimate.fat_g))) macroBits.push(`${Math.round(estimate.fat_g)}F`);
        if (macroBits.length) parts.push(macroBits.join('/'));
        return 'Logged: ' + parts.join(' · ');
    }

    // FIT-97 AC1: build an entry object in the shape openMealDetailModal
    // expects, from the /api/meal-intake (or retry) auto-log payload. The
    // food_log row is canonical (persisted by the meal-intake endpoint), so
    // start from it and fall back to estimate fields for anything food_log
    // omits. client_id is passed in because the auto-log path knows it from
    // the call context, not always echoed on food_log.
    function mealEntryFromIntakePayload(payload, clientId) {
        const estimate = (payload && payload.estimate) || {};
        const foodLog = (payload && payload.food_log) || {};
        const pick = (field) => (foodLog[field] != null ? foodLog[field] : estimate[field]);
        return {
            client_id: foodLog.client_id || clientId || null,
            item_name: pick('item_name'),
            portion_description: pick('portion_description'),
            logged_at: foodLog.logged_at || null,
            source: pick('source'),
            confidence: pick('confidence'),
            from_image: pick('from_image'),
            calories: pick('calories'),
            protein_g: pick('protein_g'),
            carbs_g: pick('carbs_g'),
            fat_g: pick('fat_g'),
            sodium_mg: pick('sodium_mg'),
            correction_state: foodLog.correction_state || estimate.correction_state || null,
        };
    }

    async function postMealUndo(clientId) {
        try {
            await api(`/api/meal-intake/${encodeURIComponent(clientId)}`, { method: 'DELETE' });
            clearMealComposerStatus(clientId);
            toast('Meal removed', 'ok');
        } catch (e) {
            console.error(e);
            toast('Undo failed', 'err');
        } finally {
            refreshMacroCard();
        }
    }

    // FIT-219 (a11y items 1+2): the pending/review list is rebuilt via
    // innerHTML on essentially every interaction, which destroyed whatever
    // control had keyboard focus and dropped focus to <body> mid-task. Snapshot
    // the focused control's identity (owning card + a within-card selector +
    // text caret) before the rebuild and restore it after; and when a brand-new
    // card is added, move focus to it so keyboard users land on the thing they
    // just created.
    function captureMealPendingFocus(pendingList) {
        const active = document.activeElement;
        if (!active || !pendingList.contains(active)) return null;
        const card = active.closest('.meal-pending-row');
        if (!card) return null;
        const cardKey = card.getAttribute('data-meal-id')
            ? `meal-id="${card.getAttribute('data-meal-id')}"`
            : (card.getAttribute('data-client-id')
                ? `client-id="${card.getAttribute('data-client-id')}"`
                : null);
        if (!cardKey) return null;
        let controlSel = null;
        if (active.hasAttribute('data-field')) {
            controlSel = `[data-field="${active.getAttribute('data-field')}"]`;
        } else if (active.hasAttribute('data-action')) {
            controlSel = `[data-action="${active.getAttribute('data-action')}"]`;
        } else if (active === card) {
            controlSel = ':scope';
        }
        if (!controlSel) return null;
        const itemEl = active.closest('.meal-review-v2-item');
        const itemId = itemEl ? itemEl.getAttribute('data-item-id') : null;
        let selStart = null;
        let selEnd = null;
        let value = null;
        if (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA') {
            // Preserve the in-progress value so the innerHTML rebuild doesn't wipe
            // a mid-edit text field out from under the user.
            value = active.value;
            // selectionStart throws on input types that don't support it (number).
            try { selStart = active.selectionStart; selEnd = active.selectionEnd; } catch (_) {}
        }
        return { cardKey, controlSel, itemId, selStart, selEnd, value };
    }

    function restoreMealPendingFocus(pendingList, snap) {
        if (!snap) return false;
        const card = pendingList.querySelector(`.meal-pending-row[data-${snap.cardKey}]`);
        if (!card) return false;
        let scope = card;
        if (snap.itemId) {
            const itemEl = card.querySelector(`.meal-review-v2-item[data-item-id="${snap.itemId}"]`);
            if (itemEl) scope = itemEl;
        }
        const target = snap.controlSel === ':scope' ? card : scope.querySelector(snap.controlSel);
        if (!target) return false;
        // Restore a mid-edit value before focusing so the caret lands correctly.
        // Only when the rebuilt field came back empty/different and isn't disabled,
        // so we never clobber a fresh value the re-render legitimately applied.
        if (snap.value != null && !target.disabled
            && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')
            && target.value !== snap.value && target.value === '') {
            target.value = snap.value;
        }
        try { target.focus({ preventScroll: true }); } catch (_) { target.focus(); }
        if (snap.selStart != null && typeof target.setSelectionRange === 'function') {
            try { target.setSelectionRange(snap.selStart, snap.selEnd); } catch (_) {}
        }
        return true;
    }

    // Don't steal focus on the initial hydration render (page load); only move
    // focus to a genuinely new card for user-initiated renders.
    let mealPendingFirstRenderDone = false;

    function renderMealPendingList() {
        const { pendingList } = mealComposerEls();
        if (!pendingList) return;
        const focusSnap = captureMealPendingFocus(pendingList);
        const isFirstRender = !mealPendingFirstRenderDone;
        mealPendingFirstRenderDone = true;
        const priorKeys = new Set();
        pendingList.querySelectorAll('.meal-pending-row').forEach((el) => {
            const k = el.getAttribute('data-meal-id') || el.getAttribute('data-client-id');
            if (k) priorKeys.add(k);
        });
        pendingList.innerHTML = '';
        let newestNewCard = null;
        mealComposerState.pending.forEach((entry) => {
            const card = (entry && entry.__v2)
                ? buildMealReviewCardV2(entry)
                : buildMealPendingRow(entry);
            pendingList.appendChild(card);
            const k = card.getAttribute('data-meal-id') || card.getAttribute('data-client-id');
            if (k && !priorKeys.has(k)) newestNewCard = card;
        });
        const restored = restoreMealPendingFocus(pendingList, focusSnap);
        // Move focus to a brand-new card only when the user wasn't already
        // focused inside the list and this isn't the page-load hydration render.
        if (!restored && newestNewCard && !isFirstRender) {
            try { newestNewCard.focus({ preventScroll: true }); } catch (_) { newestNewCard.focus(); }
        }
    }

    function padMealDatePart(value) {
        return String(value).padStart(2, '0');
    }

    function browserLocalMealTime(date = new Date()) {
        const year = date.getFullYear();
        const month = padMealDatePart(date.getMonth() + 1);
        const day = padMealDatePart(date.getDate());
        const hours = padMealDatePart(date.getHours());
        const minutes = padMealDatePart(date.getMinutes());
        const seconds = padMealDatePart(date.getSeconds());
        const offsetMinutes = -date.getTimezoneOffset();
        const sign = offsetMinutes >= 0 ? '+' : '-';
        const absOffset = Math.abs(offsetMinutes);
        const offsetHours = padMealDatePart(Math.floor(absOffset / 60));
        const offsetRemainder = padMealDatePart(absOffset % 60);
        const localDate = `${year}-${month}-${day}`;
        return {
            local_timestamp: date.toISOString(),
            local_date: localDate,
            local_iso: `${localDate}T${hours}:${minutes}:${seconds}${sign}${offsetHours}:${offsetRemainder}`,
        };
    }

    // FIT-6: derive the FIT-66 three-field timestamp from a naive
    // server-local logged_at string (FIT-59 convention:
    // "YYYY-MM-DDTHH:MM:SS" without offset). Used when an entry came
    // from /api/meal-intake/pending (FIT-67 hydration), which today
    // only returns logged_at — without this, local_date / local_iso
    // would stay null and Retry's fallback to browserLocalMealTime()
    // would misdate a cross-midnight pending meal to today. Returns
    // null when logged_at can't be parsed (the caller falls back to
    // its own browserLocalMealTime()).
    function deriveLocalTimeFromLoggedAt(loggedAt) {
        if (!loggedAt || typeof loggedAt !== 'string') return null;
        const parsed = new Date(loggedAt);
        if (Number.isNaN(parsed.getTime())) return null;
        return browserLocalMealTime(parsed);
    }

    function mealPendingEntryFromPayload(entry, fallback = {}) {
        if (!entry) return null;
        const clientId = entry.client_id || fallback.client_id;
        if (!clientId) return null;
        // FIT-6: if the source payload omitted local_date / local_iso
        // (the /api/meal-intake/pending hydration path does — see the
        // docstring on deriveLocalTimeFromLoggedAt), reconstruct them
        // from logged_at so Retry never falls back to "today" for a
        // pending meal the user submitted on a different calendar day.
        // local_timestamp keeps its original FIT-67 fallback chain
        // (entry → fallback → logged_at) so the FIT-67 source-guard
        // test still passes.
        const needsDerive = !(entry.local_date || fallback.local_date)
            || !(entry.local_iso || fallback.local_iso);
        const derived = needsDerive
            ? deriveLocalTimeFromLoggedAt(entry.logged_at || fallback.logged_at)
            : null;
        // FIT-138: prefer the multi-photo arrays when present; fall back
        // to the legacy single imageFile for callers that haven't been
        // updated yet. Server-hydrated entries (from
        // /api/meal-intake/pending) carry no File handles, so the
        // arrays end up empty and Retry degrades to text-only.
        const incomingFiles = entry.imageFiles || fallback.imageFiles;
        const incomingUrls = entry.imagePreviewUrls || fallback.imagePreviewUrls;
        let imageFiles;
        let imagePreviewUrls;
        if (Array.isArray(incomingFiles) && incomingFiles.length > 0) {
            imageFiles = incomingFiles.slice();
            imagePreviewUrls = Array.isArray(incomingUrls) && incomingUrls.length === incomingFiles.length
                ? incomingUrls.slice()
                : imageFiles.map((file) => URL.createObjectURL(file));
        } else {
            const legacyFile = entry.imageFile || fallback.imageFile || null;
            imageFiles = legacyFile ? [legacyFile] : [];
            imagePreviewUrls = legacyFile ? [URL.createObjectURL(legacyFile)] : [];
        }
        return {
            client_id: clientId,
            estimate: entry.estimate || fallback.estimate || {},
            text: entry.text_hint || fallback.text || '',
            logged_at: entry.logged_at || fallback.logged_at || null,
            local_timestamp: entry.local_timestamp || fallback.local_timestamp || entry.logged_at || fallback.logged_at || null,
            local_date: entry.local_date || fallback.local_date || (derived && derived.local_date) || null,
            local_iso: entry.local_iso || fallback.local_iso || (derived && derived.local_iso) || null,
            policy: entry.policy || fallback.policy || null,
            // FIT-138: canonical multi-photo handles for retry + privacy
            // cleanup (Phase 4). imageFile is kept as the first-file
            // alias for callers still on the legacy single-image API.
            imageFile: imageFiles[0] || null,
            imageFiles,
            imagePreviewUrls,
        };
    }

    function upsertMealPendingEntry(entry) {
        const normalized = mealPendingEntryFromPayload(entry);
        if (!normalized) return;
        const index = mealComposerState.pending.findIndex((p) => p.client_id === normalized.client_id);
        if (index >= 0) mealComposerState.pending[index] = normalized;
        else mealComposerState.pending.push(normalized);
    }

    async function hydrateMealPending() {
        try {
            const payload = await api('/api/meal-intake/pending');
            const pending = Array.isArray(payload.pending) ? payload.pending : [];
            // FIT-144: /api/meal-intake/pending now returns saved v2 snapshots
            // (meal_id + items[]) alongside legacy single-item entries. Route
            // v2 entries through normalizeMealV2Entry so reload keeps the
            // multi-item review surface intact.
            pending.forEach((entry) => {
                if (isMealV2Payload(entry)) {
                    const v2 = normalizeMealV2Entry(entry);
                    if (v2) upsertMealV2Entry(v2);
                } else {
                    upsertMealPendingEntry(entry);
                }
            });
            renderMealPendingList();
        } catch (e) {
            console.error(e);
            toast('Couldn’t refresh pending meals', 'err');
        }
    }

    // FIT-6: human-readable copy for each stable policy reason code.
    // Mirrors app.py's _POLICY_REASON_NOTES so the per-reason chip on
    // the review card stays in lock-step with the backend's policy
    // module (meal_log_policy.evaluate_meal_log).
    const MEAL_POLICY_REASON_LABELS = {
        low_confidence: 'Low confidence',
        medium_confidence: 'Medium confidence',
        ambiguous_input: 'Ambiguous input',
        implausible_calories: 'Calories look off',
        implausible_macros: 'Macros look high',
        implausible_sodium: 'Sodium looks high',
        missing_calories: 'Missing calories',
    };

    const MEAL_TYPE_OPTIONS = ['breakfast', 'lunch', 'dinner', 'snack'];

    // FIT-6 AC5: distinguish AI-estimated values from user-edited ones.
    // The "Estimated" tag shows on every field by default; once the user
    // edits a field, the tag flips to "Edited". Tracking the original
    // value per field is how we know which is which after edits.
    function mealPendingOriginals(est) {
        return {
            item_name: est && est.item_name != null ? String(est.item_name) : '',
            portion_description: est && est.portion_description != null ? String(est.portion_description) : '',
            calories: est && est.calories != null ? String(est.calories) : '',
            protein_g: est && est.protein_g != null ? String(est.protein_g) : '',
            carbs_g: est && est.carbs_g != null ? String(est.carbs_g) : '',
            fat_g: est && est.fat_g != null ? String(est.fat_g) : '',
            sodium_mg: est && est.sodium_mg != null ? String(est.sodium_mg) : '',
            meal_type: est && est.meal_type ? String(est.meal_type) : 'snack',
        };
    }

    // FIT-6: format a source code into a UI-friendly label. Source
    // strings come from the meal_estimate_schema (e.g. "ai_text_estimate",
    // "fallback_text_estimate", "stub_vision_estimate") and from the
    // FIT-72 branded-lookup pipeline (e.g. "nutritionix", "usda_fdc",
    // "local_cache", "personal_vocab"). Unknown codes fall through to
    // a humanized lower-case form.
    function mealSourceLabel(source) {
        if (!source) return 'AI estimate';
        const map = {
            ai_text_estimate: 'AI estimate',
            fallback_text_estimate: 'Fallback preset',
            stub_vision_estimate: 'Photo stub',
            manual_review_estimate: 'Manual entry',
            nutritionix: 'Nutritionix',
            usda_fdc: 'USDA',
            local_cache: 'Cached source',
            personal_vocab: 'Your vocabulary',
            open_food_facts: 'Open Food Facts',
        };
        const key = String(source).trim().toLowerCase();
        if (map[key]) return map[key];
        // Strip a known prefix like "vision_claude+nutritionix" → use the
        // more authoritative downstream source label when present.
        if (key.includes('+')) {
            const tail = key.split('+').pop();
            if (map[tail]) return map[tail];
        }
        return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
    }

    function safeExternalMealSourceUrl(value) {
        if (!value) return '';
        const candidate = String(value).trim();
        if (!candidate) return '';
        try {
            const url = new URL(candidate);
            return (url.protocol === 'https:' || url.protocol === 'http:') ? url.href : '';
        } catch (_err) {
            return '';
        }
    }

    function mealEstimateSourceUrl(est) {
        const attr = est && est.off_attribution;
        const attrUrl = attr && typeof attr === 'object'
            ? (attr.url || attr.source_url || attr.product_url || attr.license_url)
            : '';
        return safeExternalMealSourceUrl(
            attrUrl || (est && (est.verified_source_url || est.source_url || est.product_url))
        );
    }

    function mealEstimateAttributionText(est) {
        if (!est) return '';
        const attr = est.off_attribution;
        if (typeof attr === 'string' && attr.trim()) return attr.trim();
        if (attr && typeof attr === 'object') {
            const sourceName = attr.source || attr.name || attr.provider || 'Open Food Facts';
            const license = attr.license || attr.license_name || attr.license_code;
            if (license) return `Source: ${sourceName} (${license})`;
            return `Source: ${sourceName} (ODbL/DbCL data; product images CC BY-SA)`;
        }
        const sourceKey = String(est.source || '').trim().toLowerCase();
        return sourceKey === 'open_food_facts'
            ? 'Source: Open Food Facts (ODbL/DbCL data; product images CC BY-SA)'
            : '';
    }

    function mealEstimateProvenanceHtml(est) {
        const inner = mealEstimateProvenanceInnerHtml(est);
        return inner
            ? `<div class="meal-pending-provenance" aria-label="Estimate source provenance">${inner}</div>`
            : '';
    }

    function mealEstimateProvenanceInnerHtml(est) {
        const attribution = mealEstimateAttributionText(est);
        const sourceUrl = mealEstimateSourceUrl(est);
        if (!attribution && !sourceUrl) return '';
        const link = sourceUrl
            ? `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer">Source link</a>`
            : '';
        const separator = attribution && link ? '<span aria-hidden="true">·</span>' : '';
        return `
            ${attribution ? `<span>${escapeHtml(attribution)}</span>` : ''}
            ${separator}
            ${link}
        `;
    }

    function renderMealComposerProvenance(est, clientId = null) {
        const { status } = mealComposerEls();
        if (!status) return false;
        const inner = mealEstimateProvenanceInnerHtml(est);
        if (!inner) {
            clearMealComposerStatus();
            return false;
        }
        status.classList.add('meal-composer-status--provenance');
        if (clientId) status.dataset.provenanceClientId = String(clientId);
        else delete status.dataset.provenanceClientId;
        status.hidden = false;
        status.innerHTML = inner;
        return true;
    }

    function buildMealPendingRow(entry) {
        const row = document.createElement('div');
        row.className = 'meal-pending-row';
        row.setAttribute('data-client-id', entry.client_id);
        // FIT-219 item 1: focus target for renderMealPendingList() on create.
        row.setAttribute('tabindex', '-1');
        row.setAttribute('role', 'group');
        row.setAttribute('aria-label', 'Estimate to review before accepting');
        const est = entry.estimate || {};
        const policy = entry.policy || {};
        const conf = Number(est.confidence);
        const confLabel = Number.isFinite(conf) ? `${Math.round(conf * 100)}%` : '—';
        const confTitle = Number.isFinite(conf)
            ? `Confidence ${Math.round(conf * 100)}% (band: ${escapeHtml(policy.confidence_band || 'unknown')})`
            : 'Confidence unknown';
        const sourceLabel = mealSourceLabel(est.source);
        // FIT-6 AC2: surface the stable policy reason codes as chips so
        // the user sees *why* this estimate needs explicit review (rather
        // than only the merged uncertainty_notes paragraph).
        const reasonCodes = Array.isArray(policy.reasons) ? policy.reasons : [];
        const reasonChips = reasonCodes
            .map((code) => {
                const label = MEAL_POLICY_REASON_LABELS[code] || String(code).replace(/_/g, ' ');
                return `<span class="meal-pending-reason-chip" data-reason="${escapeHtml(code)}">${escapeHtml(label)}</span>`;
            })
            .join('');
        const uncertainty = Array.isArray(est.uncertainty_notes) && est.uncertainty_notes.length
            ? `<div class="meal-pending-note">${escapeHtml(est.uncertainty_notes.join(' '))}</div>`
            : '';
        const currentMealType = (est.meal_type && MEAL_TYPE_OPTIONS.includes(est.meal_type))
            ? est.meal_type
            : 'snack';
        const mealTypeOptions = MEAL_TYPE_OPTIONS.map((mt) => `
            <option value="${mt}"${mt === currentMealType ? ' selected' : ''}>${mt.charAt(0).toUpperCase() + mt.slice(1)}</option>
        `).join('');
        // FIT-6 AC1: full field set — item, portion, calories, protein,
        // carbs, fat, sodium, confidence, source.
        // FIT-6 AC3: editable — item_name, portion_description, calories,
        // protein_g, carbs_g, fat_g, sodium_mg, meal_type.
        // FIT-6 AC5: every editable field carries a per-field tag that
        // starts at "Estimated" and flips to "Edited" once the value
        // differs from the AI estimate captured at row build time.
        row.innerHTML = `
            <div class="meal-pending-head">
                <span class="meal-pending-title">Review estimate</span>
                <span class="meal-pending-source-chip" title="Source of this estimate">${escapeHtml(sourceLabel)}</span>
                <span class="meal-pending-conf" aria-label="${confTitle}" title="${confTitle}">${escapeHtml(confLabel)}${Number.isFinite(conf) && policy.confidence_band ? `<span class="meal-pending-conf-band"> · ${escapeHtml(policy.confidence_band)}</span>` : ''}</span>
            </div>
            ${mealEstimateProvenanceHtml(est)}
            ${reasonChips ? `<div class="meal-pending-policy-reasons" aria-label="Why this needs review">${reasonChips}</div>` : ''}
            <div class="meal-pending-hint">Tap any value to edit before accepting.</div>
            <div class="meal-pending-portion" role="group" aria-label="Portion multiplier">
                <span class="meal-pending-portion-label">Portion</span>
                <div class="meal-pending-portion-chips">
                    <button type="button" class="meal-pending-portion-chip" data-factor="0.5" aria-pressed="false">Half</button>
                    <button type="button" class="meal-pending-portion-chip" data-factor="1" aria-pressed="false">Standard</button>
                    <button type="button" class="meal-pending-portion-chip" data-factor="1.5" aria-pressed="false">1½</button>
                    <button type="button" class="meal-pending-portion-chip" data-factor="2" aria-pressed="false">Double</button>
                </div>
            </div>
            <div class="meal-pending-fields">
                <label data-field-label="item_name">
                    <span class="meal-pending-field-name">Item <span class="meal-pending-field-tag" data-tag="estimated">Estimated</span></span>
                    <input type="text" data-field="item_name" value="${escapeHtml(est.item_name || '')}" maxlength="160">
                </label>
                <label data-field-label="portion_description">
                    <span class="meal-pending-field-name">Portion <span class="meal-pending-field-tag" data-tag="estimated">Estimated</span></span>
                    <input type="text" data-field="portion_description" value="${escapeHtml(est.portion_description || '')}" maxlength="240">
                </label>
                <label data-field-label="meal_type">
                    <span class="meal-pending-field-name">Meal time <span class="meal-pending-field-tag" data-tag="estimated">Estimated</span></span>
                    <select data-field="meal_type">${mealTypeOptions}</select>
                </label>
                <label data-field-label="calories">
                    <span class="meal-pending-field-name">Calories <span class="meal-pending-field-tag" data-tag="estimated">Estimated</span></span>
                    <input type="number" inputmode="numeric" min="0" data-field="calories" value="${escapeHtml(est.calories ?? '')}">
                </label>
                <label data-field-label="protein_g">
                    <span class="meal-pending-field-name">Protein (g) <span class="meal-pending-field-tag" data-tag="estimated">Estimated</span></span>
                    <input type="number" inputmode="decimal" min="0" step="0.1" data-field="protein_g" value="${escapeHtml(est.protein_g ?? '')}">
                </label>
                <label data-field-label="carbs_g">
                    <span class="meal-pending-field-name">Carbs (g) <span class="meal-pending-field-tag" data-tag="estimated">Estimated</span></span>
                    <input type="number" inputmode="decimal" min="0" step="0.1" data-field="carbs_g" value="${escapeHtml(est.carbs_g ?? '')}">
                </label>
                <label data-field-label="fat_g">
                    <span class="meal-pending-field-name">Fat (g) <span class="meal-pending-field-tag" data-tag="estimated">Estimated</span></span>
                    <input type="number" inputmode="decimal" min="0" step="0.1" data-field="fat_g" value="${escapeHtml(est.fat_g ?? '')}">
                </label>
                <label data-field-label="sodium_mg">
                    <span class="meal-pending-field-name">Sodium (mg) <span class="meal-pending-field-tag" data-tag="estimated">Estimated</span></span>
                    <input type="number" inputmode="numeric" min="0" data-field="sodium_mg" value="${escapeHtml(est.sodium_mg ?? '')}">
                </label>
            </div>
            ${uncertainty}
            <div class="meal-pending-actions">
                <button type="button" class="btn btn-ghost meal-pending-discard">Discard</button>
                <button type="button" class="btn btn-ghost meal-pending-retry">Retry</button>
                <button type="button" class="btn btn-primary meal-pending-accept">Accept</button>
            </div>
        `;
        // FIT-6 AC5 wiring: every editable field watches for the first
        // value drift away from the original estimate and flips its tag
        // to "Edited" so the user can see at-a-glance which numbers are
        // theirs vs. the AI's. Drifting back to the original restores
        // "Estimated" — handy when the user undoes a typo.
        const originals = mealPendingOriginals(est);
        row.querySelectorAll('[data-field]').forEach((input) => {
            const field = input.getAttribute('data-field');
            const labelEl = row.querySelector(`label[data-field-label="${field}"]`);
            const tagEl = labelEl ? labelEl.querySelector('.meal-pending-field-tag') : null;
            if (!tagEl) return;
            const sync = () => {
                const currentVal = input.value != null ? String(input.value) : '';
                const originalVal = originals[field] != null ? String(originals[field]) : '';
                const edited = currentVal !== originalVal;
                tagEl.setAttribute('data-tag', edited ? 'edited' : 'estimated');
                tagEl.textContent = edited ? 'Edited' : 'Estimated';
                if (labelEl) labelEl.classList.toggle('edited', edited);
            };
            input.addEventListener('input', sync);
            input.addEventListener('change', sync);
        });
        // FIT-119: snapshot the AI's numeric macros so the portion chips
        // always scale from the original 1× baseline (not from the
        // possibly-already-scaled current input values). Successive chip
        // clicks therefore don't compound, and ½× → 2× round-trips
        // through 1× cleanly. Non-numeric estimate values (null, '',
        // strings) yield NaN so the chip handler skips scaling and the
        // user's input is left alone for that field.
        const baselineMacro = (v) => (typeof v === 'number' && Number.isFinite(v)) ? v : NaN;
        const aiMacroBaseline = {
            calories: baselineMacro(est.calories),
            protein_g: baselineMacro(est.protein_g),
            carbs_g: baselineMacro(est.carbs_g),
            fat_g: baselineMacro(est.fat_g),
            sodium_mg: baselineMacro(est.sodium_mg),
        };
        const PORTION_INT_FIELDS = ['calories', 'sodium_mg'];
        const PORTION_FLOAT_FIELDS = ['protein_g', 'carbs_g', 'fat_g'];
        row.querySelectorAll('.meal-pending-portion-chip').forEach((chip) => {
            chip.addEventListener('click', () => {
                // Honor the row-level lock that setMealPendingRowLocked()
                // applies while a Retry request is in flight — same guard
                // pattern as acceptMealPending(). Without this the chips
                // would let the user mutate the displayed macros while
                // the retry response is replacing the row.
                if (row.classList.contains('meal-pending-row--locked')) return;
                const factor = Number(chip.getAttribute('data-factor'));
                if (!Number.isFinite(factor) || factor <= 0) return;
                PORTION_INT_FIELDS.forEach((field) => {
                    const base = aiMacroBaseline[field];
                    if (!Number.isFinite(base)) return;
                    const input = row.querySelector(`input[data-field="${field}"]`);
                    if (!input) return;
                    input.value = String(Math.round(base * factor));
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                });
                PORTION_FLOAT_FIELDS.forEach((field) => {
                    const base = aiMacroBaseline[field];
                    if (!Number.isFinite(base)) return;
                    const input = row.querySelector(`input[data-field="${field}"]`);
                    if (!input) return;
                    input.value = String(Math.round(base * factor * 10) / 10);
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                });
                // Fiber is intentionally not scaled: it has no review-card
                // input, and mutating entry.estimate.fiber_g would corrupt
                // the original_estimate audit trail captured at /accept.
                // The user can adjust fiber explicitly in the Correct flow.
                row.querySelectorAll('.meal-pending-portion-chip').forEach((c) => {
                    const active = c === chip;
                    c.classList.toggle('is-active', active);
                    c.setAttribute('aria-pressed', active ? 'true' : 'false');
                });
            });
        });
        // Manual edits that drift away from the active chip's expected
        // values deactivate it — the chip no longer represents what the
        // user is about to accept. We compare current input values to
        // baseline × factor instead of relying on event.isTrusted so this
        // also works correctly under headless / preview drivers.
        const recomputeActivePortionChip = () => {
            const active = row.querySelector('.meal-pending-portion-chip.is-active');
            if (!active) return;
            const factor = Number(active.getAttribute('data-factor'));
            if (!Number.isFinite(factor) || factor <= 0) return;
            const stillMatches = [...PORTION_INT_FIELDS, ...PORTION_FLOAT_FIELDS].every((field) => {
                const base = aiMacroBaseline[field];
                if (!Number.isFinite(base)) return true;
                const input = row.querySelector(`input[data-field="${field}"]`);
                if (!input) return true;
                const expected = PORTION_INT_FIELDS.includes(field)
                    ? Math.round(base * factor)
                    : Math.round(base * factor * 10) / 10;
                const current = Number(input.value);
                return Number.isFinite(current) && current === expected;
            });
            if (!stillMatches) {
                active.classList.remove('is-active');
                active.setAttribute('aria-pressed', 'false');
            }
        };
        row.querySelectorAll('input[data-field]').forEach((input) => {
            input.addEventListener('input', recomputeActivePortionChip);
        });
        row.querySelector('.meal-pending-discard').addEventListener('click', () => discardMealPending(entry.client_id));
        row.querySelector('.meal-pending-accept').addEventListener('click', () => acceptMealPending(entry.client_id, row));
        row.querySelector('.meal-pending-retry').addEventListener('click', () => retryMealPending(entry.client_id, row));
        return row;
    }

    // FIT-138: privacy cleanup when a pending entry leaves the local
    // list (accept, discard, or retry-cleanup). Revokes every blob URL
    // the entry held so the browser releases the underlying File data,
    // and conditionally removes the localStorage draft key — but only
    // if the stored draft still corresponds to this entry's text, so a
    // new active draft the user started after submit-success is not
    // wiped (per Codex consensus round-3 R2).
    function releasePendingEntryArtifacts(entry) {
        if (!entry) return;
        const urls = Array.isArray(entry.imagePreviewUrls) ? entry.imagePreviewUrls : [];
        urls.forEach((url) => {
            try { URL.revokeObjectURL(url); } catch (_) { /* already revoked or invalid */ }
        });
        // Drop array references so the File objects become GC-eligible.
        entry.imagePreviewUrls = [];
        entry.imageFiles = [];
        entry.imageFile = null;
        try {
            const raw = localStorage.getItem(MEAL_DRAFT_KEY);
            if (raw) {
                const draft = JSON.parse(raw);
                const draftText = (draft && typeof draft.text === 'string') ? draft.text : '';
                const entryText = entry.text || '';
                // FIT-138 AC8 + Codex audit R2: clear MEAL_DRAFT_KEY only
                // when the stored draft still corresponds to the just-
                // resolved entry's submitted text. An empty stored
                // text combined with has_image=true is a NEW photo-only
                // draft the user started after submit-success — never
                // wipe that. Anything that doesn't match the entry's
                // text is a new active draft we must preserve.
                if (entryText && draftText === entryText) {
                    localStorage.removeItem(MEAL_DRAFT_KEY);
                }
            }
        } catch (_) { /* storage may be unavailable */ }
    }

    async function discardMealPending(clientId) {
        const entry = mealComposerState.pending.find((p) => p.client_id === clientId);
        if (!entry) return;
        try {
            const result = await api(`/api/meal-intake/${encodeURIComponent(clientId)}?correction_state=pending_review`, { method: 'DELETE' });
            if (!result || result.removed !== true) throw new Error('pending meal was not removed');
            releasePendingEntryArtifacts(entry);
            mealComposerState.pending = mealComposerState.pending.filter((p) => p.client_id !== clientId);
            renderMealPendingList();
            toast('Estimate discarded', 'ok');
            refreshMacroCard();
        } catch (e) {
            console.error(e);
            toast('Discard failed — retry when connected', 'err');
        }
    }

    // FIT-6 AC3: collect edited values from the row's inputs. Returns
    // a sanitized estimate dict ready for /accept or for replacing the
    // pending entry on Retry. Numeric fields are coerced; text fields
    // are trimmed; meal_type falls back to the schema default when the
    // select somehow holds an unsupported value (shouldn't happen with
    // a <select>, but the guard keeps the payload schema-valid).
    function collectMealEditedEstimate(entry, rowEl) {
        const edited = { ...entry.estimate };
        rowEl.querySelectorAll('[data-field]').forEach((input) => {
            const field = input.getAttribute('data-field');
            const raw = input.value;
            if (input.tagName === 'SELECT') {
                edited[field] = MEAL_TYPE_OPTIONS.includes(raw) ? raw : 'snack';
            } else if (input.type === 'number') {
                const num = raw === '' ? null : Number(raw);
                edited[field] = Number.isFinite(num) ? num : null;
            } else {
                edited[field] = raw.trim() || null;
            }
        });
        return edited;
    }

    // FIT-6: lock/unlock the whole review-card row's action buttons.
    // Used by Retry so a user can't Accept or Discard the OLD pending
    // entry while a Retry request is in flight — that race would
    // otherwise create duplicate meal state (old client_id persisted
    // via /accept + new client_id logged or pending from the retry
    // response). The lock is per-row, not global, so other pending
    // entries in the list stay actionable.
    function setMealPendingRowLocked(rowEl, locked) {
        if (!rowEl) return;
        rowEl.classList.toggle('meal-pending-row--locked', !!locked);
        rowEl.querySelectorAll('.meal-pending-actions button').forEach((btn) => {
            if (locked) {
                if (!btn.hasAttribute('data-prev-disabled')) {
                    btn.setAttribute('data-prev-disabled', btn.disabled ? '1' : '0');
                }
                btn.disabled = true;
            } else {
                const prev = btn.getAttribute('data-prev-disabled');
                btn.disabled = prev === '1';
                btn.removeAttribute('data-prev-disabled');
            }
        });
    }

    async function acceptMealPending(clientId, rowEl) {
        const entry = mealComposerState.pending.find((p) => p.client_id === clientId);
        if (!entry) return;
        // FIT-6: belt-and-suspenders guard. The retry handler already
        // disables this button via setMealPendingRowLocked, but defend
        // against any caller that bypasses the UI (test harness, future
        // refactor) by checking the lock state here too.
        if (rowEl && rowEl.classList.contains('meal-pending-row--locked')) return;
        const edited = collectMealEditedEstimate(entry, rowEl);
        if (!Number.isFinite(Number(edited.calories))) {
            toast('Set calories before accepting', 'err');
            return;
        }
        try {
            const body = {
                estimate: edited,
                original_estimate: entry.estimate || null,
                text: entry.text || '',
                local_timestamp: entry.local_timestamp || null,
                local_date: entry.local_date || null,
                local_iso: entry.local_iso || null,
            };
            await api(`/api/meal-intake/${encodeURIComponent(clientId)}/accept`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            // FIT-138 AC8: revoke blob URLs + release File references +
            // conditionally clear MEAL_DRAFT_KEY before dropping the
            // pending entry from local state.
            releasePendingEntryArtifacts(entry);
            mealComposerState.pending = mealComposerState.pending.filter((p) => p.client_id !== clientId);
            renderMealPendingList();
            renderMealComposerProvenance(edited, clientId);
            toast('Meal logged', 'ok');
            refreshMacroCard();
        } catch (e) {
            console.error(e);
            toast(apiErrorMessage(e, 'Accept failed'), 'err');
        }
    }

    // FIT-6 AC4: Retry re-runs the original meal-intake request so the
    // user can ask the estimator for a fresh answer when the first
    // estimate looked wrong. The retry submits the captured text (and
    // image, if present) under a NEW client_id, then deletes the old
    // server-side pending row (FIT-67 now persists pending entries, so
    // a local-only cleanup would leave an orphan that re-hydrates on
    // next page load). If the new submission errors (network, backend
    // unavailable), the old entry stays in place — the user doesn't
    // lose their review surface.
    async function retryMealPending(clientId, rowEl) {
        const entry = mealComposerState.pending.find((p) => p.client_id === clientId);
        if (!entry) return;
        if (mealComposerState.backendUnavailable) {
            toast('Meal intake backend unavailable — can’t retry right now.', 'err');
            return;
        }
        const text = entry.text || '';
        // FIT-138: prefer the multi-photo array; fall back to the legacy
        // single imageFile for entries created before the array shape.
        const files = Array.isArray(entry.imageFiles) && entry.imageFiles.length > 0
            ? entry.imageFiles.slice()
            : (entry.imageFile ? [entry.imageFile] : []);
        if (!text && files.length === 0) {
            toast('Nothing to retry — original input wasn’t captured.', 'err');
            return;
        }
        const retryBtn = rowEl.querySelector('.meal-pending-retry');
        // FIT-6: lock the ENTIRE row, not just the Retry button, so
        // Accept and Discard can't race the in-flight retry. Without
        // this, a quick Accept after Retry would persist the OLD
        // client_id while the retry response also logs/replaces under
        // newClientId — duplicate meal state.
        setMealPendingRowLocked(rowEl, true);
        if (retryBtn) retryBtn.textContent = 'Retrying…';
        const newClientId = newMealClientId();
        const form = new FormData();
        if (text) form.append('text', text);
        // FIT-138: send all captured photos under plural "images" so the
        // backend treats them as one combined meal context.
        files.forEach((file, idx) => {
            form.append('images', file, file.name || `meal-${idx + 1}.jpg`);
        });
        form.append('client_id', newClientId);
        // FIT-6 + FIT-66: preserve the original three-field browser-local
        // timestamp so a retry across midnight doesn't relocate the meal
        // to today. Falls back to the current time only if the original
        // wasn't captured (e.g. server-hydrated entries pre-FIT-67 that
        // never had these fields).
        const fallbackTime = browserLocalMealTime();
        const originalLocalTimestamp = entry.local_timestamp || fallbackTime.local_timestamp;
        const originalLocalDate = entry.local_date || fallbackTime.local_date;
        const originalLocalIso = entry.local_iso || fallbackTime.local_iso;
        form.append('local_timestamp', originalLocalTimestamp);
        form.append('local_date', originalLocalDate);
        form.append('local_iso', originalLocalIso);
        let cleanedUp = false;
        try {
            const res = await fetch('/api/meal-intake', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Accept': 'application/json', [CSRF_HEADER_NAME]: CSRF_HEADER_VALUE },
                body: form,
            });
            if (res.status === 404 || res.status === 501) {
                setMealBackendUnavailable();
                toast('Meal intake isn’t enabled yet.', 'err');
                return;
            }
            const ct = res.headers.get('content-type') || '';
            const payload = ct.includes('application/json') ? await res.json() : null;
            if (!res.ok) {
                const msg = (payload && payload.error && payload.error.message) || `Retry failed (${res.status}).`;
                toast(msg, 'err');
                return;
            }
            // FIT-6 + FIT-67: the new submission persisted under
            // newClientId. Now clean up the OLD pending row server-side
            // — best-effort, because the OLD row is durable post-FIT-67
            // and would otherwise re-hydrate on the next refresh. If
            // the DELETE fails (network, race), surface a toast so the
            // user can manually discard the duplicate later. Either
            // way, drop the OLD entry from local state — the retry
            // response is authoritative.
            try {
                await api(
                    `/api/meal-intake/${encodeURIComponent(clientId)}?correction_state=pending_review`,
                    { method: 'DELETE' },
                );
            } catch (deleteErr) {
                console.warn('Retry cleanup DELETE failed:', deleteErr);
                toast('Retried, but couldn’t clean up the old pending row — discard it manually if it reappears.', 'warn');
            }
            // FIT-138 AC8: release blob URLs for the old pending entry
            // before dropping it from local state. The new retry entry
            // is created by handleMealIntakeResponse with its own URLs.
            const oldEntry = mealComposerState.pending.find((p) => p.client_id === clientId);
            releasePendingEntryArtifacts(oldEntry);
            mealComposerState.pending = mealComposerState.pending.filter((p) => p.client_id !== clientId);
            cleanedUp = true;
            if (payload && payload.status === 'logged') {
                renderMealPendingList();
                // FIT-6: the retry's auto-log path must offer the same
                // undo affordance as the regular submitMealComposer
                // auto-log. Otherwise the user can't immediately
                // recover from an accidentally-logged retry.
                // FIT-97 AC1: same chip → same tap-to-inspect modal as
                // the non-retry auto-log path.
                const retryEntry = mealEntryFromIntakePayload(payload, newClientId);
                toastUndo(
                    mealEstimateChip(payload.estimate),
                    () => postMealUndo(newClientId),
                    MEAL_UNDO_MS,
                    () => openMealDetailModal(retryEntry),
                );
                renderMealComposerProvenance(payload.estimate, newClientId);
                refreshMacroCard();
                return;
            }
            if (payload && payload.status === 'pending_review') {
                // FIT-6 + FIT-67: hydrate the new pending entry from
                // the response. Carry the original three-field
                // timestamp forward so chained retries (retry → retry
                // → ...) never lose the original meal day even if the
                // server response omits them.
                // FIT-138: pass the plural `files` so the new pending
                // entry holds all photos for any future pending-card
                // retry. Previously this read a singular `file` that
                // no longer existed after the multi-photo refactor.
                const previewUrls = files.map((f) => URL.createObjectURL(f));
                upsertMealPendingEntry({
                    client_id: newClientId,
                    estimate: payload.estimate || {},
                    text,
                    text_hint: text,
                    local_timestamp: payload.local_timestamp || originalLocalTimestamp,
                    local_date: payload.local_date || originalLocalDate,
                    local_iso: payload.local_iso || originalLocalIso,
                    logged_at: payload.food_log && payload.food_log.logged_at,
                    policy: payload.policy || null,
                    imageFile: files[0] || null,
                    imageFiles: files,
                    imagePreviewUrls: previewUrls,
                });
                renderMealPendingList();
                refreshMacroCard();
                toast('New estimate — review before it counts.', 'warn');
                return;
            }
            // Unknown status — restore nothing; show generic warning.
            renderMealPendingList();
            toast('Couldn’t parse retried estimate.', 'err');
        } catch (e) {
            console.error(e);
            toast('Retry failed — check your connection.', 'err');
        } finally {
            // Only unlock the row if it still exists in the DOM (i.e.
            // we didn't already remove + re-render the pending list).
            if (!cleanedUp) {
                setMealPendingRowLocked(rowEl, false);
                if (retryBtn) retryBtn.textContent = 'Retry';
            }
        }
    }

    // FIT-138: toggle the in-composer Retry button based on whether the
    // last submit hit a transient failure (5xx / network error). The
    // Retry path re-uses the same draftClientId so the server's
    // idempotency contract prevents duplicate pending rows.
    function refreshMealComposerRetryUI() {
        const { retry } = mealComposerEls();
        if (!retry) return;
        retry.hidden = !mealComposerState.lastSubmitFailedTransient;
    }

    // FIT-138: toggle the offline banner based on navigator.onLine.
    function refreshMealComposerOfflineUI() {
        const { offline } = mealComposerEls();
        if (!offline) return;
        const online = typeof navigator === 'undefined' || navigator.onLine !== false;
        if (!online) {
            // FIT-145: keep this in sync with the HTML default in templates/index.html
            // so the meaning is clear before JS has had a chance to run.
            offline.innerHTML = 'You’re offline. Log is paused — tap <b>Save offline</b> to keep this meal on this device and sync it when you reconnect.';
        }
        offline.hidden = online;
    }

    function normalizeMealBarcode(value) {
        const digits = String(value || '').replace(/[\s_.-]+/g, '');
        return (digits.length === 8 || digits.length === 12 || digits.length === 13 || digits.length === 14)
            && /^\d+$/.test(digits)
            ? digits
            : '';
    }

    function setMealBarcodeStatus(message) {
        const { barcodeStatus } = mealComposerEls();
        if (!barcodeStatus) return;
        barcodeStatus.textContent = message || '';
    }

    function stopMealBarcodeScanner() {
        mealComposerState.barcodeScanToken += 1;
        if (mealComposerState.barcodeScanRaf) {
            cancelAnimationFrame(mealComposerState.barcodeScanRaf);
            mealComposerState.barcodeScanRaf = null;
        }
        if (mealComposerState.barcodeStream) {
            mealComposerState.barcodeStream.getTracks().forEach((track) => track.stop());
            mealComposerState.barcodeStream = null;
        }
        const { barcodeVideo } = mealComposerEls();
        if (barcodeVideo) {
            barcodeVideo.pause();
            barcodeVideo.srcObject = null;
            barcodeVideo.hidden = true;
        }
    }

    function mealBarcodeScanCancelled(scanToken) {
        const { barcodePanel } = mealComposerEls();
        return mealComposerState.barcodeScanToken !== scanToken || !barcodePanel || barcodePanel.hidden;
    }

    function closeMealBarcodePanel() {
        const { barcodePanel } = mealComposerEls();
        stopMealBarcodeScanner();
        if (barcodePanel) barcodePanel.hidden = true;
    }

    function barcodeDetectorSupported() {
        return typeof window !== 'undefined'
            && typeof window.BarcodeDetector === 'function'
            && typeof navigator !== 'undefined'
            && navigator.mediaDevices
            && typeof navigator.mediaDevices.getUserMedia === 'function';
    }

    async function startMealBarcodeScanner() {
        const { barcodeVideo } = mealComposerEls();
        if (!barcodeVideo || !barcodeDetectorSupported()) {
            setMealBarcodeStatus('Camera scan is not available here. Enter the barcode number instead.');
            return;
        }
        if (mealComposerState.barcodeStream || mealComposerState.barcodeScanRaf) {
            stopMealBarcodeScanner();
        }
        mealComposerState.barcodeScanToken += 1;
        const scanToken = mealComposerState.barcodeScanToken;
        try {
            mealComposerState.barcodeDetector = new window.BarcodeDetector({
                formats: ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128'],
            });
        } catch (_) {
            try { mealComposerState.barcodeDetector = new window.BarcodeDetector(); }
            catch (e) {
                setMealBarcodeStatus('Camera scan is not available here. Enter the barcode number instead.');
                return;
            }
        }
        try {
            const stream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: { ideal: 'environment' } },
                audio: false,
            });
            if (mealBarcodeScanCancelled(scanToken)) {
                stream.getTracks().forEach((track) => track.stop());
                return;
            }
            mealComposerState.barcodeStream = stream;
            barcodeVideo.srcObject = stream;
            barcodeVideo.hidden = false;
            await barcodeVideo.play();
            if (mealBarcodeScanCancelled(scanToken)) {
                stopMealBarcodeScanner();
                return;
            }
            setMealBarcodeStatus('Point the camera at the barcode. Only the decoded number is sent.');
            scanMealBarcodeFrame();
        } catch (e) {
            console.warn('Barcode camera unavailable:', e);
            stopMealBarcodeScanner();
            setMealBarcodeStatus('Camera permission unavailable. Enter the barcode number instead.');
        }
    }

    async function scanMealBarcodeFrame(now = 0) {
        const { barcodeVideo, barcodeInput } = mealComposerEls();
        const detector = mealComposerState.barcodeDetector;
        if (!barcodeVideo || !detector || !mealComposerState.barcodeStream || mealComposerState.barcodeSubmitting) return;
        const scanToken = mealComposerState.barcodeScanToken;
        if (now - mealComposerState.barcodeScanLastAt >= 450) {
            mealComposerState.barcodeScanLastAt = now;
            try {
                const results = await detector.detect(barcodeVideo);
                if (mealBarcodeScanCancelled(scanToken)) return;
                const raw = results && results[0] && results[0].rawValue;
                const barcode = normalizeMealBarcode(raw);
                if (barcode) {
                    if (barcodeInput) barcodeInput.value = barcode;
                    stopMealBarcodeScanner();
                    submitMealBarcode(barcode);
                    return;
                }
            } catch (e) {
                console.warn('Barcode detection failed:', e);
                stopMealBarcodeScanner();
                setMealBarcodeStatus('Camera scan stopped. Enter the barcode number instead.');
                return;
            }
        }
        mealComposerState.barcodeScanRaf = requestAnimationFrame(scanMealBarcodeFrame);
    }

    function openMealBarcodePanel() {
        if (mealComposerState.backendUnavailable || mealComposerState.barcodeUnavailable || mealComposerState.submitting || mealComposerState.barcodeSubmitting) return;
        const { barcodePanel, barcodeInput } = mealComposerEls();
        if (!barcodePanel) return;
        if (!barcodePanel.hidden) {
            if (barcodeInput) barcodeInput.focus();
            return;
        }
        barcodePanel.hidden = false;
        setMealComposerError(null);
        setMealBarcodeStatus('Starting barcode lookup…');
        if (barcodeInput) barcodeInput.focus();
        startMealBarcodeScanner();
    }

    async function postMealBarcodeLookup({ barcode, clientId, localTime, allowPending }) {
        const res = await fetch('/api/meal-intake/barcode', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                [CSRF_HEADER_NAME]: CSRF_HEADER_VALUE,
            },
            body: JSON.stringify({
                client_id: clientId,
                barcode,
                allow_pending: !!allowPending,
                local_timestamp: localTime.local_timestamp,
                local_date: localTime.local_date,
                local_iso: localTime.local_iso,
            }),
        });
        const ct = res.headers.get('content-type') || '';
        const payload = ct.includes('application/json') ? await res.json() : null;
        return { res, payload };
    }

    async function submitMealBarcode(rawValue) {
        if (mealComposerState.submitting || mealComposerState.barcodeSubmitting || mealComposerState.backendUnavailable) return;
        const barcode = normalizeMealBarcode(rawValue);
        if (!barcode) {
            setMealBarcodeStatus('Enter an 8, 12, 13, or 14 digit barcode.');
            return;
        }
        const online = typeof navigator === 'undefined' || navigator.onLine !== false;
        if (!online) {
            setMealBarcodeStatus('You are offline. Barcode lookup needs the server; try again when you reconnect.');
            return;
        }
        stopMealBarcodeScanner();
        const { text } = mealComposerEls();
        const textValue = text ? text.value.trim() : '';
        const preserveComposerDraft = !!textValue || mealComposerState.imageFiles.length > 0;
        if (!mealComposerState.barcodeDraftClientId || mealComposerState.barcodeDraftValue !== barcode) {
            mealComposerState.barcodeDraftClientId = newMealClientId();
            mealComposerState.barcodeDraftValue = barcode;
        }
        const clientId = mealComposerState.barcodeDraftClientId;
        const localTime = browserLocalMealTime();
        mealComposerState.barcodeSubmitting = true;
        refreshMealSubmitState();
        setMealComposerError(null);
        setMealBarcodeStatus('Looking up barcode…');
        try {
            let { res, payload } = await postMealBarcodeLookup({
                barcode,
                clientId,
                localTime,
                allowPending: false,
            });
            if (res.status === 404 && payload && payload.error && payload.error.code === 'barcode_not_found') {
                setMealBarcodeStatus('No verified barcode source found. Creating a manual review card.');
                ({ res, payload } = await postMealBarcodeLookup({
                    barcode,
                    clientId,
                    localTime,
                    allowPending: true,
                }));
            }
            if (res.status === 501) {
                setMealBarcodeUnavailable('Barcode lookup is not enabled yet. You can still log meals with text or photos.');
                mealComposerState.barcodeDraftClientId = null;
                mealComposerState.barcodeDraftValue = '';
                return;
            }
            // FIT-208: a residual 404 carrying the barcode_not_found code is
            // recoverable (the feature is enabled; this barcode just didn't
            // resolve). Surface a retryable message and clear the draft, but
            // keep scan/input/submit enabled — only a true 501 (feature
            // disabled) should disable the barcode controls. Gating on the code
            // keeps an unexpected 404 from being silently mislabeled as
            // "not found"; it falls through to the generic error handler below.
            if (res.status === 404 && payload && payload.error && payload.error.code === 'barcode_not_found') {
                const msg = 'We couldn’t find that barcode. Double-check the digits and try again, or log it with text or a photo.';
                setMealComposerError(msg);
                setMealBarcodeStatus(msg);
                mealComposerState.barcodeDraftClientId = null;
                mealComposerState.barcodeDraftValue = '';
                return;
            }
            if (!res.ok) {
                const msg = (payload && payload.error && payload.error.message) || `Couldn’t look up barcode (${res.status}).`;
                setMealComposerError(msg);
                setMealBarcodeStatus(msg);
                if (res.status < 500) {
                    mealComposerState.barcodeDraftClientId = null;
                    mealComposerState.barcodeDraftValue = '';
                }
                return;
            }
            handleMealIntakeResponse(payload, {
                textValue,
                clientId,
                localTime,
                barcode,
                preserveComposerDraft,
            });
            mealComposerState.barcodeDraftClientId = null;
            mealComposerState.barcodeDraftValue = '';
            closeMealBarcodePanel();
            toast(payload && payload.pending_source
                ? 'Barcode added for manual review.'
                : 'Barcode found. Review before saving.', payload && payload.pending_source ? 'warn' : 'ok');
        } catch (e) {
            console.error(e);
            setMealComposerError('Couldn’t reach barcode lookup. Try again.');
            setMealBarcodeStatus('Barcode lookup failed. Try again.');
        } finally {
            mealComposerState.barcodeSubmitting = false;
            refreshMealSubmitState();
        }
    }

    async function submitMealComposer(ev) {
        if (ev) ev.preventDefault();
        if (mealComposerState.submitting || mealComposerState.barcodeSubmitting || mealComposerState.backendUnavailable) return;
        const { text } = mealComposerEls();
        const textValue = text ? text.value.trim() : '';
        const files = mealComposerState.imageFiles.slice();
        if (!textValue && files.length === 0) {
            setMealComposerError('Type a meal or attach a photo.');
            return;
        }
        setMealComposerError(null);
        // FIT-138: generate draftClientId on the first attempt and reuse
        // it across transient-failure retries. Material draft changes
        // (text edit, photo add/remove) clear this elsewhere.
        if (!mealComposerState.draftClientId) {
            mealComposerState.draftClientId = newMealClientId();
        }
        const clientId = mealComposerState.draftClientId;
        mealComposerState.submitting = true;
        refreshMealSubmitState();

        // FIT-6 + FIT-66: capture the original submission's three-field
        // browser-local timestamp so Retry can reuse it. Without this,
        // retrying a pending entry created before midnight would misdate
        // the meal.
        const localTime = browserLocalMealTime();
        const online = typeof navigator === 'undefined' || navigator.onLine !== false;
        if (!online) {
            try {
                const entry = await enqueueMealIntakeOffline({ textValue, files, clientId, localTime });
                clearMealComposerInputs();
                clearMealDraft();
                mealComposerState.draftClientId = null;
                mealComposerState.lastSubmitFailedTransient = false;
                refreshMealComposerRetryUI();
                const photoCount = entry.image_count || 0;
                const photoNote = photoCount ? ` (${photoCount} photo${photoCount === 1 ? '' : 's'} kept on this device)` : '';
                toast(`Meal saved offline${photoNote}. It will sync when you reconnect.`);
                const { status } = mealComposerEls();
                if (status && !mealComposerState.backendUnavailable) {
                    status.classList.remove('meal-composer-status--provenance');
                    status.hidden = false;
                    status.textContent = 'Saved on this device — will sync automatically when you reconnect.';
                }
                return;
            } catch (e) {
                console.error(e);
                saveMealDraft();
                setMealComposerError('Couldn’t save this meal offline. Your draft is kept — try again, or wait until you reconnect.');
                return;
            } finally {
                mealComposerState.submitting = false;
                refreshMealSubmitState();
            }
        }
        const form = new FormData();
        if (textValue) form.append('text', textValue);
        // FIT-138: append each file under the plural "images" key. The
        // backend reads request.files.getlist("images") and treats the
        // photos as one combined meal context (one vision call). The
        // legacy singular "image" key remains accepted server-side for
        // back-compat with the existing FIT-128 pending-card retry path.
        files.forEach((file, idx) => {
            form.append('images', file, file.name || `meal-${idx + 1}.jpg`);
        });
        form.append('client_id', clientId);
        form.append('local_timestamp', localTime.local_timestamp);
        form.append('local_date', localTime.local_date);
        form.append('local_iso', localTime.local_iso);

        try {
            // FIT-134: ?fit134=mock short-circuits to a synthetic multi-item
            // payload so the new review UI can be exercised locally before
            // FIT-135 lands the real backend. Production path is unchanged.
            if (mealV2MockEnabled()) {
                const payload = mealV2Mock.createMeal(textValue);
                // FIT-138 multi-image refactor: the legacy single `file`
                // variable no longer exists; use the first attached photo
                // for the legacy imageFile field and the full list for
                // imageFiles so the mock harness mirrors the real submit.
                handleMealIntakeResponse(payload, { textValue, clientId, imageFile: files[0] || null, imageFiles: files, localTime });
                return;
            }
            const res = await fetch('/api/meal-intake', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Accept': 'application/json', [CSRF_HEADER_NAME]: CSRF_HEADER_VALUE },
                body: form,
            });
            if (res.status === 404 || res.status === 501) {
                setMealBackendUnavailable();
                setMealComposerError('Meal intake isn’t enabled yet. Your draft is saved.');
                saveMealDraft();
                // Backend gone: nothing to retry against; clear retry state.
                mealComposerState.lastSubmitFailedTransient = false;
                refreshMealComposerRetryUI();
                return;
            }
            const ct = res.headers.get('content-type') || '';
            const payload = ct.includes('application/json') ? await res.json() : null;
            if (!res.ok) {
                const msg = (payload && payload.error && payload.error.message) || `Couldn’t log meal (${res.status}).`;
                setMealComposerError(msg);
                if (res.status >= 500) {
                    // FIT-138: transient server failure — preserve the draft
                    // and the draftClientId so Retry re-uses the same id.
                    mealComposerState.lastSubmitFailedTransient = true;
                } else {
                    // 4xx: the request itself is wrong (validation, too many
                    // photos, bad mime). Don't offer Retry on a bad request.
                    mealComposerState.lastSubmitFailedTransient = false;
                    mealComposerState.draftClientId = null;
                }
                refreshMealComposerRetryUI();
                return;
            }
            // FIT-6 + FIT-66/FIT-67: pass imageFiles + the captured
            // three-field localTime through so the pending entry can
            // power Retry without needing the file picker again AND
            // without losing the original day.
            handleMealIntakeResponse(payload, {
                textValue,
                clientId,
                imageFiles: files,
                localTime,
            });
            // Success: reset the in-composer retry state.
            mealComposerState.draftClientId = null;
            mealComposerState.lastSubmitFailedTransient = false;
            refreshMealComposerRetryUI();
        } catch (e) {
            console.error(e);
            saveMealDraft();
            // FIT-138: network/transport failure is transient — keep
            // draftClientId so Retry submits idempotently under the same id.
            mealComposerState.lastSubmitFailedTransient = true;
            refreshMealComposerRetryUI();
            toast('Couldn’t reach the meal estimator — your draft is saved.', 'err');
        } finally {
            mealComposerState.submitting = false;
            refreshMealSubmitState();
        }
    }

    function handleMealIntakeResponse(payload, ctx) {
        ctx = ctx || {};
        // FIT-134: branch to multi-item review when the backend returns the
        // new contract (meal_id + items[]). Until FIT-135 lands that shape,
        // legacy single-item responses fall through to the original flow.
        if (isMealV2Payload(payload)) {
            handleMealIntakeV2Response(payload, ctx);
            return;
        }
        const status = payload && payload.status;
        if (status === 'logged') {
            if (!ctx.fromQueue) {
                clearMealComposerInputs();
                clearMealDraft();
            }
            const msg = mealEstimateChip(payload.estimate);
            const entry = mealEntryFromIntakePayload(payload, ctx.clientId);
            toastUndo(
                msg,
                () => postMealUndo(ctx.clientId),
                MEAL_UNDO_MS,
                () => openMealDetailModal(entry),
            );
            if (!ctx.fromQueue) renderMealComposerProvenance(payload.estimate, ctx.clientId);
            refreshMacroCard();
            return;
        }
        if (status === 'pending_review') {
            // FIT-6 + FIT-67 + FIT-138: hydrate the local pending entry
            // from the server response. ctx.imageFiles carries the
            // captured File handles plus per-file blob preview URLs so
            // the pending-card retry path (FIT-128) can resubmit the
            // same photos and the user can see thumbnails. The blob
            // URLs are revoked on accept/discard (Phase 4 privacy
            // cleanup).
            const localTime = ctx.localTime || {};
            const imageFiles = Array.isArray(ctx.imageFiles) ? ctx.imageFiles.slice() : [];
            const imagePreviewUrls = imageFiles.map((file) => URL.createObjectURL(file));
            upsertMealPendingEntry({
                client_id: ctx.clientId,
                estimate: payload.estimate || {},
                text: ctx.textValue || '',
                text_hint: ctx.textValue || '',
                local_timestamp: payload.local_timestamp || localTime.local_timestamp || null,
                local_date: payload.local_date || localTime.local_date || null,
                local_iso: payload.local_iso || localTime.local_iso || null,
                logged_at: payload.food_log && payload.food_log.logged_at,
                policy: payload.policy || null,
                // First file kept for the legacy single-image consumers
                // (mealPendingOriginals, the FIT-128 pending-card retry
                // path before this PR fully migrates it). The full
                // array is the canonical handle going forward.
                imageFile: imageFiles[0] || null,
                imageFiles,
                imagePreviewUrls,
            });
            if (!ctx.fromQueue && !ctx.preserveComposerDraft) {
                clearMealComposerInputs();
                clearMealDraft();
                clearMealComposerStatus();
            }
            renderMealPendingList();
            toast('Review the estimate before it counts toward today.', 'warn');
            refreshMacroCard();
            return;
        }
        setMealComposerError('Couldn’t parse that meal — try a clearer description.');
    }

    // ─────────────────────────────────────────────────────────────────────
    // FIT-134 — Multi-item meal review (V2).
    //
    // Activates when /api/meal-intake (or refresh) returns the new
    // contract shape: top-level meal_id, meal_totals, followup, items[]
    // with item_id/candidates/unclear/status, and save_blocked_item_ids[].
    // Until FIT-135 (Codex, sibling-backend) lands that contract, legacy
    // single-item responses fall through to the original review card and
    // the existing accept/discard flow.
    //
    // A ?fit134=mock URL param swaps the real backend for an in-memory
    // synthetic backend so the new UI can be exercised end-to-end locally
    // before FIT-135 ships. The production path is unchanged.
    //
    // Contract source: /Users/admin/.claude/plans/codex-is-owrking-on-shiny-ripple.md
    // (locked via codex-consensus-loop on 2026-05-22).
    // ─────────────────────────────────────────────────────────────────────

    const MEAL_V2_REFRESH_KINDS = [
        'add_item', 'edit_portion', 'followup_answer', 'choose_candidate',
        'skip_item', 'delete_item', 'restore_item', 'set_meal_type',
    ];
    // FIT-144 backend (app.py:_REVIEW_REQUEST_ID_KINDS) requires a client-
    // generated request_id for these 4 kinds and treats same-id+same-kind as
    // an idempotent replay (returns the prior payload without applying the
    // new body). A fresh UUID per logical mutation attempt is the correct
    // contract; reusing one across distinct user actions would silently drop
    // the second action's input.
    const MEAL_V2_REQUEST_ID_KINDS = new Set([
        'add_item', 'edit_portion', 'choose_candidate', 'followup_answer',
    ]);
    const MEAL_V2_MACRO_KEYS = ['calories', 'protein_g', 'carbs_g', 'fat_g', 'sodium_mg', 'fiber_g'];
    const MEAL_V2_ITEM_STATUSES = ['included', 'skipped', 'deleted'];
    const MEAL_V2_SOURCE_KINDS = ['vision', 'text', 'branded', 'vocab', 'manual'];

    function mealV2GenerateRequestId() {
        if (window.crypto && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
        return 'meal-v2-req-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
    }

    function isMealV2Payload(payload) {
        return !!(payload && typeof payload === 'object'
            && typeof payload.meal_id === 'string'
            && Array.isArray(payload.items));
    }

    function normalizeMealV2Entry(payload, ctx = {}) {
        if (!isMealV2Payload(payload)) return null;
        const existing = mealV2EntryById(payload.meal_id);
        const expandedItems = existing && existing.expandedItems instanceof Set
            ? new Set(existing.expandedItems)
            : new Set();
        const lastFollowupAnswered = !!(payload.followup && payload.followup.used)
            || (existing && existing.lastFollowupAnswered)
            || false;
        return {
            __v2: true,
            meal_id: payload.meal_id,
            meal_type: MEAL_TYPE_OPTIONS.includes(payload.meal_type) ? payload.meal_type : 'snack',
            meal_totals: payload.meal_totals || {},
            followup: payload.followup || { available: false, question: null, used: false },
            save_blocked_item_ids: Array.isArray(payload.save_blocked_item_ids)
                ? payload.save_blocked_item_ids.slice()
                : [],
            items: Array.isArray(payload.items) ? payload.items.map((it) => ({ ...it })) : [],
            expandedItems,
            pendingRefresh: false,
            lastFollowupAnswered,
            text_hint: (existing && existing.text_hint) || ctx.textValue || '',
            imageFile: (existing && existing.imageFile) || ctx.imageFile || null,
            local_timestamp: payload.local_timestamp || (existing && existing.local_timestamp) || null,
            local_date: payload.local_date || (existing && existing.local_date) || null,
            local_iso: payload.local_iso || (existing && existing.local_iso) || null,
        };
    }

    function mealV2EntryById(mealId) {
        return mealComposerState.pending.find((p) => p && p.__v2 && p.meal_id === mealId) || null;
    }

    function upsertMealV2Entry(entry) {
        if (!entry || !entry.__v2) return;
        const idx = mealComposerState.pending.findIndex((p) => p && p.__v2 && p.meal_id === entry.meal_id);
        if (idx >= 0) mealComposerState.pending[idx] = entry;
        else mealComposerState.pending.push(entry);
    }

    function removeMealV2Entry(mealId) {
        mealComposerState.pending = mealComposerState.pending.filter((p) => !(p && p.__v2 && p.meal_id === mealId));
    }

    function handleMealIntakeV2Response(payload, ctx = {}) {
        const entry = normalizeMealV2Entry(payload, ctx);
        if (!entry) return false;
        upsertMealV2Entry(entry);
        if (!ctx.fromQueue) {
            if (!ctx.preserveComposerDraft) {
                clearMealComposerInputs();
                clearMealDraft();
            }
            clearMealComposerStatus();
        }
        renderMealPendingList();
        toast('Review the meal before saving.', 'warn');
        refreshMacroCard();
        return true;
    }

    function applyMealV2Refresh(mealId, payload) {
        const entry = normalizeMealV2Entry(payload);
        if (!entry) return;
        // FIT-134: skipped/deleted items remain in items[]; auto-expand any
        // item that becomes save-blocked so the user sees what to fix.
        (entry.save_blocked_item_ids || []).forEach((id) => entry.expandedItems.add(id));
        upsertMealV2Entry(entry);
        renderMealPendingList();
    }

    function setMealV2PendingRefresh(mealId, value) {
        const entry = mealV2EntryById(mealId);
        if (!entry) return;
        entry.pendingRefresh = !!value;
        renderMealPendingList();
    }

    // ── Number/label helpers ────────────────────────────────────────────
    function formatMealV2Kcal(value) {
        const n = Number(value);
        if (!Number.isFinite(n)) return '— kcal';
        return `${Math.round(n)} kcal`;
    }

    function formatMealV2Macros(totals) {
        if (!totals) return '';
        const macros = [];
        if (Number.isFinite(Number(totals.protein_g))) macros.push(`${Math.round(totals.protein_g)}P`);
        if (Number.isFinite(Number(totals.carbs_g))) macros.push(`${Math.round(totals.carbs_g)}C`);
        if (Number.isFinite(Number(totals.fat_g))) macros.push(`${Math.round(totals.fat_g)}F`);
        return macros.join(' · ');
    }

    function formatMealV2ItemMacros(item) {
        if (!item) return '';
        const parts = [];
        if (Number.isFinite(Number(item.calories))) parts.push(`${Math.round(item.calories)} kcal`);
        const macros = [];
        if (Number.isFinite(Number(item.protein_g))) macros.push(`${Math.round(item.protein_g)}P`);
        if (Number.isFinite(Number(item.carbs_g))) macros.push(`${Math.round(item.carbs_g)}C`);
        if (Number.isFinite(Number(item.fat_g))) macros.push(`${Math.round(item.fat_g)}F`);
        if (macros.length) parts.push(macros.join('/'));
        return parts.join(' · ');
    }

    function mealV2ConfidenceLabel(value) {
        const n = Number(value);
        if (!Number.isFinite(n)) return '';
        return `${Math.round(n * 100)}% conf`;
    }

    // ── Renderer ────────────────────────────────────────────────────────
    function buildMealReviewCardV2(entry) {
        const row = document.createElement('div');
        row.className = 'meal-pending-row meal-review-v2';
        row.setAttribute('data-meal-id', entry.meal_id);
        // FIT-219 item 1: make the card a programmatic focus target so
        // renderMealPendingList() can move keyboard focus here on create.
        row.setAttribute('tabindex', '-1');
        row.setAttribute('role', 'group');
        row.setAttribute('aria-label', 'Meal to review before saving');
        if (entry.pendingRefresh) row.classList.add('meal-review-v2--refreshing');

        // FIT-210: a barcode lookup with no verified nutrition source comes back
        // as a manual-review meal (item source kind "barcode_pending_source").
        // Stamp a persistent data-source-kind on the row and render a standing
        // "Manual review" badge in the head so the card is visibly distinct from
        // a verified one and the manual-entry requirement survives every
        // re-render — unlike the prior transient toast.
        const isManualReviewMeal = Array.isArray(entry.items)
            && entry.items.some((it) => it && it.source && it.source.kind === 'barcode_pending_source');
        if (isManualReviewMeal) row.setAttribute('data-source-kind', 'barcode_pending');
        // role="note" + aria-label surfaces the manual-entry reason to assistive
        // tech (a title tooltip alone is not discoverable to screen-reader or
        // keyboard users). The accessible name keeps the visible "Manual review"
        // label so it matches what sighted users read (WCAG Label in Name).
        const reviewBadgeHtml = isManualReviewMeal
            ? '<span class="meal-pending-review-badge" role="note" aria-label="Manual review — no verified barcode nutrition source was available; review and edit the item before saving." title="No verified barcode nutrition source was available — review and edit the item before saving.">Manual review</span>'
            : '';

        const totals = entry.meal_totals || {};
        const includedItems = entry.items.filter((it) => it.status === 'included');
        const blockedSet = new Set(entry.save_blocked_item_ids || []);
        const blocked = blockedSet.size > 0;
        const allRemoved = includedItems.length === 0 && entry.items.length > 0;
        const expanded = entry.expandedItems.size > 0 || blocked || allRemoved;

        const mealTypeChip = `
            <label class="meal-review-v2-meal-type" data-field-label="meal_type">
                <span class="meal-review-v2-meal-type-label">Meal</span>
                <select data-action="set-meal-type" aria-label="Meal type"${entry.pendingRefresh ? ' disabled' : ''}>
                    ${MEAL_TYPE_OPTIONS.map((mt) => `<option value="${mt}"${mt === entry.meal_type ? ' selected' : ''}>${mt.charAt(0).toUpperCase() + mt.slice(1)}</option>`).join('')}
                </select>
            </label>
        `;

        const totalsHtml = `
            <div class="meal-review-v2-totals" aria-label="Meal totals">
                <span class="meal-review-v2-kcal">${escapeHtml(formatMealV2Kcal(totals.calories))}</span>
                <span class="meal-review-v2-macros">${escapeHtml(formatMealV2Macros(totals))}</span>
            </div>
        `;

        const actionsHtml = allRemoved ? `
            <div class="meal-pending-actions meal-review-v2-actions" data-state="all-removed">
                <button type="button" class="btn btn-primary" data-action="discard-log">Discard log</button>
            </div>
        ` : `
            <div class="meal-pending-actions meal-review-v2-actions">
                <button type="button" class="btn btn-ghost" data-action="discard">Discard</button>
                <button type="button" class="btn btn-primary" data-action="save"${blocked ? ' disabled' : ''}>${blocked ? 'Resolve items to save' : 'Save'}</button>
            </div>
        `;

        const showFollowup = entry.followup
            && entry.followup.available
            && !entry.followup.used
            && !entry.lastFollowupAnswered;
        const followupHtml = showFollowup ? `
            <form class="meal-review-v2-followup" data-action="followup-form" role="region" aria-label="Follow-up question">
                <div class="meal-review-v2-followup-q">${escapeHtml(entry.followup.question || '')}</div>
                <div class="meal-review-v2-followup-row">
                    <input type="text" data-field="followup-answer" placeholder="Type your answer" maxlength="240" required${entry.pendingRefresh ? ' disabled' : ''}>
                    <button type="submit" class="btn btn-primary"${entry.pendingRefresh ? ' disabled' : ''}>Submit</button>
                    <button type="button" class="btn btn-ghost" data-action="followup-dismiss"${entry.pendingRefresh ? ' disabled' : ''}>Skip</button>
                </div>
            </form>
        ` : '';

        const itemsHtml = entry.items.length ? `
            <div class="meal-review-v2-items" role="list">
                ${entry.items.map((item) => buildMealReviewV2ItemHtml(item, {
                    blocked: blockedSet.has(item.item_id),
                    expanded: entry.expandedItems.has(item.item_id) || blockedSet.has(item.item_id),
                    pendingRefresh: entry.pendingRefresh,
                    mealId: entry.meal_id,
                })).join('')}
            </div>
        ` : '<div class="meal-review-v2-empty">No items in this meal yet.</div>';

        const addItemHtml = `
            <form class="meal-review-v2-add-item" data-action="add-item-form">
                <label>
                    <span>Add an item (describe in your own words)</span>
                    <input type="text" data-field="add-item-text" placeholder="e.g. a small side of rice, or a 16 oz coke" maxlength="240" required${entry.pendingRefresh ? ' disabled' : ''}>
                </label>
                <button type="submit" class="btn btn-ghost"${entry.pendingRefresh ? ' disabled' : ''}>Add</button>
            </form>
        `;

        row.innerHTML = `
            <div class="meal-review-v2-collapsed">
                <div class="meal-pending-head">
                    <span class="meal-pending-title">Review meal</span>
                    ${reviewBadgeHtml}
                    ${mealTypeChip}
                </div>
                ${totalsHtml}
                ${actionsHtml}
                <button type="button" class="meal-review-v2-expand" id="meal-v2-expand-${escapeHtml(entry.meal_id)}" aria-controls="meal-v2-expanded-${escapeHtml(entry.meal_id)}" data-action="toggle-expand" aria-expanded="${expanded}">${expanded ? 'Hide items' : 'Show items'}</button>
                ${entry.pendingRefresh ? '<div class="meal-review-v2-refreshing-note" role="status">Looking up…</div>' : ''}
            </div>
            <div class="meal-review-v2-expanded" id="meal-v2-expanded-${escapeHtml(entry.meal_id)}"${expanded ? '' : ' hidden'}>
                ${followupHtml}
                ${itemsHtml}
                ${addItemHtml}
            </div>
        `;

        wireMealReviewCardV2(row, entry);
        return row;
    }

    function buildMealReviewV2ItemHtml(item, opts = {}) {
        if (!item) return '';
        const status = item.status || 'included';
        const isIncluded = status === 'included';
        const isRemoved = status === 'skipped' || status === 'deleted';
        const blocked = !!opts.blocked && isIncluded;
        const expanded = !!opts.expanded || blocked;
        const pendingRefresh = !!opts.pendingRefresh;
        // FIT-219 item 3: item_id is only unique within a meal, so namespace the
        // toggle/body ids with the meal id to keep aria-controls targets globally
        // unique across multiple review cards on the page.
        const itemKey = `${opts.mealId || 'meal'}-${item.item_id}`;
        const sourceLabel = (item.source && item.source.label) || 'AI estimate';
        const sourceKind = item.source && MEAL_V2_SOURCE_KINDS.includes(item.source.kind) ? item.source.kind : 'manual';
        const sourceLink = item.source && item.source.link ? String(item.source.link) : '';
        const confLabel = mealV2ConfidenceLabel(item.confidence);
        const candidates = Array.isArray(item.candidates) ? item.candidates.slice(0, 3) : [];

        const candidatesHtml = (isIncluded && candidates.length) ? `
            <div class="meal-review-v2-candidates" role="group" aria-label="Top choices">
                <span class="meal-review-v2-candidates-label">Top choices</span>
                ${candidates.map((c) => `
                    <button type="button" class="meal-review-v2-candidate-chip" data-action="choose-candidate" data-candidate-id="${escapeHtml(c.candidate_id)}"${pendingRefresh ? ' disabled' : ''}>
                        ${escapeHtml(c.name || '')}${(c.portion || c.portion_description) ? ` · ${escapeHtml(c.portion || c.portion_description)}` : ''}
                    </button>
                `).join('')}
            </div>
        ` : '';

        const portionEditHtml = isIncluded ? `
            <form class="meal-review-v2-portion-edit" data-action="portion-edit-form" hidden>
                <label>
                    <span>Edit portion (describe in your own words)</span>
                    <input type="text" data-field="portion-text" placeholder="e.g. half the plate, or a 16 oz serving" maxlength="240" required${pendingRefresh ? ' disabled' : ''}>
                </label>
                <div class="meal-review-v2-portion-edit-actions">
                    <button type="button" class="btn btn-ghost" data-action="portion-edit-cancel"${pendingRefresh ? ' disabled' : ''}>Cancel</button>
                    <button type="submit" class="btn btn-primary"${pendingRefresh ? ' disabled' : ''}>Update</button>
                </div>
            </form>
        ` : '';

        const itemActions = isIncluded ? `
            <div class="meal-review-v2-item-actions">
                <button type="button" class="btn btn-ghost meal-review-v2-portion-edit-toggle" data-action="portion-edit-open"${pendingRefresh ? ' disabled' : ''}>Edit portion</button>
                <button type="button" class="btn btn-ghost" data-action="skip-item"${pendingRefresh ? ' disabled' : ''}>Skip</button>
                <button type="button" class="btn btn-ghost" data-action="delete-item"${pendingRefresh ? ' disabled' : ''}>Delete</button>
            </div>
        ` : `
            <div class="meal-review-v2-item-actions meal-review-v2-item-actions--removed">
                <span class="meal-review-v2-item-removed-label">${status === 'skipped' ? 'Skipped' : 'Deleted'}</span>
                <button type="button" class="btn btn-ghost meal-review-v2-undo" data-action="restore-item"${pendingRefresh ? ' disabled' : ''}>Undo</button>
            </div>
        `;

        const blockedNote = blocked ? '<div class="meal-review-v2-item-blocked" role="alert">Save blocked — clarify, choose a top match, edit, or skip.</div>' : '';

        const sourceChip = sourceLink ? `
            <button type="button" class="meal-review-v2-source-chip" data-action="open-source" data-source-link="${escapeHtml(sourceLink)}" data-source-label="${escapeHtml(sourceLabel)}" title="Open source details in app">
                ${escapeHtml(sourceLabel)}
            </button>
        ` : `<span class="meal-review-v2-source-chip" data-source-kind="${escapeHtml(sourceKind)}">${escapeHtml(sourceLabel)}</span>`;

        return `
            <div class="meal-review-v2-item${isRemoved ? ' meal-review-v2-item--removed' : ''}${blocked ? ' meal-review-v2-item--blocked' : ''}${expanded ? ' meal-review-v2-item--expanded' : ''}" role="listitem" data-item-id="${escapeHtml(item.item_id)}" data-item-status="${escapeHtml(status)}">
                <header class="meal-review-v2-item-head">
                    <button type="button" class="meal-review-v2-item-toggle" id="meal-v2-item-toggle-${escapeHtml(itemKey)}" aria-controls="meal-v2-item-body-${escapeHtml(itemKey)}" data-action="toggle-item" aria-expanded="${expanded}">
                        <span class="meal-review-v2-item-name">${escapeHtml(item.name || 'Item')}</span>
                        <span class="meal-review-v2-item-portion">${escapeHtml(item.portion || item.portion_description || '')}</span>
                    </button>
                    ${sourceChip}
                    ${confLabel ? `<span class="meal-review-v2-item-conf">${escapeHtml(confLabel)}</span>` : ''}
                </header>
                <div class="meal-review-v2-item-body" id="meal-v2-item-body-${escapeHtml(itemKey)}"${expanded ? '' : ' hidden'}>
                    <div class="meal-review-v2-item-macros">${escapeHtml(formatMealV2ItemMacros(item))}</div>
                    ${blockedNote}
                    ${candidatesHtml}
                    ${portionEditHtml}
                    ${itemActions}
                </div>
            </div>
        `;
    }

    // ── Event wiring ───────────────────────────────────────────────────
    function wireMealReviewCardV2(row, entry) {
        const mealId = entry.meal_id;

        // Toggle whole card expanded/collapsed
        row.querySelector('[data-action="toggle-expand"]').addEventListener('click', () => {
            const expandedEl = row.querySelector('.meal-review-v2-expanded');
            const isHidden = expandedEl.hasAttribute('hidden');
            if (isHidden) {
                // Mark every item visible — gives the user the full picture
                entry.items.forEach((it) => entry.expandedItems.add(it.item_id));
            } else {
                entry.expandedItems.clear();
            }
            renderMealPendingList();
        });

        // Meal type edit (collapsed-view chip)
        const mealTypeSelect = row.querySelector('[data-action="set-meal-type"]');
        if (mealTypeSelect) {
            mealTypeSelect.addEventListener('change', () => {
                const next = mealTypeSelect.value;
                if (!MEAL_TYPE_OPTIONS.includes(next)) return;
                submitMealV2Refresh(mealId, { kind: 'set_meal_type', meal_type: next });
            });
        }

        // Save / Discard / Discard log
        const saveBtn = row.querySelector('[data-action="save"]');
        if (saveBtn) saveBtn.addEventListener('click', () => acceptMealV2(mealId));
        const discardBtn = row.querySelector('[data-action="discard"]');
        if (discardBtn) discardBtn.addEventListener('click', () => discardMealV2(mealId));
        const discardLogBtn = row.querySelector('[data-action="discard-log"]');
        if (discardLogBtn) discardLogBtn.addEventListener('click', () => discardMealV2(mealId));

        // Follow-up
        const followupForm = row.querySelector('[data-action="followup-form"]');
        if (followupForm) {
            followupForm.addEventListener('submit', (ev) => {
                ev.preventDefault();
                const input = followupForm.querySelector('[data-field="followup-answer"]');
                const text = (input && input.value || '').trim();
                if (!text) return;
                // FIT-144 backend (app.py followup_answer handler) reads
                // the user's reply from `answer` and treats absent text as
                // a no-op. `skipped: true` would also be accepted but here
                // the user is submitting an answer so we send `answer`.
                submitMealV2Refresh(mealId, { kind: 'followup_answer', answer: text });
            });
            const dismissBtn = followupForm.querySelector('[data-action="followup-dismiss"]');
            if (dismissBtn) {
                dismissBtn.addEventListener('click', () => {
                    entry.lastFollowupAnswered = true;
                    renderMealPendingList();
                });
            }
        }

        // Add item
        const addItemForm = row.querySelector('[data-action="add-item-form"]');
        if (addItemForm) {
            addItemForm.addEventListener('submit', (ev) => {
                ev.preventDefault();
                const input = addItemForm.querySelector('[data-field="add-item-text"]');
                const text = (input && input.value || '').trim();
                if (!text) return;
                submitMealV2Refresh(mealId, { kind: 'add_item', text });
                if (input) input.value = '';
            });
        }

        // Per-item actions
        row.querySelectorAll('.meal-review-v2-item').forEach((itemEl) => {
            const itemId = itemEl.getAttribute('data-item-id');
            const toggleBtn = itemEl.querySelector('[data-action="toggle-item"]');
            if (toggleBtn) {
                toggleBtn.addEventListener('click', () => {
                    if (entry.expandedItems.has(itemId)) entry.expandedItems.delete(itemId);
                    else entry.expandedItems.add(itemId);
                    renderMealPendingList();
                });
            }
            itemEl.querySelectorAll('[data-action="choose-candidate"]').forEach((chip) => {
                chip.addEventListener('click', () => {
                    const candidateId = chip.getAttribute('data-candidate-id');
                    submitMealV2Refresh(mealId, { kind: 'choose_candidate', item_id: itemId, candidate_id: candidateId });
                });
            });
            const portionForm = itemEl.querySelector('[data-action="portion-edit-form"]');
            const portionOpen = itemEl.querySelector('[data-action="portion-edit-open"]');
            const portionCancel = itemEl.querySelector('[data-action="portion-edit-cancel"]');
            if (portionForm && portionOpen) {
                portionOpen.addEventListener('click', () => {
                    portionForm.hidden = false;
                    portionOpen.hidden = true;
                    const input = portionForm.querySelector('[data-field="portion-text"]');
                    if (input) input.focus();
                });
                if (portionCancel) {
                    portionCancel.addEventListener('click', () => {
                        portionForm.hidden = true;
                        portionOpen.hidden = false;
                    });
                }
                portionForm.addEventListener('submit', (ev) => {
                    ev.preventDefault();
                    const input = portionForm.querySelector('[data-field="portion-text"]');
                    const text = (input && input.value || '').trim();
                    if (!text) return;
                    submitMealV2Refresh(mealId, { kind: 'edit_portion', item_id: itemId, text });
                });
            }
            const skipBtn = itemEl.querySelector('[data-action="skip-item"]');
            if (skipBtn) skipBtn.addEventListener('click', () => submitMealV2Refresh(mealId, { kind: 'skip_item', item_id: itemId }));
            const deleteBtn = itemEl.querySelector('[data-action="delete-item"]');
            if (deleteBtn) deleteBtn.addEventListener('click', () => submitMealV2Refresh(mealId, { kind: 'delete_item', item_id: itemId }));
            const restoreBtn = itemEl.querySelector('[data-action="restore-item"]');
            if (restoreBtn) restoreBtn.addEventListener('click', () => submitMealV2Refresh(mealId, { kind: 'restore_item', item_id: itemId }));
            const sourceBtn = itemEl.querySelector('[data-action="open-source"]');
            if (sourceBtn) {
                sourceBtn.addEventListener('click', () => {
                    openMealV2SourceViewer(sourceBtn.getAttribute('data-source-link'), sourceBtn.getAttribute('data-source-label'));
                });
            }
        });
    }

    // ── Network: refresh / accept / discard ────────────────────────────
    async function submitMealV2Refresh(mealId, body) {
        if (!body || !MEAL_V2_REFRESH_KINDS.includes(body.kind)) return;
        const entry = mealV2EntryById(mealId);
        if (!entry || entry.pendingRefresh) return;
        // FIT-144 idempotency: every user-initiated mutation in the guarded
        // set gets a fresh request_id. `pendingRefresh` already serializes
        // calls so each invocation is one logical attempt.
        const liveBody = MEAL_V2_REQUEST_ID_KINDS.has(body.kind) && !body.request_id
            ? { ...body, request_id: mealV2GenerateRequestId() }
            : body;
        setMealV2PendingRefresh(mealId, true);
        try {
            const payload = await postMealV2Refresh(mealId, liveBody);
            applyMealV2Refresh(mealId, payload);
        } catch (e) {
            console.error(e);
            toast(apiErrorMessage(e, 'Couldn’t update meal'), 'err');
            setMealV2PendingRefresh(mealId, false);
        }
    }

    // FIT-144 _meal_intake_accept_multi (app.py:5709) expects a JSON body
    // `{ meal_id, items: [{state, item_id, estimate, ...}] }`. The backend's
    // `_review_sanitize_estimate` re-validates the per-item `estimate` dict
    // (requires `ambiguous` bool, `source` string, etc.); reuse the backend's
    // own item.estimate so it round-trips schema-clean. Skipped/deleted items
    // stay in the array so the backend can record negative feedback.
    //
    // The persistence path reads `meal_type` from each item's estimate
    // (food_logs row meal_type comes from estimate.meal_type), so the
    // collapsed-chip selection (`entry.meal_type`) is merged into each
    // included item before send. set_meal_type refresh updates
    // payload.meal_type but does not rewrite per-item estimates, so without
    // this merge the user's edit silently fails to persist.
    function buildMealV2AcceptBody(entry) {
        const mealType = MEAL_TYPE_OPTIONS.includes(entry.meal_type) ? entry.meal_type : null;
        const body = {
            meal_id: entry.meal_id,
            meal_type: entry.meal_type,
            items: (entry.items || []).map((item) => {
                const state = MEAL_V2_ITEM_STATUSES.includes(item.status) ? item.status : 'included';
                const baseEstimate = item.estimate || item.original_estimate || {};
                const estimate = (state === 'included' && mealType)
                    ? { ...baseEstimate, meal_type: mealType }
                    : baseEstimate;
                return {
                    state,
                    item_id: item.item_id,
                    text: item.text || item.name || '',
                    estimate,
                    original_estimate: item.original_estimate || item.estimate || null,
                };
            }),
        };
        if (entry.local_timestamp) body.local_timestamp = entry.local_timestamp;
        if (entry.local_date) body.local_date = entry.local_date;
        if (entry.local_iso) body.local_iso = entry.local_iso;
        return body;
    }

    async function acceptMealV2(mealId) {
        const entry = mealV2EntryById(mealId);
        if (!entry || entry.pendingRefresh) return;
        if ((entry.save_blocked_item_ids || []).length > 0) {
            (entry.save_blocked_item_ids || []).forEach((id) => entry.expandedItems.add(id));
            renderMealPendingList();
            toast('Resolve flagged items before saving.', 'warn');
            return;
        }
        const includedCount = entry.items.filter((it) => it.status === 'included').length;
        if (includedCount === 0) {
            toast('No items left to save — discard the log instead.', 'warn');
            return;
        }
        setMealV2PendingRefresh(mealId, true);
        try {
            if (mealV2MockEnabled()) {
                mealV2Mock.accept(mealId);
            } else {
                await api(`/api/meal-intake/${encodeURIComponent(mealId)}/accept`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(buildMealV2AcceptBody(entry)),
                });
            }
            removeMealV2Entry(mealId);
            renderMealPendingList();
            toast('Meal saved.', 'ok');
            refreshMacroCard();
        } catch (e) {
            console.error(e);
            toast(apiErrorMessage(e, 'Save failed'), 'err');
            setMealV2PendingRefresh(mealId, false);
        }
    }

    async function discardMealV2(mealId) {
        const entry = mealV2EntryById(mealId);
        if (!entry) return;
        setMealV2PendingRefresh(mealId, true);
        try {
            if (mealV2MockEnabled()) {
                mealV2Mock.discard(mealId);
            } else {
                await api(`/api/meal-intake/${encodeURIComponent(mealId)}`, { method: 'DELETE' });
            }
            removeMealV2Entry(mealId);
            renderMealPendingList();
            toast('Meal discarded.', 'ok');
            refreshMacroCard();
        } catch (e) {
            console.error(e);
            toast(apiErrorMessage(e, 'Discard failed'), 'err');
            setMealV2PendingRefresh(mealId, false);
        }
    }

    async function postMealV2Refresh(mealId, body) {
        if (mealV2MockEnabled()) {
            return mealV2Mock.refresh(mealId, body);
        }
        return api(`/api/meal-intake/${encodeURIComponent(mealId)}/refresh`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
    }

    // ── In-app source viewer ────────────────────────────────────────────
    // The contract guarantees source.link is null, a same-origin route, or a
    // backend-sanitized URL safe for an in-app sandboxed viewer. We never
    // open target=_blank on these links per FIT-134 acceptance.
    function openMealV2SourceViewer(link, label) {
        if (!link) return;
        const safe = sanitizeMealV2SourceLink(link);
        if (!safe) return;
        const modal = document.createElement('div');
        modal.className = 'meal-review-v2-source-modal modal';
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-label', `${label || 'Source'} details`);
        modal.innerHTML = `
            <div class="modal-backdrop" data-action="close-source"></div>
            <div class="modal-content meal-review-v2-source-modal-content">
                <header class="meal-review-v2-source-modal-head">
                    <h3>${escapeHtml(label || 'Source')}</h3>
                    <button type="button" class="meal-review-v2-source-modal-close" aria-label="Close" data-action="close-source">✕</button>
                </header>
                <iframe class="meal-review-v2-source-frame" src="${escapeHtml(safe)}" sandbox="allow-same-origin" referrerpolicy="no-referrer" loading="lazy"></iframe>
                <button type="button" class="visually-hidden" data-action="focus-source-close">Return to source viewer controls</button>
            </div>
        `;
        document.body.appendChild(modal);
        const close = () => modal.remove();
        modal.__fit192Close = close;
        focusOpenModal(modal);
        const closeButton = modal.querySelector('.meal-review-v2-source-modal-close');
        modal.querySelectorAll('[data-action="close-source"]').forEach((el) => el.addEventListener('click', () => closeModal(modal)));
        modal.querySelectorAll('[data-action="focus-source-close"]').forEach((el) => {
            el.addEventListener('focus', () => closeButton && closeButton.focus({ preventScroll: true }));
        });
    }

    function sanitizeMealV2SourceLink(link) {
        const value = String(link || '').trim();
        if (!value) return '';
        // Defense-in-depth at the iframe-src seam. The contract commits to
        // null | same-origin route | sanitized URL, but a protocol-relative
        // value like "//evil.example.com/x" passed straight through would
        // escape the current origin once rendered in an iframe src — the
        // browser pairs it with the current page's protocol against the
        // attacker-controlled host. Always resolve through new URL() and
        // require the resolved origin to equal window.location.origin,
        // with an extra explicit reject for "//"-leading inputs so that
        // even a buggy URL polyfill cannot widen the surface.
        if (value.startsWith('//')) return '';
        try {
            const u = new URL(value, window.location.origin);
            if (u.origin !== window.location.origin) return '';
            if (u.protocol !== window.location.protocol) return '';
            return u.pathname + u.search + u.hash;
        } catch (_) {
            return '';
        }
    }

    // ── Mock backend (?fit134=mock) ─────────────────────────────────────
    function mealV2MockEnabled() {
        try {
            const params = new URLSearchParams(window.location.search);
            return params.get('fit134') === 'mock';
        } catch (_) {
            return false;
        }
    }

    const mealV2Mock = {
        store: new Map(),
        createMeal(textValue) {
            const mealId = 'mock-meal-' + Math.random().toString(36).slice(2, 10);
            const payload = {
                meal_id: mealId,
                meal_type: this.inferMealType(textValue) || 'lunch',
                followup: {
                    available: true,
                    question: 'How big was the side of rice — a small scoop, half a cup, or a full cup?',
                    used: false,
                },
                items: [
                    {
                        item_id: mealId + '-i1',
                        name: 'Grilled chicken breast',
                        portion_description: '6 oz',
                        calories: 280, protein_g: 53, carbs_g: 0, fat_g: 6, sodium_mg: 95, fiber_g: 0,
                        confidence: 0.88,
                        source: { kind: 'vision', label: 'AI estimate', link: null },
                        unclear: false,
                        candidates: [],
                        status: 'included',
                    },
                    {
                        item_id: mealId + '-i2',
                        name: 'Side of rice',
                        portion_description: 'small',
                        calories: 160, protein_g: 3, carbs_g: 35, fat_g: 0, sodium_mg: 1, fiber_g: 0,
                        confidence: 0.42,
                        source: { kind: 'text', label: 'Text parser', link: null },
                        unclear: true,
                        candidates: [
                            { candidate_id: 'c-rice-white-cup', name: 'White rice', portion_description: '1 cup', source: { kind: 'branded', label: 'USDA', link: '/api/sources/usda/white-rice' } },
                            { candidate_id: 'c-rice-brown-cup', name: 'Brown rice', portion_description: '1 cup', source: { kind: 'branded', label: 'USDA', link: '/api/sources/usda/brown-rice' } },
                        ],
                        status: 'included',
                    },
                ],
            };
            this.recompute(payload);
            this.store.set(mealId, payload);
            return JSON.parse(JSON.stringify(payload));
        },
        refresh(mealId, body) {
            const payload = this.store.get(mealId);
            if (!payload) throw new Error('mock meal not found: ' + mealId);
            const kind = body && body.kind;
            const findItem = (id) => payload.items.find((x) => x.item_id === id);
            if (kind === 'add_item') {
                const text = String(body.text || '').trim();
                const branded = /\b(heb|h-?e-?b|hot cheetos?|chipotle|bill miller|whataburger|coca-?cola|coke|pepsi|trader joe|costco|starbucks|mcdonalds?)\b/i.test(text);
                const itemId = mealId + '-i' + (payload.items.length + 1);
                payload.items.push({
                    item_id: itemId,
                    name: text || 'Added item',
                    portion_description: branded ? 'looking up…' : '1 serving',
                    calories: branded ? 240 : 120,
                    protein_g: branded ? 3 : 2,
                    carbs_g: branded ? 30 : 18,
                    fat_g: branded ? 12 : 3,
                    sodium_mg: branded ? 320 : 50,
                    fiber_g: 1,
                    confidence: branded ? 0.62 : 0.35,
                    source: { kind: branded ? 'branded' : 'text', label: branded ? 'Branded lookup' : 'Text parser', link: branded ? '/api/sources/branded/' + encodeURIComponent(text.toLowerCase().replace(/\s+/g, '-')) : null },
                    unclear: !branded,
                    candidates: [],
                    status: 'included',
                });
            } else if (kind === 'edit_portion') {
                const it = findItem(body.item_id);
                if (it) {
                    it.portion_description = String(body.text || '').trim() || it.portion_description;
                    it.unclear = false;
                }
            } else if (kind === 'followup_answer') {
                payload.followup.used = true;
                const unclear = payload.items.find((x) => x.status === 'included' && x.unclear);
                if (unclear) unclear.unclear = false;
            } else if (kind === 'choose_candidate') {
                const it = findItem(body.item_id);
                if (it) {
                    const choice = (it.candidates || []).find((c) => c.candidate_id === body.candidate_id);
                    if (choice) {
                        it.name = choice.name;
                        it.portion_description = choice.portion_description;
                        it.source = choice.source || it.source;
                        it.unclear = false;
                        if (!Number.isFinite(Number(it.calories)) || it.calories === 0) it.calories = 200;
                    }
                    it.candidates = [];
                }
            } else if (kind === 'skip_item') {
                const it = findItem(body.item_id);
                if (it) it.status = 'skipped';
            } else if (kind === 'delete_item') {
                const it = findItem(body.item_id);
                if (it) it.status = 'deleted';
            } else if (kind === 'restore_item') {
                const it = findItem(body.item_id);
                if (it) it.status = 'included';
            } else if (kind === 'set_meal_type') {
                if (MEAL_TYPE_OPTIONS.includes(body.meal_type)) payload.meal_type = body.meal_type;
            }
            this.recompute(payload);
            return JSON.parse(JSON.stringify(payload));
        },
        accept(mealId) {
            if (!this.store.has(mealId)) throw new Error('mock meal not found: ' + mealId);
            this.store.delete(mealId);
            return { saved: true };
        },
        discard(mealId) {
            this.store.delete(mealId);
            return { removed: true };
        },
        recompute(payload) {
            payload.save_blocked_item_ids = payload.items
                .filter((it) => it.status === 'included' && it.unclear)
                .map((it) => it.item_id);
            const totals = { calories: 0, protein_g: 0, carbs_g: 0, fat_g: 0, sodium_mg: 0, fiber_g: 0 };
            payload.items
                .filter((it) => it.status === 'included')
                .forEach((it) => {
                    MEAL_V2_MACRO_KEYS.forEach((k) => {
                        const v = Number(it[k]);
                        if (Number.isFinite(v)) totals[k] += v;
                    });
                });
            MEAL_V2_MACRO_KEYS.forEach((k) => { totals[k] = Math.round(totals[k] * 10) / 10; });
            payload.meal_totals = totals;
        },
        inferMealType(text) {
            const t = String(text || '').toLowerCase();
            if (/(breakfast|eggs?|bagel|cereal|oatmeal|pancake)/.test(t)) return 'breakfast';
            if (/(lunch|sandwich|salad|burrito|taco)/.test(t)) return 'lunch';
            if (/(dinner|steak|pasta|pizza|rice and|grill)/.test(t)) return 'dinner';
            return 'snack';
        },
    };

    function clearMealComposerInputs() {
        const { text } = mealComposerEls();
        if (text) text.value = '';
        clearMealComposerImages();
        refreshMealSubmitState();
    }

    function wireMealComposer() {
        const { form, text, image, scan, barcodeInput, barcodeSubmit, barcodeClose, retry } = mealComposerEls();
        if (!form) return;
        loadMealDraft();
        hydrateMealPending();
        form.addEventListener('submit', submitMealComposer);
        if (scan) scan.addEventListener('click', openMealBarcodePanel);
        if (barcodeClose) barcodeClose.addEventListener('click', closeMealBarcodePanel);
        if (barcodeSubmit) {
            barcodeSubmit.addEventListener('click', (ev) => {
                ev.preventDefault();
                submitMealBarcode(barcodeInput ? barcodeInput.value : '');
            });
        }
        if (barcodeInput) {
            barcodeInput.addEventListener('keydown', (ev) => {
                if (ev.key !== 'Enter') return;
                ev.preventDefault();
                submitMealBarcode(barcodeInput.value);
            });
            barcodeInput.addEventListener('input', () => {
                setMealBarcodeStatus('');
            });
        }
        if (text) {
            text.addEventListener('input', () => {
                clearMealComposerStatus();
                // FIT-138: typing is a material draft change after a
                // failed submit; invalidate same-id retry so the next
                // submit is a fresh attempt.
                if (mealComposerState.draftClientId || mealComposerState.lastSubmitFailedTransient) {
                    mealComposerState.draftClientId = null;
                    mealComposerState.lastSubmitFailedTransient = false;
                    refreshMealComposerRetryUI();
                }
                refreshMealSubmitState();
                saveMealDraft();
            });
        }
        if (image) {
            image.addEventListener('change', () => {
                clearMealComposerStatus();
                onMealComposerImageSelected(image.files);
                // Reset the input value so re-selecting the same file
                // after a remove still fires `change`.
                image.value = '';
                refreshMealComposerRetryUI();
            });
        }
        if (retry) {
            // FIT-138: in-composer Retry re-submits the same draft
            // under the SAME draftClientId so the server's idempotency
            // contract prevents duplicate pending rows.
            retry.addEventListener('click', () => {
                clearMealComposerStatus();
                submitMealComposer();
            });
        }
        // FIT-138: react to network state changes so the offline banner
        // and submit-disabled state stay in sync.
        if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
            window.addEventListener('online', () => {
                refreshMealComposerOfflineUI();
                refreshMealSubmitState();
            });
            window.addEventListener('offline', () => {
                refreshMealComposerOfflineUI();
                refreshMealSubmitState();
            });
        }
        refreshMealComposerOfflineUI();
        refreshMealComposerRetryUI();
        refreshMealSubmitState();
    }

    function boot() {
        renderGreeting();
        wireEvents();
        switchTab('tab-dashboard');
        fetchFoodLogRefreshNotices().catch((err) => console.warn('food-log refresh notices failed:', err));
        refreshAiStatus();
        if (aiStatusTimer) clearInterval(aiStatusTimer);
        aiStatusTimer = setInterval(refreshAiStatus, 60_000);
        renderSyncBanner();
        wireMealComposer();
        registerServiceWorker();
        refreshMealQueueAuthScope({ timeoutMs: 2500 })
            .then((scopeResult) => {
                settleActiveWorkoutDraftAfterAuthScope(scopeResult);
                scheduleMealQueueAuthScopeRetry(scopeResult && scopeResult.status);
            })
            .catch((err) => {
                console.warn('Meal queue auth scope refresh failed:', err);
                scheduleMealQueueAuthScopeRetry('pending');
            })
            .finally(() => {
                fetchWorkoutAdaptationNotices().catch((err) => console.warn('workout adaptation notices failed:', err));
            });
        cleanupOrphanedMealQueuePhotos().catch((err) => console.warn('Meal queue cleanup failed:', err));
        window.addEventListener('pagehide', saveActiveWorkoutDraftBeforePageHidden);
        window.addEventListener('beforeunload', saveActiveWorkoutDraftBeforePageHidden);
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'hidden') saveActiveWorkoutDraftBeforePageHidden();
        });
        window.addEventListener('online', () => {
            refreshMealQueueAuthScope()
                .then((scopeResult) => {
                    settleActiveWorkoutDraftAfterAuthScope(scopeResult);
                    scheduleMealQueueAuthScopeRetry(scopeResult && scopeResult.status);
                })
                .catch((err) => {
                    console.warn('Meal queue auth scope refresh failed:', err);
                    scheduleMealQueueAuthScopeRetry('pending');
                });
            flushSyncQueue();
            flushMealSyncQueue();
        });
        if (navigator.onLine) {
            flushSyncQueue();
            flushMealSyncQueue();
        }
    }

    function registerServiceWorker() {
        // FIT-40: register the existing sw.js so PushManager + display-mode
        // detection downstream have a registration to talk to. Failure here
        // (private browsing, file://) is non-fatal — the rest of the app
        // works without it; renderPushSection() reports "Unsupported".
        if (!('serviceWorker' in navigator)) return;
        let reloadingForController = false;
        navigator.serviceWorker.addEventListener('controllerchange', () => {
            if (reloadingForController) return;
            if (activeWorkoutHasProgress()) {
                toast('Update ready after workout. Refresh when finished.');
                return;
            }
            reloadingForController = true;
            window.location.reload();
        });
        navigator.serviceWorker.register('/sw.js').then((reg) => {
            if (reg.waiting) reg.waiting.postMessage({ type: 'SKIP_WAITING' });
            reg.addEventListener('updatefound', () => {
                const worker = reg.installing;
                if (!worker) return;
                worker.addEventListener('statechange', () => {
                    if (worker.state === 'installed' && navigator.serviceWorker.controller) {
                        worker.postMessage({ type: 'SKIP_WAITING' });
                    }
                });
            });
            reg.update().catch((err) => {
                console.warn('Service worker update check failed:', err);
            });
        }).catch((err) => {
            console.warn('Service worker registration failed:', err);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }

    // Expose for console debugging (read-only) + macro card refresh hook (FIT-23)
    window.__aicoach = { state, switchTab, loadTab, invalidateCaches, refreshMacroCard };
})();
