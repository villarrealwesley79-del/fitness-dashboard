// Fitness Dashboard - Mobile App JavaScript

let currentRecommendation = null;
let currentSettings = null;
let charts = {};

function escapeHtml(value) {
    if (value == null) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// Global function to refresh Oura data
async function refreshOura() {
    console.log('refreshOura called');
    const refreshBtn = document.getElementById('oura-refresh');
    const readinessEl = document.getElementById('oura-readiness');
    const hrvEl = document.getElementById('oura-hrv');
    
    if (refreshBtn) {
        refreshBtn.textContent = '⏳';
    }
    
    try {
        // Fetch fresh data from Oura API
        const statusResp = await fetch('/api/oura/status?refresh=true');
        const status = await statusResp.json();
        console.log('Oura status:', status);
        
        // Update readiness
        if (readinessEl) {
            readinessEl.textContent = (status && status.readiness != null) ? status.readiness : '--';
        }
        
        // Fetch trends
        const trendResp = await fetch('/api/oura/trends');
        const trend = await trendResp.json();
        console.log('Oura trend:', trend);
        
        // Update HRV trend
        if (hrvEl) {
            const hrvText = (trend && trend.hrv_trend && trend.hrv_trend !== 'unknown') ? trend.hrv_trend : '--';
            hrvEl.textContent = hrvText;
        }
        
        // Refresh smart recommendation
        await loadSmartRecommendation();
        
        if (refreshBtn) {
            refreshBtn.textContent = '✅';
            setTimeout(() => { refreshBtn.textContent = '🔄'; }, 2000);
        }
    } catch (e) {
        console.error('refreshOura error:', e);
        if (refreshBtn) {
            refreshBtn.textContent = '❌';
            setTimeout(() => { refreshBtn.textContent = '🔄'; }, 2000);
        }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    initForms();
    initSettings();
    initWorkoutButtons();
    initHistoryFilters();
    initBaselineConfig();
    initBackupButtons();
    initOfflineBanner();
    initAccordions();
    loadDashboard();
    loadSettings();
    loadHistory();
    loadBodyRecomp();
    loadSleepAnalytics();
    checkInstallBanner();
    registerServiceWorker();
});

// Tab Navigation
function initTabs() {
    const tabBtns = document.querySelectorAll('.tab-btn');
    const tabContents = document.querySelectorAll('.tab-content');

    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const tabId = btn.dataset.tab;

            tabBtns.forEach(b => b.classList.remove('active'));
            tabContents.forEach(c => c.classList.remove('active'));

            btn.classList.add('active');
            document.getElementById(tabId).classList.add('active');

            // Load tab-specific data
            if (tabId === 'analytics') {
                loadInsights();
                loadWeightChart();
                loadAdvancedAnalytics();
                loadRecompTrendChart();
            } else if (tabId === 'vitals') {
                loadVitals();
            } else if (tabId === 'body') {
                loadBodyRecomp();
                loadSleepAnalytics();
            } else if (tabId === 'history') {
                loadHistory();
            } else if (tabId === 'settings') {
                loadSettings();
            }

            // Haptic feedback on iOS
            if (navigator.vibrate) {
                navigator.vibrate(10);
            }
        });
    });
}

function initAccordions() {
    const headers = document.querySelectorAll('.accordion-header');
    headers.forEach(header => {
        header.addEventListener('click', () => {
            const accordion = header.closest('.accordion');
            if (!accordion) return;
            const isOpen = accordion.classList.toggle('open');
            header.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
        });
    });
}

// Navigate to a specific tab
function navigateToTab(tabId) {
    const tabBtn = document.querySelector(`[data-tab="${tabId}"]`);
    if (tabBtn) {
        tabBtn.click();
    }
}

// Load Dashboard Data
async function loadDashboard() {
    try {
        const response = await fetch('/api/dashboard');
        const data = await response.json();

        updateHeadlineKPIs(data.headline, data.body_stats);
        updateGoalBanner(data.body_stats);
        updateAlerts(data.alerts);
        updateMuscleGroups(data.muscles);
        updateExercises(data.exercises);
        updateNextWorkout(data.next_workout);
        updateRecompCommandCenter(data.recomp_command, data.nutrition_today);
        updateReadinessGauge(data.recomp_command);
        updateDashboardRecommendation(data.next_workout);
        loadVitals();

        // Oura recovery widget + smart recommendation + sleep insights (best-effort)
        loadOuraWidget();
        loadSmartRecommendation();
        loadSleepInsights();

        // Store current recommendation
        currentRecommendation = data.next_workout;

        // Advanced KPIs
        if (data.advanced_kpis) {
            updateAdvancedKPIs(data.advanced_kpis);
        }

    } catch (error) {
        console.error('Failed to load dashboard:', error);
    }
}

function updateReadinessGauge(recomp) {
    const gauge = document.getElementById('readiness-gauge');
    const scoreEl = document.getElementById('readiness-gauge-score');
    const statusEl = document.getElementById('readiness-gauge-status');
    const noteEl = document.getElementById('readiness-gauge-note');
    if (!gauge || !scoreEl || !statusEl || !noteEl) return;

    const readiness = recomp?.readiness ?? 0;
    scoreEl.textContent = readiness || '--';

    let color = 'var(--success)';
    let status = 'Green Light';
    if (readiness < 60) {
        color = 'var(--danger)';
        status = 'Recovery Priority';
    } else if (readiness < 75) {
        color = 'var(--warning)';
        status = 'Caution / Technique Focus';
    }

    gauge.style.background = `conic-gradient(${color} ${Math.min(readiness, 100) * 3.6}deg, rgba(255,255,255,0.08) 0deg)`;
    statusEl.textContent = status;
    noteEl.textContent = recomp?.reason || 'Oura readiness signal';
}

function updateDashboardRecommendation(workout) {
    const titleEl = document.getElementById('today-workout-title');
    const focusEl = document.getElementById('today-workout-focus');
    const durationEl = document.getElementById('today-workout-duration');
    const rpeEl = document.getElementById('today-workout-rpe');
    const notesEl = document.getElementById('today-workout-notes');
    if (!titleEl || !focusEl || !durationEl || !rpeEl || !notesEl) return;

    titleEl.textContent = workout?.focus ? `${workout.focus} Session` : '--';
    focusEl.textContent = workout?.goal_name ? `Goal: ${workout.goal_name}` : '--';
    durationEl.textContent = workout?.estimated_duration ? `${workout.estimated_duration}` : '--';
    const firstRpe = workout?.exercises?.[0]?.rpe_target;
    rpeEl.textContent = firstRpe ? `Target RPE ${firstRpe}` : 'RPE auto-regulated';
    if (workout?.mesocycle) {
        notesEl.textContent = `Week ${workout.mesocycle.week} (${workout.mesocycle.phase}) · Volume x${workout.mesocycle.volume_multiplier}`;
    } else {
        notesEl.textContent = 'Auto-adjusted by readiness and soreness.';
    }
}

// Update Headline KPIs
function updateHeadlineKPIs(headline, bodyStats) {
    document.getElementById('total-sets').textContent = headline.total_sets;
    document.getElementById('progression').textContent = `${headline.improving}/${headline.total_exercises}`;
    document.getElementById('readiness').textContent = `${headline.avg_readiness}/10`;
    document.getElementById('sessions').textContent = headline.sessions;
    
    // Update body weight
    const bodyWeightEl = document.getElementById('body-weight');
    if (bodyWeightEl && bodyStats && bodyStats.latest_weight) {
        bodyWeightEl.textContent = Math.round(bodyStats.latest_weight * 10) / 10;
    } else if (bodyWeightEl) {
        bodyWeightEl.textContent = '--';
    }
}

function updateGoalBanner(bodyStats) {
    const weightCurrent = document.getElementById('goal-weight-current');
    const weightRemaining = document.getElementById('goal-weight-remaining');
    const bfCurrent = document.getElementById('goal-bf-current');
    const bfRemaining = document.getElementById('goal-bf-remaining');
    const bfRemainingLabel = document.getElementById('goal-bf-remaining-label');
    const bfRemainingWrap = document.getElementById('goal-bf-remaining-wrap');
    const targetWeight = 175;
    const targetBf = 18;

    if (!weightCurrent || !weightRemaining || !bfCurrent || !bfRemaining) return;

    const currentWeight = bodyStats?.latest_weight;
    const currentBf = bodyStats?.latest_body_fat;

    if (currentWeight != null && !Number.isNaN(currentWeight)) {
        const remaining = Math.max(0, currentWeight - targetWeight);
        weightCurrent.textContent = `${(Math.round(currentWeight * 10) / 10).toFixed(1)} lbs`;
        weightRemaining.textContent = `${remaining.toFixed(1)} lbs`;
    } else {
        weightCurrent.textContent = '--';
        weightRemaining.textContent = '--';
    }

    if (currentBf != null && !Number.isNaN(currentBf)) {
        const remaining = Math.max(0, currentBf - targetBf);
        bfCurrent.textContent = `${(Math.round(currentBf * 10) / 10).toFixed(1)}% BF`;
        bfRemaining.textContent = `${remaining.toFixed(1)}%`;
        if (bfRemainingWrap) bfRemainingWrap.style.display = 'inline';
        if (bfRemainingLabel) bfRemainingLabel.style.display = 'inline';
    } else {
        bfCurrent.innerHTML = '<button type="button" class="goal-inline-btn" onclick="navigateToTab(\'body\')">Log BF%</button>';
        bfRemaining.textContent = '';
        if (bfRemainingWrap) bfRemainingWrap.style.display = 'none';
        if (bfRemainingLabel) bfRemainingLabel.style.display = 'none';
    }
}

async function loadVitals() {
    try {
        const response = await fetch('/api/vitals');
        const data = await response.json();
        updateVitalsMini(data);
        updateVitalsTab(data);
    } catch (error) {
        console.error('Failed to load vitals:', error);
        updateVitalsMini(null);
        updateVitalsTab(null);
    }
}

function updateVitalsMini(data) {
    const weightEl = document.getElementById('vitals-mini-weight');
    const rhrEl = document.getElementById('vitals-mini-rhr');
    const sleepEl = document.getElementById('vitals-mini-sleep');
    const stepsEl = document.getElementById('vitals-mini-steps');

    if (!weightEl || !rhrEl || !sleepEl || !stepsEl) return;

    const weight = data?.weight?.current_lbs;
    const rhr = data?.heart_rate?.resting_bpm;
    const sleepHours = data?.sleep?.last_night?.duration_hours;
    const steps = data?.activity?.steps_today;

    weightEl.textContent = (weight != null) ? `${weight}` : '--';
    rhrEl.textContent = (rhr != null) ? `${rhr}` : '--';
    sleepEl.textContent = (sleepHours != null) ? `${sleepHours}h` : '--';
    stepsEl.textContent = (steps != null) ? `${steps}` : '--';
}

function updateVitalsTab(data) {
    const weightCurrentEl = document.getElementById('vitals-weight-current');
    const weightChangeEl = document.getElementById('vitals-weight-change');
    const weightBfEl = document.getElementById('vitals-weight-bodyfat');
    const hrRestingEl = document.getElementById('vitals-hr-resting');
    const hrAverageEl = document.getElementById('vitals-hr-average');
    const sleepDurationEl = document.getElementById('vitals-sleep-duration');
    const sleepAvgEl = document.getElementById('vitals-sleep-avg');
    const sleepBreakdownEl = document.getElementById('vitals-sleep-breakdown');
    const stepsRingEl = document.getElementById('vitals-steps-ring');
    const stepsTodayEl = document.getElementById('vitals-steps-today');
    const stepsAvgEl = document.getElementById('vitals-steps-avg');
    const activeCaloriesEl = document.getElementById('vitals-active-calories');
    const activeMinutesEl = document.getElementById('vitals-active-minutes');

    if (!weightCurrentEl || !weightChangeEl || !weightBfEl || !hrRestingEl || !hrAverageEl ||
        !sleepDurationEl || !sleepAvgEl || !sleepBreakdownEl || !stepsRingEl || !stepsTodayEl ||
        !stepsAvgEl || !activeCaloriesEl || !activeMinutesEl) {
        return;
    }

    const weight = data?.weight?.current_lbs;
    weightCurrentEl.textContent = (weight != null) ? `${weight} lbs` : '--';

    const weightChange = data?.weight?.change_7d;
    weightChangeEl.className = '';
    if (weightChange == null) {
        weightChangeEl.textContent = '--';
        weightChangeEl.classList.add('vitals-change-flat');
    } else if (weightChange > 0) {
        weightChangeEl.textContent = `▲ +${weightChange} lbs (7d)`;
        weightChangeEl.classList.add('vitals-change-up');
    } else if (weightChange < 0) {
        weightChangeEl.textContent = `▼ ${weightChange} lbs (7d)`;
        weightChangeEl.classList.add('vitals-change-down');
    } else {
        weightChangeEl.textContent = '— 0.0 lbs (7d)';
        weightChangeEl.classList.add('vitals-change-flat');
    }

    const bf = data?.weight?.body_fat_pct;
    weightBfEl.textContent = (bf != null) ? `BF ${bf}%` : '--';

    const rhr = data?.heart_rate?.resting_bpm;
    const avgHr = data?.heart_rate?.average_bpm;
    hrRestingEl.textContent = (rhr != null) ? `${rhr} bpm` : '--';
    hrAverageEl.textContent = (avgHr != null) ? `${avgHr} bpm` : '--';

    hrRestingEl.classList.remove('hr-low', 'hr-mid', 'hr-high');
    if (rhr != null) {
        if (rhr < 60) hrRestingEl.classList.add('hr-low');
        else if (rhr < 70) hrRestingEl.classList.add('hr-mid');
        else hrRestingEl.classList.add('hr-high');
    }

    const lastNight = data?.sleep?.last_night;
    const sleepHours = lastNight?.duration_hours;
    sleepDurationEl.textContent = (sleepHours != null) ? `${sleepHours}h` : '--';
    sleepAvgEl.textContent = (data?.sleep?.avg_7d_hours != null) ? `${data.sleep.avg_7d_hours}h` : '--';

    const deep = lastNight?.deep_min;
    const rem = lastNight?.rem_min;
    const light = lastNight?.light_min;
    const awake = lastNight?.awake_min;
    const total = [deep, rem, light, awake].reduce((sum, v) => sum + (v || 0), 0);

    const deepEl = document.getElementById('sleep-seg-deep');
    const remEl = document.getElementById('sleep-seg-rem');
    const lightEl = document.getElementById('sleep-seg-light');
    const awakeEl = document.getElementById('sleep-seg-awake');

    if (deepEl && remEl && lightEl && awakeEl) {
        deepEl.style.width = total ? `${(deep || 0) / total * 100}%` : '0%';
        remEl.style.width = total ? `${(rem || 0) / total * 100}%` : '0%';
        lightEl.style.width = total ? `${(light || 0) / total * 100}%` : '0%';
        awakeEl.style.width = total ? `${(awake || 0) / total * 100}%` : '0%';
    }

    if (deep != null || rem != null || light != null || awake != null) {
        sleepBreakdownEl.textContent = `Deep ${deep ?? '--'}m · REM ${rem ?? '--'}m · Light ${light ?? '--'}m · Awake ${awake ?? '--'}m`;
    } else {
        sleepBreakdownEl.textContent = '--';
    }

    const stepsToday = data?.activity?.steps_today;
    const stepsAvg = data?.activity?.steps_avg_7d;
    const activeCalories = data?.activity?.active_calories_today;
    const activeMinutes = data?.activity?.active_minutes_today;

    stepsTodayEl.textContent = (stepsToday != null) ? `${stepsToday}` : '--';
    stepsAvgEl.textContent = (stepsAvg != null) ? `${stepsAvg}` : '--';
    activeCaloriesEl.textContent = (activeCalories != null) ? `${activeCalories}` : '--';
    activeMinutesEl.textContent = (activeMinutes != null) ? `${activeMinutes}` : '--';

    const target = 8000;
    const pct = (stepsToday != null && target) ? Math.min(stepsToday / target, 1) : 0;
    const ringDeg = Math.round(pct * 360);
    stepsRingEl.style.background = `conic-gradient(var(--success) ${ringDeg}deg, rgba(255, 255, 255, 0.08) 0deg)`;

    renderVitalsWeightChart(data?.weight?.trend_30d || []);
    renderVitalsHrChart(data?.heart_rate?.trend_7d || []);
}

