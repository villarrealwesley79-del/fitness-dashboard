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
        exercises: null,
        ranges: { history: 30, stats: 30 },
        historyTypeFilter: 'all',
        activeWorkout: null,
        adjustedWorkout: null,
        swapContext: null,
    };

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

    async function api(path, opts = {}) {
        const res = await fetch(path, {
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json', ...(opts.headers || {}) },
            ...opts,
        });
        if (res.status === 401) {
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

    function toastUndo(msg, onUndo, durationMs = 10000) {
        const host = $('toast-host');
        if (!host) return null;
        const el = document.createElement('div');
        el.className = 'toast toast-undo';
        const text = document.createElement('span');
        text.className = 'toast-undo-text';
        text.textContent = msg;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'toast-undo-btn';
        btn.textContent = 'Undo';
        el.appendChild(text);
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
        const minPad = min - range * 0.12;
        const maxPad = max + range * 0.12;
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
        qsa('.tab-content').forEach((el) => el.classList.toggle('active', el.id === tabId));
        qsa('.tab-btn').forEach((b) => b.classList.toggle('active', b.getAttribute('data-tab') === tabId));
        loadTab(tabId);
        window.scrollTo({ top: 0, behavior: 'instant' });
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
        state.dashboard = await api('/api/dashboard');
        return state.dashboard;
    }
    async function getVitals(force = false) {
        if (!force && state.vitals) return state.vitals;
        state.vitals = await api('/api/vitals');
        return state.vitals;
    }
    async function getOuraStatus(force = false, refreshApi = false) {
        if (!force && !refreshApi && state.oura) return state.oura;
        try { state.oura = await api('/api/oura/status' + (refreshApi ? '?refresh=true' : '')); }
        catch { state.oura = null; }
        return state.oura;
    }
    async function getOuraSleep(force = false) {
        if (!force && state.ouraSleep) return state.ouraSleep;
        try { state.ouraSleep = await api('/api/oura/sleep-summary'); }
        catch { state.ouraSleep = null; }
        return state.ouraSleep;
    }
    async function getOuraTrends(force = false) {
        if (!force && state.ouraTrends) return state.ouraTrends;
        try { state.ouraTrends = await api('/api/oura/trends'); }
        catch { state.ouraTrends = null; }
        return state.ouraTrends;
    }
    async function getReco(force = false) {
        if (!force && state.reco) return state.reco;
        try { state.reco = await api('/api/recommendation/smart'); }
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
        state.body = state.settings = state.analytics = state.muscleFatigue = null;
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
            return { cls: 'unknown', label: 'Apple · —' };
        }
        if (apple.status === 'missing') {
            return { cls: 'stale', label: 'Apple · no data' };
        }
        // Render both signals when distinct: backend last_sync_attempt vs latest data point.
        const syncedAgo = ago(apple.last_sync_attempt);
        const dataAgo = ago(apple.last_data_point);
        let label;
        if (syncedAgo && dataAgo && syncedAgo !== dataAgo) {
            label = 'Apple · synced ' + syncedAgo + ' · data ' + dataAgo;
        } else if (dataAgo) {
            label = 'Apple · ' + dataAgo;
        } else {
            label = 'Apple · —';
        }
        if (apple.status === 'fresh')  return { cls: 'ok',    label };
        if (apple.status === 'aging')  return { cls: 'warn',  label };
        if (apple.status === 'stale')  return { cls: 'stale', label };
        return { cls: 'unknown', label };
    }

    function renderFreshnessChips(freshness) {
        const ago = (window.__dashHelpers && window.__dashHelpers.ago) || function (s) { return s || ''; };
        const slots = [
            { id: 'reco-fresh-oura',  key: 'oura',         render: formatOuraChip  },
            { id: 'reco-fresh-apple', key: 'apple_health', render: formatAppleChip },
            { id: 'reco-fresh-food',  key: 'food',         render: formatFoodChip  },
        ];
        slots.forEach(function (slot) {
            const el = $(slot.id);
            if (!el) return;
            const node = freshness ? freshness[slot.key] : null;
            const { cls, label } = slot.render(node, ago);
            el.classList.remove('ok', 'warn', 'stale', 'unknown');
            el.classList.add(cls);
            el.textContent = label;
        });
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
            renderFoodContext(coaching);
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
        renderFoodContext(coaching);
    }

    function renderFoodContext(coaching) {
        const chipsHost = $('food-context-chips');
        const nextDayHost = $('food-context-nextday');
        if (!chipsHost || !nextDayHost) return;
        if (!coaching || !Array.isArray(coaching.warnings)) {
            chipsHost.innerHTML = '';
            chipsHost.hidden = true;
            nextDayHost.hidden = true;
            nextDayHost.textContent = '';
            return;
        }
        const remaining = coaching.remaining || {};
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
    }

    // --- Dashboard render ----------------------------------------
    async function renderDashboard() {
        const [dash, oura, reco, sleep] = await Promise.all([
            getDashboard(), getOuraStatus(), getReco(), getOuraSleep(),
        ]);
        const readiness = (oura && oura.readiness) || (dash && dash.recomp_command && dash.recomp_command.readiness) || 0;

        gaugeChart($('readiness-gauge-svg'), readiness, { label: readiness >= 75 ? 'Very Good' : readiness >= 55 ? 'Good' : 'Low' });

        if ($('dash-hrv')) $('dash-hrv').textContent = oura && oura.hrv != null ? `${oura.hrv} ms` : '--';
        if ($('dash-rhr')) $('dash-rhr').textContent = oura && oura.resting_hr != null ? `${oura.resting_hr} bpm` : '--';
        if ($('dash-sleep')) $('dash-sleep').textContent = oura && oura.sleep_duration_min != null ? fmtDur(oura.sleep_duration_min) : '--';

        // Recommendation card — FIT-1 brief + FIT-2 honest freshness
        const nw = dash && dash.next_workout ? dash.next_workout : null;
        const freshness = (reco && reco.freshness) || (dash && dash.freshness) || null;
        const wearableStatuses = freshness ? [
            freshness.oura && freshness.oura.status,
            freshness.apple_health && freshness.apple_health.status,
        ] : [];
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
            recoTitle = (reco && reco.suggested_workout) || (nw && (nw.focus || nw.goal_name)) || 'Rest Day';
        }
        if ($('reco-title')) $('reco-title').textContent = recoTitle.replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });

        // Intensity / time / RPE chips
        const focusLabel = nw ? (nw.focus || nw.goal_name || '') : '';
        const intensityWord = reco && reco.recommendation
            ? (reco.recommendation === 'intensity' ? 'High'
                : reco.recommendation === 'moderate' ? 'Moderate'
                : reco.recommendation === 'recovery' ? 'Low'
                : reco.recommendation)
            : 'Moderate';
        if ($('reco-intensity')) {
            $('reco-intensity').textContent = [focusLabel.replace(/_/g, ' '), intensityWord].filter(Boolean).join(' · ') || 'Moderate';
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

        // Avoid list — surface existing avoid_muscles as chips (0-3 max).
        // Build via DOM + textContent (not innerHTML) so user-supplied soreness
        // muscle names cannot inject HTML/JS into the dashboard card.
        const avoidEl = $('reco-avoid');
        if (avoidEl) {
            const avoidRaw = (reco && reco.avoid_muscles) || [];
            const avoid = (Array.isArray(avoidRaw) ? avoidRaw : []).slice(0, 3);
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
                    chip.className = 'chip chip-avoid';
                    chip.textContent = humanizeMuscle(m);
                    avoidEl.appendChild(chip);
                });
                avoidEl.hidden = false;
            }
        }

        // Reason / "why" — wearable reasoning + explicit food guidance (FIT-1 AC)
        const whyEl = $('reco-why');
        if (whyEl) {
            let whyText;
            if (wearableAllMissing) {
                whyText = 'No recent wearable data — showing a conservative default. Sync Oura or Apple Health for a personalized recommendation.';
            } else if (wearableStale) {
                whyText = ((reco && reco.reasoning) ? reco.reasoning + '. ' : '') + 'Confidence is lowered because wearable data is stale.';
            } else {
                whyText = (reco && reco.reasoning) || 'Based on your readiness, sleep, and training load.';
            }
            // Append food guidance line so the brief always explains how today's
            // food changed (or could change) remaining-day guidance.
            const foodLine = buildFoodGuidanceLine(freshness && freshness.food);
            if (foodLine) whyText = whyText.replace(/\.\s*$/, '') + '. ' + foodLine;
            whyEl.textContent = whyText;
            whyEl.classList.toggle('lower-confidence', wearableDegraded);
        }

        // Confidence — server-driven bucket → label; legacy ladder as fallback
        const confLabel = (reco && reco.confidence_level && RECO_CONF_LABEL[reco.confidence_level])
            || (readiness >= 80 ? '92%' : readiness >= 65 ? '78%' : readiness >= 50 ? '62%' : '45%');
        if ($('reco-confidence-pct')) $('reco-confidence-pct').textContent = confLabel;

        // Freshness chips (always render — null freshness shows "unknown" state)
        renderFreshnessChips(freshness);

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

        // Insight card
        const recoFactors = reco && reco.readiness_factors;
        let insightTitle = 'Recovery is on track';
        let insightBody = reco && reco.reasoning ? reco.reasoning : 'Keep your sleep consistent and you\'ll stay ready.';
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

        // Sparkline: sleep scores from Oura trend
        const sleepSeries = (sleep && sleep.trend_data ? sleep.trend_data : []).map((d) => d.score);
        sparkline($('insight-sparkline'), sleepSeries, { color: '#22d3ee', height: 32 });

        // Readiness 7D line
        const trends = await getOuraTrends();
        const series = trends && trends.series ? trends.series : [];
        const readinessPts = series.map((s) => ({ value: s.readiness_score, label: fmtDate(s.day) })).filter(p => p.value != null);
        lineChart($('chart-readiness-7d'), readinessPts, { color: '#22c55e' });
        if ($('readiness-7d-avg')) {
            const vals = readinessPts.map(p => p.value);
            const avg = vals.length ? Math.round(vals.reduce((a, b) => a + b, 0) / vals.length) : null;
            $('readiness-7d-avg').textContent = avg != null ? `avg ${avg}` : '—';
        }

        // Volume 4W bar chart (bucket dashboard.next_workout + history by week)
        const hist = (state.history || (await getHistory().catch(() => null)))?.workouts || [];
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
        const zone = rhr ? (rhr < 58 ? 'Zone 2' : rhr < 68 ? 'Zone 2' : 'Zone 3') : '—';
        $('v-hr-zone').textContent = zone;
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
        const [dash, reco, st] = await Promise.all([getDashboard(), getReco(), getSettings()]);
        const nw = dash && dash.next_workout;
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
        const goalRpe = (st && st.goal_details && st.goal_details.rpe_target) || null;
        const exRpes = (nw.exercises || []).map((e) => Number(e.rpe_target)).filter((v) => Number.isFinite(v));
        const avgExRpe = exRpes.length ? Math.round((exRpes.reduce((a, b) => a + b, 0) / exRpes.length) * 10) / 10 : null;
        const rpeTarget = goalRpe || avgExRpe;
        $('nw-rpe').textContent = rpeTarget ? `RPE ${rpeTarget}` : 'RPE —';
        const why = reco && reco.reasoning ? reco.reasoning : 'Your readiness is high and your plan optimizes strength while managing fatigue.';
        $('nw-why').textContent = why;

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
        } else {
            card.hidden = true;
        }
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
        const [hist, aw] = await Promise.all([
            getHistory(),
            getAppleHealthWorkouts(Math.max(state.ranges.history, 30)),
        ]);
        const allLifts = (hist && hist.workouts) || [];
        const days = state.ranges.history;
        const cutoff = new Date();
        cutoff.setDate(cutoff.getDate() - days);

        const lifts = allLifts
            .map((w, i) => ({ ...w, source: 'lifted', _origIndex: i }))
            .filter((w) => w.date && new Date(w.date + 'T00:00:00') >= cutoff);
        const watch = (aw || [])
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
        const liftFreqDates = new Set(lifts.map((w) => w.date));
        const watchFreqDates = new Set(watchFiltered.map((w) => w.date));
        const sessionDates = new Set([...liftFreqDates, ...watchFreqDates]);

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
        barChart($('chart-history-freq'), barBuckets.map((b) => ({ value: b.count, label: b.label })), { color: '#60a5fa' });

        // volume over time (line)
        const volPts = barBuckets.map((b) => ({ value: b.volume, label: b.label }));
        workouts.forEach((w) => {
            if (!w.date) return;
            const dt = new Date(w.date + 'T00:00:00');
            const idx = barBuckets.findIndex((b) => dt >= b.start && dt <= b.end);
            if (idx >= 0) barBuckets[idx].volume = (barBuckets[idx].volume || 0) + Number(w.total_volume || 0);
        });
        lineChart($('chart-history-volume'), barBuckets.map((b) => ({ value: b.volume || 0, label: b.label })), { color: '#a78bfa' });

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
        if (!merged.length) { listHost.innerHTML = '<div class="empty">No workouts in this range.</div>'; return; }

        renderHistoryTypeFilter(merged, filterHost);
        const visible = merged.filter((w) => historyFilterKey(w) === state.historyTypeFilter || state.historyTypeFilter === 'all');
        if (!visible.length) {
            const label = state.historyTypeFilter === 'lifted' ? 'Lifted' : state.historyTypeFilter;
            listHost.innerHTML = `<div class="empty">No ${escapeHtml(label)} workouts in this range.</div>`;
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
            return `
                <div class="workout-detail-ex">
                    <div class="workout-detail-ex-head">
                        <h4>${escapeHtml(workoutExerciseName(ex))}</h4>
                        <span>${sets.length} sets · ${fmtKilo(exVolume)} lbs</span>
                    </div>
                    ${rows || '<div class="empty">No sets logged.</div>'}
                </div>
            `;
        }).join('');

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

        body.innerHTML = `
            <div class="workout-detail-kpis">
                <div><span>${fmtInt(totalSets)}</span><label>sets</label></div>
                <div><span>${fmtKilo(totalVolume)}</span><label>lbs</label></div>
                <div><span>${fmtInt(item.duration_minutes || 0)}</span><label>minutes</label></div>
            </div>
            ${item.notes ? `<div class="workout-detail-section"><div class="analyze-label">WORKOUT NOTES</div><div class="workout-note">${escapeHtml(item.notes)}</div></div>` : ''}
            <div class="workout-detail-section">
                <div class="analyze-label">EXERCISES</div>
                ${exerciseHtml || '<div class="empty">No exercises logged.</div>'}
            </div>
            ${cardioHtml}
        `;
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
        const items = (insights && insights.insights) || [];
        if (!items.length) {
            list.innerHTML = '<div class="empty">No insights yet — log more workouts to unlock.</div>';
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
    async function renderSettings() {
        const [st, oura] = await Promise.all([getSettings(), getOuraStatus(true, true)]);
        const host = $('settings-goals');
        host.innerHTML = '';
        (st.available_goals || []).forEach((g) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'goal-opt' + (st.training_goal === g.value || st.goal === g.value || (st.goal_details && st.goal_details.name === g.name) ? ' active' : '');
            btn.dataset.goal = g.value;
            btn.innerHTML = `
                <div>
                    <div class="goal-title">${escapeHtml(g.name)}</div>
                    <span class="goal-sub">${escapeHtml(g.description)}</span>
                </div>
                <span class="goal-check">✓</span>
            `;
            btn.addEventListener('click', () => updateSetting({ training_goal: g.value }));
            host.appendChild(btn);
        });

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

        // integration state
        const ouraState = $('oura-connect-state');
        if (oura && oura.source) {
            ouraState.textContent = oura.source === 'api' ? 'Connected' : 'Cached';
            ouraState.className = 'state-chip ' + (oura.source === 'api' ? 'ok' : 'warn');
        } else { ouraState.textContent = 'Not connected'; ouraState.className = 'state-chip'; }

        // Apple Health — prefer the real sync-status endpoint over
        // the file-existence probe, and only claim "connected" when a
        // sync actually landed recently.
        try {
            const ah = await api('/api/apple-health/sync/status');
            const lastExportRaw = ah && (ah.last_attempt || ah.last_sync);
            const last = parseServerDateTime(lastExportRaw);
            const ageDays = last ? Math.floor((Date.now() - last.getTime()) / 86400000) : Infinity;
            const connected = last && ageDays <= 3;
            const setupConfigured = Boolean(ah && ah.setup_configured);
            const chip = $('apple-connect-state');
            const detail = $('apple-last-export');
            if (connected) {
                chip.textContent = `Synced ${ageDays === 0 ? 'today' : ageDays + 'd ago'}`;
                chip.className = 'state-chip ok';
                $('apple-int-dot').className = 'int-dot int-dot-on';
            } else if (setupConfigured) {
                chip.textContent = last ? `Setup · last sync ${ageDays}d ago` : 'Setup · waiting for export';
                chip.className = 'state-chip warn';
                $('apple-int-dot').className = 'int-dot int-dot-on';
            } else {
                chip.textContent = last ? `Last sync ${ageDays}d ago` : 'Not connected';
                chip.className = 'state-chip';
                $('apple-int-dot').className = 'int-dot';
            }
            if (detail) {
                detail.textContent = last
                    ? `Last export ${fmtDateTime(lastExportRaw)} · ${ah.total_records || 0} records`
                    : 'No accepted export yet';
            }
        } catch {
            $('apple-connect-state').textContent = 'Not connected';
            $('apple-int-dot').className = 'int-dot';
            const detail = $('apple-last-export');
            if (detail) detail.textContent = 'Export status unavailable';
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

        const pLabel = _aiCheckLabel(primary);
        const fLabel = fallback ? _aiCheckLabel(fallback) : { text: 'Same as primary', cls: 'state-chip unknown' };

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
                : 'No distinct fallback endpoint';
        }
        if (primaryDot) primaryDot.className = 'int-dot' + (primary && primary.reachable && primary.model_loaded ? ' int-dot-on' : '');
        if (fallbackDot) fallbackDot.className = 'int-dot' + (fallback && fallback.reachable && fallback.model_loaded ? ' int-dot-on' : '');
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
    }

    async function updateSetting(patch) {
        try {
            await api('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(patch),
            });
            toast('Setting saved');
            state.settings = null; state.dashboard = null;
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
            state.reco = null;
            state.activeWorkout = null;
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
            exercises: (nw.exercises || []).map((ex, i) => buildActiveExercise(ex, previousExercises[i])),
            cardio: buildActiveCardio(nw.cardio, existing && existing.cardio),
            saveState: existing && existing.saveState ? existing.saveState : null,
        };
    }

    function hasRecommendedCardio(cardio) {
        return Boolean(cardio && cardio.include_cardio !== false && (cardio.type || cardio.machine || Number(cardio.duration_minutes || 0) > 0));
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
        ex.logged_sets[setIdx] = {
            weight: qs('input[data-field="weight"]', row).value,
            reps: qs('input[data-field="reps"]', row).value,
            done: qs('input[data-field="done"]', row).checked,
            notes: qs('input[data-field="notes"]', row).value,
        };
    }

    function updateActiveCardio() {
        const cardio = state.activeWorkout && state.activeWorkout.cardio;
        const card = qs('.active-cardio', $('active-workout-body'));
        if (!cardio || !card) return;
        cardio.completed = qs('input[data-cardio-field="completed"]', card).checked;
        cardio.activity_type = qs('input[data-cardio-field="activity_type"]', card).value;
        cardio.duration_minutes = qs('input[data-cardio-field="duration_minutes"]', card).value;
        cardio.notes = qs('textarea[data-cardio-field="notes"]', card).value;
    }

    function renderActiveWorkout() {
        if (!state.activeWorkout) return;
        $('active-workout-title').textContent = (state.activeWorkout.focus + ' Workout').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
        const body = $('active-workout-body');
        body.innerHTML = '';
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
                rowsHtml += `
                    <div class="set-row" data-ex="${i}" data-set="${sidx}">
                        <label>${sidx + 1}</label>
                        <input type="number" placeholder="Weight" data-field="weight" inputmode="decimal" value="${escapeHtml(set.weight)}">
                        <input type="number" placeholder="Reps" data-field="reps" inputmode="numeric" value="${escapeHtml(set.reps)}">
                        <input type="checkbox" data-field="done" aria-label="done"${set.done ? ' checked' : ''}>
                        <input class="set-notes" type="text" placeholder="Set notes" data-field="notes" value="${escapeHtml(set.notes)}">
                    </div>
                `;
            });
            const name = exerciseName(ex);
            const muscle = exerciseMuscle(ex);
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
        wireActiveWorkoutCancel(modal);
        modal.hidden = false;
    }

    function cancelActiveWorkout() {
        const modal = $('modal-active');
        if (!modal) return;
        state.activeWorkout = null;
        clearAdjustIntent();
        modal.hidden = true;
    }

    function wireActiveWorkoutCancel(modal) {
        if (!modal) return;
        const closeBtn = modal.querySelector('.modal-close');
        if (closeBtn) {
            const fresh = closeBtn.cloneNode(true);
            fresh.removeAttribute('data-close-modal');
            closeBtn.parentNode.replaceChild(fresh, closeBtn);
            fresh.addEventListener('click', cancelActiveWorkout);
        }
        if (modal.__fit24BackdropHandler) {
            modal.removeEventListener('click', modal.__fit24BackdropHandler, true);
        }
        const handler = (e) => {
            if (e.target === modal) {
                e.stopImmediatePropagation();
                cancelActiveWorkout();
            }
        };
        modal.__fit24BackdropHandler = handler;
        modal.addEventListener('click', handler, true);
    }

    function removeActiveExercise(exIdx, name) {
        const aw = state.activeWorkout;
        if (!aw || !Array.isArray(aw.exercises) || exIdx < 0 || exIdx >= aw.exercises.length) return;
        aw.exercises.splice(exIdx, 1);
        if (!aw.exercises.length) aw.saveState = null;
        renderActiveWorkout();
        toast(`Removed ${name}`, 'ok');
    }

    async function startWorkout() {
        const dash = await getDashboard();
        const nw = dash && dash.next_workout;
        if (!nw) { toast('No workout planned', 'err'); return; }
        setActiveWorkoutFromRecommendation(nw);
        renderActiveWorkout();
    }

    async function viewAdjustedPlan() {
        if ($('modal-adjust')) $('modal-adjust').hidden = true;
        await switchTab('tab-workout');
    }

    function startAdjustedWorkout() {
        const nw = state.adjustedWorkout || (state.dashboard && state.dashboard.next_workout);
        if (!nw) { toast('No adjusted workout available', 'err'); return; }
        if ($('modal-adjust')) $('modal-adjust').hidden = true;
        setActiveWorkoutFromRecommendation(nw);
        renderActiveWorkout();
    }

    const SYNC_QUEUE_KEY = 'fit51:sync-queue:v1';
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
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify(payload),
        });
        const body = await res.json().catch(() => null);
        if (res.ok) {
            return { ok: true, status: res.status, body, syncStatus: (body && body.sync_status) || 'inserted' };
        }
        const err = body && body.error;
        const syncStatus = (err && err.details && err.details.sync_status) || (res.status === 409 ? 'conflicted' : 'rejected');
        const reason = err && err.message;
        return { ok: false, status: res.status, body, syncStatus, reason };
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
                reject_reason: result.reason || null,
            });
            return { ok: false, status: result.syncStatus };
        } catch (e) {
            updateQueueEntry(clientId, {
                last_status: 'pending',
                last_attempt_at: new Date().toISOString(),
                attempts: (entry.attempts || 0) + 1,
            });
            return { ok: false, status: 'pending', error: e && e.message };
        }
    }

    async function flushSyncQueue() {
        if (!navigator.onLine || _syncFlushInFlight) return;
        _syncFlushInFlight = true;
        try {
            const ids = loadSyncQueue()
                .filter((e) => e.last_status === 'pending')
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
        if (!queue.length) { banner.hidden = true; return; }
        const pending = queue.filter((e) => e.last_status === 'pending').length;
        const failed = queue.filter((e) => e.last_status === 'rejected' || e.last_status === 'conflicted').length;
        const parts = [];
        if (pending) parts.push(`${pending} pending`);
        if (failed) parts.push(`${failed} failed`);
        textEl.textContent = parts.length ? parts.join(' · ') : `${queue.length} queued`;
        banner.classList.toggle('has-failed', failed > 0);
        banner.hidden = false;
    }

    function openSyncQueueModal() {
        renderSyncQueueModal();
        const modal = $('modal-sync-queue');
        if (modal) modal.hidden = false;
    }

    function renderSyncQueueModal() {
        const host = $('sync-queue-list');
        if (!host) return;
        const queue = loadSyncQueue();
        host.innerHTML = '';
        if (!queue.length) {
            host.innerHTML = '<div class="empty">No queued workouts.</div>';
            return;
        }
        const statusLabels = { pending: 'Pending', conflicted: 'Conflict', rejected: 'Rejected', inserted: 'Synced', already_synced: 'Synced' };
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
                renderSyncQueueModal();
                if (res && res.ok) toast('Workout synced');
                else if (res) toast(`Sync ${res.status || 'failed'}`, 'err');
            });
        });
        host.querySelectorAll('[data-sync-discard]').forEach((btn) => {
            btn.addEventListener('click', () => {
                const clientId = btn.dataset.syncDiscard;
                const entry = loadSyncQueue().find((e) => e.client_workout_id === clientId);
                const needsConfirm = entry && (entry.last_status === 'rejected' || entry.last_status === 'conflicted');
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
            if (completedSets.length) exercises.push({ machine: exerciseName(ex), muscle_group: ex.muscle_group || ex.muscle, sets: completedSets });
        });
        if (!exercises.length) {
            const message = 'Validation failed: log at least one set before completing this workout.';
            aw.saveState = { message, variant: 'err' };
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
                clearAdjustIntent();
                invalidateCaches();
                loadTab(state.currentTab);
                openWorkoutSavedConfirm(summary);
            } else {
                // Backend reported conflicted or rejected — enqueue so user can retry/discard.
                enqueueOfflineWorkout(completePayload, result.syncStatus || 'rejected');
                updateQueueEntry(clientWorkoutId, {
                    last_status: result.syncStatus || 'rejected',
                    last_attempt_at: new Date().toISOString(),
                    attempts: 1,
                    server_response: result.body || null,
                    reject_reason: result.reason || null,
                });
                const msg = result.syncStatus === 'conflicted'
                    ? 'Server reported a conflict — see the sync queue.'
                    : `Save rejected — ${result.reason || 'see the sync queue'}.`;
                aw.saveState = { message: msg, variant: 'err' };
                setActiveWorkoutStatus(msg, 'err');
                toast(result.syncStatus === 'conflicted' ? 'Sync conflict' : 'Save rejected', 'err');
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
                dismissToHistory();
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
        const freshAnalyze = analyzeBtn.cloneNode(true);
        analyzeBtn.parentNode.replaceChild(freshAnalyze, analyzeBtn);
        freshAnalyze.addEventListener('click', () => {
            detachBackdrop();
            modal.hidden = true;
            openAnalyzeModal({ latest: true }, `Analysis · ${dateLabel}`);
        });
        const freshDismiss = dismissBtn.cloneNode(true);
        dismissBtn.parentNode.replaceChild(freshDismiss, dismissBtn);
        freshDismiss.addEventListener('click', dismissToHistory);
        if (closeBtn) {
            const freshClose = closeBtn.cloneNode(true);
            freshClose.removeAttribute('data-close-modal');
            closeBtn.parentNode.replaceChild(freshClose, closeBtn);
            freshClose.addEventListener('click', dismissToHistory);
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
            btn.innerHTML = `
                <span>${escapeHtml(alt.name)}${alt.compound ? ' <span class="swap-current-tag">COMPOUND</span>' : ''}</span>
                ${isCurrent ? '<span class="swap-current-tag">CURRENT</span>' : `<span class="swap-row-equip ${equipClass}">${escapeHtml(alt.equipment || '—')}</span>`}
            `;
            if (!isCurrent) {
                btn.addEventListener('click', () => applySwap(exIdx, alt.name, currentName));
            }
            host.appendChild(btn);
        });
    }

    async function applySwap(exIdx, newName, oldName) {
        const host = $('swap-alternatives');
        host.innerHTML = '<div class="skeleton">Swapping…</div>';
        try {
            const resp = await api('/api/workout/swap', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ workout_index: 0, exercise_index: exIdx, new_exercise_name: newName }),
            });
            if (resp && resp.recommendation) {
                if (!state.dashboard) state.dashboard = {};
                state.dashboard.next_workout = resp.recommendation;
            }
            $('modal-swap').hidden = true;
            toast(`Swapped ${oldName} → ${newName}`, 'ok');
            if (state.swapContext && state.swapContext.source === 'active' && resp && resp.recommendation) {
                const previous = (state.activeWorkout && state.activeWorkout.exercises) || [];
                setActiveWorkoutFromRecommendation(resp.recommendation, previous);
                renderActiveWorkout();
            } else {
                renderNextWorkout();
            }
        } catch (e) {
            console.error(e);
            host.innerHTML = `<div class="empty">Swap failed — ${escapeHtml(String(e.message || e))}</div>`;
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
            state.adjustedWorkout = payload.recommendation;
            renderAdjustedPlanPreview(payload.recommendation);
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
            renderSyncQueueModal();
        });
        $('btn-sync-oura') && $('btn-sync-oura').addEventListener('click', syncOura);
        $('btn-export') && $('btn-export').addEventListener('click', downloadExport);
        $('btn-import') && $('btn-import').addEventListener('click', () => $('import-file').click());
        $('import-file') && $('import-file').addEventListener('change', (e) => importBackupFile(e.target.files && e.target.files[0]));

        // Close modals
        qsa('[data-close-modal]').forEach((b) => b.addEventListener('click', () => {
            const modal = b.closest('.modal');
            if (modal) modal.hidden = true;
        }));
        qsa('.modal').forEach((m) => {
            m.addEventListener('click', (e) => { if (e.target === m) m.hidden = true; });
        });

        // AI status button (top right)
        $('btn-ai-status') && $('btn-ai-status').addEventListener('click', toggleAiPopover);
        document.addEventListener('click', closeAiPopoverOnOutsideClick, true);
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

    function boot() {
        renderGreeting();
        wireEvents();
        switchTab('tab-dashboard');
        refreshAiStatus();
        if (aiStatusTimer) clearInterval(aiStatusTimer);
        aiStatusTimer = setInterval(refreshAiStatus, 60_000);
        renderSyncBanner();
        window.addEventListener('online', () => { flushSyncQueue(); });
        if (navigator.onLine) flushSyncQueue();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }

    // Expose for console debugging (read-only) + macro card refresh hook (FIT-23)
    window.__aicoach = { state, switchTab, loadTab, invalidateCaches, refreshMacroCard };
})();
