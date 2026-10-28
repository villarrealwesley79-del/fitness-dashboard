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
        openWearablesStatus: null,
        wearableSources: null,
        openWearablesUi: {
            saveInFlight: false,
            checkInFlight: false,
            syncInFlight: false,
            bootstrapInFlight: false,
            pairInFlight: false,
            mobileInviteInFlight: false,
            lastError: '',
            portalUrl: '',
            selectedProvider: '',
            mobileInvite: null,
            providerActions: [],
            config: null,
        },
        aiFactUi: {
            queryInFlight: false,
            pendingSuggestionId: null,
            actionInFlight: false,
        },
        whoopUi: {
            connectInFlight: false,
            importInFlight: false,
            oauthPending: false,
            oauthRefreshInFlight: false,
            oauthStartedAt: 0,
            oauthPopupTimer: null,
            syncInFlight: false,
            disconnectInFlight: false,
            deleteInFlight: false,
            lastError: '',
            selectedImportFile: null,
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
        await handleUnauthorizedResponse(res);
        if (!res.ok) {
            const text = await res.text().catch(() => '');
            const requestError = new Error(`${res.status} ${path}: ${text.slice(0, 120)}`);
            try {
                const parsed = JSON.parse(text);
                if (parsed && parsed.error) {
                    requestError.apiErrorCode = parsed.error.code || null;
                    requestError.apiErrorMessage = parsed.error.message || null;
                }
            } catch {}
            throw requestError;
        }
        const ct = res.headers.get('content-type') || '';
        return ct.includes('application/json') ? res.json() : res.text();
    }

    async function handleUnauthorizedResponse(res) {
        if (!res || res.status !== 401) return;
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
        const panel = document.getElementById(tabId);
        tabId = panel && panel.classList.contains('tab-content') ? tabId : 'tab-dashboard';
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

    function initialTabFromHash() {
        const hash = window.location && window.location.hash ? window.location.hash.toLowerCase() : '';
        if (hash === '#settings') return 'tab-settings';
        if (hash === '#history') return 'tab-history';
        if (hash === '#workout') return 'tab-workout';
        return 'tab-dashboard';
    }

    function switchTabFromHash(force = false) {
        const tabId = initialTabFromHash();
        if (tabId && (force || tabId !== state.currentTab)) switchTab(tabId);
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
        if (modal.id === 'modal-whoop-intake') clearWhoopImportInput();
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
            refreshWhoopAfterOAuthReturn();
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
    async function getOpenWearablesStatus(force = false) {
        if (!force && state.openWearablesStatus) return state.openWearablesStatus;
        try { state.openWearablesStatus = await api('/api/open-wearables/status', { timeoutMs: DASHBOARD_FETCH_TIMEOUT_MS }); }
        catch { state.openWearablesStatus = null; }
        return state.openWearablesStatus;
    }
    async function getWearableSources(force = false) {
        if (!force && state.wearableSources) return state.wearableSources;
        try {
            const payload = await api('/api/wearable-sources', { timeoutMs: DASHBOARD_FETCH_TIMEOUT_MS });
            state.wearableSources = payload && Array.isArray(payload.sources) ? payload.sources : [];
        } catch {
            state.wearableSources = [];
        }
        return state.wearableSources;
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
    async function getOpenWearablesWorkouts(force = false) {
        const key = 'open_wearables_workouts';
        if (!force && state[key]) return state[key];
        try {
            const r = await api('/api/open-wearables/workouts', { timeoutMs: DASHBOARD_FETCH_TIMEOUT_MS });
            state[key] = r && Array.isArray(r.workouts) ? r.workouts : [];
        } catch {
            delete state[key];
            return [];
        }
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
        missing_config: 'missing_config',
        csv_only: 'csv_only',
        source_conflict: 'source_conflict',
        error: 'error',
    };

    function normalizeSourceKey(value) {
        const raw = String(value == null ? '' : value).trim().toLowerCase();
        if (!raw) return '';
        if (raw === 'apple' || raw === 'apple health' || raw === 'apple-health') return 'apple_health';
        if (raw === 'whoop' || raw === 'official_whoop') return 'whoop';
        if (raw === 'open wearables' || raw === 'open-wearables' || raw === 'open_wearables') return 'open_wearables';
        if (raw === 'oura' || raw === 'oura ring') return 'oura';
        if (raw === 'noop' || raw === 'noop import') return 'noop';
        return raw.replace(/\s+/g, '_');
    }

    function sourceDisplayName(value) {
        const key = normalizeSourceKey(value);
        if (key === 'whoop') return 'WHOOP';
        if (key === 'open_wearables') return 'Open Wearables';
        if (key === 'oura') return 'Oura';
        if (key === 'apple_health') return 'Apple Health';
        if (key === 'noop') return 'Noop import';
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
        if (raw === 'missing_config') return WHOOP_UI_STATES.missing_config;
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
                || whoop.mode
            )
        );
        const scoreState = normalizeWhoopStateToken(whoop && whoop.score_state);
        if (whoop && (whoop.error || status === WHOOP_UI_STATES.error)) return WHOOP_UI_STATES.error;
        if (whoop && (whoop.syncing || status === WHOOP_UI_STATES.syncing)) return WHOOP_UI_STATES.syncing;
        if (whoop && (whoop.reauth_required || status === WHOOP_UI_STATES.reauth_required)) return WHOOP_UI_STATES.reauth_required;
        if (whoop && (whoop.csv_only || normalizeWhoopStateToken(whoop.source_kind) === WHOOP_UI_STATES.csv_only || status === WHOOP_UI_STATES.csv_only)) {
            return WHOOP_UI_STATES.csv_only;
        }
        if (status === WHOOP_UI_STATES.missing_config) return WHOOP_UI_STATES.missing_config;
        if (status === WHOOP_UI_STATES.disconnected || whoop && whoop.connected === false) return WHOOP_UI_STATES.disconnected;
        if (firstWhoopConflict(conflicts) || (whoop && (whoop.source_conflict || status === WHOOP_UI_STATES.source_conflict))) {
            return WHOOP_UI_STATES.source_conflict;
        }
        if (whoop && (whoop.calibrating || status === WHOOP_UI_STATES.calibrating || scoreState === WHOOP_UI_STATES.calibrating)) return WHOOP_UI_STATES.calibrating;
        if (whoop && (whoop.pending_score || status === WHOOP_UI_STATES.pending_score || scoreState === WHOOP_UI_STATES.pending_score)) return WHOOP_UI_STATES.pending_score;
        if (whoop && (whoop.unscorable || status === WHOOP_UI_STATES.unscorable || scoreState === WHOOP_UI_STATES.unscorable)) return WHOOP_UI_STATES.unscorable;
        if (status === WHOOP_UI_STATES.fresh || status === WHOOP_UI_STATES.aging || status === WHOOP_UI_STATES.stale) return status;
        if (status === WHOOP_UI_STATES.missing || whoop && (whoop.no_data || whoop.has_data === false)) return WHOOP_UI_STATES.missing;
        if (whoop && (whoop.connected || whoop.connected_at || whoop.last_sync_at || whoop.last_successful_sync_at)) return WHOOP_UI_STATES.connected;
        return WHOOP_UI_STATES.disconnected;
    }

    function mergeWhoopFreshnessNode(freshnessNode, whoopStatus, conflicts) {
        const merged = Object.assign({}, freshnessNode || {}, whoopStatus || {});
        const connectionState = normalizeWhoopStateToken(whoopStatus && (whoopStatus.status || whoopStatus.connection_status));
        if (freshnessNode && freshnessNode.status) merged.status = freshnessNode.status;
        if (connectionState === WHOOP_UI_STATES.missing_config || connectionState === WHOOP_UI_STATES.reauth_required) {
            merged.status = connectionState;
        }
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
        if (uiState === WHOOP_UI_STATES.missing_config) {
            return { cls: 'stale', label: 'WHOOP · config missing', title: 'WHOOP OAuth is not configured on this server' };
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
            if (entries.open_wearables) {
                const ow = entries.open_wearables;
                normalizedEntries.push({
                    key: 'open_wearables',
                    label: 'Open Wearables',
                    role: ow.role || (ow.used_for_recommendation ? 'wearable hub' : 'display only'),
                    detail: ow.detail || 'Open Wearables is the wearable hub; this web app keeps coaching authority.',
                });
            }
            if (entries.whoop && !entries.whoop.summary_hidden) {
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
            if (entries.load_source && !entries.load_source_summary_hidden) {
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
    ];
    const SETTINGS_FRESHNESS_SLOTS = [
        { id: 'whoop-connect-state', key: 'whoop',        render: formatWhoopChip },
        { id: 'oura-connect-state',  key: 'oura',         render: formatOuraChip,  rowId: 'settings-row-oura' },
        { id: 'apple-connect-state', key: 'apple_health', render: formatAppleChip, rowId: 'settings-row-apple' },
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
            // formatter omits one (e.g. Oura still returns {cls,label}).
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

    function elementOrAncestorHidden(el) {
        for (let node = el; node; node = node.parentElement) {
            if (node.hidden) return true;
        }
        return false;
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
        const openWearablesChip = $('open-wearables-state');
        if (openWearablesChip && !elementOrAncestorHidden(openWearablesChip)) {
            if (openWearablesChip.classList.contains('ok')) fresh++;
            else if (openWearablesChip.classList.contains('warn') || openWearablesChip.classList.contains('stale')) stale++;
        }
        SETTINGS_FRESHNESS_SLOTS.forEach((slot) => {
            const chip = $(slot.id);
            if (!chip) return;
            const row = slot.rowId ? $(slot.rowId) : null;
            if (row && row.hidden) return;
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

    function openAiFactQueryModal() {
        const modal = $('modal-ai-fact-query');
        const trigger = $('btn-open-ai-fact-query');
        if (!modal) return;
        modal.__fit192Close = function () {
            modal.hidden = true;
            if (trigger) trigger.setAttribute('aria-expanded', 'false');
        };
        modal.hidden = false;
        if (trigger) trigger.setAttribute('aria-expanded', 'true');
        focusOpenModal(modal);
    }

    function setOpenWearablesSetupStatus(message, tone = 'info') {
        const modalStatus = $('open-wearables-setup-modal-status');
        const setupStatus = $('open-wearables-setup-status');
        const text = String(message || '').trim();
        if (modalStatus) {
            modalStatus.className = 'state-chip state-chip-sm ' + (tone === 'ok' ? 'ok' : tone === 'error' ? 'stale' : 'warn');
            modalStatus.textContent = tone === 'ok' ? 'Ready' : tone === 'error' ? 'Needs attention' : 'Add device';
        }
        if (setupStatus && text) {
            setupStatus.textContent = text;
            setupStatus.className = 'settings-row-detail whoop-intake-status ' + (tone === 'ok' ? 'ok' : tone === 'error' ? 'err' : tone === 'warn' ? 'warn' : '');
        }
    }

    function openWearablesIsConnected(status, checkStatus = '') {
        const stateToken = normalizeWhoopStateToken(status && status.status);
        return stateToken === WHOOP_UI_STATES.connected || checkStatus === 'ok';
    }

    function normalizeOpenWearablesProviderId(value) {
        return String(value || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
    }

    function openWearablesProviderAliases(provider) {
        const normalized = normalizeOpenWearablesProviderId(provider);
        if (normalized === 'apple' || normalized === 'apple_health' || normalized === 'healthkit') {
            return ['apple', 'apple_health', 'healthkit'];
        }
        if (normalized === 'google' || normalized === 'google_health_connect' || normalized === 'health_connect') {
            return ['google', 'google_health_connect', 'health_connect'];
        }
        if (normalized === 'samsung' || normalized === 'samsung_health') {
            return ['samsung', 'samsung_health'];
        }
        return normalized ? [normalized] : [];
    }

    function openWearablesConnectedProviderIds(status) {
        if (!openWearablesIsConnected(status)) return new Set();
        const providers = Array.isArray(status && status.providers) ? status.providers : [];
        const activeStates = new Set(['active', 'connected', 'enabled', 'ok', 'ready']);
        return new Set(providers
            .filter((provider) => {
                if (!provider || typeof provider !== 'object') return false;
                if (provider.stale || provider.error_code) return false;
                const providerState = normalizeOpenWearablesProviderId(provider.state || provider.status || 'connected');
                return activeStates.has(providerState);
            })
            .map((provider) => normalizeOpenWearablesProviderId(
                provider.provider_id || provider.provider || provider.id || provider.name
            ))
            .filter(Boolean));
    }

    function openWearablesProviderConnected(provider, status = state.openWearablesStatus) {
        const providerIds = openWearablesConnectedProviderIds(status);
        return openWearablesProviderAliases(provider).some((alias) => providerIds.has(alias));
    }

    function openWearablesDirectSourceReplaced(provider, status = state.openWearablesStatus) {
        const replacementSources = Array.isArray(status && status.replacement_sources)
            ? status.replacement_sources.map(normalizeOpenWearablesProviderId)
            : [];
        return openWearablesProviderAliases(provider).some((alias) => replacementSources.includes(alias))
            && openWearablesProviderConnected(provider, status);
    }

    function applyOpenWearablesDirectSourceVisibility(status = state.openWearablesStatus) {
        [
            { provider: 'oura', rowId: 'settings-row-oura', detailId: 'oura-detail' },
            { provider: 'apple_health', rowId: 'settings-row-apple', detailId: 'apple-detail' },
        ].forEach((entry) => {
            const hidden = openWearablesDirectSourceReplaced(entry.provider, status);
            const row = $(entry.rowId);
            const detail = $(entry.detailId);
            if (row) row.hidden = hidden;
            if (detail) detail.hidden = hidden;
        });
    }

    function openWearablesCanSync(status, config, setupState) {
        if (openWearablesIsConnected(status) || setupState === 'ready_to_sync') return true;
        const safeConfig = config || state.openWearablesUi.config || {};
        const userMapped = safeConfig.user_mapped != null
            ? Boolean(safeConfig.user_mapped)
            : Boolean(status && status.user_mapped);
        const hubAccountReady = safeConfig.hub_account_ready != null
            ? Boolean(safeConfig.hub_account_ready)
            : Boolean(status && status.auth_configured);
        return Boolean(
            userMapped
            && hubAccountReady
            && Array.isArray(status && status.providers)
            && status.providers.length
        );
    }

    function deriveOpenWearablesSetupState(status, config) {
        if (state.openWearablesUi.syncInFlight) return 'syncing';
        if (state.openWearablesUi.checkInFlight) return 'checking_connection';
        if (state.openWearablesUi.bootstrapInFlight) return 'checking';
        const stateToken = normalizeWhoopStateToken(status && status.status);
        if (stateToken === 'blocked') return 'blocked';
        if (stateToken === WHOOP_UI_STATES.error) return 'error';
        if (openWearablesIsConnected(status)) return 'connected';
        const safeConfig = config || state.openWearablesUi.config || {};
        const hubAccountReady = safeConfig.hub_account_ready != null
            ? Boolean(safeConfig.hub_account_ready)
            : Boolean(status && status.auth_configured);
        const userMapped = safeConfig.user_mapped != null
            ? Boolean(safeConfig.user_mapped)
            : Boolean(status && status.user_mapped);
        if (!hubAccountReady) return 'needs_hub_account';
        if (!userMapped) return 'needs_person';
        if (
            safeConfig.managed_connector_restart_required === true
        ) return 'hub_restart_needed';
        const providerActions = Array.isArray(safeConfig.provider_actions) ? safeConfig.provider_actions : [];
        const hasProviderSetupPath = providerActions.some((action) => (
            action && (action.enabled || action.reason === 'sdk_provider' || action.reason === 'prepare_profile')
        ));
        if (
            (safeConfig.provider_setup_ready === false || (status && status.setup_hint === 'provider_credentials_missing'))
            && !hasProviderSetupPath
        ) return 'needs_provider_credentials';
        if (Array.isArray(status && status.providers) && status.providers.length) return 'ready_to_sync';
        if (state.openWearablesUi.selectedProvider) return 'pairing_provider';
        return 'ready_to_choose_provider';
    }

    function openWearablesSetupCopy(setupState) {
        const copy = {
            checking: 'Preparing local wearable setup...',
            needs_hub_account: 'Prepare pairing to use the local hub on this Mac.',
            needs_person: 'Add the person whose wearable data should appear here.',
            needs_provider_credentials: 'This wearable needs owner setup before anyone can pair it.',
            hub_restart_needed: 'Finishing local wearable setup. Try again after the hub restarts.',
            ready_to_choose_provider: 'Choose your wearable to continue.',
            pairing_provider: 'Finish pairing, then check setup.',
            checking_connection: 'Checking whether the hub can see your device...',
            ready_to_sync: 'Device is visible. Sync now when ready.',
            connected: 'Wearable data is ready.',
            syncing: 'Syncing Open Wearables data...',
            blocked: 'Needs attention. Advanced diagnostics has details.',
            error: 'Needs attention. Advanced diagnostics has details.',
        };
        return copy[setupState] || copy.needs_hub_account;
    }

    function openWearablesChipText(setupState) {
        if (setupState === 'connected' || setupState === 'ready_to_sync') return 'Ready';
        if (setupState === 'syncing') return 'Syncing';
        if (setupState === 'blocked' || setupState === 'error') return 'Blocked';
        if (setupState === 'checking' || setupState === 'checking_connection') return 'Checking';
        if (setupState === 'hub_restart_needed') return 'Finishing setup';
        if (setupState === 'needs_provider_credentials') return 'Setup needed';
        return 'Add device';
    }

    function renderOpenWearablesSetupSteps(setupState, config) {
        const safeConfig = config || state.openWearablesUi.config || {};
        _setText('open-wearables-step-person', safeConfig.user_mapped ? 'Person added' : 'Not prepared');
        _setText('open-wearables-step-device', setupState === 'needs_hub_account' || setupState === 'needs_person'
            ? 'Choose after preparing'
            : state.openWearablesUi.selectedProvider
                ? `Continue to ${state.openWearablesUi.selectedProvider}`
                : 'Choose a wearable');
        _setText('open-wearables-step-connection', openWearablesIsConnected(state.openWearablesStatus)
            ? 'Connected'
            : setupState === 'ready_to_sync'
                ? 'Device visible'
                : setupState === 'needs_provider_credentials'
                    ? 'Provider sign-in needed'
                    : setupState === 'hub_restart_needed'
                        ? 'Finishing local setup'
                    : 'Waiting for pairing');
    }

    function renderOpenWearablesMobileInvite(invite) {
        const card = $('open-wearables-mobile-invite');
        if (!card) return;
        const safe = invite || state.openWearablesUi.mobileInvite || null;
        card.hidden = !safe;
        if (!safe) return;
        _setText('open-wearables-mobile-title', `Connect ${safe.label || 'phone app'}`);
        _setText('open-wearables-mobile-body', 'Enter this server address and one-time code inside the Open Wearables mobile app. Do not open the server address in a browser.');
        _setText('open-wearables-mobile-server', safe.server_url || '—');
        _setText('open-wearables-mobile-code', safe.code || '—');
        _setText('open-wearables-mobile-expires', safe.expires_at ? `Expires ${fmtDateTime(safe.expires_at)}` : 'This code expires soon.');
    }

    function showOpenWearablesProviderSetupNeeded(label = 'Wearable', reason = '') {
        const name = String(label || 'Wearable').trim();
        state.openWearablesUi.selectedProvider = name;
        const reasonToken = String(reason || '').trim();
        const message = reasonToken === 'sdk_provider'
            ? `${name} connects through a phone health-store setup instead of a provider sign-in popup.`
            : reasonToken === 'provider_app_needed'
                ? `${name} needs connector credentials in Open Wearables first. Opening Open Wearables now; finish ${name} setup there, then return and check connection.`
            : reasonToken === 'provider_disabled'
                ? `${name} is disabled in Open Wearables. Opening Open Wearables now so you can turn it on there.`
            : reasonToken === 'provider_catalog_unavailable'
                ? `Open Wearables did not return provider details yet. Opening the hub admin so you can inspect setup there.`
            : `${name} needs owner setup before anyone can pair it here. Once the connector is prepared on this Mac, this button will open ${name} sign-in.`;
        state.openWearablesUi.lastError = message;
        setOpenWearablesSetupStatus(message, 'warn');
        const providerNote = document.querySelector('.open-wearables-provider-note');
        if (providerNote) providerNote.textContent = message;
        if (reasonToken !== 'sdk_provider') {
            state.openWearablesUi.mobileInvite = null;
            renderOpenWearablesMobileInvite(null);
        }
        if (reasonToken && reasonToken !== 'sdk_provider' && reasonToken !== 'prepare_profile') {
            openOpenWearablesPortal(message);
        }
        renderOpenWearablesProviderActions(state.openWearablesUi.providerActions);
        renderOpenWearablesSetupSteps('needs_provider_credentials', state.openWearablesUi.config);
    }

    function openWearablesProviderBadge(action) {
        const reason = String(action && action.reason || '');
        if (reason === 'sdk_provider') return 'Phone setup';
        if (reason === 'provider_disabled') return 'Disabled';
        if (reason === 'hub_restart_needed') return 'Finishing';
        if (reason === 'prepare_profile') return 'Prepare first';
        return 'Owner setup';
    }

    function renderOpenWearablesProviderActions(actions) {
        const host = $('open-wearables-provider-actions');
        if (!host) return;
        const providerActions = Array.isArray(actions) && actions.length
            ? actions
            : [
                { provider: 'garmin', label: 'Garmin', enabled: false },
                { provider: 'oura', label: 'Oura', enabled: false },
                { provider: 'whoop', label: 'WHOOP', enabled: false },
                { provider: 'suunto', label: 'Suunto', enabled: false },
                { provider: 'polar', label: 'Polar', enabled: false },
                { provider: 'ultrahuman', label: 'Ultrahuman', enabled: false },
                { provider: 'strava', label: 'Strava', enabled: false },
                { provider: 'fitbit', label: 'Fitbit', enabled: false },
                { provider: 'apple', label: 'Apple Health', enabled: false, reason: 'sdk_provider' },
                { provider: 'samsung', label: 'Samsung Health', enabled: false, reason: 'sdk_provider' },
                { provider: 'google', label: 'Google Health Connect', enabled: false, reason: 'sdk_provider' },
            ];
        host.innerHTML = '';
        providerActions.slice(0, 12).forEach((action) => {
            const provider = String(action.provider || '').trim();
            if (!provider) return;
            const label = action.label || provider;
            const providerConnected = openWearablesProviderConnected(provider) || openWearablesProviderConnected(label);
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'open-wearables-provider-btn';
            btn.dataset.openWearablesProvider = provider;
            btn.textContent = label;
            btn.disabled = state.openWearablesUi.pairInFlight;
            btn.disabled = btn.disabled || state.openWearablesUi.mobileInviteInFlight;
            btn.classList.toggle('is-unavailable', !action.enabled && !providerConnected);
            btn.dataset.setupState = providerConnected ? 'connected' : action.enabled ? 'ready' : 'setup_needed';
            btn.dataset.statusLabel = providerConnected ? 'Connected' : openWearablesProviderBadge(action);
            if (providerConnected) {
                btn.title = `${label} is connected through Open Wearables.`;
            } else if (action.reason === 'provider_app_needed') {
                btn.title = 'Owner setup is needed on this Mac first.';
            } else if (action.reason === 'sdk_provider') {
                btn.title = 'This source connects through the phone health-store setup.';
            } else if (action.reason === 'provider_disabled') {
                btn.title = 'This provider is disabled in Open Wearables.';
            } else if (action.reason === 'hub_restart_needed') {
                btn.title = 'The local wearable hub is finishing setup.';
            }
            btn.classList.toggle('is-selected', state.openWearablesUi.selectedProvider === label);
            btn.addEventListener('click', () => {
                if (providerConnected) {
                    state.openWearablesUi.selectedProvider = label;
                    setOpenWearablesSetupStatus(`${label} is connected through Open Wearables.`, 'ok');
                    renderOpenWearablesProviderActions(state.openWearablesUi.providerActions);
                    renderOpenWearablesSetupSteps('connected', state.openWearablesUi.config);
                } else if (action.enabled) {
                    pairOpenWearablesProvider(provider, label);
                } else if (action.reason === 'sdk_provider') {
                    createOpenWearablesMobileInvite(provider, label);
                } else if (action.reason === 'prepare_profile') {
                    prepareOpenWearablesThenContinue(provider, label);
                } else {
                    showOpenWearablesProviderSetupNeeded(label, action.reason);
                }
            });
            host.appendChild(btn);
        });
        renderOpenWearablesMobileInvite(state.openWearablesUi.mobileInvite);
    }

    function handleOpenWearablesPrimarySetupAction() {
        const setupState = deriveOpenWearablesSetupState(state.openWearablesStatus, state.openWearablesUi.config);
        if (setupState === 'needs_provider_credentials') {
            showOpenWearablesProviderSetupNeeded('Wearable');
            const modal = $('modal-open-wearables-setup');
            const advanced = modal && modal.querySelector('.open-wearables-advanced');
            if (advanced) advanced.open = true;
            return;
        }
        if (setupState === 'hub_restart_needed') {
            setOpenWearablesSetupStatus(openWearablesSetupCopy(setupState), 'warn');
            return;
        }
        if (setupState === 'ready_to_choose_provider') {
            setOpenWearablesSetupStatus('Choose a wearable above to continue.', 'warn');
            return;
        }
        bootstrapOpenWearablesSetup();
    }

    async function bootstrapOpenWearablesSetup() {
        if (state.openWearablesUi.bootstrapInFlight) return;
        state.openWearablesUi.bootstrapInFlight = true;
        state.openWearablesUi.lastError = '';
        renderOpenWearablesDetail(state.openWearablesStatus, state.wearableSources);
        setOpenWearablesSetupStatus('Preparing the local wearable profile...', 'warn');
        try {
            const body = await api('/api/open-wearables/setup/bootstrap', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({}),
            });
            if (body && body.config) populateOpenWearablesSetupFields(body.config);
            state.openWearablesStatus = body && body.open_wearables ? body.open_wearables : await getOpenWearablesStatus(true);
            state.dashboard = null;
            state.open_wearables_workouts = null;
            renderOpenWearablesDetail(state.openWearablesStatus, state.wearableSources);
            const nextState = deriveOpenWearablesSetupState(state.openWearablesStatus, state.openWearablesUi.config);
            setOpenWearablesSetupStatus(
                nextState === 'hub_restart_needed'
                    ? openWearablesSetupCopy(nextState)
                    : 'Person added. Choose a wearable to pair.',
                nextState === 'hub_restart_needed' ? 'warn' : 'ok'
            );
            toast('Wearable profile prepared.', 'ok');
            return true;
        } catch (error) {
            state.openWearablesUi.lastError = error && error.message ? error.message : 'Could not prepare pairing.';
            setOpenWearablesSetupStatus(state.openWearablesUi.lastError, 'error');
            toast('Could not prepare pairing.', 'err');
            return false;
        } finally {
            state.openWearablesUi.bootstrapInFlight = false;
            renderOpenWearablesProviderActions(state.openWearablesUi.providerActions);
            renderOpenWearablesDetail(state.openWearablesStatus, state.wearableSources);
        }
    }

    async function prepareOpenWearablesThenContinue(provider, label) {
        const providerId = String(provider || '').trim().toLowerCase();
        if (!providerId) return;
        const prepared = await bootstrapOpenWearablesSetup();
        if (!prepared) return;
        const nextAction = (state.openWearablesUi.providerActions || [])
            .find((action) => String(action && action.provider || '').toLowerCase() === providerId);
        if (nextAction && nextAction.enabled) {
            pairOpenWearablesProvider(providerId, nextAction.label || label || providerId);
            return;
        }
        if (nextAction && (nextAction.kind === 'sdk' || nextAction.reason === 'sdk_provider')) {
            createOpenWearablesMobileInvite(providerId, nextAction.label || label || providerId);
            return;
        }
        showOpenWearablesProviderSetupNeeded(
            (nextAction && nextAction.label) || label || providerId,
            nextAction && nextAction.reason
        );
    }

    async function pairOpenWearablesProvider(provider, label) {
        const providerId = String(provider || '').trim().toLowerCase();
        if (!providerId || state.openWearablesUi.pairInFlight) return;
        state.openWearablesUi.pairInFlight = true;
        state.openWearablesUi.selectedProvider = label || providerId;
        state.openWearablesUi.lastError = '';
        renderOpenWearablesProviderActions(state.openWearablesUi.providerActions);
        renderOpenWearablesDetail(state.openWearablesStatus, state.wearableSources);
        setOpenWearablesSetupStatus(`Opening ${label || providerId} pairing...`, 'warn');
        try {
            const body = await api(`/api/open-wearables/pair/${encodeURIComponent(providerId)}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({}),
            });
            const url = body && body.authorization_url;
            if (!url) throw new Error('Pairing link is not available for this wearable yet.');
            setOpenWearablesSetupStatus(`Finish ${label || providerId} pairing, then check connection.`, 'warn');
            window.open(url, '_blank', 'noopener,noreferrer');
        } catch (error) {
            const message = error && error.message ? error.message : 'This wearable is not ready to pair yet.';
            state.openWearablesUi.lastError = message;
            setOpenWearablesSetupStatus(message, 'error');
            toast('Wearable pairing is not ready.', 'err');
        } finally {
            state.openWearablesUi.pairInFlight = false;
            renderOpenWearablesProviderActions(state.openWearablesUi.providerActions);
            renderOpenWearablesDetail(state.openWearablesStatus, state.wearableSources);
        }
    }

    async function createOpenWearablesMobileInvite(provider, label) {
        const providerId = String(provider || '').trim().toLowerCase();
        if (!providerId || state.openWearablesUi.mobileInviteInFlight) return;
        state.openWearablesUi.mobileInviteInFlight = true;
        state.openWearablesUi.selectedProvider = label || providerId;
        state.openWearablesUi.lastError = '';
        renderOpenWearablesProviderActions(state.openWearablesUi.providerActions);
        renderOpenWearablesDetail(state.openWearablesStatus, state.wearableSources);
        setOpenWearablesSetupStatus(`Creating ${label || providerId} phone app code...`, 'warn');
        try {
            const body = await api(`/api/open-wearables/mobile-invite/${encodeURIComponent(providerId)}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({}),
            });
            const invite = body && body.invite;
            if (!invite || !invite.code) throw new Error('Phone app code is not available yet.');
            state.openWearablesUi.mobileInvite = invite;
            renderOpenWearablesMobileInvite(invite);
            setOpenWearablesSetupStatus(`Enter the server address and ${label || providerId} code in the Open Wearables mobile app. Do not open the server address in a browser.`, 'ok');
        } catch (error) {
            const message = error && error.message ? error.message : 'Phone app setup is not ready yet.';
            state.openWearablesUi.lastError = message;
            setOpenWearablesSetupStatus(message, 'error');
            toast('Phone app setup is not ready.', 'err');
        } finally {
            state.openWearablesUi.mobileInviteInFlight = false;
            renderOpenWearablesProviderActions(state.openWearablesUi.providerActions);
            renderOpenWearablesDetail(state.openWearablesStatus, state.wearableSources);
        }
    }

    function populateOpenWearablesSetupFields(config) {
        const safe = config || {};
        const fields = {
            'open-wearables-base-url': safe.base_url || 'http://localhost:8000',
            'open-wearables-portal-url': safe.portal_url || '',
            'open-wearables-username': safe.username || '',
            'open-wearables-user-id': safe.user_id || '',
        };
        Object.entries(fields).forEach(([id, value]) => {
            const el = $(id);
            if (el) el.value = value;
        });
        state.openWearablesUi.config = safe;
        state.openWearablesUi.providerActions = Array.isArray(safe.provider_actions) ? safe.provider_actions : [];
        renderOpenWearablesMobileInvite(state.openWearablesUi.mobileInvite);
        const password = $('open-wearables-password');
        if (password) {
            password.value = '';
            password.placeholder = safe.password_configured
                ? 'Saved - leave blank to keep it'
                : 'Password or token';
        }
        state.openWearablesUi.portalUrl = safe.pairing_url || safe.portal_url || '';
        const linkLabel = $('open-wearables-pairing-link-label');
        const providerNote = document.querySelector('.open-wearables-provider-note');
        const setupState = deriveOpenWearablesSetupState(state.openWearablesStatus, safe);
        if (linkLabel) linkLabel.textContent = safe.user_mapped
            ? 'Person added. Choose a wearable below.'
            : 'Prepare pairing to create the local hub profile.';
        if (providerNote) {
            providerNote.textContent = setupState === 'needs_provider_credentials'
                ? 'Some cloud wearables need owner setup on this Mac before they can be paired. Phone health sources use the mobile health-store setup.'
                : setupState === 'hub_restart_needed'
                    ? 'The web app prepared a connector. The local hub needs to restart before pairing opens.'
                : 'Cloud wearables open provider sign-in. Phone health sources use the mobile health-store setup.';
        }
        renderOpenWearablesProviderActions(state.openWearablesUi.providerActions);
        renderOpenWearablesSetupSteps(setupState, safe);
        setOpenWearablesSetupStatus(openWearablesSetupCopy(setupState), setupState === 'connected' || setupState === 'ready_to_sync' ? 'ok' : setupState === 'blocked' || setupState === 'error' ? 'error' : 'warn');
    }

    async function loadOpenWearablesSetup() {
        try {
            const body = await api('/api/open-wearables/setup');
            if (body && body.open_wearables) {
                state.openWearablesStatus = body.open_wearables;
                renderOpenWearablesDetail(state.openWearablesStatus, state.wearableSources);
            }
            populateOpenWearablesSetupFields(body && body.config);
            return body;
        } catch (error) {
            setOpenWearablesSetupStatus('Could not load local setup values.', 'error');
            return null;
        }
    }

    async function openOpenWearablesSetupModal() {
        const modal = $('modal-open-wearables-setup');
        if (!modal) return;
        const status = state.openWearablesStatus;
        const connected = openWearablesIsConnected(status);
        const detail = connected
            ? 'Open Wearables is ready. Add another wearable any time.'
            : 'Prepare pairing, choose a wearable, then check connection.';
        setOpenWearablesSetupStatus(detail, connected ? 'ok' : 'warn');
        const advanced = modal.querySelector('.open-wearables-advanced');
        if (advanced) advanced.open = false;
        modal.hidden = false;
        focusOpenModal(modal);
        await loadOpenWearablesSetup();
    }

    function readOpenWearablesSetupFields() {
        return {
            base_url: String(($('open-wearables-base-url') || {}).value || '').trim(),
            portal_url: String(($('open-wearables-portal-url') || {}).value || '').trim(),
            username: String(($('open-wearables-username') || {}).value || '').trim(),
            password: String(($('open-wearables-password') || {}).value || '').trim(),
            user_id: String(($('open-wearables-user-id') || {}).value || '').trim(),
        };
    }

    async function saveOpenWearablesSetup() {
        if (state.openWearablesUi.saveInFlight) return;
        state.openWearablesUi.saveInFlight = true;
        renderOpenWearablesDetail(state.openWearablesStatus, state.wearableSources);
        setOpenWearablesSetupStatus('Saving local Open Wearables setup...', 'warn');
        const saveBtn = $('btn-open-wearables-setup-save');
        if (saveBtn) saveBtn.disabled = true;
        try {
            const body = await api('/api/open-wearables/setup', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(readOpenWearablesSetupFields()),
            });
            populateOpenWearablesSetupFields(body && body.config);
            state.openWearablesStatus = body && body.open_wearables ? body.open_wearables : await getOpenWearablesStatus(true);
            if (openWearablesIsConnected(state.openWearablesStatus, body && body.status)) {
                setOpenWearablesSetupStatus('Saved locally. Open Wearables is connected.', 'ok');
            } else {
                setOpenWearablesSetupStatus('Saved locally. Check setup once the wrapper and providers are running.', 'warn');
            }
            state.dashboard = null;
            state.open_wearables_workouts = null;
            toast('Open Wearables setup saved.', 'ok');
        } catch (error) {
            setOpenWearablesSetupStatus(error && error.message ? error.message : 'Open Wearables setup save failed.', 'error');
            toast('Open Wearables setup save failed.', 'err');
        } finally {
            state.openWearablesUi.saveInFlight = false;
            if (saveBtn) saveBtn.disabled = false;
            await renderSettings();
        }
    }

    function openOpenWearablesPortal(statusMessage = '') {
        const url = state.openWearablesUi.portalUrl || String(($('open-wearables-portal-url') || {}).value || '').trim();
        if (!url) {
            setOpenWearablesSetupStatus('Hub admin link is not available yet. Check connection or use Advanced diagnostics.', 'warn');
            return;
        }
        if (!/^https?:\/\//i.test(url)) {
            setOpenWearablesSetupStatus('Pairing portal link must start with http:// or https://.', 'error');
            return;
        }
        if (statusMessage) setOpenWearablesSetupStatus(statusMessage, 'warn');
        window.open(url, '_blank', 'noopener,noreferrer');
    }

    async function copyOpenWearablesPairingLink() {
        const url = state.openWearablesUi.portalUrl || String(($('open-wearables-portal-url') || {}).value || '').trim();
        if (!url) {
            setOpenWearablesSetupStatus('Hub admin link is not available yet. Check connection or use Advanced diagnostics.', 'warn');
            return;
        }
        try {
            await navigator.clipboard.writeText(url);
            setOpenWearablesSetupStatus('Hub admin link copied.', 'ok');
            toast('Hub link copied.', 'ok');
        } catch {
            setOpenWearablesSetupStatus(url, 'warn');
        }
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

    function setWhoopIntakeStatus(message, tone = 'info') {
        const liveStatus = $('whoop-connect-modal-status');
        const importStatus = $('whoop-import-status');
        const text = String(message || '').trim();
        if (liveStatus) {
            liveStatus.textContent = tone === 'error' ? 'Needs attention' : tone === 'ok' ? 'Ready' : tone === 'warn' ? 'Unavailable' : 'Ready';
            liveStatus.className = 'state-chip state-chip-sm ' + (tone === 'error' ? 'err' : tone === 'ok' ? 'ok' : tone === 'warn' ? 'warn' : '');
        }
        if (importStatus && text) {
            importStatus.textContent = text;
            importStatus.className = 'settings-row-detail whoop-intake-status ' + (tone === 'error' ? 'err' : tone === 'ok' ? 'ok' : tone === 'warn' ? 'warn' : '');
        }
    }

    function setWhoopConnectFallback(url, message) {
        const link = $('whoop-connect-open-link');
        const fallback = $('whoop-connect-fallback');
        if (link) {
            if (url) {
                link.href = url;
                link.hidden = false;
            } else {
                link.removeAttribute('href');
                link.hidden = true;
            }
        }
        if (fallback) {
            fallback.textContent = message || '';
            fallback.hidden = !message;
        }
    }

    function applyWhoopIntakeAvailability() {
        const liveBtn = $('btn-whoop-connect-live');
        const url = currentWhoopConnectUrl();
        const uiState = resolveWhoopUiState(state.whoopStatus);
        const liveUnavailable = uiState === WHOOP_UI_STATES.missing_config && !url;
        if (liveBtn) {
            liveBtn.disabled = liveUnavailable || state.whoopUi.connectInFlight;
            liveBtn.setAttribute('aria-disabled', liveUnavailable ? 'true' : 'false');
            liveBtn.textContent = state.whoopUi.connectInFlight ? 'Opening…' : liveUnavailable ? 'Unavailable' : 'Open WHOOP';
        }
        if (liveUnavailable) {
            setWhoopConnectFallback('', 'WHOOP live sync is not configured on this server. Paste or upload a WHOOP export below.');
            setWhoopIntakeStatus('WHOOP live sync is not configured on this server. Paste or upload a WHOOP export below.', 'warn');
        }
        return !liveUnavailable;
    }

    function markWhoopOAuthPending(popup) {
        state.whoopUi.oauthPending = true;
        state.whoopUi.oauthStartedAt = Date.now();
        if (state.whoopUi.oauthPopupTimer) {
            window.clearInterval(state.whoopUi.oauthPopupTimer);
            state.whoopUi.oauthPopupTimer = null;
        }
        if (!popup) return;
        state.whoopUi.oauthPopupTimer = window.setInterval(() => {
            if (!state.whoopUi.oauthPending) {
                window.clearInterval(state.whoopUi.oauthPopupTimer);
                state.whoopUi.oauthPopupTimer = null;
                return;
            }
            if (popup.closed) {
                window.clearInterval(state.whoopUi.oauthPopupTimer);
                state.whoopUi.oauthPopupTimer = null;
                refreshWhoopAfterOAuthReturn();
            }
        }, 1500);
    }

    async function refreshWhoopAfterOAuthReturn() {
        if (!state.whoopUi.oauthPending || state.whoopUi.oauthRefreshInFlight) return;
        state.whoopUi.oauthRefreshInFlight = true;
        try {
            const whoop = await getWhoopStatus(true);
            if (state.currentTab === 'tab-settings') await renderSettings();
            const expired = Date.now() - (state.whoopUi.oauthStartedAt || 0) > 5 * 60 * 1000;
            if ((whoop && whoop.connected) || expired) {
                state.whoopUi.oauthPending = false;
                state.whoopUi.oauthStartedAt = 0;
                if (state.whoopUi.oauthPopupTimer) {
                    window.clearInterval(state.whoopUi.oauthPopupTimer);
                    state.whoopUi.oauthPopupTimer = null;
                }
                if (whoop && whoop.connected) {
                    setWhoopIntakeStatus('WHOOP connection refreshed.', 'ok');
                    setWhoopConnectFallback('', '');
                }
            }
        } catch {
            // Keep the pending marker so a later focus/close event can retry.
        } finally {
            state.whoopUi.oauthRefreshInFlight = false;
        }
    }

    function openWhoopIntakeModal() {
        const modal = $('modal-whoop-intake');
        const text = $('whoop-import-csv-text');
        if (!modal) return;
        setWhoopConnectFallback(currentWhoopConnectUrl(), '');
        setWhoopIntakeStatus('Manual import stays available even when live WHOOP sync is not configured.', 'info');
        applyWhoopIntakeAvailability();
        modal.hidden = false;
        if (text && !text.value) text.placeholder = 'date,recovery_score,recovery_band,strain,sleep_performance_pct';
        focusOpenModal(modal);
    }

    async function startWhoopConnectFromModal() {
        if (state.whoopUi.connectInFlight) return;
        if (!applyWhoopIntakeAvailability()) return;
        state.whoopUi.connectInFlight = true;
        applyWhoopIntakeAvailability();
        setWhoopIntakeStatus('Preparing WHOOP authorization…', 'info');
        let popup = null;
        try {
            popup = window.open('', 'fitnessDashboardWhoopOAuth', 'width=520,height=720');
            if (popup) popup.opener = null;
        } catch {
            popup = null;
        }
        try {
            const body = await api('/api/whoop/connect/start', { method: 'POST' });
            const nextUrl = body && (body.authorization_url || body.url);
            if (!nextUrl) {
                if (popup && !popup.closed) popup.close();
                setWhoopConnectFallback('', 'WHOOP live sync is not available yet. You can still import a WHOOP export below.');
                setWhoopIntakeStatus('WHOOP live sync is not available yet. You can still import a WHOOP export below.', 'warn');
                return;
            }
            state.whoopStatus = Object.assign({}, state.whoopStatus || {}, body.connection || {}, { authorization_url: nextUrl });
            setWhoopConnectFallback(nextUrl, 'If the WHOOP window did not open, use the link above.');
            if (popup && !popup.closed) {
                markWhoopOAuthPending(popup);
                popup.location.href = nextUrl;
                setWhoopIntakeStatus('WHOOP opened in a new window. Return here after approval.', 'ok');
            } else {
                markWhoopOAuthPending(null);
                setWhoopIntakeStatus('Popup blocked. Use the Open link button to continue.', 'warn');
            }
        } catch (err) {
            if (popup && !popup.closed) popup.close();
            const message = (err && err.message && err.message.includes('missing_whoop_config'))
                ? 'WHOOP live sync is not configured on this server. Paste or upload a WHOOP export below.'
                : ((err && err.message) || 'WHOOP connect failed. Paste or upload a WHOOP export below.');
            setWhoopConnectFallback('', message);
            setWhoopIntakeStatus(message, 'warn');
        } finally {
            state.whoopUi.connectInFlight = false;
            applyWhoopIntakeAvailability();
        }
    }

    function formatWhoopImportSummary(importResult) {
        if (!importResult) return null;
        const parsed = importResult.parsed_rows;
        const imported = importResult.imported_rows;
        const unsupported = importResult.skipped_unsupported_rows;
        const naps = importResult.ignored_nap_rows;
        const duplicates = importResult.duplicate_or_upserted_rows;
        if (![parsed, imported, unsupported, naps, duplicates].every(Number.isFinite)) return null;
        return `Parsed ${parsed} · Imported ${imported} · Unsupported ${unsupported} · Naps ${naps} · Duplicates/updates ${duplicates}`;
    }

    async function importWhoopCsvFromModal() {
        if (state.whoopUi.importInFlight) return;
        const textArea = $('whoop-import-csv-text');
        const submitBtn = $('btn-whoop-import-submit');
        const text = textArea ? String(textArea.value || '').trim() : '';
        const file = state.whoopUi.selectedImportFile;
        if (!text && !file) {
            setWhoopIntakeStatus('Paste WHOOP CSV data or choose a CSV file first.', 'error');
            return;
        }
        state.whoopUi.importInFlight = true;
        if (submitBtn) submitBtn.disabled = true;
        setWhoopIntakeStatus('Importing WHOOP data…', 'info');
        try {
            const options = { method: 'POST' };
            if (text) {
                options.headers = { 'Content-Type': 'application/json', 'Accept': 'application/json', [CSRF_HEADER_NAME]: CSRF_HEADER_VALUE };
                options.body = JSON.stringify({ csv: text });
            } else {
                const fd = new FormData();
                fd.append('file', file);
                options.headers = { 'Accept': 'application/json', [CSRF_HEADER_NAME]: CSRF_HEADER_VALUE };
                options.body = fd;
            }
            const response = await fetch('/api/whoop/import-csv', Object.assign({}, options, { credentials: 'same-origin' }));
            await handleUnauthorizedResponse(response);
            const body = await response.json().catch(() => null);
            if (!response.ok) {
                const message = body && body.error && body.error.message ? body.error.message : 'WHOOP import failed.';
                throw new Error(message);
            }
            state.whoopStatus = Object.assign({}, state.whoopStatus || {}, body.connection || {});
            state.dashboard = null;
            state.reco = null;
            await getWhoopStatus(true);
            await renderSettings();
            const importResult = body && body.import ? body.import : null;
            const summary = formatWhoopImportSummary(importResult);
            const count = importResult && importResult.records_upserted != null ? importResult.records_upserted : null;
            clearWhoopImportInput();
            setWhoopIntakeStatus(summary || (count != null ? `WHOOP import saved ${count} record${count === 1 ? '' : 's'}.` : 'WHOOP import saved.'), 'ok');
            toast('WHOOP import saved.', 'ok');
        } catch (err) {
            setWhoopIntakeStatus((err && err.message) || 'WHOOP import failed.', 'error');
            toast('WHOOP import failed.', 'err');
        } finally {
            state.whoopUi.importInFlight = false;
            if (submitBtn) submitBtn.disabled = false;
        }
    }

    function clearWhoopImportInput() {
        const text = $('whoop-import-csv-text');
        const file = $('whoop-import-file');
        const name = $('whoop-import-file-name');
        if (text) text.value = '';
        if (file) file.value = '';
        if (name) name.textContent = 'No file selected';
        state.whoopUi.selectedImportFile = null;
        setWhoopIntakeStatus('Manual import stays available even when live WHOOP sync is not configured.', 'info');
    }

    async function connectWhoop() {
        openWhoopIntakeModal();
        const url = currentWhoopConnectUrl();
        if (url) {
            setWhoopConnectFallback(url, 'Use the link above if you already started a WHOOP authorization flow.');
            return;
        }
    }

    function setWhoopActionButtons(whoop, uiState) {
        const connectBtn = $('btn-connect-whoop');
        const syncBtn = $('btn-sync-whoop');
        const disconnectBtn = $('btn-disconnect-whoop');
        const deleteBtn = $('btn-delete-whoop-data');
        const connectUrl = currentWhoopConnectUrl();
        const disconnected = uiState === WHOOP_UI_STATES.disconnected;
        const missingConfig = uiState === WHOOP_UI_STATES.missing_config;
        const reauth = uiState === WHOOP_UI_STATES.reauth_required;
        const csvOnlyDisconnected = uiState === WHOOP_UI_STATES.csv_only && (!whoop || whoop.connected === false);
        const liveSyncUnavailable = disconnected || missingConfig || reauth || csvOnlyDisconnected;
        const cleanupUnavailable = disconnected || csvOnlyDisconnected;
        const busySync = state.whoopUi.syncInFlight || uiState === WHOOP_UI_STATES.syncing;
        const busyDisconnect = state.whoopUi.disconnectInFlight;
        const busyDelete = state.whoopUi.deleteInFlight;
        const busy = busySync || busyDisconnect || busyDelete;

        if (connectBtn) {
            connectBtn.hidden = false;
            connectBtn.disabled = busy;
            connectBtn.textContent = reauth ? 'Reconnect' : missingConfig && !connectUrl ? 'Import' : liveSyncUnavailable || connectUrl ? 'Connect' : 'Import';
        }
        if (syncBtn) {
            syncBtn.hidden = liveSyncUnavailable && !connectUrl;
            syncBtn.disabled = busy || liveSyncUnavailable;
            syncBtn.textContent = busySync ? 'Syncing…' : 'Sync';
        }
        if (disconnectBtn) {
            disconnectBtn.hidden = cleanupUnavailable;
            disconnectBtn.disabled = busy || cleanupUnavailable;
            disconnectBtn.textContent = busyDisconnect ? 'Disconnecting…' : 'Disconnect';
        }
        if (deleteBtn) {
            deleteBtn.disabled = busy;
            deleteBtn.textContent = busyDelete ? 'Deleting…' : 'Delete data';
        }
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
        const dashboardSleepQualityKnown = sleep && sleep.data_quality
            && sleep.data_quality.status;
        const dashboardSleepInconsistent = sleep && sleep.data_quality
            && sleep.data_quality.status === 'inconsistent';
        const sleepQualityWarning = sleep && sleep.data_quality
            && sleep.data_quality.status === 'partial';
        const sleepQualityAction = dashboardSleepInconsistent || sleepQualityWarning
            ? [sleep.data_quality.source, sleep.data_quality.observed_at, 'Check sync'].filter(Boolean).join(' · ')
            : null;

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
        if ($('dash-sleep')) $('dash-sleep').textContent = !dashboardSleepQualityKnown || dashboardSleepInconsistent ? '--'
            : (sleep && sleep.last_night && sleep.last_night.total_sleep_min != null ? fmtDur(sleep.last_night.total_sleep_min) : '--');

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

        // Intensity metadata / time chip. RPE is an exercise-derived range;
        // `nw.goal` is an id string, not an object carrying an RPE target.
        const focusLabel = nw ? (nw.focus || nw.goal_name || '') : '';
        const intensityWord = reco && reco.recommendation
            ? (reco.recommendation === 'intensity' ? 'High'
                : reco.recommendation === 'moderate' ? 'Moderate'
                : reco.recommendation === 'recovery' ? 'Low'
                : reco.recommendation)
            : null;
        const readinessText = [focusLabel.replace(/_/g, ' '), intensityWord].filter(Boolean).join(' · ');
        const rpeLabel = nw && nw.rpe_range && nw.rpe_range.label;
        const targetIntensity = rpeLabel ? `Target intensity · RPE ${rpeLabel}` : '';
        if ($('reco-intensity')) {
            // Reset to HTML placeholder so a prior session's chip doesn't linger.
            $('reco-intensity').textContent = targetIntensity || readinessText || '—';
        }
        if ($('reco-readiness')) {
            // With an RPE range the readiness word moves here, so both show.
            const readinessShown = targetIntensity && readinessText;
            $('reco-readiness').textContent = readinessShown ? readinessText : '';
            $('reco-readiness').hidden = !readinessShown;
        }
        const timeMin = nw && nw.estimated_minutes;
        if ($('reco-time')) {
            if (timeMin) { $('reco-time').textContent = `${timeMin} min`; $('reco-time').hidden = false; }
            else { $('reco-time').hidden = true; }
        }
        if ($('reco-rpe')) {
            $('reco-rpe').hidden = true;
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

        // Reason / "why" — wearable reasoning
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

        // Today at a glance
        if ($('glance-steps')) $('glance-steps').textContent = fmtInt(oura && oura.steps);
        if ($('glance-steps-goal')) $('glance-steps-goal').textContent = oura && oura.steps != null ? `${Math.round((oura.steps / 10000) * 100)}% of 10,000` : 'Pending sync';
        if ($('glance-cal')) $('glance-cal').textContent = fmtInt(oura && oura.active_calories);
        if ($('glance-cal-goal')) $('glance-cal-goal').textContent = oura && oura.active_calories != null ? `${Math.round((oura.active_calories / 800) * 100)}% of 800` : '';
        if ($('glance-sleep')) $('glance-sleep').textContent = !dashboardSleepQualityKnown || dashboardSleepInconsistent ? '--'
            : (sleep && sleep.last_night && sleep.last_night.total_sleep_min != null ? fmtDur(sleep.last_night.total_sleep_min) : '--');
        if ($('glance-sleep-quality')) {
            const score = oura && oura.sleep_score;
            $('glance-sleep-quality').textContent = dashboardSleepInconsistent || sleepQualityWarning ? sleepQualityAction
                : (score != null ? (score >= 85 ? 'Excellent' : score >= 70 ? 'Good' : 'Fair') : '—');
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
            if (dashboardSleepInconsistent || sleepQualityWarning) {
                if ($('insight-title')) $('insight-title').textContent = 'Sleep data needs review';
                if ($('insight-body')) $('insight-body').textContent = sleepQualityAction;
            } else {
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
            }
        } else if (dashboardSleepInconsistent || sleepQualityWarning) {
            if ($('insight-title')) $('insight-title').textContent = 'Sleep data needs review';
            if ($('insight-body')) $('insight-body').textContent = sleepQualityAction;
        } else {
            if ($('insight-title')) $('insight-title').textContent = 'Gathering data…';
            if ($('insight-body')) $('insight-body').textContent = '';
        }

        // Sparkline: sleep scores from Oura trend
        const sleepTrendRows = sleep && sleep.trend_data ? sleep.trend_data : [];
        // trend_data is already vetted server-side (valid full nights kept), so no excluded_dates filter here.
        const safeSleepTrendRows = sleepTrendRows.filter((d) => d.score != null
            && (!dashboardSleepInconsistent || d.date !== sleep.data_quality.observed_at));
        const sleepSeries = safeSleepTrendRows.map((d) => d.score);
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
        const sleepInconsistent = sleep && sleep.data_quality && sleep.data_quality.status === 'inconsistent';
        const sleepQualityWarning = sleep && sleep.data_quality && sleep.data_quality.status === 'partial';
        const sleepQualityAction = sleepInconsistent || sleepQualityWarning
            ? [sleep.data_quality.source, sleep.data_quality.observed_at, 'Check sync'].filter(Boolean).join(' · ')
            : null;
        const excludedSleepDates = new Set(
            sleep && sleep.data_quality && sleep.data_quality.excluded_dates
                ? sleep.data_quality.excluded_dates
                : []
        );
        const safeSleepSeries = series.filter((s) => s.sleep_duration_min != null && s.nightly_sleep === true
            && !excludedSleepDates.has(s.day)
            && (!sleepInconsistent || s.day !== sleep.data_quality.observed_at));
        sparkline($('spark-steps'), series.map((s) => s.steps), { color: '#22c55e' });
        sparkline($('spark-active-min'), series.map((s) => s.activity_score), { color: '#fbbf24' });
        sparkline($('spark-sleep'), safeSleepSeries.map((s) => (s.sleep_duration_min || 0) / 60), { color: '#a78bfa' });

        // Sleep details
        // Sleep summary failed: fall back to the Oura status night.
        // shortcut: status comes from the unvetted daily cache, so only a >=60 min night is trusted.
        const statusNight = !sleep && oura && oura.sleep_duration_min >= 60 ? {
            total_sleep_min: oura.sleep_duration_min,
            rem_sleep_min: (oura.sleep_breakdown_min || {}).rem,
            deep_sleep_min: (oura.sleep_breakdown_min || {}).deep,
            sleep_score: oura.sleep_score,
        } : null;
        const last = sleep ? sleep.last_night : statusNight;
        if (sleepInconsistent) {
            $('v-sleep-dur').textContent = '--';
            $('v-sleep-dur-sub').textContent = 'Sleep data inconsistent · Check Oura sync';
            $('v-sleep-score').textContent = '--';
            $('v-sleep-score-sub').textContent = sleep.data_quality.observed_at || '';
        } else if (last) {
            const totalSleepMin = last.total_sleep_min;
            $('v-sleep-dur').textContent = totalSleepMin != null ? fmtDur(totalSleepMin) : '--';
            const remLabel = last.rem_sleep_min != null ? `${Math.round(last.rem_sleep_min)}m REM` : 'REM unknown';
            const deepLabel = last.deep_sleep_min != null ? `${Math.round(last.deep_sleep_min)}m Deep` : 'Deep unknown';
            $('v-sleep-dur-sub').textContent = `${remLabel} · ${deepLabel}`;
        } else {
            $('v-sleep-dur').textContent = '--';
            $('v-sleep-dur-sub').textContent = '—';
        }
        if (!sleepInconsistent) {
            $('v-sleep-score').textContent = last && last.sleep_score != null ? last.sleep_score : '--';
            const wa = sleep && sleep.week_average;
            $('v-sleep-score-sub').textContent = sleepQualityWarning ? sleepQualityAction
                : (wa && wa.score ? `avg ${wa.score} · 7d` : '');
        }

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
            const weightStr = ex.bodyweight ? ' · BW' : (w != null && Number(w) > 0 ? ` · ${Math.round(w)} lb` : '');
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
        if (ex.bodyweight) bits.push('BW');
        else if (w != null && Number(w) > 0) bits.push(`${Math.round(w)} lb`);
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
    function canonicalHistoryCategory(label, source) {
        const raw = String(label || '').trim().toLowerCase().replace(/[_-]+/g, ' ').replace(/\s+/g, ' ');
        const src = String(source || '').trim().toLowerCase();
        if (src === 'lifted') return 'strength_training';
        if (['lifted', 'functional strength training', 'traditional strength training', 'strength training', 'weight training', 'resistance training'].includes(raw)) return 'strength_training';
        if (raw.includes('strength') || raw.includes('weight') || raw.includes('resistance')) return 'strength_training';
        return raw ? raw.replace(/\s+/g, '_') : 'other';
    }

    function normalizeWatchHistoryRow(w) {
        const label = w.original_label || w.activity_type || w.activity || 'Workout';
        const category = w.canonical_category || canonicalHistoryCategory(label, 'watch');
        return {
            ...w,
            source: 'watch',
            original_label: label,
            canonical_category: category,
            source_label: w.source_label || (category === 'strength_training' ? 'Strength - Watch' : 'Watch'),
        };
    }

    function normalizeOpenWearablesHistoryRow(w) {
        const label = w.original_label || w.activity_type || w.session_type || 'Workout';
        return {
            ...w,
            source: 'open_wearables',
            provider: 'Open Wearables',
            original_label: label,
            canonical_category: w.canonical_category || canonicalHistoryCategory(label, 'open_wearables'),
            source_label: 'Open Wearables',
        };
    }

    function historyWorkoutFingerprints(row) {
        const rawStart = row && (row.startDate || row.start || row.start_time || row.created_at);
        const timestamp = Date.parse(rawStart || '');
        if (!Number.isFinite(timestamp)) return [];
        const labels = row && row.source === 'open_wearables'
            ? [row.session_type || row.type, row.activity_type || row.activity || row.canonical_category]
            : [row && (row.canonical_category || canonicalHistoryCategory(
                row.activity_type || row.activity || row.session_type,
                row.source
            ))];
        const aliases = {
            bike: 'cycling',
            rower: 'rowing',
            'treadmill incline walk': 'treadmill',
            'treadmill run': 'treadmill',
            'outdoor run': 'running',
            'outdoor walk': 'walking',
            walk: 'walking',
        };
        const second = Math.floor(timestamp / 1000);
        return Array.from(new Set(labels.map((label) => {
            const canonicalLabel = canonicalHistoryCategory(label, row && row.source);
            const normalized = String(canonicalLabel || '').trim().toLowerCase().replace(/[_-]+/g, ' ');
            const category = aliases[normalized] || normalized.replace(/\s+/g, '_');
            return category ? `${second}:${category}` : null;
        }).filter(Boolean)));
    }

    function mergeOpenWearablesHistory(baseRows, openWearablesRows) {
        const base = (baseRows || []).map((row) => ({ ...row }));
        const openWearables = (openWearablesRows || []).map((row) => ({ ...row }));
        const baseByFingerprint = new Map();
        const openWearablesByFingerprint = new Map();
        base.forEach((row, index) => {
            historyWorkoutFingerprints(row).forEach((fingerprint) => {
                baseByFingerprint.set(fingerprint, new Set([...(baseByFingerprint.get(fingerprint) || []), index]));
            });
        });
        openWearables.forEach((row, index) => {
            historyWorkoutFingerprints(row).forEach((fingerprint) => {
                openWearablesByFingerprint.set(fingerprint, new Set([...(openWearablesByFingerprint.get(fingerprint) || []), index]));
            });
        });

        const matchedOpenWearables = new Set();
        base.forEach((baseRow, baseIndex) => {
            const openWearablesCandidates = new Set();
            historyWorkoutFingerprints(baseRow).forEach((fingerprint) => {
                (openWearablesByFingerprint.get(fingerprint) || []).forEach((index) => openWearablesCandidates.add(index));
            });
            if (openWearablesCandidates.size !== 1) return;
            const openWearablesIndex = openWearablesCandidates.values().next().value;
            const baseCandidates = new Set();
            historyWorkoutFingerprints(openWearables[openWearablesIndex]).forEach((fingerprint) => {
                (baseByFingerprint.get(fingerprint) || []).forEach((index) => baseCandidates.add(index));
            });
            if (baseCandidates.size !== 1 || !baseCandidates.has(baseIndex)) return;
            const openWearablesRow = openWearables[openWearablesIndex];
            let importedMetric = false;
            ['calories_burned', 'avg_heart_rate', 'max_heart_rate'].forEach((field) => {
                if (baseRow[field] == null && openWearablesRow[field] != null) {
                    baseRow[field] = openWearablesRow[field];
                    importedMetric = true;
                }
            });
            baseRow.open_wearables_match = true;
            if (importedMetric) baseRow.open_wearables_metrics = true;
            matchedOpenWearables.add(openWearablesIndex);
        });
        return base.concat(openWearables.filter((_, index) => !matchedOpenWearables.has(index)));
    }

    function mergeStrengthHistorySources(lifts, watchRows) {
        const loggedByDate = new Map();
        const mergedLifts = (lifts || []).map((w) => {
            const row = {
                ...w,
                source: 'lifted',
                canonical_category: w.canonical_category || 'strength_training',
                original_label: w.original_label || 'Lifted',
                source_label: w.source_label || 'Strength - Logged',
                merged_sources: Array.isArray(w.merged_sources) ? [...w.merged_sources] : ['logged'],
            };
            if (row.date && row.canonical_category === 'strength_training') {
                loggedByDate.set(row.date, [...(loggedByDate.get(row.date) || []), row]);
            }
            return row;
        });
        const absorbed = new Set();
        const visibleWatch = [];
        (watchRows || []).forEach((row) => {
            // A wearable strength session counts once: it folds into a logged lift
            // that day, but each lift absorbs at most one session. Extra distinct
            // sessions stay visible as their own rows.
            const candidates = row.canonical_category === 'strength_training' && row.date ? (loggedByDate.get(row.date) || []) : [];
            const logged = candidates.find((lift) => !absorbed.has(lift));
            if (logged) {
                absorbed.add(logged);
                const sourceKey = row.source === 'open_wearables' ? 'open_wearables' : 'watch';
                logged.merged_sources = Array.from(new Set([...(logged.merged_sources || []), sourceKey]));
                logged.source_label = sourceKey === 'watch' ? 'Strength - Logged + Watch' : 'Strength - Logged + Open Wearables';
                logged.watch_duration_minutes = row.duration_minutes || row.duration || logged.watch_duration_minutes || null;
                logged.watch_activity_label = row.original_label || row.activity_type || row.activity || logged.watch_activity_label || null;
                // Logged lifts carry no start time, so the match is by date alone. Only
                // copy the metrics when that date has exactly one logged lift; with
                // several, the figures may belong to a different session.
                const hasWearableMetrics = ['calories_burned', 'avg_heart_rate', 'max_heart_rate'].some((field) => row[field] != null);
                if (candidates.length === 1 && hasWearableMetrics && (row.source === 'open_wearables' || row.open_wearables_metrics)) {
                    logged.wearable_calories_burned = row.calories_burned ?? null;
                    logged.wearable_avg_heart_rate = row.avg_heart_rate ?? null;
                    logged.wearable_max_heart_rate = row.max_heart_rate ?? null;
                    logged.wearable_metrics_source = 'Open Wearables';
                }
                return;
            }
            visibleWatch.push(row);
        });
        return mergedLifts.concat(visibleWatch);
    }

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

        let hist, aw, ow;
        try {
            [hist, aw, ow] = await Promise.all([
                getHistory(),
                getAppleHealthWorkouts(Math.max(days, 30)),
                getOpenWearablesWorkouts(),
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
            .map((w) => ({ ...w, source: 'lifted', canonical_category: w.canonical_category || 'strength_training', original_label: w.original_label || 'Lifted' }))
            .filter((w) => w.date && new Date(w.date + 'T00:00:00') >= cutoff);
        const watch = (Array.isArray(aw) ? aw : [])
            .filter((w) => w.date && new Date(w.date + 'T00:00:00') >= cutoff)
            .map(normalizeWatchHistoryRow);
        const openWearables = (Array.isArray(ow) ? ow : [])
            .filter((w) => w.date && new Date(w.date + 'T00:00:00') >= cutoff)
            .map(normalizeOpenWearablesHistoryRow);
        const wearableRows = mergeOpenWearablesHistory(watch, openWearables);
        const mergedHistoryRows = mergeStrengthHistorySources(lifts, wearableRows);

        const workouts = mergedHistoryRows.filter((w) => w.source === 'lifted');

        const totalVol = lifts.reduce((a, w) => a + Number(w.total_volume || 0), 0);
        const totalSessions = mergedHistoryRows.length;
        $('history-count').textContent = totalSessions || '0';
        const watchOnlyCount = mergedHistoryRows.filter((w) => w.source === 'watch').length;
        const mergedWatchCount = mergedHistoryRows.filter((w) => Array.isArray(w.merged_sources) && w.merged_sources.includes('watch')).length;
        const openWearablesCount = mergedHistoryRows.filter((w) => w.source === 'open_wearables').length;
        if (openWearablesCount) {
            const sourceCounts = [];
            if (lifts.length) sourceCounts.push(`${lifts.length} logged`);
            if (watchOnlyCount) sourceCounts.push(`${watchOnlyCount} Watch-only`);
            if (mergedWatchCount) sourceCounts.push(`${mergedWatchCount} merged`);
            sourceCounts.push(`${openWearablesCount} Open Wearables`);
            $('history-freq-sub').textContent = `Last ${days} days · ${sourceCounts.join(' + ')}`;
        } else {
            $('history-freq-sub').textContent = lifts.length && (watchOnlyCount || mergedWatchCount)
                ? `Last ${days} days · ${lifts.length} logged + ${watchOnlyCount} Watch-only + ${mergedWatchCount} merged`
                : `Last ${days} days`;
        }
        $('history-total-volume').textContent = fmtKilo(totalVol);
        $('history-vol-sub').textContent = `Last ${days} days · lifting only`;

        // frequency bars by day - count canonical merged sessions once.
        const buckets = buildDailyBuckets(days);
        mergedHistoryRows.forEach((w) => {
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
        const merged = mergedHistoryRows.sort((a, b) => (b.date || '').localeCompare(a.date || ''));
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
            const label = historyFilterLabel(state.historyTypeFilter);
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
            if (w.source === 'open_wearables') {
                const title = w.activity_type || w.session_type || 'Workout';
                const meta = [
                    formatOptionalWorkoutMetric(w.duration_minutes, 'min', { omitMissing: true }),
                    formatOptionalWorkoutMetric(w.calories_burned, 'kcal', { omitMissing: true }),
                    formatOptionalWorkoutMetric(w.avg_heart_rate, 'bpm avg', { omitMissing: true }),
                    formatOptionalWorkoutMetric(w.max_heart_rate, 'bpm max', { omitMissing: true }),
                ].filter(Boolean).join(' · ');
                row.innerHTML = `
                    <div class="w-date">${escapeHtml(fmtDate(w.date))}</div>
                    <div>
                        <div class="w-summary"><span class="src-tag src-open-wearables">OPEN WEARABLES</span>${escapeHtml(title)}</div>
                        <div class="w-meta">${escapeHtml(meta) || '—'}</div>
                    </div>
                    <div class="w-volume open-wearables-volume">${escapeHtml(formatOptionalWorkoutMetric(w.duration_minutes, 'min', { omitMissing: true }) || '')}</div>
                `;
                row.addEventListener('click', () => openWorkoutDetail(w));
                row.addEventListener('keydown', (ev) => {
                    if (ev.key === 'Enter' || ev.key === ' ') {
                        ev.preventDefault();
                        openWorkoutDetail(w);
                    }
                });
            } else if (w.source === 'watch') {
                const title = w.activity_type || w.activity || 'Workout';
                const mins = w.duration_minutes || w.duration_min || 0;
                const kcal = historyCaloriesForDisplay(w);
                const meta = [
                    mins ? `${Math.round(mins)} min` : null,
                    formatOptionalWorkoutMetric(kcal, 'kcal', { omitMissing: true }),
                    formatOptionalWorkoutMetric(w.avg_heart_rate, 'bpm avg', { omitMissing: true }),
                    formatOptionalWorkoutMetric(w.max_heart_rate, 'bpm max', { omitMissing: true }),
                ].filter(Boolean).join(' · ');
                row.innerHTML = `
                    <div class="w-date">${fmtDate(w.date)}</div>
                    <div>
                        <div class="w-summary"><span class="src-tag src-watch">WATCH</span>${w.open_wearables_metrics ? '<span class="src-tag src-open-wearables">OPEN WEARABLES</span>' : ''}${escapeHtml(title)}</div>
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
                const mergedLabel = Array.isArray(w.merged_sources) && w.merged_sources.includes('watch')
                    ? ` · merged Watch ${w.watch_duration_minutes ? Math.round(w.watch_duration_minutes) + ' min' : 'strength'}`
                    : '';
                const wearableMeta = w.wearable_metrics_source
                    ? ` · ${w.wearable_metrics_source}: ${loggedWearableMetricParts(w).join(' · ')}`
                    : '';
                const sourceMeta = mergedLabel + wearableMeta;
                row.innerHTML = `
                    <div class="w-date">${fmtDate(w.date)}</div>
                    <div>
                        <div class="w-summary"><span class="src-tag src-lifted">STRENGTH</span>${escapeHtml(exNames || '—')}</div>
                        <div class="w-meta">${w.total_sets || 0} sets · ${w.duration_minutes || 0} min${escapeHtml(sourceMeta)}</div>
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
        if (w.canonical_category) return w.canonical_category;
        if (w.source === 'lifted') return 'strength_training';
        return (w.activity_type || w.activity || 'Other').toString().trim().toLowerCase() || 'other';
    }

    function historyFilterLabel(key) {
        if (key === 'all') return 'All';
        if (key === 'strength_training') return 'Strength Training';
        if (key === 'lifted') return 'Strength Training';
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
        if (counts.has('strength_training')) keys.push('strength_training');
        Array.from(counts.keys())
            .filter((key) => key !== 'strength_training')
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

    function formatOptionalWorkoutMetric(value, unit, { omitMissing = false } = {}) {
        if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) {
            return omitMissing ? '' : '—';
        }
        const rounded = Math.round(Number(value));
        return unit ? `${rounded} ${unit}` : String(rounded);
    }

    function loggedWearableMetricParts(item) {
        // Dash (never 0) for any metric the wearable did not report.
        return [
            [item.wearable_calories_burned, 'kcal'],
            [item.wearable_avg_heart_rate, 'bpm avg'],
            [item.wearable_max_heart_rate, 'bpm max'],
        ].map(([value, unit]) => {
            const text = formatOptionalWorkoutMetric(value, unit);
            return text === '\u2014' ? `\u2014 ${unit}` : text;
        });
    }

    function historyCaloriesForDisplay(item) {
        if (item && item.open_wearables_metrics && item.calories_burned != null) {
            return item.calories_burned;
        }
        return item && (item.total_energy_kcal ?? item.energy_kcal ?? item.calories_burned);
    }

    function openWorkoutDetail(item) {
        const modal = $('modal-workout-detail');
        const body = $('workout-detail-body');
        const title = $('workout-detail-title');
        const foot = $('workout-detail-foot');
        const deleteBtn = $('btn-delete-workout');
        if (!modal || !body || !title || !item) return;

        if (foot && deleteBtn) {
            const deletable = item.source === 'lifted' && typeof item.id === 'string' && item.id.length > 0;
            foot.hidden = !deletable;
            const fresh = deleteBtn.cloneNode(true);
            deleteBtn.parentNode.replaceChild(fresh, deleteBtn);
            if (deletable) {
                fresh.addEventListener('click', () => openDeleteConfirm(item));
            }
        }

        if (item.source === 'open_wearables') {
            const activity = item.activity_type || item.session_type || 'Workout';
            title.textContent = `${activity} · ${fmtDate(item.date)}`;
            body.innerHTML = `
                <div class="workout-source-line">
                    <span class="src-tag src-open-wearables">OPEN WEARABLES</span>
                    ${item.provider_source ? `<span>${escapeHtml(item.provider_source)}</span>` : ''}
                </div>
                <dl class="workout-metrics-list">
                    <div><dt>Duration</dt><dd>${escapeHtml(formatOptionalWorkoutMetric(item.duration_minutes, 'min'))}</dd></div>
                    <div><dt>Calories burned</dt><dd>${escapeHtml(formatOptionalWorkoutMetric(item.calories_burned, 'kcal'))}</dd></div>
                    <div><dt>Average heart rate</dt><dd>${escapeHtml(formatOptionalWorkoutMetric(item.avg_heart_rate, 'bpm'))}</dd></div>
                    <div><dt>Maximum heart rate</dt><dd>${escapeHtml(formatOptionalWorkoutMetric(item.max_heart_rate, 'bpm'))}</dd></div>
                </dl>
                ${item.notes ? `<div class="workout-detail-section"><div class="analyze-label">NOTES</div><div class="workout-note">${escapeHtml(item.notes)}</div></div>` : ''}
                <div class="workout-detail-actions">
                    <button type="button" class="btn btn-ghost btn-sm" id="btn-analyze-open-wearables">Analyze workout</button>
                </div>
            `;
            const analyzeBtn = $('btn-analyze-open-wearables');
            if (analyzeBtn) {
                analyzeBtn.addEventListener('click', () => {
                    modal.hidden = true;
                    openAnalyzeModal(
                        { workout_id: item.id },
                        `Analysis · ${fmtDate(item.date)}`,
                    );
                });
            }
            modal.hidden = false;
            return;
        }

        if (item.source === 'watch') {
            const activity = item.activity_type || item.activity || 'Workout';
            const mins = item.duration_minutes || item.duration_min || 0;
            const kcal = historyCaloriesForDisplay(item);
            title.textContent = `${activity} · ${fmtDate(item.date)}`;
            body.innerHTML = `
                <div class="workout-source-line">
                    <span class="src-tag src-watch">WATCH</span>
                    ${item.open_wearables_metrics ? '<span class="src-tag src-open-wearables">OPEN WEARABLES METRICS</span>' : ''}
                </div>
                <div class="workout-detail-kpis workout-detail-kpis-four">
                    <div><span>${Math.round(mins || 0)}</span><label>minutes</label></div>
                    <div><span>${escapeHtml(formatOptionalWorkoutMetric(kcal, ''))}</span><label>kcal</label></div>
                    <div><span>${escapeHtml(formatOptionalWorkoutMetric(item.avg_heart_rate, ''))}</span><label>avg HR</label></div>
                    <div><span>${escapeHtml(formatOptionalWorkoutMetric(item.max_heart_rate, ''))}</span><label>max HR</label></div>
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
        const [loggedKcal, loggedAvgHr, loggedMaxHr] = item.wearable_metrics_source ? loggedWearableMetricParts(item) : [];
        const wearableMetricsHtml = item.wearable_metrics_source ? `
            <div class="workout-source-line"><span class="src-tag src-open-wearables">${escapeHtml(String(item.wearable_metrics_source).toUpperCase())}</span></div>
            <dl class="workout-metrics-list">
                <div><dt>Calories burned</dt><dd>${escapeHtml(loggedKcal)}</dd></div>
                <div><dt>Average heart rate</dt><dd>${escapeHtml(loggedAvgHr)}</dd></div>
                <div><dt>Maximum heart rate</dt><dd>${escapeHtml(loggedMaxHr)}</dd></div>
            </dl>
        ` : '';
        body.innerHTML = `
            <div class="workout-detail-kpis">
                <div><span>${fmtInt(totalSets)}</span><label>sets</label></div>
                <div><span>${fmtKilo(totalVolume)}</span><label>lbs</label></div>
                <div><span>${fmtInt(item.duration_minutes || 0)}</span><label>minutes</label></div>
            </div>
            ${wearableMetricsHtml}
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
        if (!workout || typeof workout.id !== 'string' || !workout.id) return;
        const confirmModal = $('modal-delete-confirm');
        if (button) {
            button.disabled = true;
            button.textContent = 'Deleting…';
        }
        try {
            const res = await api('/api/delete-history', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ type: 'workout', id: workout.id }),
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

        renderBodyRecompTargetProgress();
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

    function progressInsightVisual(ins) {
        const kind = String(ins && ins.type || 'info').toLowerCase();
        const iconName = String(ins && ins.icon || '').toLowerCase();
        const toneByType = {
            positive: 'pos',
            success: 'pos',
            warning: 'warn',
            negative: 'neg',
            danger: 'neg',
            info: 'info',
        };
        const glyphByIcon = {
            trending_up: '↑',
            trending_down: '↓',
            pause: '‖',
            schedule: '◷',
            balance: '↔',
            warning: '!',
            emoji_events: '★',
        };
        const fallbackGlyphByType = {
            positive: '↑',
            success: '↑',
            warning: '!',
            negative: '!',
            danger: '▲',
            info: 'i',
        };
        return {
            iconClass: toneByType[kind] || 'info',
            iconChar: glyphByIcon[iconName] || fallbackGlyphByType[kind] || 'i',
        };
    }

    async function renderStats() {
        const days = state.ranges.stats;
        const [hist, appleWorkouts] = await Promise.all([
            getHistory(),
            getAppleHealthWorkouts(Math.max(days, 30)),
        ]);
        const logged = Array.isArray(hist && hist.workouts) ? hist.workouts.map((w) => ({ ...w, source: 'lifted', canonical_category: w.canonical_category || 'strength_training' })) : [];
        const watch = Array.isArray(appleWorkouts)
            ? appleWorkouts.map(normalizeWatchHistoryRow)
            : [];
        const all = mergeStrengthHistorySources(logged, watch);
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
                const { iconClass, iconChar } = progressInsightVisual(ins);
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
        const [st, oura, whoop, openWearables, wearableSources] = await Promise.all([
            getSettings(),
            getOuraStatus(true, true),
            getWhoopStatus(true),
            getOpenWearablesStatus(true),
            getWearableSources(true),
        ]);
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
        renderOpenWearablesDetail(openWearables, wearableSources);
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

    async function checkOpenWearables() {
        if (state.openWearablesUi.checkInFlight) return;
        state.openWearablesUi.checkInFlight = true;
        state.openWearablesUi.lastError = '';
        renderOpenWearablesDetail(state.openWearablesStatus, state.wearableSources);
        try {
            const body = await api('/api/open-wearables/setup/check', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({}),
            });
            state.openWearablesStatus = body && body.open_wearables ? body.open_wearables : await getOpenWearablesStatus(true);
            if (body && body.config) populateOpenWearablesSetupFields(body.config);
            if (openWearablesIsConnected(state.openWearablesStatus, body && body.status)) {
                setOpenWearablesSetupStatus('Device is visible. Sync now when ready.', 'ok');
            } else {
                setOpenWearablesSetupStatus('Finish pairing, then check connection again.', 'warn');
            }
            toast('Open Wearables checked.', 'ok');
        } catch (error) {
            state.openWearablesUi.lastError = error && error.message ? error.message : 'Open Wearables check failed.';
            setOpenWearablesSetupStatus(state.openWearablesUi.lastError, 'error');
            toast('Open Wearables check failed.', 'err');
        } finally {
            state.openWearablesUi.checkInFlight = false;
            await renderSettings();
        }
    }

    async function syncOpenWearables() {
        if (state.openWearablesUi.syncInFlight) return;
        state.openWearablesUi.syncInFlight = true;
        state.openWearablesUi.lastError = '';
        renderOpenWearablesDetail(state.openWearablesStatus, state.wearableSources);
        try {
            await api('/api/open-wearables/sync', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ trigger: 'manual' }),
            });
            state.openWearablesStatus = null;
            state.wearableSources = null;
            state.dashboard = null;
            state.open_wearables_workouts = null;
            toast('Open Wearables sync complete.', 'ok');
        } catch (error) {
            state.openWearablesUi.lastError = error && error.message ? error.message : 'Open Wearables sync failed.';
            toast('Open Wearables sync failed.', 'err');
        } finally {
            state.openWearablesUi.syncInFlight = false;
            await renderSettings();
        }
    }

    async function askAiFactQuestion() {
        if (state.aiFactUi.queryInFlight) return;
        const input = $('ai-fact-question');
        const answerEl = $('ai-fact-answer');
        const actions = $('ai-fact-suggestion-actions');
        const question = input ? String(input.value || '').trim() : '';
        if (!question) {
            if (answerEl) answerEl.textContent = 'Ask about strength history, wearable sources, or freshness.';
            if (actions) actions.hidden = true;
            state.aiFactUi.pendingSuggestionId = null;
            return;
        }
        state.aiFactUi.queryInFlight = true;
        state.aiFactUi.pendingSuggestionId = null;
        if (actions) actions.hidden = true;
        const btn = $('btn-ai-fact-query');
        if (btn) btn.disabled = true;
        if (answerEl) answerEl.textContent = 'Checking sanitized history and wearable facts…';
        try {
            const payload = await api('/api/ai/facts/query', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ question, suggest: true }),
            });
            const evidenceCount = Array.isArray(payload && payload.evidence) ? payload.evidence.length : 0;
            if (answerEl) answerEl.textContent = `${payload.answer || 'No answer available.'}${evidenceCount ? ` · ${evidenceCount} evidence item${evidenceCount === 1 ? '' : 's'}` : ''}`;
            if (payload && payload.suggested_action_id) {
                state.aiFactUi.pendingSuggestionId = payload.suggested_action_id;
                if (actions) actions.hidden = false;
            }
        } catch (error) {
            if (answerEl) answerEl.textContent = 'AI fact query failed. No records were changed.';
            toast('AI fact query failed.', 'err');
        } finally {
            state.aiFactUi.queryInFlight = false;
            if (btn) btn.disabled = false;
        }
    }

    async function resolveAiSuggestion(decision) {
        const suggestionId = state.aiFactUi.pendingSuggestionId;
        if (!suggestionId || state.aiFactUi.actionInFlight) return;
        state.aiFactUi.actionInFlight = true;
        const approveBtn = $('btn-ai-suggestion-approve');
        const rejectBtn = $('btn-ai-suggestion-reject');
        if (approveBtn) approveBtn.disabled = true;
        if (rejectBtn) rejectBtn.disabled = true;
        try {
            const payload = await api(`/api/ai/suggestions/${encodeURIComponent(suggestionId)}/${decision}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({}),
            });
            const status = payload && payload.suggestion && payload.suggestion.status;
            const answerEl = $('ai-fact-answer');
            if (answerEl) {
                answerEl.textContent = status === 'approved'
                    ? 'Suggestion approved. No records were changed in this version.'
                    : 'Suggestion rejected. No records were changed.';
            }
            state.aiFactUi.pendingSuggestionId = null;
            const actions = $('ai-fact-suggestion-actions');
            if (actions) actions.hidden = true;
        } catch (error) {
            toast('AI suggestion action failed.', 'err');
        } finally {
            state.aiFactUi.actionInFlight = false;
            if (approveBtn) approveBtn.disabled = false;
            if (rejectBtn) rejectBtn.disabled = false;
        }
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
            state.reco = null;
            await renderSettings();
        }
    }

    async function deleteWhoopData() {
        if (state.whoopUi.deleteInFlight) return;
        if (typeof confirm === 'function' && !confirm('Delete local WHOOP data from this web app?')) return;
        state.whoopUi.deleteInFlight = true;
        state.whoopUi.lastError = '';
        renderWhoopFreshnessDetail(state.whoopStatus, mergeWhoopFreshnessNode(null, state.whoopStatus, []), []);
        try {
            const body = await api('/api/whoop/delete-data', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({}),
            });
            state.whoopStatus = Object.assign({}, state.whoopStatus || {}, body.connection || {}, {
                freshness: Object.assign({}, (body.connection && body.connection.freshness) || {}, {
                    status: WHOOP_UI_STATES.missing,
                    last_data_point: null,
                }),
            });
            toast('WHOOP data deleted.', 'ok');
        } catch (error) {
            state.whoopUi.lastError = error && error.message ? error.message : 'WHOOP data delete failed.';
            toast('WHOOP data delete failed.', 'err');
        } finally {
            state.whoopUi.deleteInFlight = false;
            state.dashboard = null;
            state.reco = null;
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
        prompt: 'Preview coaching alerts and verify push delivery. This does not schedule reminders.',
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
        // FIT-39's deterministic stale-wearable rules and
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

    function _setText(id, text) {
        const el = $(id);
        if (el) el.textContent = text;
    }

    function renderWhoopFreshnessDetail(whoop, freshness, conflicts) {
        const uiState = resolveWhoopUiState(freshness || whoop, conflicts);
        const dataThrough = (freshness && (freshness.last_data_point || freshness.local_date))
            || (whoop && (whoop.last_data_point || whoop.local_date));
        const hideDirectFallback = Boolean(
            !dataThrough
            && (uiState === WHOOP_UI_STATES.missing_config || uiState === WHOOP_UI_STATES.disconnected)
        );
        const row = $('whoop-settings-row');
        const detailPanel = $('whoop-detail');
        if (row) row.hidden = false;
        if (detailPanel) detailPanel.hidden = hideDirectFallback;
        const dot = $('whoop-int-dot');
        if (dot) dot.className = uiState === WHOOP_UI_STATES.disconnected || uiState === WHOOP_UI_STATES.missing_config
            ? 'int-dot'
            : 'int-dot int-dot-on';

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
            missing_config: 'Config missing',
            csv_only: 'CSV import only',
            source_conflict: 'Connected · source conflict',
            error: 'Connection error',
        }[uiState] || 'Disconnected';
        _setDetail('whoop-detail-connection', connectionText);

        _setDetail('whoop-detail-data-through', dataThrough ? `${dataThrough} (${uiState.replace(/_/g, ' ')})` : 'No WHOOP data yet');

        const recoveryParts = [];
        const recoveryScore = whoop && (whoop.recovery_score != null ? whoop.recovery_score : whoop.score);
        if (recoveryScore != null) recoveryParts.push(`Recovery ${recoveryScore}`);
        if (whoop && whoop.recovery_band) recoveryParts.push(String(whoop.recovery_band));
        if (whoop && whoop.strain != null) recoveryParts.push(`strain ${whoop.strain}`);
        if (whoop && whoop.sleep_performance_pct != null) recoveryParts.push(`sleep ${whoop.sleep_performance_pct}%`);
        _setDetail('whoop-detail-recovery', recoveryParts.length ? recoveryParts.join(' · ') : 'No scored WHOOP recovery yet');

        let sourceText = 'Direct WHOOP fallback';
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
        else if (uiState === WHOOP_UI_STATES.missing_config) attentionText = 'WHOOP OAuth is not configured on this server. Disconnect and delete local data remain available.';
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

    function renderOpenWearablesDetail(status, sources) {
        const dot = $('open-wearables-int-dot');
        const chip = $('open-wearables-state');
        const detail = $('open-wearables-detail');
        const panel = $('open-wearables-detail-panel');
        const stateToken = normalizeWhoopStateToken(status && status.status);
        const providers = Array.isArray(status && status.providers) ? status.providers : [];
        const connected = openWearablesIsConnected(status);
        const setupState = deriveOpenWearablesSetupState(status, state.openWearablesUi.config);
        if (dot) dot.className = connected ? 'int-dot int-dot-on' : 'int-dot';
        if (chip) {
            chip.className = 'state-chip';
            if (setupState === 'connected' || setupState === 'ready_to_sync') chip.classList.add('ok');
            else if (stateToken === 'blocked' || stateToken === WHOOP_UI_STATES.error) chip.classList.add('stale');
            else chip.classList.add('warn');
            chip.textContent = openWearablesChipText(setupState);
        }
        if (detail) {
            const checked = status && status.last_checked_at ? ` · checked ${fmtDateTime(status.last_checked_at)}` : '';
            detail.textContent = `${openWearablesSetupCopy(setupState)}${checked}`;
        }
        if (panel) panel.hidden = false;
        const hubText = setupState === 'connected' || setupState === 'ready_to_sync'
            ? 'Hub account ready'
            : setupState === 'needs_hub_account' || setupState === 'needs_person'
                ? 'Prepare pairing'
                : setupState === 'needs_provider_credentials'
                    ? 'Sign-in setup needed'
                : setupState === 'blocked' || setupState === 'error'
                    ? 'Needs diagnostics'
                    : 'Pairing in progress';
        _setDetail('open-wearables-detail-hub', hubText);
        const providerCount = providers.length;
        _setDetail('open-wearables-detail-providers', providerCount ? `${providerCount} hub provider${providerCount === 1 ? '' : 's'} visible` : 'No hub providers visible yet');
        const attentionRow = $('open-wearables-detail-attention-row');
        if (attentionRow) {
            const errorCode = status && status.error_code;
            const message = state.openWearablesUi.lastError || (errorCode ? String(errorCode).replace(/_/g, ' ') : '');
            attentionRow.hidden = !message;
            if (message) _setDetail('open-wearables-detail-attention', message);
        }
        const setupCheckBtn = $('btn-open-wearables-setup-check');
        if (setupCheckBtn) setupCheckBtn.disabled = state.openWearablesUi.checkInFlight;
        const bootstrapBtn = $('btn-open-wearables-bootstrap');
        if (bootstrapBtn) {
            const userMapped = Boolean(state.openWearablesUi.config && state.openWearablesUi.config.user_mapped);
            const setupState = deriveOpenWearablesSetupState(status, state.openWearablesUi.config);
            bootstrapBtn.disabled = state.openWearablesUi.bootstrapInFlight;
            bootstrapBtn.textContent = state.openWearablesUi.bootstrapInFlight
                ? 'Preparing...'
                : setupState === 'needs_provider_credentials'
                    ? 'Show details'
                    : userMapped
                        ? 'Choose wearable'
                    : 'Prepare pairing';
        }
        const setupSaveBtn = $('btn-open-wearables-setup-save');
        if (setupSaveBtn) setupSaveBtn.disabled = state.openWearablesUi.saveInFlight;
        const setupBtn = $('btn-open-wearables-setup');
        if (setupBtn) {
            setupBtn.textContent = connected ? 'Manage devices' : 'Add device';
            setupBtn.classList.toggle('btn-primary', !connected);
            setupBtn.classList.toggle('btn-ghost', connected);
        }
        const syncBtn = $('btn-sync-open-wearables');
        if (syncBtn) {
            syncBtn.disabled = state.openWearablesUi.syncInFlight || !openWearablesCanSync(
                status,
                state.openWearablesUi.config,
                setupState
            );
        }
        applyOpenWearablesDirectSourceVisibility(status);
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

    function syncBodyLogValidation() {
        const weightInput = $('body-log-weight');
        const bodyFatInput = $('body-log-bf');
        const saveButton = $('btn-log-body');
        const error = $('body-log-error');
        const weightPresent = Number(weightInput.value) > 0;
        const bodyFatPresent = bodyFatInput.value.trim() !== '';
        const missingWeight = bodyFatPresent && !weightPresent;

        saveButton.disabled = bodyFatPresent && !weightPresent;
        if (bodyNavyCalculationPending) saveButton.disabled = true;
        weightInput.setAttribute('aria-invalid', missingWeight ? 'true' : 'false');
        error.hidden = !missingWeight;
        return !saveButton.disabled;
    }

    let bodyNavyDerivedValue = null;
    let bodyNavyInputVersion = 0;
    let bodyNavyCalculationPending = false;

    function navyInputValue(id) {
        const el = $(id);
        const value = el ? String(el.value ?? '').trim() : '';
        return value === '' ? null : Number(value);
    }

    function bodyNavyContextPayload() {
        const values = {
            height_in: navyInputValue('body-navy-height'),
            neck_in: navyInputValue('body-navy-neck'),
            waist_in: navyInputValue('body-navy-waist'),
            hip_in: navyInputValue('body-navy-hip'),
        };
        if (!Object.values(values).some((value) => value !== null)) return {};
        return {
            sex: $('body-navy-sex').value,
            ...values,
        };
    }

    function syncBodyNavySex() {
        const hipField = $('body-navy-hip-field');
        const hipInput = $('body-navy-hip');
        const isFemale = $('body-navy-sex').value === 'female';
        hipField.hidden = !isFemale;
        hipInput.required = isFemale;
        if (!isFemale) hipInput.value = '';
    }

    function setBodyNavyFeedback(id, message) {
        const feedback = $(id);
        feedback.textContent = message;
        feedback.hidden = !message;
    }

    function setBodyNavyError(message) {
        setBodyNavyFeedback('body-navy-error', message);
        qsa('#body-navy-sex, #body-navy-height, #body-navy-neck, #body-navy-waist, #body-navy-hip')
            .forEach((input) => input.setAttribute('aria-invalid', message ? 'true' : 'false'));
    }

    function setBodyNavyCalculationPending(pending) {
        bodyNavyCalculationPending = pending;
        $('btn-calc-navy').disabled = pending;
        syncBodyLogValidation();
    }

    function invalidateBodyNavyEstimate() {
        if (bodyNavyDerivedValue === null) return;
        const bodyFatInput = $('body-log-bf');
        if (bodyFatInput.value === bodyNavyDerivedValue) bodyFatInput.value = String();
        bodyNavyDerivedValue = null;
        setBodyNavyFeedback('body-navy-result', '');
        syncBodyLogValidation();
    }

    function detachManualBodyFatFromNavyEstimate() {
        bodyNavyInputVersion += 1;
        setBodyNavyCalculationPending(false);
        if (bodyNavyDerivedValue === null) return;
        bodyNavyDerivedValue = null;
        setBodyNavyFeedback('body-navy-result', '');
    }

    function noteBodyNavyInputChange() {
        bodyNavyInputVersion += 1;
        setBodyNavyCalculationPending(false);
        setBodyNavyError('');
        invalidateBodyNavyEstimate();
    }

    async function calculateNavyEstimate() {
        const bodyFatInput = $('body-log-bf');
        const requestVersion = bodyNavyInputVersion;
        const requestPayload = bodyNavyContextPayload();
        setBodyNavyFeedback('body-navy-result', '');
        setBodyNavyError('');
        setBodyNavyCalculationPending(true);
        try {
            const result = await api('/api/body/navy-calc', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(requestPayload),
            });
            if (requestVersion !== bodyNavyInputVersion) return;
            bodyFatInput.value = String(result.body_fat_pct);
            bodyNavyDerivedValue = bodyFatInput.value;
            setBodyNavyFeedback('body-navy-result', `Navy estimate ${result.body_fat_pct}% — review before saving.`);
            syncBodyLogValidation();
        } catch (e) {
            if (requestVersion !== bodyNavyInputVersion) return;
            console.error(e);
            setBodyNavyError(apiErrorMessage(e, 'Could not calculate Navy estimate'));
        } finally {
            if (requestVersion === bodyNavyInputVersion) setBodyNavyCalculationPending(false);
        }
    }

    async function logBody() {
        if (bodyNavyCalculationPending) return;
        const navyContext = bodyNavyContextPayload();
        const payload = {
            weight_lbs: Number($('body-log-weight').value) || null,
            body_fat_pct: Number($('body-log-bf').value) || null,
            ...navyContext,
        };
        if (!syncBodyLogValidation()) return;
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
        } catch (e) {
            console.error(e);
            const message = apiErrorMessage(e, 'Save failed');
            if (Object.keys(navyContext).length) setBodyNavyError(message);
            toast(message, 'err');
        }
    }

    function _ouraSyncErrorDetails(err) {
        if (err && (err.apiErrorCode || err.apiErrorMessage)) {
            return {
                code: String(err.apiErrorCode || 'oura_sync_failed'),
                message: String(err.apiErrorMessage || 'Oura sync failed'),
            };
        }
        const raw = String((err && err.message) || err || '');
        const jsonStart = raw.indexOf('{');
        if (jsonStart >= 0) {
            try {
                const parsed = JSON.parse(raw.slice(jsonStart));
                const serverError = parsed && parsed.error;
                if (serverError) {
                    return {
                        code: String(serverError.code || 'oura_sync_failed'),
                        message: String(serverError.message || 'Oura sync failed'),
                    };
                }
            } catch {}
        }
        return { code: 'oura_sync_failed', message: 'Oura sync failed' };
    }

    function renderOuraSyncResult(payload, err = null) {
        const row = $('oura-detail-sync-row');
        const value = $('oura-detail-sync-result');
        if (!row || !value) return;
        row.hidden = false;
        if (err) {
            const detail = _ouraSyncErrorDetails(err);
            value.textContent = `${detail.code} · ${detail.message}`;
            return;
        }
        const latestDays = Array.isArray(payload && payload.latest_days)
            ? payload.latest_days.filter(Boolean)
            : [];
        const rangeEnd = payload && payload.synced_through || 'unknown end';
        const count = Number(payload && payload.latest_records || 0);
        value.textContent = `Success · ${payload.synced_from || 'unknown start'} → ${rangeEnd} · ${count} latest saved record${count === 1 ? '' : 's'} · saved days ${latestDays.join(', ') || 'none'}`;
    }

    async function syncOura() {
        toast('Syncing Oura…');
        try {
            const result = await api('/api/oura/sync-sleep', { method: 'POST' });
            renderOuraSyncResult(result);
            invalidateCaches();
            toast('Oura synced');
            loadTab(state.currentTab);
        } catch (e) {
            console.error(e);
            renderOuraSyncResult(null, e);
            toast(apiErrorMessage(e, 'Oura sync failed'), 'err');
        }
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
    let _activeWorkoutDraftPersistenceFailed = false;

    // Signed-in scope from /api/auth/scope; tags the active-workout draft so a
    // different account on this device never restores it.
    let _authScope = '';
    let _authScopeRetryTimer = null;
    let _authScopeRetryDelayMs = 5_000;

    async function refreshAuthScope({ timeoutMs = 0 } = {}) {
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
            const res = await fetch('/api/auth/scope', fetchOpts);
            if (res.status === 401 || res.status === 403) return { ok: false, status: 'auth_required' };
            if (!res.ok) return { ok: false, status: 'pending' };
            const body = await res.json().catch(() => null);
            const scope = String(body && body.auth_scope || '').trim();
            if (!scope) return { ok: false, status: 'pending' };
            _authScope = scope;
            return { ok: true, scope };
        } catch (e) {
            return { ok: false, status: 'pending' };
        } finally {
            if (timer) clearTimeout(timer);
        }
    }

    function clearAuthScopeRetry() {
        if (_authScopeRetryTimer) clearTimeout(_authScopeRetryTimer);
        _authScopeRetryTimer = null;
        _authScopeRetryDelayMs = 5_000;
    }

    function scheduleAuthScopeRetry(status) {
        if (status !== 'pending' || _authScopeRetryTimer) return;
        const delayMs = _authScopeRetryDelayMs;
        _authScopeRetryDelayMs = Math.min(_authScopeRetryDelayMs * 2, 30_000);
        _authScopeRetryTimer = setTimeout(() => {
            _authScopeRetryTimer = null;
            refreshAuthScope()
                .then((scopeResult) => {
                    settleActiveWorkoutDraftAfterAuthScope(scopeResult);
                    scheduleAuthScopeRetry(scopeResult && scopeResult.status);
                })
                .catch((err) => {
                    console.warn('Auth scope refresh failed:', err);
                    scheduleAuthScopeRetry('pending');
                });
        }, delayMs);
    }

    function currentActiveWorkoutDraftScope() {
        try {
            return String(_authScope || '').trim();
        } catch (_) {
            return '';
        }
    }

    function activeWorkoutDraftScopeForWorkout(workout) {
        const workoutScope = String(workout && workout.auth_scope || '').trim();
        return workoutScope || currentActiveWorkoutDraftScope();
    }

    function setActiveWorkoutDraftPersistenceFailed(failed) {
        _activeWorkoutDraftPersistenceFailed = Boolean(failed);
        const warning = $('active-workout-persistence-warning');
        if (!warning) return;
        warning.hidden = !_activeWorkoutDraftPersistenceFailed;
        warning.textContent = _activeWorkoutDraftPersistenceFailed
            ? 'This workout still works, but it cannot be recovered after closing or reloading this page.'
            : '';
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
            const rpeInput = qs('input[data-field="rpe"]', row);
            const rpe = normalizeActiveSetRpe(
                rpeInput ? rpeInput.value : (set.rpe ?? prescribedRpeValue(ex)),
                Boolean(set.rpe_observed),
            );
            const next = {
                weight: qs('input[data-field="weight"]', row).value,
                reps: qs('input[data-field="reps"]', row).value,
                done: qs('input[data-field="done"]', row).checked,
                notes: qs('input[data-field="notes"]', row).value,
                rpe: rpe.rpe,
                rpe_observed: rpe.rpe_observed,
            };
            if (
                String(set.weight ?? '') !== String(next.weight)
                || String(set.reps ?? '') !== String(next.reps)
                || Boolean(set.done) !== Boolean(next.done)
                || String(set.notes ?? '') !== String(next.notes)
                || set.rpe !== next.rpe
                || Boolean(set.rpe_observed) !== next.rpe_observed
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
            setActiveWorkoutDraftPersistenceFailed(false);
        } catch (_) {
            // localStorage may be unavailable; keep the workout in memory for this page.
            setActiveWorkoutDraftPersistenceFailed(true);
        }
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
        setActiveWorkoutDraftPersistenceFailed(false);
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
        clearAuthScopeRetry();
        restoreActiveWorkoutDraft();
        flushPendingActiveWorkoutDraftSave();
    }


    function prescribedRpeValue(ex) {
        const candidate = Number(ex && (ex.rpe_target ?? ex.rpe ?? 7));
        return Number.isFinite(candidate) && candidate >= 1 && candidate <= 10 ? candidate : 7;
    }

    function normalizeActiveSetRpe(value, observed = false) {
        if (value === '' || value == null || typeof value === 'boolean') {
            return { rpe: null, rpe_observed: false };
        }
        const candidate = Number(value);
        const rpe = Number.isFinite(candidate) && candidate >= 1 && candidate <= 10
            ? candidate
            : null;
        return { rpe, rpe_observed: rpe !== null && observed === true };
    }

    function rpeInteractionObserves(event) {
        return Boolean(
            event
            && event.isTrusted === true
            && ['click', 'input', 'change'].includes(event.type)
        );
    }

    function buildLoggedSets(ex, previousSets) {
        const reps = recommendedRepsValue(ex);
        const weight = recommendedWeightValue(ex);
        const priorCount = Array.isArray(previousSets) ? previousSets.length : 0;
        const rowCount = Math.max(setCountForExercise(ex), priorCount);
        return Array.from({ length: rowCount }, (_, idx) => {
            const prev = previousSets && previousSets[idx];
            const previousRpe = normalizeActiveSetRpe(prev && prev.rpe, Boolean(prev && prev.rpe_observed));
            const previousRpeWasCleared = Boolean(
                prev
                && Object.prototype.hasOwnProperty.call(prev, 'rpe')
                && previousRpe.rpe === null
            );
            return {
                reps: prev && prev.reps !== '' && prev.reps != null ? prev.reps : reps,
                weight: prev && prev.weight !== '' && prev.weight != null ? prev.weight : weight,
                done: prev ? Boolean(prev.done) : false,
                notes: prev && prev.notes != null ? prev.notes : '',
                rpe: previousRpe.rpe_observed
                    ? previousRpe.rpe
                    : (previousRpeWasCleared ? null : prescribedRpeValue(ex)),
                rpe_observed: previousRpe.rpe_observed,
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
            if (s.done || s.rpe_observed || (s.notes != null && String(s.notes).trim())) return true;
            if (
                Object.prototype.hasOwnProperty.call(s, 'rpe')
                && normalizeActiveSetRpe(s.rpe, Boolean(s.rpe_observed)).rpe === null
            ) return true;
            if (previousReps && String(s.reps ?? '') !== previousReps) return true;
            if (previousWeight && String(s.weight ?? '') !== previousWeight) return true;
            return false;
        };
        const copySet = (s) => {
            const rpe = normalizeActiveSetRpe(s && s.rpe, Boolean(s && s.rpe_observed));
            const rpeWasCleared = Boolean(
                s
                && Object.prototype.hasOwnProperty.call(s, 'rpe')
                && rpe.rpe === null
            );
            return {
                reps: s && s.reps != null ? s.reps : '',
                weight: s && s.weight != null ? s.weight : '',
                done: Boolean(s && s.done),
                notes: s && s.notes != null ? s.notes : '',
                rpe: rpe.rpe !== null ? rpe.rpe : (rpeWasCleared ? null : prescribedRpeValue(newEx)),
                rpe_observed: rpe.rpe_observed,
            };
        };
        const freshSet = () => ({
            reps: targetReps,
            weight: targetWeight,
            done: false,
            notes: '',
            rpe: prescribedRpeValue(newEx),
            rpe_observed: false,
        });
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
        if (set.done || set.rpe_observed || (set.notes != null && String(set.notes).trim())) return true;

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

    function updateLoggedSetFromRow(row, { rpeObserved = null } = {}) {
        const exIdx = Number(row.dataset.ex);
        const setIdx = Number(row.dataset.set);
        const ex = state.activeWorkout && state.activeWorkout.exercises && state.activeWorkout.exercises[exIdx];
        if (!ex || !ex.logged_sets || !ex.logged_sets[setIdx]) return;
        const previousSet = ex.logged_sets[setIdx];
        const wasDone = !!previousSet.done;
        const rpe = normalizeActiveSetRpe(
            qs('input[data-field="rpe"]', row).value,
            rpeObserved === null ? Boolean(previousSet.rpe_observed) : rpeObserved,
        );
        ex.logged_sets[setIdx] = {
            weight: qs('input[data-field="weight"]', row).value,
            reps: qs('input[data-field="reps"]', row).value,
            done: qs('input[data-field="done"]', row).checked,
            notes: qs('input[data-field="notes"]', row).value,
            rpe: rpe.rpe,
            rpe_observed: rpe.rpe_observed,
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
            const target = exerciseTargetText(ex);
            let rowsHtml = '';
            ex.logged_sets.forEach((set, sidx) => {
                const storedRpe = normalizeActiveSetRpe(set.rpe, Boolean(set.rpe_observed));
                const rpeWasCleared = Object.prototype.hasOwnProperty.call(set, 'rpe') && storedRpe.rpe === null;
                const renderedRpe = storedRpe.rpe_observed
                    ? storedRpe
                    : {
                        rpe: rpeWasCleared ? null : prescribedRpeValue(ex),
                        rpe_observed: false,
                    };
                set.rpe = renderedRpe.rpe;
                set.rpe_observed = renderedRpe.rpe_observed;
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
                        <label class="set-rpe-cell${set.rpe_observed ? ' observed' : ''}">
                            <span>RPE</span>
                            <input type="number" data-field="rpe" inputmode="decimal" min="1" max="10" step="0.5" value="${escapeHtml(set.rpe)}" aria-label="RPE for set ${sidx + 1}; tap to confirm prescribed value">
                        </label>
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
                if (input.dataset.field === 'rpe') {
                    ['click', 'input', 'change'].forEach((eventName) => {
                        input.addEventListener(eventName, (event) => {
                            updateLoggedSetFromRow(row, {
                                rpeObserved: rpeInteractionObserves(event) ? true : null,
                            });
                            const set = state.activeWorkout.exercises[Number(row.dataset.ex)].logged_sets[Number(row.dataset.set)];
                            const cell = input.closest('.set-rpe-cell');
                            if (cell) cell.classList.toggle('observed', Boolean(set.rpe_observed));
                        });
                    });
                    return;
                }
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


    function renderSyncBanner() {
        const banner = $('sync-banner');
        const textEl = $('sync-banner-text');
        if (!banner || !textEl) return;
        const queue = loadSyncQueue();
        const pending = queue.filter((e) => (e.last_status || 'pending') === 'pending').length;
        const failedStatuses = new Set(['rejected', 'conflicted', 'eviction_failed', 'auth_required']);
        const failed = queue.filter((e) => failedStatuses.has(e.last_status)).length;
        const total = queue.length;
        if (!total) { banner.hidden = true; return; }
        const parts = [];
        if (pending) parts.push(`${pending} pending`);
        if (failed) parts.push(`${failed} failed`);
        textEl.textContent = parts.length ? parts.join(' · ') : `${total} queued`;
        banner.classList.toggle('has-failed', failed > 0);
        banner.hidden = false;
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
        host.innerHTML = '';
        if (!queue.length) {
            host.innerHTML = '<div class="empty">No queued workouts.</div>';
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
    }

    async function completeWorkout() {
        const aw = state.activeWorkout;
        if (!aw) return;
        setActiveWorkoutStatus('', '');
        const exercises = [];
        aw.exercises.forEach((ex, i) => {
            const rows = qsa(`.set-row[data-ex="${i}"]`, $('active-workout-body'));
            const sets = rows.map((r) => {
                const setIdx = Number(r.dataset.set);
                const set = ex.logged_sets[setIdx] || {};
                const rpe = normalizeActiveSetRpe(
                    qs('input[data-field="rpe"]', r).value,
                    Boolean(set.rpe_observed),
                );
                return {
                    reps: Number(qs('input[data-field="reps"]', r).value || 0),
                    weight_lbs: Number(qs('input[data-field="weight"]', r).value || 0),
                    rpe: rpe.rpe,
                    rpe_observed: rpe.rpe_observed,
                    done: qs('input[data-field="done"]', r).checked,
                    notes: (qs('input[data-field="notes"]', r).value || '').trim(),
                };
            });
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
        const recommendationId = source === 'active'
            ? (state.activeWorkout && state.activeWorkout.recommendation_id)
            : (state.nextWorkout && state.nextWorkout.id);
        state.swapContext = { exIdx, muscle, currentName, source, recommendationId };
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

    // FIT-397: a swap answered 409 plan_changed (the plan was regenerated or swapped
    // since this screen loaded) or workout_plan_conflict (it changed mid-request).
    // Adopt the server's current plan, keeping logged sets in an active workout, so
    // the next swap carries the current recommendation_id instead of dead-ending.
    function isSwapPlanChangedError(err) {
        return Boolean(err) && (err.apiErrorCode === 'plan_changed' || err.apiErrorCode === 'workout_plan_conflict');
    }

    async function adoptCurrentPlanAfterSwapConflict() {
        let nw = null;
        try { nw = await getNextWorkout(true); } catch (err) { console.warn('swap conflict refresh failed:', err); }
        if (nw) {
            if (state.dashboard) state.dashboard.next_workout = nw;
            if (state.activeWorkout) {
                applyAdjustedRecommendationToActiveWorkout(nw, state.activeWorkout.exercises || []);
                renderActiveWorkout();
            } else {
                renderNextWorkout();
            }
        }
        // Exercise slots may have shifted in the new plan, so make the user re-pick.
        closeModal($('modal-swap'));
        toast(nw
            ? 'Workout plan changed — your logged sets were kept. Pick the swap again.'
            : 'Workout plan changed — refresh the workout and try again.', 'err');
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
                body: JSON.stringify({ recommendation_id: state.swapContext && state.swapContext.recommendationId, exercise_index: exIdx, new_exercise_name: newName }),
            });
            _finalizeSwap(resp, oldName, newName);
        } catch (e) {
            console.error(e);
            if (isSwapPlanChangedError(e)) {
                await adoptCurrentPlanAfterSwapConflict();
            } else {
                host.innerHTML = `<div class="empty">Swap failed — ${escapeHtml(String(e.message || e))}</div>`;
            }
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
                body: JSON.stringify({ recommendation_id: ctx.recommendationId, exercise_index: ctx.exIdx, new_exercise_name: raw }),
            });
            _finalizeSwap(resp, ctx.currentName, raw);
            input.value = '';
        } catch (e) {
            console.error('applyCustomSwap', e);
            if (isSwapPlanChangedError(e)) {
                await adoptCurrentPlanAfterSwapConflict();
                return;
            }
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
        const headerStep = $('apple-sync-header-step');
        const headerEl = $('apple-sync-header');
        const detail = $('apple-sync-detail');
        if (detail) detail.textContent = 'Checking…';
        if (headerStep) headerStep.hidden = true;

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
            const syncToken = setup && setup.headers && setup.headers['X-Sync-Token'];
            if (syncToken && headerEl && headerStep) {
                headerEl.textContent = `X-Sync-Token: ${syncToken}`;
                headerStep.hidden = false;
            }
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
                skipped_notes: Array.isArray(payload && payload.skipped_notes) ? payload.skipped_notes : [],
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
        const skippedNotes = (Array.isArray(payload && payload.skipped_notes) ? payload.skipped_notes : [])
            .map((n) => String(n || '').trim()).filter(Boolean);
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
            // FIT-265: say why nothing changed (e.g. exercise not found), not just "clamped".
            notesEl.innerHTML = skippedNotes.length
                ? '<div class="dim">Nothing was changed:</div><ul>' + skippedNotes.map((n) => `<li>${escapeHtml(n)}</li>`).join('') + '</ul><div class="dim">The original plan stands.</div>'
                : '<div class="dim">No change was applied. The original plan stands.</div>';
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
        const bodyWeightInput = $('body-log-weight');
        const bodyFatInput = $('body-log-bf');
        const bodyNavySex = $('body-navy-sex');
        bodyWeightInput && bodyWeightInput.addEventListener('input', syncBodyLogValidation);
        bodyFatInput && bodyFatInput.addEventListener('input', syncBodyLogValidation);
        bodyFatInput && bodyFatInput.addEventListener('input', detachManualBodyFatFromNavyEstimate);
        bodyNavySex && bodyNavySex.addEventListener('change', () => {
            noteBodyNavyInputChange();
            syncBodyNavySex();
        });
        qsa('#body-navy-height, #body-navy-neck, #body-navy-waist, #body-navy-hip').forEach((input) => {
            input.addEventListener('input', noteBodyNavyInputChange);
        });
        $('btn-calc-navy') && $('btn-calc-navy').addEventListener('click', calculateNavyEstimate);
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
            await flushSyncQueue();
            await renderSyncQueueModal();
        });
        $('btn-sync-oura') && $('btn-sync-oura').addEventListener('click', syncOura);
        $('btn-open-ai-fact-query') && $('btn-open-ai-fact-query').addEventListener('click', openAiFactQueryModal);
        $('btn-open-wearables-setup') && $('btn-open-wearables-setup').addEventListener('click', openOpenWearablesSetupModal);
        $('btn-open-wearables-bootstrap') && $('btn-open-wearables-bootstrap').addEventListener('click', handleOpenWearablesPrimarySetupAction);
        $('btn-open-wearables-portal') && $('btn-open-wearables-portal').addEventListener('click', () => openOpenWearablesPortal());
        $('btn-open-wearables-portal-inline') && $('btn-open-wearables-portal-inline').addEventListener('click', () => openOpenWearablesPortal());
        $('btn-open-wearables-copy-link') && $('btn-open-wearables-copy-link').addEventListener('click', copyOpenWearablesPairingLink);
        $('btn-open-wearables-copy-link-inline') && $('btn-open-wearables-copy-link-inline').addEventListener('click', copyOpenWearablesPairingLink);
        $('btn-open-wearables-setup-save') && $('btn-open-wearables-setup-save').addEventListener('click', saveOpenWearablesSetup);
        $('btn-open-wearables-setup-check') && $('btn-open-wearables-setup-check').addEventListener('click', checkOpenWearables);
        $('btn-sync-open-wearables') && $('btn-sync-open-wearables').addEventListener('click', syncOpenWearables);
        $('btn-ai-fact-query') && $('btn-ai-fact-query').addEventListener('click', askAiFactQuestion);
        $('btn-ai-suggestion-approve') && $('btn-ai-suggestion-approve').addEventListener('click', () => resolveAiSuggestion('approve'));
        $('btn-ai-suggestion-reject') && $('btn-ai-suggestion-reject').addEventListener('click', () => resolveAiSuggestion('reject'));
        $('ai-fact-question') && $('ai-fact-question').addEventListener('keydown', (ev) => {
            if (ev.key === 'Enter') {
                ev.preventDefault();
                askAiFactQuestion();
            }
        });
        $('btn-connect-whoop') && $('btn-connect-whoop').addEventListener('click', connectWhoop);
        $('btn-whoop-connect-live') && $('btn-whoop-connect-live').addEventListener('click', startWhoopConnectFromModal);
        $('btn-whoop-import-submit') && $('btn-whoop-import-submit').addEventListener('click', importWhoopCsvFromModal);
        $('btn-whoop-import-clear') && $('btn-whoop-import-clear').addEventListener('click', clearWhoopImportInput);
        $('btn-whoop-import-file') && $('btn-whoop-import-file').addEventListener('click', () => $('whoop-import-file') && $('whoop-import-file').click());
        $('whoop-import-file') && $('whoop-import-file').addEventListener('change', (e) => {
            const file = e.target.files && e.target.files[0];
            const name = $('whoop-import-file-name');
            state.whoopUi.selectedImportFile = file || null;
            if (name) name.textContent = file ? file.name : 'No file selected';
            if (file) setWhoopIntakeStatus(`Selected ${file.name}.`, 'info');
        });
        $('btn-sync-whoop') && $('btn-sync-whoop').addEventListener('click', syncWhoop);
        $('btn-disconnect-whoop') && $('btn-disconnect-whoop').addEventListener('click', disconnectWhoop);
        $('btn-delete-whoop-data') && $('btn-delete-whoop-data').addEventListener('click', deleteWhoopData);
        $('btn-export') && $('btn-export').addEventListener('click', downloadExport);
        $('btn-import') && $('btn-import').addEventListener('click', () => $('import-file').click());
        $('import-file') && $('import-file').addEventListener('change', (e) => importBackupFile(e.target.files && e.target.files[0]));

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
        if (document.visibilityState === 'hidden') return;
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


    function boot() {
        renderGreeting();
        wireEvents();
        switchTabFromHash(true);
        window.addEventListener('hashchange', switchTabFromHash);
        refreshAiStatus();
        if (aiStatusTimer) clearInterval(aiStatusTimer);
        aiStatusTimer = setInterval(refreshAiStatus, 60_000);
        renderSyncBanner();
        registerServiceWorker();
        refreshAuthScope({ timeoutMs: 2500 })
            .then((scopeResult) => {
                settleActiveWorkoutDraftAfterAuthScope(scopeResult);
                scheduleAuthScopeRetry(scopeResult && scopeResult.status);
            })
            .catch((err) => {
                console.warn('Auth scope refresh failed:', err);
                scheduleAuthScopeRetry('pending');
            });
        window.addEventListener('pagehide', saveActiveWorkoutDraftBeforePageHidden);
        window.addEventListener('beforeunload', saveActiveWorkoutDraftBeforePageHidden);
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'hidden') saveActiveWorkoutDraftBeforePageHidden();
            else refreshAiStatus();
        });
        window.addEventListener('online', () => {
            refreshAuthScope()
                .then((scopeResult) => {
                    settleActiveWorkoutDraftAfterAuthScope(scopeResult);
                    scheduleAuthScopeRetry(scopeResult && scopeResult.status);
                })
                .catch((err) => {
                    console.warn('Auth scope refresh failed:', err);
                    scheduleAuthScopeRetry('pending');
                });
            flushSyncQueue();
        });
        if (navigator.onLine) {
            flushSyncQueue();
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

    // Expose for console debugging (read-only)
    window.__aicoach = { state, switchTab, loadTab, invalidateCaches };
})();