function renderVitalsWeightChart(trend) {
    const canvas = document.getElementById('vitals-weight-chart');
    if (!canvas) return;
    const labels = trend.map(p => p.date);
    const values = trend.map(p => p.weight_lbs);

    if (charts.vitalsWeight) {
        charts.vitalsWeight.destroy();
    }

    charts.vitalsWeight = new Chart(canvas, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                data: values,
                borderColor: '#22d3ee',
                backgroundColor: 'rgba(34, 211, 238, 0.15)',
                fill: true,
                tension: 0.35,
                pointRadius: 0,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false }, tooltip: { enabled: false } },
            scales: { x: { display: false }, y: { display: false } }
        }
    });
}

function renderVitalsHrChart(trend) {
    const canvas = document.getElementById('vitals-hr-chart');
    if (!canvas) return;
    const labels = trend.map(p => p.date);
    const resting = trend.map(p => p.resting);
    const average = trend.map(p => p.average);

    if (charts.vitalsHr) {
        charts.vitalsHr.destroy();
    }

    charts.vitalsHr = new Chart(canvas, {
        type: 'line',
        data: {
            labels,
            datasets: [
                {
                    data: resting,
                    borderColor: '#22c55e',
                    tension: 0.35,
                    pointRadius: 0,
                },
                {
                    data: average,
                    borderColor: '#f59e0b',
                    tension: 0.35,
                    pointRadius: 0,
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false }, tooltip: { enabled: false } },
            scales: { x: { display: false }, y: { display: false } }
        }
    });
}

function updateRecompCommandCenter(recomp, nutrition) {
    const badge = document.getElementById('recomp-signal');
    const reason = document.getElementById('recomp-reason');
    if (badge && recomp) {
        badge.textContent = recomp.signal || '--';
        badge.classList.remove('train', 'recover');
        if (recomp.signal === 'TRAIN') {
            badge.classList.add('train');
        } else if (recomp.signal === 'RECOVER') {
            badge.classList.add('recover');
        }
    }
    if (reason) {
        reason.textContent = recomp?.reason || '--';
    }

    const proteinText = document.getElementById('protein-today');
    const caloriesText = document.getElementById('calories-today');
    const proteinFill = document.getElementById('protein-progress');
    const caloriesFill = document.getElementById('calories-progress');
    if (nutrition) {
        if (proteinText) {
            proteinText.textContent = `${Math.round(nutrition.protein_g)}g / ${Math.round(nutrition.protein_target_g)}g`;
        }
        if (caloriesText) {
            caloriesText.textContent = `${nutrition.calories} / ${nutrition.calories_target}`;
        }
        if (proteinFill) {
            const pct = Math.max(0, nutrition.protein_pct || 0);
            proteinFill.style.width = `${Math.min(pct, 100)}%`;
            proteinFill.classList.toggle('over', pct > 110);
        }
        if (caloriesFill) {
            const pct = Math.max(0, nutrition.calories_pct || 0);
            caloriesFill.style.width = `${Math.min(pct, 100)}%`;
            caloriesFill.classList.toggle('over', pct > 110);
        }
    } else {
        if (proteinText) proteinText.textContent = '--';
        if (caloriesText) caloriesText.textContent = '--';
        if (proteinFill) proteinFill.style.width = '0%';
        if (caloriesFill) caloriesFill.style.width = '0%';
    }
}

// Oura Recovery Widget
async function loadOuraWidget(forceRefresh = false) {
    const readinessEl = document.getElementById('oura-readiness');
    const hrvEl = document.getElementById('oura-hrv');
    if (!readinessEl || !hrvEl) return;

    // Check if URL has refresh param
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('refresh')) {
        forceRefresh = true;
        // Clean URL without refresh param
        window.history.replaceState({}, '', '/');
    }

    try {
        const statusUrl = forceRefresh ? '/api/oura/status?refresh=true' : '/api/oura/status';
        const [statusResp, trendResp] = await Promise.all([
            fetch(statusUrl),
            fetch('/api/oura/trends')
        ]);

        const status = await statusResp.json();
        const trend = await trendResp.json();

        if (status && status.readiness != null) {
            readinessEl.textContent = status.readiness;
        } else {
            readinessEl.textContent = '--';
        }

        // Populate Recovery Card (best-effort)
        const setText = (id, val) => {
            const el = document.getElementById(id);
            if (!el) return;
            el.textContent = (val != null && val !== '') ? val : '--';
        };
        const fmtMin = (m) => {
            if (m == null) return '--';
            const hh = Math.floor(m / 60);
            const mm = m % 60;
            return hh > 0 ? `${hh}h ${mm}m` : `${mm}m`;
        };
        if (status) {
            setText('oura-steps', status.steps != null ? status.steps.toLocaleString() : null);
            setText('oura-activity-score', status.activity_score);
            setText('oura-rhr', status.resting_hr != null ? `${Math.round(status.resting_hr)} bpm` : null);
            setText('oura-temp', status.temperature_deviation != null ? `${status.temperature_deviation > 0 ? '+' : ''}${status.temperature_deviation}°` : null);

            setText('oura-sleep-duration', status.sleep_duration_min != null ? fmtMin(status.sleep_duration_min) : null);
            const b = status.sleep_breakdown_min || {};
            setText('oura-sleep-deep', b.deep != null ? `${b.deep}m` : null);
            setText('oura-sleep-rem', b.rem != null ? `${b.rem}m` : null);
            setText('oura-sleep-light', b.light != null ? `${b.light}m` : null);
            setText('oura-sleep-awake', b.awake != null ? `${b.awake}m` : null);

            const noteEl = document.getElementById('oura-recovery-note');
            if (noteEl) {
                noteEl.textContent = '';
            }
        }

        if (trend && trend.hrv_trend) {
            const hrvText = trend.hrv_trend === 'unknown' ? '--' : trend.hrv_trend;
            hrvEl.textContent = hrvText;
        } else {
            hrvEl.textContent = '--';
        }
    } catch (e) {
        readinessEl.textContent = '--';
        hrvEl.textContent = '--';
    }
}

// Load Sleep Insights
async function loadSleepInsights() {
    try {
        const resp = await fetch('/api/oura/sleep-summary');
        const data = await resp.json();

        if (!data || data.status === 'error') {
            console.error('Failed to load sleep data:', data?.message);
            return;
        }

        // Last night summary
        const lastNight = data.last_night || {};
        const weekAvg = data.week_average || {};
        const consistency = data.consistency || {};

        // Update KPIs
        const setVal = (id, val, unit = '') => {
            const el = document.getElementById(id);
            if (el) el.textContent = val != null ? `${val}${unit}` : '--';
        };

        // Convert minutes to hours
        const minToHrs = (min) => min ? (min / 60).toFixed(1) : null;

        setVal('sleep-last-night-duration', minToHrs(lastNight.total_sleep_min), ' h');
        const sleepScoreEl = document.getElementById('sleep-last-night-score');
        if (sleepScoreEl) {
            if (lastNight.sleep_score && lastNight.sleep_score > 0) {
                sleepScoreEl.textContent = lastNight.sleep_score;
            } else {
                sleepScoreEl.innerHTML = 'N/A <span class="kpi-note">(not tracked by Oura)</span>';
            }
        }
        setVal('sleep-week-avg', minToHrs(weekAvg.duration_min), ' h');
        
        // Consistency status
        const consistencyText = consistency.status === 'excellent' ? '✅ Excellent' :
                               consistency.status === 'good' ? '✔️ Good' :
                               consistency.status === 'fair' ? '⚠️ Fair' :
                               consistency.status === 'poor' ? '❌ Poor' : '--';
        setVal('sleep-consistency', consistencyText);

        // Sleep stage percentages (last night)
        if (lastNight.total_sleep_min && lastNight.total_sleep_min > 0) {
            const total = lastNight.total_sleep_min;
            const deepPct = lastNight.deep_sleep_min ? ((lastNight.deep_sleep_min / total) * 100).toFixed(0) : 0;
            const remPct = lastNight.rem_sleep_min ? ((lastNight.rem_sleep_min / total) * 100).toFixed(0) : 0;
            const lightPct = lastNight.light_sleep_min ? ((lastNight.light_sleep_min / total) * 100).toFixed(0) : 0;

            setVal('sleep-deep-pct', deepPct, '%');
            setVal('sleep-rem-pct', remPct, '%');
            setVal('sleep-light-pct', lightPct, '%');
        } else {
            setVal('sleep-deep-pct', '--');
            setVal('sleep-rem-pct', '--');
            setVal('sleep-light-pct', '--');
        }

        setVal('sleep-hr', lastNight.avg_heart_rate ? Math.round(lastNight.avg_heart_rate) : null, ' bpm');

        // Chart is now rendered inline in index.html (bypasses app.js caching issues)

    } catch (error) {
        console.error('Failed to load sleep insights:', error);
    }
}

// Render Sleep Trend Chart
function renderSleepTrendChart(trendData) {
    const canvas = document.getElementById('sleepTrendChart');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    
    // Destroy existing chart if present
    if (window.sleepTrendChart) {
        window.sleepTrendChart.destroy();
    }

    const labels = trendData.map(d => d.date);
    const durations = trendData.map(d => (d.duration_min / 60).toFixed(1)); // Convert to hours
    const scores = trendData.map(d => d.score || 0);

    window.sleepTrendChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Duration (hrs)',
                    data: durations,
                    borderColor: '#4361ee',
                    backgroundColor: 'rgba(67, 97, 238, 0.1)',
                    yAxisID: 'y',
                    tension: 0.3,
                    fill: true
                },
                {
                    label: 'Score',
                    data: scores,
                    borderColor: '#3a0ca3',
                    backgroundColor: 'rgba(58, 12, 163, 0.1)',
                    yAxisID: 'y1',
                    tension: 0.3,
                    fill: false
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                mode: 'index',
                intersect: false,
            },
            plugins: {
                legend: {
                    display: true,
                    position: 'top',
                },
                title: {
                    display: true,
                    text: '7-Day Sleep Trend'
                }
            },
            scales: {
                y: {
                    type: 'linear',
                    display: true,
                    position: 'left',
                    title: {
                        display: true,
                        text: 'Duration (hours)'
                    },
                    min: 0,
                    max: 12
                },
                y1: {
                    type: 'linear',
                    display: true,
                    position: 'right',
                    title: {
                        display: true,
                        text: 'Score'
                    },
                    min: 0,
                    max: 100,
                    grid: {
                        drawOnChartArea: false,
                    },
                }
            }
        }
    });
}

// Smart recommendation summary (Oura + soreness)
async function loadSmartRecommendation() {
    const el = document.getElementById('smart-reco');
    if (!el) return;

    try {
        const resp = await fetch('/api/recommendation/smart');
        const data = await resp.json();

        if (!data) {
            el.textContent = '';
            return;
        }

        const summaryEl = document.getElementById('readiness-summary');
        if (summaryEl) {
            if (data.readiness == null) {
                summaryEl.textContent = '';
            } else if (data.readiness >= 80) {
                summaryEl.textContent = '✅ High readiness — train hard';
            } else if (data.readiness >= 60) {
                summaryEl.textContent = '⚠️ Moderate readiness — train smart';
            } else {
                summaryEl.textContent = '🔴 Low readiness — consider recovery';
            }
        }

        const rec = data.recommendation ? data.recommendation.toUpperCase() : '';
        const avoid = (data.avoid_muscles && data.avoid_muscles.length) ? ` | Avoid: ${data.avoid_muscles.join(', ')}` : '';
        const readiness = data.readiness != null ? `Readiness ${data.readiness}` : 'Readiness --';
        const trend = data.hrv_trend && data.hrv_trend !== 'unknown' ? `HRV ${data.hrv_trend}` : 'HRV --';

        // Show effective readiness if available
        const effReadiness = data.effective_readiness != null ? `Eff. ${Math.round(data.effective_readiness)}` : readiness;
        el.textContent = `${rec} • ${effReadiness} • ${trend}${avoid}`;

        // Show reasoning factors
        const reasoningContainer = document.getElementById('recommendation-reasoning');
        const factorsContainer = document.getElementById('reasoning-factors');
        if (reasoningContainer && factorsContainer && data.readiness_factors) {
            const reasoningToggle = document.getElementById('reasoning-toggle');
            const factors = [];
            const rf = data.readiness_factors;
            
            // ACWR - hide if 0 (no meaningful data yet)
            if (rf.acwr && rf.acwr.acwr > 0) {
                const acwrClass = rf.acwr.risk === 'optimal' ? 'positive' : 
                                  rf.acwr.risk === 'high' ? 'negative' : 'neutral';
                factors.push(`<div class="reasoning-factor ${acwrClass}">
                    <span class="icon">📈</span>
                    <span>Training load ratio: ${rf.acwr.acwr.toFixed(2)}</span>
                </div>`);
            }
            
            // Sleep Debt - show friendly label, hide "severe" when data is sparse
            if (rf.sleep_debt && rf.sleep_debt.debt_hours != null) {
                const debtH = rf.sleep_debt.debt_hours;
                const sleepClass = debtH < 2 ? 'positive' : debtH < 5 ? 'neutral' : 'negative';
                const sleepLabel = debtH < 2 ? 'Well rested' : debtH < 5 ? 'Mild sleep debt' : 'Sleep debt: recover tonight';
                factors.push(`<div class="reasoning-factor ${sleepClass}">
                    <span class="icon">😴</span>
                    <span>${sleepLabel}</span>
                </div>`);
            }
            
            // Recovery Bonus
            if (rf.recovery_bonus && rf.recovery_bonus.bonus_points > 0) {
                factors.push(`<div class="reasoning-factor positive">
                    <span class="icon">🧊</span>
                    <span>Recovery bonus: +${rf.recovery_bonus.bonus_points} (${rf.recovery_bonus.modalities_used.join(', ')})</span>
                </div>`);
            }
            
            // Base readiness vs effective
            if (data.readiness != null && data.effective_readiness != null && data.readiness !== data.effective_readiness) {
                factors.push(`<div class="reasoning-factor neutral">
                    <span class="icon">💪</span>
                    <span>Readiness: ${data.readiness} → ${Math.round(data.effective_readiness)} (adjusted)</span>
                </div>`);
            }
            
            if (factors.length > 0) {
                factorsContainer.innerHTML = factors.join('');
                reasoningContainer.style.display = 'block';
                factorsContainer.style.display = 'flex';
                if (reasoningToggle) {
                    reasoningToggle.setAttribute('aria-expanded', 'true');
                    reasoningToggle.onclick = () => {
                        const isOpen = factorsContainer.style.display !== 'none';
                        factorsContainer.style.display = isOpen ? 'none' : 'flex';
                        reasoningToggle.setAttribute('aria-expanded', isOpen ? 'false' : 'true');
                    };
                }
            } else {
                reasoningContainer.style.display = 'none';
            }
        }

        // Also mirror avoid muscles into the existing avoid section (if present)
        if (data.avoid_muscles && data.avoid_muscles.length) {
            const section = document.getElementById('avoid-section');
            const container = document.getElementById('avoid-container');
            if (section && container) {
                section.style.display = 'block';
                const existing = container.innerHTML || '';
                const extra = data.avoid_muscles.map(m => `
                    <div class="avoid-item">
                        <span class="avoid-muscle">${m}</span>
                        <span class="avoid-reason">Recent soreness</span>
                    </div>
                `).join('');
                container.innerHTML = existing + extra;
            }
        }

    } catch (e) {
        el.textContent = '';
    }
}

// Update Alerts
function updateAlerts(alerts) {
    const container = document.getElementById('alerts-container');
    const section = document.getElementById('alerts-section');

    if (!alerts || alerts.length === 0) {
        section.style.display = 'none';
        return;
    }

    section.style.display = 'block';
    container.innerHTML = alerts.slice(0, 5).map(alert => `
        <div class="alert alert-${alert.priority.toLowerCase()}">
            <div class="alert-header">
                <span class="alert-type">${alert.type}</span>
                <span class="alert-priority priority-${alert.priority.toLowerCase()}">${alert.priority}</span>
            </div>
            <div class="alert-message">${alert.message}</div>
            <div class="alert-action">${alert.action}</div>
        </div>
    `).join('');
}

// Update Muscle Groups
function updateMuscleGroups(muscles) {
    const container = document.getElementById('muscle-container');
    const title = document.getElementById('muscle-accordion-title');
    if (title) {
        const count = Array.isArray(muscles) ? muscles.length : 0;
        title.textContent = `Muscle Groups (${count})`;
    }

    if (container) {
        container.innerHTML = muscles.map(muscle => `
            <div class="muscle-card">
                <div class="muscle-info">
                    <span class="muscle-name">${muscle.muscle}</span>
                    <span class="muscle-stats">${muscle.sets} sets | ${muscle.status} | Last: ${formatDate(muscle.last_trained)}</span>
                </div>
                <div class="muscle-readiness">
                    <span class="readiness-score text-${muscle.readiness_color}">${muscle.readiness}/10</span>
                    <span class="readiness-label">Readiness</span>
                </div>
            </div>
        `).join('');
    }

    const heatmap = document.getElementById('muscle-heatmap');
    if (heatmap) {
        const muscleMap = {};
        muscles.forEach(m => { muscleMap[m.muscle.toLowerCase()] = m; });

        const getColor = (name) => {
            const m = muscleMap[name];
            if (!m) return 'rgba(255,255,255,0.06)';
            const r = m.readiness || 0;
            if (r >= 8) return 'rgba(16, 185, 129, 0.7)';
            if (r >= 5) return 'rgba(245, 158, 11, 0.6)';
            return 'rgba(239, 68, 68, 0.6)';
        };
        const getLabel = (name) => {
            const m = muscleMap[name];
            return m ? `${m.readiness}/10` : '--';
        };

        heatmap.innerHTML = `
        <div class="body-heatmap-container">
            <svg viewBox="0 0 200 380" class="body-svg" xmlns="http://www.w3.org/2000/svg">
                <!-- Head -->
                <circle cx="100" cy="28" r="18" fill="rgba(255,255,255,0.08)" stroke="rgba(255,255,255,0.15)" stroke-width="1"/>
                <!-- Neck -->
                <rect x="93" y="46" width="14" height="12" rx="4" fill="rgba(255,255,255,0.06)"/>
                <!-- Shoulders -->
                <ellipse cx="60" cy="72" rx="22" ry="12" fill="${getColor('shoulders')}" class="muscle-zone" data-muscle="shoulders"/>
                <ellipse cx="140" cy="72" rx="22" ry="12" fill="${getColor('shoulders')}" class="muscle-zone" data-muscle="shoulders"/>
                <!-- Chest -->
                <ellipse cx="80" cy="95" rx="20" ry="18" fill="${getColor('chest')}" class="muscle-zone" data-muscle="chest"/>
                <ellipse cx="120" cy="95" rx="20" ry="18" fill="${getColor('chest')}" class="muscle-zone" data-muscle="chest"/>
                <!-- Back (shown as outline behind chest) -->
                <rect x="68" y="80" width="64" height="50" rx="8" fill="${getColor('back')}" opacity="0.3" class="muscle-zone" data-muscle="back"/>
                <!-- Biceps -->
                <ellipse cx="42" cy="110" rx="10" ry="22" fill="${getColor('biceps')}" class="muscle-zone" data-muscle="biceps"/>
                <ellipse cx="158" cy="110" rx="10" ry="22" fill="${getColor('biceps')}" class="muscle-zone" data-muscle="biceps"/>
                <!-- Triceps -->
                <ellipse cx="38" cy="115" rx="7" ry="18" fill="${getColor('triceps')}" opacity="0.5" class="muscle-zone" data-muscle="triceps"/>
                <ellipse cx="162" cy="115" rx="7" ry="18" fill="${getColor('triceps')}" opacity="0.5" class="muscle-zone" data-muscle="triceps"/>
                <!-- Core -->
                <rect x="78" y="118" width="44" height="40" rx="6" fill="${getColor('core')}" class="muscle-zone" data-muscle="core"/>
                <!-- Forearms -->
                <ellipse cx="36" cy="145" rx="6" ry="16" fill="rgba(255,255,255,0.08)"/>
                <ellipse cx="164" cy="145" rx="6" ry="16" fill="rgba(255,255,255,0.08)"/>
                <!-- Glutes -->
                <ellipse cx="85" cy="170" rx="18" ry="14" fill="${getColor('glutes')}" class="muscle-zone" data-muscle="glutes"/>
                <ellipse cx="115" cy="170" rx="18" ry="14" fill="${getColor('glutes')}" class="muscle-zone" data-muscle="glutes"/>
                <!-- Quads -->
                <ellipse cx="80" cy="220" rx="16" ry="40" fill="${getColor('quads')}" class="muscle-zone" data-muscle="quads"/>
                <ellipse cx="120" cy="220" rx="16" ry="40" fill="${getColor('quads')}" class="muscle-zone" data-muscle="quads"/>
                <!-- Hamstrings (behind quads, subtle) -->
                <ellipse cx="80" cy="225" rx="13" ry="35" fill="${getColor('hamstrings')}" opacity="0.4" class="muscle-zone" data-muscle="hamstrings"/>
                <ellipse cx="120" cy="225" rx="13" ry="35" fill="${getColor('hamstrings')}" opacity="0.4" class="muscle-zone" data-muscle="hamstrings"/>
                <!-- Adductors -->
                <ellipse cx="95" cy="210" rx="6" ry="25" fill="${getColor('adductors')}" class="muscle-zone" data-muscle="adductors"/>
                <ellipse cx="105" cy="210" rx="6" ry="25" fill="${getColor('adductors')}" class="muscle-zone" data-muscle="adductors"/>
                <!-- Calves -->
                <ellipse cx="78" cy="300" rx="10" ry="28" fill="${getColor('calves')}" class="muscle-zone" data-muscle="calves"/>
                <ellipse cx="122" cy="300" rx="10" ry="28" fill="${getColor('calves')}" class="muscle-zone" data-muscle="calves"/>
                <!-- Feet -->
                <ellipse cx="78" cy="340" rx="12" ry="6" fill="rgba(255,255,255,0.06)"/>
                <ellipse cx="122" cy="340" rx="12" ry="6" fill="rgba(255,255,255,0.06)"/>
            </svg>
            <div class="body-heatmap-legend">
                ${muscles.map(m => `<div class="legend-row"><span class="legend-dot" style="background:${getColor(m.muscle.toLowerCase())}"></span><span class="legend-name">${m.muscle}</span><strong class="legend-score">${m.readiness}/10</strong></div>`).join('')}
            </div>
        </div>`;

        const allMax = muscles.length > 0 && muscles.every(m => Number(m.readiness) >= 10);
        if (allMax) {
            const note = document.createElement('div');
            note.className = 'muscle-readiness-note';
            note.textContent = 'All muscles fully recovered — time to train! 💪';
            heatmap.appendChild(note);
        }
    }
}

// Update Exercises
function updateExercises(exercises) {
    const container = document.getElementById('exercise-container');
    const title = document.getElementById('exercise-accordion-title');
    if (title) {
        const count = Array.isArray(exercises) ? exercises.length : 0;
        title.textContent = `Exercise Progress (${count})`;
    }

    container.innerHTML = exercises.map(ex => `
        <div class="exercise-card">
            <div class="exercise-info">
                <div class="exercise-name">${ex.exercise}</div>
                <div class="exercise-stats">Peak: ${ex.peak_e1rm} lbs | Current: ${ex.current_e1rm} lbs</div>
            </div>
            <div class="exercise-trend">
                <div class="trend-value text-${ex.trend_color}">${ex.trend_icon}${ex.trend_pct}%</div>
                <span class="status-badge status-${ex.status_color}">${ex.status}</span>
            </div>
        </div>
    `).join('');
}

// Update Next Workout
function updateNextWorkout(workout) {
    document.getElementById('workout-focus').textContent = workout.focus;

    // Show duration with time info
    const durationEl = document.getElementById('workout-duration');
    let durationText = workout.estimated_duration;
    if (workout.available_time) {
        durationText = `${workout.estimated_duration} (${workout.available_time} min available)`;
    }
    durationEl.textContent = durationText;

    // Show goal and time adjustment badge
    const goalEl = document.getElementById('workout-goal');
    if (workout.goal_name) {
        let goalHtml = `Goal: ${workout.goal_name}`;
        if (workout.time_adjusted) {
            goalHtml += ' <span class="time-adjusted-badge">Time-Adjusted</span>';
        }
        goalEl.innerHTML = goalHtml;
    }

    const container = document.getElementById('next-workout-exercises');
    if (!container || !workout?.exercises?.length) return;
    container.innerHTML = workout.exercises.map((ex, i) => {
        const restLabel = ex.rest_label ? ex.rest_label : (ex.rest_minutes != null ? `${ex.rest_minutes} min` : '--');
        return `
        <div class="workout-card">
            <div class="workout-exercise-header">
                <span class="workout-exercise-name">${i + 1}. ${ex.exercise}</span>
                <span class="workout-muscle">${ex.muscle}</span>
            </div>
            <div class="workout-target">${ex.target_weight} lbs x ${ex.target_reps} reps x ${ex.target_sets} sets</div>
            <div class="workout-rationale">${ex.rationale}</div>
            <div class="workout-rest">Rest: ${restLabel}${ex.rpe_target ? ` | Target RPE: ${ex.rpe_target}` : ''}${ex.estimated_time ? ` | ~${ex.estimated_time} min` : ''}</div>
        </div>
    `}).join('');

    // Cardio recommendation
    const cardioSection = document.getElementById('cardio-section');
    const cardioContainer = document.getElementById('cardio-container');

    if (cardioSection && cardioContainer && workout.cardio) {
        if (workout.cardio.type) {
            cardioSection.style.display = 'block';
            cardioContainer.innerHTML = `
                <div class="cardio-recommendation">
                    <div class="cardio-header">
                        <span class="cardio-type">${workout.cardio.type}</span>
                        <span class="cardio-duration">${workout.cardio.duration_minutes} min</span>
                    </div>
                    <div class="cardio-zone">
                        <span class="zone-label">${workout.cardio.zone}</span>
                        <span class="zone-desc">${workout.cardio.zone_description}</span>
                    </div>
                    <div class="cardio-hr">
                        <span class="material-icons">favorite</span>
                        <span>${workout.cardio.heart_rate_range}</span>
                    </div>
                    <div class="cardio-intensity">${workout.cardio.intensity}</div>
                    <div class="cardio-technique">
                        <strong>Technique:</strong> ${workout.cardio.technique}
                    </div>
                    <div class="cardio-reason">${workout.cardio.reason}</div>
                </div>
            `;
        } else if (workout.cardio.reason) {
            cardioSection.style.display = 'block';
            cardioContainer.innerHTML = `
                <div class="cardio-skip">
                    <span class="material-icons">info</span>
                    <span>${workout.cardio.reason}</span>
                </div>
            `;
        } else {
            cardioSection.style.display = 'none';
        }
    }

    // Muscles to avoid
    const avoidSection = document.getElementById('avoid-section');
    const avoidContainer = document.getElementById('avoid-container');

    if (workout.muscles_to_avoid && workout.muscles_to_avoid.length > 0) {
        avoidSection.style.display = 'block';
        avoidContainer.innerHTML = workout.muscles_to_avoid.map(m => `
            <div class="avoid-item">
                <span class="avoid-muscle">${m.muscle}</span>
                <span class="avoid-reason">${m.reason}</span>
            </div>
        `).join('');
    } else {
        avoidSection.style.display = 'none';
    }

    // Store for completion tracking
    currentRecommendation = workout;

    const dashMeta = document.getElementById('dashboard-workout-meta');
    const dashList = document.getElementById('dashboard-workout-exercises');
    if (dashMeta && dashList) {
        const meso = workout.mesocycle ? `Week ${workout.mesocycle.week} (${workout.mesocycle.phase})` : '';
        dashMeta.textContent = `${workout.focus} · ${workout.estimated_duration}${meso ? ` · ${meso}` : ''}`;
        dashList.innerHTML = workout.exercises.map(ex => `
            <div class="workout-card">
                <div class="workout-exercise-header">
                    <span class="workout-exercise-name">${ex.exercise}</span>
                    <span class="workout-muscle">${ex.muscle}</span>
                </div>
                <div class="workout-target">${ex.target_weight} lbs · ${ex.target_reps} reps · ${ex.target_sets} sets</div>
                <div class="workout-rationale">${ex.rationale}</div>
                <div class="workout-rest workout-rpe">RPE ${ex.rpe_target}</div>
            </div>
        `).join('');
    }
}

// ==================== Advanced KPIs ====================

function updateAdvancedKPIs(kpis) {
    updateConsistency(kpis.consistency);
    updateDeloadStatus(kpis.deload_check);
    updateInjuryRisk(kpis.injury_risk);
    updatePersonalRecords(kpis.personal_records);
}

function updateConsistency(consistency) {
    const currentStreak = document.getElementById('current-streak');
    const longestStreak = document.getElementById('longest-streak');
    const weeklyAvg = document.getElementById('weekly-avg');
    const consistencyPct = document.getElementById('consistency-pct');
    const streakCount = document.getElementById('streak-count');
    const streakMeta = document.getElementById('streak-meta');

    if (currentStreak) currentStreak.textContent = consistency.current_streak;
    if (longestStreak) longestStreak.textContent = consistency.longest_streak;
    if (weeklyAvg) weeklyAvg.textContent = consistency.weekly_avg;
    if (consistencyPct) consistencyPct.textContent = consistency.consistency_pct + '%';
    if (streakCount) streakCount.textContent = consistency.current_streak;
    if (streakMeta) streakMeta.textContent = `${consistency.weekly_avg} sessions/week · Longest ${consistency.longest_streak}`;
}

function updateDeloadStatus(deload) {
    const container = document.getElementById('deload-container');
    if (!container) return;

    const statusColor = deload.needed ? 'yellow' : 'green';
    const statusText = deload.needed ? 'Deload Recommended' : 'Continue Training';

    container.innerHTML = `
        <div class="status-header">
            <span>Recovery Status</span>
            <span class="status-indicator ${statusColor}">${statusText}</span>
        </div>
        <div class="status-details">
            <p>Weeks since deload: ${deload.weeks_since_deload}</p>
            ${deload.indicators && deload.indicators.length > 0 ? `
                <p style="margin-top: 8px; font-weight: 600;">Indicators:</p>
                ${deload.indicators.map(i => `<div class="status-item">${i}</div>`).join('')}
            ` : ''}
            <p style="margin-top: 12px; color: var(--primary);">${deload.recommendation}</p>
        </div>
    `;
}

function updateInjuryRisk(risk) {
    const container = document.getElementById('injury-risk-container');
    if (!container) return;

    container.innerHTML = `
        <div class="status-header">
            <span>Risk Level</span>
            <span class="status-indicator ${risk.color}">${risk.overall}</span>
        </div>
        <div class="status-details">
            ${risk.risks && risk.risks.length > 0 ? risk.risks.map(r => `
                <div class="status-item">
                    <span class="risk-badge ${r.severity}">${r.type}</span>
                    <span>${r.message}</span>
                </div>
            `).join('') : '<p>No significant risks detected</p>'}
        </div>
    `;
}

function updatePersonalRecords(prs) {
    const container = document.getElementById('prs-container');
    if (!container) return;

    const prList = Object.entries(prs)
        .filter(([_, data]) => data.all_time > 0)
        .sort((a, b) => b[1].all_time - a[1].all_time)
        .slice(0, 8);

    container.innerHTML = prList.map(([exercise, data]) => `
        <div class="pr-card">
            <div class="pr-info">
                <div class="pr-exercise">${exercise}</div>
                <div class="pr-date">${formatDate(data.all_time_date)}</div>
            </div>
            <div class="pr-value">
                <div class="pr-weight">${data.all_time} lbs</div>
                <div class="pr-label">All-Time e1RM</div>
            </div>
        </div>
    `).join('');
}

// ==================== Insights & Graphs Tab ====================

async function loadInsights() {
    try {
        const response = await fetch('/api/insights');
        const data = await response.json();

        renderInsights(data.insights);
        renderCharts(data.charts);
    } catch (error) {
        console.error('Failed to load insights:', error);
    }
}

function renderInsights(insights) {
    const container = document.getElementById('insights-container');
    if (!container) return;

    container.innerHTML = insights.map(insight => `
        <div class="insight-card insight-${insight.type}">
            <span class="material-icons insight-icon">${insight.icon}</span>
            <div class="insight-content">
                <div class="insight-title">${insight.title}</div>
                <div class="insight-detail">${insight.detail}</div>
            </div>
        </div>
    `).join('');
}

function renderCharts(chartData) {
    // Destroy existing charts
    Object.values(charts).forEach(chart => chart.destroy());
    charts = {};

    // e1RM Trends Chart
    const progressCtx = document.getElementById('progressChart');
    if (progressCtx && chartData.e1rm_trends) {
        const allDates = new Set();
        chartData.e1rm_trends.forEach(ex => {
            ex.data.forEach(d => allDates.add(d.date));
        });
        const dates = Array.from(allDates).sort();

        const colors = ['#4361ee', '#7209b7', '#10b981', '#f59e0b', '#ef4444'];
        const datasets = chartData.e1rm_trends.slice(0, 5).map((ex, i) => ({
            label: ex.exercise,
            data: dates.map(date => {
                const point = ex.data.find(d => d.date === date);
                return point ? point.e1rm : null;
            }),
            borderColor: colors[i],
            backgroundColor: colors[i] + '20',
            tension: 0.3,
            fill: false,
            spanGaps: true
        }));

        charts.progress = new Chart(progressCtx, {
            type: 'line',
            data: { labels: dates.map(d => formatDate(d)), datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'bottom', labels: { color: '#a0a0b0', font: { size: 10 }, boxWidth: 12 } }
                },
                scales: {
                    x: { ticks: { color: '#a0a0b0', font: { size: 10 } }, grid: { color: '#2a2a4a' } },
                    y: { ticks: { color: '#a0a0b0', font: { size: 10 } }, grid: { color: '#2a2a4a' }, title: { display: true, text: 'e1RM (lbs)', color: '#a0a0b0', font: { size: 10 } } }
                }
            }
        });
    }

    // Volume Distribution Chart
    const volumeCtx = document.getElementById('volumeChart');
    if (volumeCtx && chartData.muscle_volume) {
        charts.volume = new Chart(volumeCtx, {
            type: 'bar',
            data: {
                labels: chartData.muscle_volume.map(m => m.muscle),
                datasets: [{
                    label: 'Weekly Sets',
                    data: chartData.muscle_volume.map(m => m.sets),
                    backgroundColor: '#4361ee80',
                    borderColor: '#4361ee',
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ticks: { color: '#a0a0b0' }, grid: { color: '#2a2a4a' } },
                    y: { ticks: { color: '#a0a0b0' }, grid: { color: '#2a2a4a' }, beginAtZero: true }
                }
            }
        });
    }

    // Push/Pull Chart
    const pushPullCtx = document.getElementById('pushPullChart');
    const pushPullEmpty = document.getElementById('pushpull-empty');
    if (pushPullCtx && chartData.push_pull) {
        const pushSets = chartData.push_pull.push || 0;
        const pullSets = chartData.push_pull.pull || 0;
        const totalSets = pushSets + pullSets;
        if (totalSets < 10) {
            if (charts.pushPull) {
                charts.pushPull.destroy();
            }
            pushPullCtx.style.display = 'none';
            if (pushPullEmpty) pushPullEmpty.style.display = 'block';
        } else {
            pushPullCtx.style.display = 'block';
            if (pushPullEmpty) pushPullEmpty.style.display = 'none';
            if (charts.pushPull) {
                charts.pushPull.destroy();
            }
            charts.pushPull = new Chart(pushPullCtx, {
                type: 'doughnut',
                data: {
                    labels: ['Push', 'Pull'],
                    datasets: [{
                        data: [pushSets, pullSets],
                        backgroundColor: ['#ef4444', '#3b82f6'],
                        borderWidth: 0
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { position: 'bottom', labels: { color: '#a0a0b0' } }
                    }
                }
            });
        }
    }
}

async function loadWeightChart() {
    try {
        const response = await fetch('/api/body-history');
        const data = await response.json();
        
        if (!data.history || data.history.length === 0) {
            return; // No data to display
        }
        
        const weightCtx = document.getElementById('weightChart');
        if (!weightCtx) return;
        
        // Destroy existing weight chart if any
        if (charts.weight) {
            charts.weight.destroy();
        }
        
        // Sort by date ascending for chronological display
        const sortedHistory = [...data.history].reverse();
        const dates = sortedHistory.map(entry => entry.date);
        const weights = sortedHistory.map(entry => entry.weight_lbs);
        
        charts.weight = new Chart(weightCtx, {
            type: 'line',
            data: {
                labels: dates.map(d => formatDate(d)),
                datasets: [{
                    label: 'Weight (lbs)',
                    data: weights,
                    borderColor: '#10b981',
                    backgroundColor: '#10b98120',
                    tension: 0.3,
                    fill: true,
                    pointRadius: 4,
                    pointHoverRadius: 6
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { 
                        display: true,
                        position: 'top',
                        labels: { 
                            color: '#a0a0b0', 
                            font: { size: 12 },
                            boxWidth: 15
                        }
                    }
                },
                scales: {
                    x: { 
                        ticks: { 
                            color: '#a0a0b0', 
                            font: { size: 10 }
                        }, 
                        grid: { color: '#2a2a4a' }
                    },
                    y: { 
                        ticks: { 
                            color: '#a0a0b0', 
                            font: { size: 10 }
                        }, 
                        grid: { color: '#2a2a4a' },
                        title: { 
                            display: true, 
                            text: 'Weight (lbs)', 
                            color: '#a0a0b0', 
                            font: { size: 12 } 
                        }
                    }
                }
            }
        });
    } catch (error) {
        console.error('Failed to load weight chart:', error);
    }
}

async function loadRecompTrendChart() {
    const emptyEl = document.getElementById('recomp-empty');
    try {
        const response = await fetch('/api/body-history');
        const data = await response.json();
        const history = data.history || [];
        if (!history || history.length < 2) {
            if (emptyEl) emptyEl.style.display = 'block';
            if (charts.recomp) {
                charts.recomp.destroy();
                charts.recomp = null;
            }
            return;
        }

        if (emptyEl) emptyEl.style.display = 'none';

        const recompCtx = document.getElementById('recompChart');
        if (!recompCtx) return;

        if (charts.recomp) {
            charts.recomp.destroy();
        }

        const sortedHistory = [...history].reverse();
        const dates = sortedHistory.map(entry => entry.date);
        const weights = sortedHistory.map(entry => entry.weight_lbs);
        const bodyFat = sortedHistory.map(entry => entry.body_fat_pct);

        charts.recomp = new Chart(recompCtx, {
            type: 'line',
            data: {
                labels: dates.map(d => formatDate(d)),
                datasets: [
                    {
                        label: 'Weight (lbs)',
                        data: weights,
                        borderColor: '#10b981',
                        backgroundColor: 'rgba(16, 185, 129, 0.15)',
                        yAxisID: 'y',
                        tension: 0.3,
                        fill: true,
                        pointRadius: 3
                    },
                    {
                        label: 'Body Fat %',
                        data: bodyFat,
                        borderColor: '#f59e0b',
                        backgroundColor: 'rgba(245, 158, 11, 0.15)',
                        yAxisID: 'y1',
                        tension: 0.3,
                        fill: false,
                        pointRadius: 3
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: true,
                        position: 'top',
                        labels: { color: '#a0a0b0', font: { size: 12 } }
                    }
                },
                scales: {
                    x: { ticks: { color: '#a0a0b0' }, grid: { color: '#2a2a4a' } },
                    y: {
                        type: 'linear',
                        position: 'left',
                        ticks: { color: '#a0a0b0' },
                        grid: { color: '#2a2a4a' },
                        title: { display: true, text: 'Weight (lbs)', color: '#a0a0b0', font: { size: 12 } }
                    },
                    y1: {
                        type: 'linear',
                        position: 'right',
                        ticks: { color: '#a0a0b0' },
                        grid: { drawOnChartArea: false },
                        title: { display: true, text: 'Body Fat %', color: '#a0a0b0', font: { size: 12 } }
                    }
                }
            }
        });
    } catch (error) {
        if (emptyEl) emptyEl.style.display = 'block';
        console.error('Failed to load recomp chart:', error);
    }
}

// ==================== History Tab ====================

let currentHistoryFilter = 'workouts';
let currentDateRange = 'all';
let customDateFrom = null;
let customDateTo = null;
let historyData = { workouts: [], cardio: [], recovery: [] };

function initHistoryFilters() {
    // Activity type filter buttons
    const filterBtns = document.querySelectorAll('.history-filter-btn');
    filterBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const filter = btn.dataset.filter;
            filterBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentHistoryFilter = filter;
            renderHistoryByFilter();
            if (navigator.vibrate) navigator.vibrate(10);
        });
    });

    // Date range filter buttons
    const dateRangeBtns = document.querySelectorAll('.date-range-btn');
    const customDateContainer = document.getElementById('custom-date-range');

    dateRangeBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const range = btn.dataset.range;
            dateRangeBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentDateRange = range;

            // Show/hide custom date inputs
            if (range === 'custom') {
                customDateContainer.style.display = 'flex';
            } else {
                customDateContainer.style.display = 'none';
                customDateFrom = null;
                customDateTo = null;
                renderHistoryByFilter();
            }

            if (navigator.vibrate) navigator.vibrate(10);
        });
    });

    // Apply custom date range button
    const applyBtn = document.getElementById('apply-date-range');
    if (applyBtn) {
        applyBtn.addEventListener('click', () => {
            const fromInput = document.getElementById('date-from');
            const toInput = document.getElementById('date-to');
            customDateFrom = fromInput.value ? new Date(fromInput.value) : null;
            customDateTo = toInput.value ? new Date(toInput.value + 'T23:59:59') : null;
            renderHistoryByFilter();
            if (navigator.vibrate) navigator.vibrate(10);
        });
    }
}

function filterByDateRange(items) {
    // Add original index to each item for delete functionality
    const itemsWithIndex = items.map((item, idx) => ({ ...item, _originalIndex: idx }));

    if (currentDateRange === 'all') {
        return itemsWithIndex;
    }

    const now = new Date();
    let startDate;

    if (currentDateRange === 'custom') {
        return itemsWithIndex.filter(item => {
            const itemDate = new Date(item.date);
            if (customDateFrom && itemDate < customDateFrom) return false;
            if (customDateTo && itemDate > customDateTo) return false;
            return true;
        });
    } else {
        const days = parseInt(currentDateRange);
        startDate = new Date(now.getTime() - (days * 24 * 60 * 60 * 1000));
        return itemsWithIndex.filter(item => {
            const itemDate = new Date(item.date);
            return itemDate >= startDate;
        });
    }
}

async function loadHistory() {
    try {
        const response = await fetch('/api/history-all');
        historyData = await response.json();
        renderHistoryByFilter();
    } catch (error) {
        console.error('Failed to load history:', error);
    }
}

function renderHistoryByFilter() {
    const container = document.getElementById('history-container');
    const totalEl = document.getElementById('history-total');
    const volumeEl = document.getElementById('history-volume');
    const totalLabel = document.getElementById('history-total-label');
    const volumeLabel = document.getElementById('history-volume-label');
    const overloadSection = document.getElementById('progressive-overload-section');

    if (!container) return;
    if (overloadSection) overloadSection.style.display = 'none';

    if (currentHistoryFilter === 'workouts') {
        const allWorkouts = historyData.workouts || [];
        const workouts = filterByDateRange(allWorkouts);
        totalEl.textContent = workouts.length;
        totalLabel.textContent = 'Total Workouts';
        const totalVolume = workouts.reduce((sum, w) => sum + (w.total_volume || 0), 0);
        volumeEl.textContent = (totalVolume / 1000).toFixed(0) + 'K';
        volumeLabel.textContent = 'Total Volume';
        renderWorkoutHeatmap(allWorkouts);
        renderWorkoutHistory(workouts);
        loadProgressiveOverload();
    } else if (currentHistoryFilter === 'cardio') {
        const allCardio = historyData.cardio || [];
        const cardio = filterByDateRange(allCardio);
        totalEl.textContent = cardio.length;
        totalLabel.textContent = 'Total Sessions';
        const totalMinutes = cardio.reduce((sum, c) => sum + (c.duration_minutes || 0), 0);
        volumeEl.textContent = totalMinutes;
        volumeLabel.textContent = 'Total Minutes';
        renderCardioHistory(cardio);
    } else if (currentHistoryFilter === 'recovery') {
        const allRecovery = historyData.recovery || [];
        const recovery = filterByDateRange(allRecovery);
        totalEl.textContent = recovery.length;
        totalLabel.textContent = 'Total Sessions';
        const totalMinutes = recovery.reduce((sum, r) => sum + (r.duration_minutes || 0), 0);
        volumeEl.textContent = totalMinutes;
        volumeLabel.textContent = 'Total Minutes';
        renderRecoveryHistory(recovery);
    }
}

async function loadProgressiveOverload() {
    const section = document.getElementById('progressive-overload-section');
    const container = document.getElementById('progressive-overload-container');
    if (!section || !container) return;
    try {
        const response = await fetch('/api/progressive-overload');
        const data = await response.json();
        const exercises = data.exercises || [];
        if (!exercises.length) {
            section.style.display = 'none';
            return;
        }
        section.style.display = 'block';
        container.innerHTML = exercises.map(ex => {
            const last = ex.last_weight != null ? `${ex.last_weight} lbs` : '--';
            const prev = ex.previous_weight != null ? `${ex.previous_weight} lbs` : '--';
            let changeText = '—';
            if (ex.change_lbs != null) {
                const arrow = ex.change_dir === 'up' ? '▲' : ex.change_dir === 'down' ? '▼' : '•';
                changeText = `${arrow} ${Math.abs(ex.change_lbs)} lbs`;
            }
            const trend = ex.trend || '--';
            return `
                <div class="overload-card">
                    <div class="overload-header">
                        <span class="overload-name">${escapeHtml(ex.exercise)}</span>
                        <span class="overload-change ${ex.change_dir}">${changeText}</span>
                    </div>
                    <div class="overload-row">
                        <span>Last: ${last}</span>
                        <span>Prev: ${prev}</span>
                    </div>
                    <div class="overload-trend">Trend: ${trend}</div>
                </div>
            `;
        }).join('');
    } catch (error) {
        section.style.display = 'none';
        console.error('Failed to load progressive overload:', error);
    }
}

function renderWorkoutHistory(workouts) {
    const container = document.getElementById('history-container');
    const prs = historyData.personal_records || {};

    if (workouts.length === 0) {
        container.innerHTML = '<p class="empty-state">No workouts logged yet</p>';
        return;
    }

    container.innerHTML = workouts.map((w) => `
        <div class="history-card" id="history-${w.date}" onclick="toggleHistoryDetail(this)">
            <div class="history-header">
                <div class="history-date">${formatDate(w.date)}</div>
                <div class="history-type">${(w.session_type || 'general').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}</div>
            </div>
            <div class="history-summary">
                <span>${w.total_sets} sets</span>
                <span>${((w.total_volume || 0) / 1000).toFixed(1)}K lbs</span>
                <span>${w.duration_minutes || '--'} min</span>
            </div>
            ${w.notes ? `<div class="history-notes-preview"><span class="material-icons" style="font-size:14px;vertical-align:middle;">notes</span> ${escapeHtml(w.notes.substring(0, 40))}${w.notes.length > 40 ? '...' : ''}</div>` : ''}
            <div class="history-detail" style="display:none;">
                ${(w.exercises || []).map(e => `
                    <div class="history-exercise">
                        <strong>${escapeHtml(e.machine)}</strong>
                        ${prs[e.machine] && prs[e.machine].all_time_date === w.date ? `<span class="pr-badge">PR</span>` : ''}
                        ${(e.sets || []).map(s => `<div class="history-set">${s.weight_lbs}lbs x ${s.reps} @ RPE ${s.rpe || '?'}</div>`).join('')}
                    </div>
                `).join('')}
                ${w.notes ? `<div class="history-notes-full"><strong>Notes:</strong> ${escapeHtml(w.notes)}</div>` : ''}
                <button class="btn-danger" onclick="event.stopPropagation(); deleteHistoryItem('workout', ${w._originalIndex})" title="Delete this workout">
                    <span class="material-icons">delete</span> Delete Workout
                </button>
            </div>
        </div>
    `).join('');
}

function renderWorkoutHeatmap(workouts) {
    const heatmap = document.getElementById('history-heatmap');
    if (!heatmap) return;

    const byDate = {};
    (workouts || []).forEach(w => {
        if (!w.date) return;
        byDate[w.date] = (byDate[w.date] || 0) + 1;
    });

    const totalDays = 84;
    const end = new Date();
    const start = new Date();
    start.setDate(end.getDate() - totalDays + 1);

    const cells = [];
    for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
        const dateStr = d.toISOString().slice(0, 10);
        const count = byDate[dateStr] || 0;
        const level = count >= 3 ? 3 : count === 2 ? 2 : count === 1 ? 1 : 0;
        cells.push(`<div class="heatmap-cell level-${level}" data-date="${dateStr}" title="${dateStr} • ${count} workout${count === 1 ? '' : 's'}"></div>`);
    }

    // Add day-of-week labels
    const dayLabels = ['M', '', 'W', '', 'F', '', 'S'];
    const dayLabelHtml = dayLabels.map(d => `<div class="heatmap-day-label">${d}</div>`).join('');
    
    // Add month labels
    const months = [];
    let lastMonth = -1;
    for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
        const m = d.getMonth();
        if (m !== lastMonth) {
            const weekOffset = Math.floor((d - start) / (7 * 86400000));
            months.push({ name: d.toLocaleString('en', { month: 'short' }), offset: weekOffset });
            lastMonth = m;
        }
    }
    const monthLabelHtml = `<div class="heatmap-month-labels">${months.map(m => `<span style="grid-column:${m.offset + 2}">${m.name}</span>`).join('')}</div>`;
    
    heatmap.innerHTML = monthLabelHtml + `<div class="heatmap-grid"><div class="heatmap-day-labels">${dayLabelHtml}</div><div class="heatmap-cells">${cells.join('')}</div></div>`;
    heatmap.querySelectorAll('.heatmap-cell').forEach(cell => {
        cell.addEventListener('click', () => {
            const date = cell.getAttribute('data-date');
            const card = document.getElementById(`history-${date}`);
            if (card) {
                card.scrollIntoView({ behavior: 'smooth', block: 'center' });
                card.classList.add('flash');
                setTimeout(() => card.classList.remove('flash'), 1200);
            }
        });
    });
}

function renderCardioHistory(cardio) {
    const container = document.getElementById('history-container');

    if (cardio.length === 0) {
        container.innerHTML = '<p class="empty-state">No cardio sessions logged yet</p>';
        return;
    }

    container.innerHTML = cardio.map((c) => `
        <div class="cardio-history-card" onclick="toggleHistoryDetail(this)">
            <div class="cardio-history-header">
                <div class="cardio-history-type">
                    <span class="material-icons">directions_run</span>
                    <span class="cardio-history-activity">${escapeHtml(c.activity_type || 'Cardio')}</span>
                </div>
                <div class="cardio-history-date">${formatDate(c.date)}</div>
            </div>
            <div class="cardio-history-stats">
                <span><span class="material-icons">timer</span> ${c.duration_minutes} min</span>
                ${c.avg_heart_rate ? `<span class="cardio-history-hr"><span class="material-icons">favorite</span> ${c.avg_heart_rate} BPM</span>` : ''}
                <span><span class="material-icons">speed</span> ${c.intensity}/10</span>
            </div>
            ${c.notes ? `<div class="history-notes-preview" style="margin-top:8px;">${escapeHtml(c.notes)}</div>` : ''}
            <div class="history-detail" style="display:none;">
                <button class="btn-danger" onclick="event.stopPropagation(); deleteHistoryItem('cardio', ${c._originalIndex})" title="Delete this cardio session">
                    <span class="material-icons">delete</span> Delete Session
                </button>
            </div>
        </div>
    `).join('');
}

function renderRecoveryHistory(recovery) {
    const container = document.getElementById('history-container');

    if (recovery.length === 0) {
        container.innerHTML = '<p class="empty-state">No recovery sessions logged yet</p>';
        return;
    }

    container.innerHTML = recovery.map((r) => `
        <div class="recovery-history-card" onclick="toggleHistoryDetail(this)">
            <div class="recovery-history-header">
                <div class="recovery-history-type">
                    <span class="material-icons">spa</span>
                    <span class="recovery-history-activity">${escapeHtml((r.recovery_type || 'Recovery').replace('_', ' '))}</span>
                </div>
                <div class="recovery-history-date">${formatDate(r.date)}</div>
            </div>
            <div class="recovery-history-stats">
                <span><span class="material-icons">timer</span> ${r.duration_minutes} min</span>
                ${r.temperature ? `<span><span class="material-icons">thermostat</span> ${r.temperature}°F</span>` : ''}
            </div>
            ${r.notes ? `<div class="history-notes-preview" style="margin-top:8px;">${escapeHtml(r.notes)}</div>` : ''}
            <div class="history-detail" style="display:none;">
                <button class="btn-danger" onclick="event.stopPropagation(); deleteHistoryItem('recovery', ${r._originalIndex})" title="Delete this recovery session">
                    <span class="material-icons">delete</span> Delete Session
                </button>
            </div>
        </div>
    `).join('');
}

async function deleteHistoryItem(type, index) {
    if (!confirm('Delete this entry?')) return;

    try {
        await fetch('/api/delete-history', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type, index })
        });

        // Reload history
        loadHistory();
        loadDashboard();

        if (navigator.vibrate) navigator.vibrate(20);
    } catch (error) {
        console.error('Failed to delete:', error);
        alert('Failed to delete entry');
    }
}

function toggleHistoryDetail(card) {
    const detail = card.querySelector('.history-detail');
    if (detail) {
        detail.style.display = detail.style.display === 'none' ? 'block' : 'none';
    }
}

// ==================== Settings Tab ====================

async function loadSettings() {
    try {
        const response = await fetch('/api/settings');
        currentSettings = await response.json();

        renderGoals(currentSettings.available_goals, currentSettings.training_goal);
        renderTimeOptions(currentSettings.time_options, currentSettings.available_time_minutes);
        document.getElementById('sessions-target').value = currentSettings.sessions_per_week_target;
        document.getElementById('target-value').textContent = currentSettings.sessions_per_week_target;
        const calTarget = document.getElementById('settings-calories-target');
        const proteinTarget = document.getElementById('settings-protein-target');
        if (calTarget) calTarget.value = currentSettings.daily_calorie_target || '';
        if (proteinTarget) proteinTarget.value = currentSettings.daily_protein_target_g || '';

        // Update subtitle with current goal
        const goalName = currentSettings.goal_details?.name || 'Training';
        document.getElementById('goal-subtitle').textContent = goalName + ' Mode';

        // Load baseline configuration
        loadBaselines();
        
        // Load protocols
        loadProtocols();
    } catch (error) {
        console.error('Failed to load settings:', error);
    }
}

async function loadProtocols() {
    try {
        const response = await fetch('/api/protocols');
        const data = await response.json();
        const protocols = data.lean_gain;
        
        const container = document.getElementById('protocols-container');
        if (!container) return;
        
        let html = '';
        
        // Protein
        html += `
            <div class="protocol-card" style="background: var(--card-bg); padding: 16px; border-radius: 12px; border: 1px solid var(--border);">
                <h3 style="margin: 0 0 8px 0; font-size: 16px; color: var(--text-primary);">💪 Protein</h3>
                <div style="font-size: 14px; color: var(--text-secondary); line-height: 1.5;">
                    <strong>Target:</strong> ${protocols.protein.target}<br>
                    <strong>Timing:</strong> ${protocols.protein.timing}<br>
                    <strong>Sources:</strong> ${protocols.protein.sources}
                </div>
            </div>
        `;
        
        // Calories
        html += `
            <div class="protocol-card" style="background: var(--card-bg); padding: 16px; border-radius: 12px; border: 1px solid var(--border);">
                <h3 style="margin: 0 0 8px 0; font-size: 16px; color: var(--text-primary);">🔥 Calories</h3>
                <div style="font-size: 14px; color: var(--text-secondary); line-height: 1.5;">
                    <strong>Surplus:</strong> ${protocols.calories.surplus}<br>
                    <em>${protocols.calories.note}</em>
                </div>
            </div>
        `;
        
        // Training
        html += `
            <div class="protocol-card" style="background: var(--card-bg); padding: 16px; border-radius: 12px; border: 1px solid var(--border);">
                <h3 style="margin: 0 0 8px 0; font-size: 16px; color: var(--text-primary);">🏋️ Training</h3>
                <div style="font-size: 14px; color: var(--text-secondary); line-height: 1.5;">
                    <strong>Frequency:</strong> ${protocols.training.frequency}<br>
                    <strong>Volume:</strong> ${protocols.training.volume}<br>
                    <strong>Overload:</strong> ${protocols.training.overload}<br>
                    <strong>Rest:</strong> ${protocols.training.rest}
                </div>
            </div>
        `;
        
        // Sleep
        html += `
            <div class="protocol-card" style="background: var(--card-bg); padding: 16px; border-radius: 12px; border: 1px solid var(--border);">
                <h3 style="margin: 0 0 8px 0; font-size: 16px; color: var(--text-primary);">😴 Sleep</h3>
                <div style="font-size: 14px; color: var(--text-secondary); line-height: 1.5;">
                    <strong>Target:</strong> ${protocols.sleep.target}<br>
                    <strong>Why:</strong> ${protocols.sleep.why}
                </div>
            </div>
        `;
        
        // Hydration
        html += `
            <div class="protocol-card" style="background: var(--card-bg); padding: 16px; border-radius: 12px; border: 1px solid var(--border);">
                <h3 style="margin: 0 0 8px 0; font-size: 16px; color: var(--text-primary);">💧 Hydration</h3>
                <div style="font-size: 14px; color: var(--text-secondary); line-height: 1.5;">
                    <strong>Target:</strong> ${protocols.hydration.target}
                </div>
            </div>
        `;
        
        // Supplements
        html += `
            <div class="protocol-card" style="background: var(--card-bg); padding: 16px; border-radius: 12px; border: 1px solid var(--border);">
                <h3 style="margin: 0 0 8px 0; font-size: 16px; color: var(--text-primary);">💊 Supplements</h3>
                <div style="font-size: 14px; color: var(--text-secondary); line-height: 1.5;">
                    ${protocols.supplements.map(s => `• ${s}`).join('<br>')}
                </div>
            </div>
        `;
        
        // Key Principles
        if (protocols.key_principles && protocols.key_principles.length > 0) {
            html += `
                <div class="protocol-card" style="background: var(--card-bg); padding: 16px; border-radius: 12px; border: 1px solid var(--border);">
                    <h3 style="margin: 0 0 8px 0; font-size: 16px; color: var(--text-primary);">🎯 Key Principles</h3>
                    <ul style="font-size: 14px; color: var(--text-secondary); line-height: 1.6; margin: 0; padding-left: 20px;">
                        ${protocols.key_principles.map(p => `<li>${escapeHtml(p)}</li>`).join('')}
                    </ul>
                </div>
            `;
        }
        
        container.innerHTML = html;
    } catch (error) {
        console.error('Failed to load protocols:', error);
    }
}

function renderTimeOptions(options, currentTime) {
    const container = document.getElementById('time-options-container');
    if (!container || !options) return;

    container.innerHTML = options.map(opt => `
        <div class="time-option ${opt.value === currentTime ? 'active' : ''}" data-time="${opt.value}">
            <span class="time-option-value">${opt.label}</span>
            <span class="time-option-desc">${opt.description.split(' - ')[1] || ''}</span>
        </div>
    `).join('');

    // Add click handlers
    container.querySelectorAll('.time-option').forEach(opt => {
        opt.addEventListener('click', () => selectTime(parseInt(opt.dataset.time)));
    });
}

async function selectTime(timeValue) {
    try {
        await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ available_time_minutes: timeValue })
        });

        // Update UI
        document.querySelectorAll('.time-option').forEach(c => c.classList.remove('active'));
        document.querySelector(`[data-time="${timeValue}"]`)?.classList.add('active');

        // Reload dashboard to get updated recommendations
        loadDashboard();

        if (navigator.vibrate) navigator.vibrate(20);
    } catch (error) {
        console.error('Failed to update time:', error);
    }
}

function renderGoals(goals, currentGoal) {
    const container = document.getElementById('goals-container');
    if (!container) return;

    container.innerHTML = goals.map(g => `
        <div class="goal-card ${g.value === currentGoal ? 'active' : ''}" data-goal="${g.value}">
            <div class="goal-name">${g.name}</div>
            <div class="goal-desc">${g.description}</div>
        </div>
    `).join('');

    // Add click handlers
    container.querySelectorAll('.goal-card').forEach(card => {
        card.addEventListener('click', () => selectGoal(card.dataset.goal));
    });
}

async function selectGoal(goalValue) {
    try {
        await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ training_goal: goalValue })
        });

        // Update UI
        document.querySelectorAll('.goal-card').forEach(c => c.classList.remove('active'));
        document.querySelector(`[data-goal="${goalValue}"]`)?.classList.add('active');

        // Reload dashboard to get updated recommendations
        loadDashboard();
        loadSettings();

        if (navigator.vibrate) navigator.vibrate(20);
    } catch (error) {
        console.error('Failed to update goal:', error);
    }
}

function initSettings() {
    const targetSlider = document.getElementById('sessions-target');
    const targetValue = document.getElementById('target-value');

    if (targetSlider && targetValue) {
        targetSlider.addEventListener('input', async () => {
            targetValue.textContent = targetSlider.value;
            await fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sessions_per_week_target: parseInt(targetSlider.value) })
            });
        });
    }

    const saveNutritionBtn = document.getElementById('save-nutrition-targets');
    if (saveNutritionBtn) {
        saveNutritionBtn.addEventListener('click', async () => {
            const calTarget = document.getElementById('settings-calories-target');
            const proteinTarget = document.getElementById('settings-protein-target');
            const calValue = parseInt(calTarget?.value || 0);
            const proteinValue = parseFloat(proteinTarget?.value || 0);
            if (!calValue || !proteinValue) {
                alert('Please enter calorie and protein targets.');
                return;
            }
            try {
                await fetch('/api/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        daily_calorie_target: calValue,
                        daily_protein_target_g: proteinValue
                    })
                });
                loadDashboard();
                if (navigator.vibrate) navigator.vibrate(20);
            } catch (error) {
                console.error('Failed to update nutrition targets:', error);
            }
        });
    }

    // Export button
    const exportBtn = document.getElementById('export-btn');
    if (exportBtn) {
        exportBtn.addEventListener('click', exportWorkouts);
    }

    const exportAllBtn = document.getElementById('export-all-btn');
    if (exportAllBtn) {
        exportAllBtn.addEventListener('click', exportWorkouts);
    }
}

// ==================== Baseline Configuration ====================

let baselineData = {};

function initBaselineConfig() {
    const saveBtn = document.getElementById('save-baseline-btn');
    if (saveBtn) {
        saveBtn.addEventListener('click', saveBaselines);
    }
}

async function loadBaselines() {
    try {
        const response = await fetch('/api/baselines');
        const data = await response.json();
        baselineData = data;
        renderBaselines(data);
    } catch (error) {
        console.error('Failed to load baselines:', error);
    }
}

function renderBaselines(data) {
    const container = document.getElementById('baseline-container');
    if (!container) return;

    const exercises = data.exercises || [];

    container.innerHTML = exercises.map(ex => `
        <div class="baseline-item">
            <div>
                <div class="baseline-exercise">${ex.name}</div>
                <div class="baseline-muscle">${ex.muscle}</div>
            </div>
            <div style="display:flex;align-items:center;">
                <input type="number" class="baseline-input"
                    data-exercise="${ex.name}"
                    value="${ex.baseline_weight || ''}"
                    placeholder="${ex.suggested || 50}"
                    min="0" step="5">
                <span class="baseline-status ${ex.has_history ? 'has-data' : 'no-data'}">
                    ${ex.has_history ? 'Has Data' : 'No Data'}
                </span>
            </div>
        </div>
    `).join('');
}

async function saveBaselines() {
    const inputs = document.querySelectorAll('.baseline-input');
    const baselines = {};

    inputs.forEach(input => {
        const exercise = input.dataset.exercise;
        const weight = parseInt(input.value) || null;
        if (weight) {
            baselines[exercise] = weight;
        }
    });

    try {
        await fetch('/api/baselines', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ baselines })
        });

        alert('Baselines saved!');
        loadDashboard();
        if (navigator.vibrate) navigator.vibrate(20);
    } catch (error) {
        console.error('Failed to save baselines:', error);
        alert('Failed to save baselines');
    }
}

async function exportWorkouts() {
    try {
        const response = await fetch('/api/export-md');
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'workout_export.md';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
    } catch (error) {
        console.error('Export failed:', error);
        alert('Export failed. Please try again.');
    }
}

// ==================== Backup Functions ====================

async function exportBackup() {
    try {
        const response = await fetch('/api/export-backup');
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        // Get filename from response headers or use default
        const contentDisposition = response.headers.get('Content-Disposition');
        let filename = `fitness_backup_${new Date().toISOString().split('T')[0]}.json`;
        if (contentDisposition) {
            const match = contentDisposition.match(/filename=(.+)/);
            if (match) filename = match[1];
        }
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);

        if (navigator.vibrate) navigator.vibrate(20);
        alert('Backup exported successfully!');
    } catch (error) {
        console.error('Backup export failed:', error);
        alert('Backup export failed. Please try again.');
    }
}

function initBackupButtons() {
    const exportBackupBtn = document.getElementById('export-backup-btn');
    if (exportBackupBtn) {
        exportBackupBtn.addEventListener('click', exportBackup);
    }

    const importBackupBtn = document.getElementById('import-backup-btn');
    const importBackupInput = document.getElementById('import-backup-input');

    if (importBackupBtn && importBackupInput) {
        importBackupBtn.addEventListener('click', () => {
            importBackupInput.click();
        });

        importBackupInput.addEventListener('change', async (e) => {
            const file = e.target.files[0];
            if (!file) return;

            // Confirm before importing
            const confirmed = confirm(
                'Import backup?\n\nThis will REPLACE all your current data with the backup data.\n\nAre you sure you want to continue?'
            );

            if (!confirmed) {
                importBackupInput.value = '';
                return;
            }

            try {
                const text = await file.text();
                const backupData = JSON.parse(text);

                // Validate it looks like a backup
                if (!backupData.data) {
                    alert('Invalid backup file: missing data field');
                    importBackupInput.value = '';
                    return;
                }

                const response = await fetch('/api/import-backup', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(backupData)
                });

                const result = await response.json();

                if (result.status === 'success') {
                    if (navigator.vibrate) navigator.vibrate(20);

                    // Show what was imported
                    const imported = result.imported;
                    let message = 'Backup restored successfully!\n\nImported:';
                    if (imported.workouts) message += `\n- ${imported.workouts} workouts`;
                    if (imported.soreness) message += `\n- ${imported.soreness} soreness entries`;
                    if (imported.cardio) message += `\n- ${imported.cardio} cardio sessions`;
                    if (imported.recovery) message += `\n- ${imported.recovery} recovery sessions`;
                    if (imported.settings) message += `\n- Settings`;
                    if (imported.baselines) message += `\n- ${imported.baselines} baseline weights`;

                    alert(message);

                    // Reload the dashboard to show restored data
                    loadDashboard();
                    loadSettings();
                    loadHistory();
                } else {
                    alert('Import failed: ' + (result.message || 'Unknown error'));
                }
            } catch (error) {
                console.error('Import failed:', error);
                alert('Import failed: ' + error.message);
            }

            // Reset file input
            importBackupInput.value = '';
        });
    }
}

// ==================== Workout Completion ====================

let hasCardioRecommendation = false;

function initWorkoutButtons() {
    const startBtn = document.getElementById('start-workout-btn');
    if (startBtn) {
        startBtn.addEventListener('click', startWorkout);
    }

    const completeBtn = document.getElementById('complete-workout');
    if (completeBtn) {
        completeBtn.addEventListener('click', completeWorkout);
    }

    const cancelBtn = document.getElementById('cancel-workout');
    if (cancelBtn) {
        cancelBtn.addEventListener('click', closeWorkoutModal);
    }

    // Next step button (exercises -> cardio)
    const nextStepBtn = document.getElementById('next-step-btn');
    if (nextStepBtn) {
        nextStepBtn.addEventListener('click', handleNextStep);
    }

    // Back button (cardio -> exercises)
    const backBtn = document.getElementById('back-to-exercises');
    if (backBtn) {
        backBtn.addEventListener('click', () => {
            document.getElementById('workout-step-cardio').style.display = 'none';
            document.getElementById('workout-step-exercises').style.display = 'block';
        });
    }

    // Cardio intensity slider
    const cardioIntensity = document.getElementById('cardio-actual-intensity');
    const cardioIntensityValue = document.getElementById('cardio-actual-intensity-value');
    if (cardioIntensity && cardioIntensityValue) {
        cardioIntensity.addEventListener('input', () => {
            cardioIntensityValue.textContent = cardioIntensity.value;
        });
    }

    // Cardio skipped checkbox
    const cardioSkipped = document.getElementById('cardio-skipped');
    if (cardioSkipped) {
        cardioSkipped.addEventListener('change', () => {
            const inputs = document.querySelectorAll('#workout-step-cardio input:not([type="checkbox"])');
            inputs.forEach(input => {
                input.disabled = cardioSkipped.checked;
                if (cardioSkipped.checked) {
                    input.style.opacity = '0.5';
                } else {
                    input.style.opacity = '1';
                }
            });
        });
    }
}

function closeWorkoutModal() {
    const modal = document.getElementById('workout-modal');
    modal.style.display = 'none';
    // Reset to exercises step
    document.getElementById('workout-step-exercises').style.display = 'block';
    document.getElementById('workout-step-cardio').style.display = 'none';
}

function createSetRow(setNum, weight, reps, rpe) {
    const weightValue = Number.isFinite(weight) ? weight : (weight ?? '');
    const repsValue = Number.isFinite(reps) ? reps : (reps ?? '');
    const rpeValue = Number.isFinite(rpe) ? rpe : (rpe ?? '');
    return `<div class="set-row" data-set="${setNum}">
        <span class="set-label">Set ${setNum}</span>
        <input type="number" class="set-weight" value="${weightValue}" min="0" step="5" placeholder="lbs">
        <span class="set-x">×</span>
        <input type="number" class="set-reps" value="${repsValue}" min="1" max="50" placeholder="reps">
        <input type="number" class="set-rpe" value="${rpeValue}" min="1" max="10" step="0.5" placeholder="RPE">
        <button type="button" class="set-complete" title="Tap to log set">✓</button>
        <button class="btn-remove-set" onclick="removeSetRow(this)">✕</button>
    </div>`;
}

function addSetRow(btn) {
    const container = btn.previousElementSibling;
    const rows = container.querySelectorAll('.set-row');
    const lastRow = rows[rows.length - 1];
    const lastWeight = lastRow ? lastRow.querySelector('.set-weight').value : '';
    const lastReps = lastRow ? lastRow.querySelector('.set-reps').value : '';
    const lastRpe = lastRow ? (parseFloat(lastRow.querySelector('.set-rpe').value) || 7) : 7;
    const newSetNum = rows.length + 1;
    const div = document.createElement('div');
    div.innerHTML = createSetRow(newSetNum, lastWeight === '' ? '' : parseFloat(lastWeight), lastReps === '' ? '' : parseInt(lastReps, 10), lastRpe);
    container.appendChild(div.firstElementChild);
    bindSetRow(container.lastElementChild);
}

function removeSetRow(btn) {
    const container = btn.closest('.exercise-sets-container');
    const rows = container.querySelectorAll('.set-row');
    if (rows.length <= 1) return; // keep at least 1 set
    btn.closest('.set-row').remove();
    // Re-number remaining rows
    container.querySelectorAll('.set-row').forEach((row, i) => {
        row.dataset.set = i + 1;
        row.querySelector('.set-label').textContent = `Set ${i + 1}`;
    });
    updateWorkoutVolume();
}

function startWorkout() {
    if (!currentRecommendation) return;

    const modal = document.getElementById('workout-modal');
    const container = document.getElementById('active-workout-exercises');

    // Check if cardio is recommended
    hasCardioRecommendation = currentRecommendation.cardio && currentRecommendation.cardio.type;

    // Update button text based on whether cardio follows
    const nextBtn = document.getElementById('next-step-btn');
    if (hasCardioRecommendation) {
        nextBtn.textContent = 'Next: Cardio';
        nextBtn.innerHTML = 'Next: Cardio <span class="material-icons" style="font-size:16px;vertical-align:middle;margin-left:4px;">arrow_forward</span>';

        // Pre-fill cardio info
        const cardioInfo = document.getElementById('cardio-recommendation-info');
        const cardio = currentRecommendation.cardio;
        cardioInfo.innerHTML = `
            <div class="cardio-step-header">
                <span class="material-icons">directions_run</span>
                <strong>${cardio.type}</strong>
            </div>
            <div class="cardio-step-details">
                <span>Recommended: ${cardio.duration_minutes} min</span>
                <span>${cardio.zone} (${cardio.heart_rate_range})</span>
            </div>
        `;

        // Pre-fill duration
        document.getElementById('cardio-actual-duration').value = cardio.duration_minutes;
        document.getElementById('cardio-skipped').checked = false;
    } else {
        nextBtn.textContent = 'Complete Workout';
    }

    // Reset steps
    document.getElementById('workout-step-exercises').style.display = 'block';
    document.getElementById('workout-step-cardio').style.display = 'none';

    container.innerHTML = currentRecommendation.exercises.map((ex, i) => {
        const numSets = Math.max(1, ex.target_sets || 3);
        let setRowsHtml = '';
        const defaultWeight = ex.target_weight ?? '';
        const defaultReps = ex.target_reps ?? '';
        for (let s = 1; s <= numSets; s++) {
            setRowsHtml += createSetRow(s, defaultWeight, defaultReps, 7);
        }
        return `
        <div class="active-exercise" data-exercise="${ex.exercise}">
            <div class="active-exercise-header">
                <label>
                    <input type="checkbox" class="exercise-done" checked>
                    ${ex.exercise}
                </label>
            </div>
            <div class="exercise-sets-header">
                <span class="set-col-label">Set</span>
                <span class="set-col-label">lbs</span>
                <span class="set-col-spacer"></span>
                <span class="set-col-label">Reps</span>
                <span class="set-col-label">RPE</span>
                <span class="set-col-remove"></span>
            </div>
            <div class="exercise-sets-container">
                ${setRowsHtml}
            </div>
            <button class="btn-add-set btn-secondary btn-small" onclick="addSetRow(this)">+ Add Set</button>
        </div>
        `;
    }).join('');

    bindWorkoutInteractions(container);
    updateWorkoutVolume();
    modal.style.display = 'flex';
}

function bindWorkoutInteractions(container) {
    container.querySelectorAll('.set-row').forEach(row => bindSetRow(row));
}

function bindSetRow(row) {
    if (row.dataset.bound === 'true') return;
    row.dataset.bound = 'true';

    const completeBtn = row.querySelector('.set-complete');
    if (completeBtn) {
        completeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            toggleSetRow(row);
        });
    }

    row.addEventListener('click', (e) => {
        const tag = e.target.tagName.toLowerCase();
        if (tag === 'input' || e.target.classList.contains('btn-remove-set')) return;
        toggleSetRow(row);
    });

    let touchStartX = null;
    row.addEventListener('touchstart', (e) => {
        touchStartX = e.touches[0].clientX;
    }, { passive: true });

    row.addEventListener('touchend', (e) => {
        if (touchStartX == null) return;
        const touchEndX = e.changedTouches[0].clientX;
        const delta = touchEndX - touchStartX;
        if (delta > 50) {
            copyPreviousSet(row);
        }
        touchStartX = null;
    });

    row.querySelectorAll('input').forEach(input => {
        input.addEventListener('input', updateWorkoutVolume);
    });
}

function toggleSetRow(row) {
    row.classList.toggle('completed');
    updateWorkoutVolume();
}

function copyPreviousSet(row) {
    const container = row.closest('.exercise-sets-container');
    if (!container) return;
    const rows = Array.from(container.querySelectorAll('.set-row'));
    const index = rows.indexOf(row);
    if (index <= 0) return;
    const prev = rows[index - 1];
    const prevWeight = prev.querySelector('.set-weight')?.value;
    const prevReps = prev.querySelector('.set-reps')?.value;
    const prevRpe = prev.querySelector('.set-rpe')?.value;
    if (prevWeight != null) row.querySelector('.set-weight').value = prevWeight;
    if (prevReps != null) row.querySelector('.set-reps').value = prevReps;
    if (prevRpe != null) row.querySelector('.set-rpe').value = prevRpe;
    updateWorkoutVolume();
}

function updateWorkoutVolume() {
    const volumeEl = document.getElementById('workout-volume-total');
    const setCountEl = document.getElementById('workout-set-count');
    if (!volumeEl || !setCountEl) return;

    const rows = Array.from(document.querySelectorAll('.set-row'));
    const completed = rows.filter(r => r.classList.contains('completed'));
    const activeRows = completed.length ? completed : rows;
    let totalVolume = 0;
    let setCount = 0;

    activeRows.forEach(row => {
        const weight = parseFloat(row.querySelector('.set-weight')?.value || 0);
        const reps = parseInt(row.querySelector('.set-reps')?.value || 0, 10);
        if (weight > 0 && reps > 0) {
            totalVolume += weight * reps;
            setCount += 1;
        }
    });

    volumeEl.textContent = Math.round(totalVolume);
    setCountEl.textContent = setCount;
}

function handleNextStep() {
    if (hasCardioRecommendation) {
        // Move to cardio step
        document.getElementById('workout-step-exercises').style.display = 'none';
        document.getElementById('workout-step-cardio').style.display = 'block';
    } else {
        // No cardio, complete directly
        completeWorkout();
    }
}

async function completeWorkout() {
    const modal = document.getElementById('workout-modal');
    const exercises = [];

    document.querySelectorAll('.active-exercise').forEach(el => {
        const done = el.querySelector('.exercise-done').checked;
        if (done) {
            const machine = el.dataset.exercise;
            const sets = [];
            el.querySelectorAll('.set-row').forEach((row, i) => {
                const weightValue = row.querySelector('.set-weight').value;
                const repsValue = row.querySelector('.set-reps').value;
                const rpeValue = row.querySelector('.set-rpe').value;
                const weight = weightValue === '' ? null : parseFloat(weightValue);
                const reps = repsValue === '' ? null : parseInt(repsValue, 10);
                const rpe = rpeValue === '' ? null : parseFloat(rpeValue);
                if (weight !== null || reps !== null || rpe !== null) {
                    sets.push({
                        set_number: i + 1,
                        weight_lbs: weight ?? 0,
                        reps: reps ?? 0,
                        rpe: rpe ?? 7
                    });
                }
            });
            if (sets.length > 0) {
                exercises.push({ machine, muscle_group: 'unknown', sets });
            }
        }
    });

    // Get workout notes
    const notesEl = document.getElementById('workout-notes');
    const workoutNotes = notesEl ? notesEl.value.trim() : '';

    // Log cardio if it was part of the workout
    if (hasCardioRecommendation) {
        const cardioSkipped = document.getElementById('cardio-skipped').checked;

        if (!cardioSkipped) {
            const cardioDuration = parseInt(document.getElementById('cardio-actual-duration').value) || 0;
            const cardioHR = parseInt(document.getElementById('cardio-actual-hr').value) || null;
            const cardioIntensity = parseInt(document.getElementById('cardio-actual-intensity').value) || 5;

            if (cardioDuration > 0) {
                // Log cardio session
                try {
                    await fetch('/api/add-cardio', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            date: new Date().toISOString().split('T')[0],
                            activity_type: currentRecommendation.cardio.type.toLowerCase(),
                            duration_minutes: cardioDuration,
                            avg_heart_rate: cardioHR,
                            intensity: cardioIntensity,
                            notes: `Post-workout ${currentRecommendation.cardio.zone}`
                        })
                    });
                } catch (error) {
                    console.error('Failed to log cardio:', error);
                }
            }
        }
    }

    try {
        const response = await fetch('/api/complete-workout', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                recommendation_id: currentRecommendation?.id,
                exercises: exercises,
                session_type: currentRecommendation?.focus?.toLowerCase() || 'general',
                duration_minutes: currentRecommendation?.estimated_minutes || 45,
                notes: workoutNotes
            })
        });

        const result = await response.json();

        closeWorkoutModal();

        // Clear notes for next time
        if (notesEl) notesEl.value = '';

        // Show feedback
        let message = 'Workout completed!';
        if (hasCardioRecommendation && !document.getElementById('cardio-skipped').checked) {
            message = 'Workout + Cardio logged!';
        }
        if (result.adherence && !result.adherence.followed) {
            message += ` (Skipped: ${result.adherence.skipped.join(', ')})`;
        }
        alert(message);

        // Auto-navigate to history
        navigateToTab('history');
        loadHistory();
        loadDashboard();

    } catch (error) {
        console.error('Failed to complete workout:', error);
        alert('Failed to log workout. Please try again.');
    }
}

// Initialize Forms
function initForms() {
    // Soreness level display
    const sorenessSlider = document.getElementById('soreness-level');
    const sorenessValue = document.getElementById('soreness-value');
    if (sorenessSlider && sorenessValue) {
        sorenessSlider.addEventListener('input', () => {
            sorenessValue.textContent = sorenessSlider.value;
        });
    }

    // RPE display
    const rpeSlider = document.getElementById('workout-rpe');
    const rpeValue = document.getElementById('rpe-value');
    if (rpeSlider && rpeValue) {
        rpeSlider.addEventListener('input', () => {
            rpeValue.textContent = rpeSlider.value;
        });
    }

    // Cardio intensity display
    const cardioIntensitySlider = document.getElementById('cardio-intensity');
    const cardioIntensityValue = document.getElementById('cardio-intensity-value');
    if (cardioIntensitySlider && cardioIntensityValue) {
        cardioIntensitySlider.addEventListener('input', () => {
            cardioIntensityValue.textContent = cardioIntensitySlider.value;
        });
    }

    // Nutrition form submission
    const nutritionForm = document.getElementById('nutrition-form');
    if (nutritionForm) {
        nutritionForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            const data = {
                date: new Date().toISOString().split('T')[0],
                calories: parseInt(document.getElementById('nutrition-calories').value),
                protein_g: parseFloat(document.getElementById('nutrition-protein').value),
                carbs_g: parseFloat(document.getElementById('nutrition-carbs').value) || null,
                fat_g: parseFloat(document.getElementById('nutrition-fat').value) || null,
                notes: document.getElementById('nutrition-notes').value
            };

            try {
                await fetch('/api/add-nutrition', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                });

                alert('Nutrition logged!');
                e.target.reset();
                loadDashboard();
            } catch (error) {
                alert('Failed to log nutrition');
            }
        });
    }

    // Cardio form submission
    const cardioForm = document.getElementById('cardio-form');
    if (cardioForm) {
        cardioForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            const data = {
                date: new Date().toISOString().split('T')[0],
                activity_type: document.getElementById('cardio-type').value,
                duration_minutes: parseInt(document.getElementById('cardio-duration').value),
                avg_heart_rate: parseInt(document.getElementById('cardio-hr').value) || null,
                intensity: parseInt(document.getElementById('cardio-intensity').value),
                notes: document.getElementById('cardio-notes').value
            };

            try {
                await fetch('/api/add-cardio', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                });

                alert('Cardio session logged!');
                e.target.reset();
                if (cardioIntensityValue) cardioIntensityValue.textContent = '5';
                loadDashboard();
            } catch (error) {
                alert('Failed to log cardio');
            }
        });
    }

    // Sauna/Recovery form submission
    const saunaForm = document.getElementById('sauna-form');
    if (saunaForm) {
        saunaForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            const data = {
                date: new Date().toISOString().split('T')[0],
                recovery_type: document.getElementById('recovery-type').value,
                duration_minutes: parseInt(document.getElementById('recovery-duration').value),
                temperature: parseInt(document.getElementById('recovery-temp').value) || null,
                notes: document.getElementById('recovery-notes').value
            };

            try {
                await fetch('/api/add-recovery', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                });

                alert('Recovery session logged!');
                e.target.reset();
                loadDashboard();
            } catch (error) {
                alert('Failed to log recovery session');
            }
        });
    }

    // Body measurement form submission
    const bodyForm = document.getElementById('body-form');
    if (bodyForm) {
        bodyForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            const data = {
                date: new Date().toISOString().split('T')[0],
                weight_lbs: parseFloat(document.getElementById('body-weight-input').value),
                body_fat_pct: parseFloat(document.getElementById('body-fat').value) || null,
                neck_in: parseFloat(document.getElementById('body-neck')?.value) || null,
                waist_in: parseFloat(document.getElementById('body-waist')?.value) || null,
                chest_in: parseFloat(document.getElementById('body-chest')?.value) || null,
                hips_in: parseFloat(document.getElementById('body-hips')?.value) || null,
                arms: (document.getElementById('body-arms')?.value || '').trim(),
                legs: (document.getElementById('body-legs')?.value || '').trim(),
                notes: document.getElementById('body-notes').value
            };

            try {
                await fetch('/api/add-body-measurement', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                });

                alert('Body measurement logged!');
                e.target.reset();
                loadDashboard();
            } catch (error) {
                alert('Failed to log body measurement');
            }
        });
    }


    const navyBtn = document.getElementById('navy-calc-btn');
    if (navyBtn) {
        navyBtn.addEventListener('click', async () => {
            try {
                const payload = {
                    sex: 'male',
                    height_in: 70,
                    neck_in: parseFloat(document.getElementById('body-neck')?.value),
                    waist_in: parseFloat(document.getElementById('body-waist')?.value),
                    hip_in: parseFloat(document.getElementById('body-hips')?.value) || null
                };
                const r = await fetch('/api/body/navy-calc', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
                const j = await r.json();
                if (j.body_fat_pct != null) {
                    document.getElementById('body-fat').value = j.body_fat_pct;
                }
            } catch (e) {
                alert('Failed to estimate body fat');
            }
        });
    }

    const sleepImportForm = document.getElementById('sleep-import-form');
    if (sleepImportForm) {
        sleepImportForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const text = (document.getElementById('sleep-import-text')?.value || '').trim();
            if (!text) return;
            try {
                let payload;
                if (text.startsWith('[') || text.startsWith('{')) {
                    const parsed = JSON.parse(text);
                    payload = { entries: Array.isArray(parsed) ? parsed : (parsed.entries || []) };
                } else {
                    payload = { csv: text };
                }
                const r = await fetch('/api/sleep/import', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
                const j = await r.json();
                alert(`Imported ${j.imported || 0} sleep rows`);
                loadSleepAnalytics();
            } catch (e) {
                alert('Sleep import failed. Check CSV/JSON format.');
            }
        });
    }

    // Soreness form submission
    const sorenessForm = document.getElementById('soreness-form');
    if (sorenessForm) {
        sorenessForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            const data = {
                date: new Date().toISOString().split('T')[0],
                muscle: document.getElementById('soreness-muscle').value,
                soreness_level: parseInt(document.getElementById('soreness-level').value),
                notes: document.getElementById('soreness-notes').value
            };

            try {
                await fetch('/api/add-soreness', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                });

                alert('Soreness logged successfully!');
                e.target.reset();
                sorenessValue.textContent = '5';
                loadDashboard();
            } catch (error) {
                alert('Failed to log soreness');
            }
        });
    }

    // Workout form
    const workoutForm = document.getElementById('workout-form');
    if (workoutForm) {
        workoutForm.addEventListener('submit', (e) => {
            e.preventDefault();
            alert('Set logged! Use the Workout tab for full workout logging.');
            e.target.reset();
            if (rpeValue) rpeValue.textContent = '7';
        });
    }
}

// Format date for display
function formatDate(dateStr) {
    if (!dateStr) return '--';
    const date = new Date(dateStr);
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

// iOS Install Banner
function checkInstallBanner() {
    const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
    const isStandalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone;

    if (isIOS && !isStandalone && !localStorage.getItem('installBannerDismissed')) {
        document.getElementById('install-banner').style.display = 'flex';
    }

    const closeBanner = document.getElementById('close-banner');
    if (closeBanner) {
        closeBanner.addEventListener('click', () => {
            document.getElementById('install-banner').style.display = 'none';
            localStorage.setItem('installBannerDismissed', 'true');
        });
    }
}

// Register Service Worker
function registerServiceWorker() {
    if ('serviceWorker' in navigator) {
        // Unregister old service worker to clear stale cache
        navigator.serviceWorker.getRegistrations().then(regs => regs.forEach(r => r.unregister()));
    }
}

function initOfflineBanner() {
    const banner = document.getElementById('offline-banner');
    if (!banner) return;

    const update = () => {
        banner.style.display = navigator.onLine ? 'none' : 'block';
    };

    window.addEventListener('online', update);
    window.addEventListener('offline', update);
    update();
}

// Pull-to-refresh (native iOS feel)
let touchStartY = 0;
document.addEventListener('touchstart', (e) => {
    touchStartY = e.touches[0].clientY;
});

document.addEventListener('touchmove', (e) => {
    const touchY = e.touches[0].clientY;
    const scrollTop = document.documentElement.scrollTop || document.body.scrollTop;

    if (scrollTop === 0 && touchY > touchStartY + 100) {
        loadDashboard();
    }
});


async function loadBodyRecomp() {
    try {
        const r = await fetch('/api/body-recomp');
        const data = await r.json();
        const summary = data.summary || {};
        const box = document.getElementById('body-summary');
        if (box) {
            box.innerHTML = `
                <div class="kpi-card"><span class="kpi-value">${summary.latest?.weight_lbs || '--'}</span><span class="kpi-label">Current Weight</span></div>
                <div class="kpi-card"><span class="kpi-value">${summary.latest?.body_fat_pct || '--'}%</span><span class="kpi-label">Body Fat</span></div>
                <div class="kpi-card"><span class="kpi-value">${summary.target_weight_lbs || '--'}</span><span class="kpi-label">Target Weight</span></div>
                <div class="kpi-card"><span class="kpi-value">${summary.eta_weeks || '--'}</span><span class="kpi-label">ETA (weeks)</span></div>`;
        }
        if (!data.dates || !data.dates.length) return;
        if (charts.bodyWeightTrend) charts.bodyWeightTrend.destroy();
        const c1 = document.getElementById('bodyWeightTrendChart');
        if (c1) charts.bodyWeightTrend = new Chart(c1, {
            type: 'line',
            data: { labels: data.dates.map(formatDate), datasets: [
                {label:'Weight', data:data.weight, borderColor:'#3b82f6', tension:0.25},
                {label:'7d Avg', data:data.weight_7d_avg, borderColor:'#10b981', tension:0.25}
            ]}, options:{responsive:true, maintainAspectRatio:false}
        });
        if (charts.bodyComp) charts.bodyComp.destroy();
        const c2 = document.getElementById('bodyCompositionChart');
        if (c2) charts.bodyComp = new Chart(c2, {
            type: 'line',
            data: { labels: data.dates.map(formatDate), datasets: [
                {label:'Lean Mass', data:data.lean_mass_lbs, borderColor:'#22c55e', tension:0.25},
                {label:'Fat Mass', data:data.fat_mass_lbs, borderColor:'#ef4444', tension:0.25}
            ]}, options:{responsive:true, maintainAspectRatio:false}
        });
    } catch (e) { console.error('loadBodyRecomp', e); }
}

async function loadSleepAnalytics() {
    try {
        const r = await fetch('/api/sleep/analytics');
        const data = await r.json();
        const el = document.getElementById('sleep-summary');
        if (el) {
            el.innerHTML = `Consistency: <strong>${data.consistency_score ?? '--'}</strong>/100<br>Sleep→Next-Day Performance Correlation: <strong>${data.sleep_perf_correlation ?? '--'}</strong>`;
        }
    } catch (e) { console.error(e); }
}

async function loadAdvancedAnalytics() {
    try {
        const r = await fetch('/api/analytics/advanced');
        const data = await r.json();
        const deload = document.getElementById('deload-container');
        if (deload) {
            deload.innerHTML = `Fatigue: <strong>${data.fatigue_score}</strong>/100 • Mesocycle week: <strong>${data.mesocycle_weeks}</strong><br>Deload: <strong>${data.deload_recommended ? 'Recommended' : 'Not needed'}</strong>`;
        }
    } catch (e) { console.error(e); }
}

// Global Chart.js defaults for better label display
Chart.defaults.scales.category.ticks.maxRotation = 45;
Chart.defaults.scales.category.ticks.autoSkip = true;
Chart.defaults.scales.category.ticks.maxTicksLimit = 10;
Chart.defaults.color = '#9ca3af';
Chart.defaults.borderColor = 'rgba(255,255,255,0.06)';
