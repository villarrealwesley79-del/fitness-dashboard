// Fitness Dashboard - Mobile App JavaScript

let currentRecommendation = null;
let currentSettings = null;
let charts = {};

document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    initForms();
    initSettings();
    initWorkoutButtons();
    loadDashboard();
    loadSettings();
    loadHistory();
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
            if (tabId === 'graphs') {
                loadInsights();
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

        updateHeadlineKPIs(data.headline);
        updateAlerts(data.alerts);
        updateMuscleGroups(data.muscles);
        updateExercises(data.exercises);
        updateNextWorkout(data.next_workout);

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

// Update Headline KPIs
function updateHeadlineKPIs(headline) {
    document.getElementById('total-sets').textContent = headline.total_sets;
    document.getElementById('progression').textContent = `${headline.improving}/${headline.total_exercises}`;
    document.getElementById('readiness').textContent = `${headline.avg_readiness}/10`;
    document.getElementById('sessions').textContent = headline.sessions;
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

// Update Exercises
function updateExercises(exercises) {
    const container = document.getElementById('exercise-container');

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

    const container = document.getElementById('workout-exercises');
    container.innerHTML = workout.exercises.map((ex, i) => `
        <div class="workout-card">
            <div class="workout-exercise-header">
                <span class="workout-exercise-name">${i + 1}. ${ex.exercise}</span>
                <span class="workout-muscle">${ex.muscle}</span>
            </div>
            <div class="workout-target">${ex.target_weight} lbs x ${ex.target_reps} reps x ${ex.target_sets} sets</div>
            <div class="workout-rationale">${ex.rationale}</div>
            <div class="workout-rest">Rest: ${ex.rest_minutes} min${ex.rpe_target ? ` | Target RPE: ${ex.rpe_target}` : ''}${ex.estimated_time ? ` | ~${ex.estimated_time} min` : ''}</div>
        </div>
    `).join('');

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

    if (currentStreak) currentStreak.textContent = consistency.current_streak;
    if (longestStreak) longestStreak.textContent = consistency.longest_streak;
    if (weeklyAvg) weeklyAvg.textContent = consistency.weekly_avg;
    if (consistencyPct) consistencyPct.textContent = consistency.consistency_pct + '%';
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
    if (pushPullCtx && chartData.push_pull) {
        charts.pushPull = new Chart(pushPullCtx, {
            type: 'doughnut',
            data: {
                labels: ['Push', 'Pull'],
                datasets: [{
                    data: [chartData.push_pull.push, chartData.push_pull.pull],
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

// ==================== History Tab ====================

async function loadHistory() {
    try {
        const response = await fetch('/api/history');
        const data = await response.json();

        document.getElementById('history-total').textContent = data.count;

        const totalVolume = data.workouts.reduce((sum, w) => sum + w.total_volume, 0);
        document.getElementById('history-volume').textContent = (totalVolume / 1000).toFixed(0) + 'K';

        renderHistoryList(data.workouts);
    } catch (error) {
        console.error('Failed to load history:', error);
    }
}

function renderHistoryList(workouts) {
    const container = document.getElementById('history-container');
    if (!container) return;

    if (workouts.length === 0) {
        container.innerHTML = '<p class="empty-state">No workouts logged yet</p>';
        return;
    }

    container.innerHTML = workouts.map(w => `
        <div class="history-card" onclick="toggleHistoryDetail(this)">
            <div class="history-header">
                <div class="history-date">${formatDate(w.date)}</div>
                <div class="history-type">${w.session_type}</div>
            </div>
            <div class="history-summary">
                <span>${w.total_sets} sets</span>
                <span>${(w.total_volume / 1000).toFixed(1)}K lbs</span>
                <span>${w.duration_minutes || '--'} min</span>
            </div>
            <div class="history-detail" style="display:none;">
                ${w.exercises.map(e => `
                    <div class="history-exercise">
                        <strong>${e.machine}</strong>
                        ${e.sets.map(s => `<div class="history-set">${s.weight_lbs}lbs x ${s.reps} @ RPE ${s.rpe || '?'}</div>`).join('')}
                    </div>
                `).join('')}
            </div>
        </div>
    `).join('');
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

        // Update subtitle with current goal
        const goalName = currentSettings.goal_details?.name || 'Training';
        document.getElementById('goal-subtitle').textContent = goalName + ' Mode';
    } catch (error) {
        console.error('Failed to load settings:', error);
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

// ==================== Workout Completion ====================

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
        cancelBtn.addEventListener('click', () => {
            document.getElementById('workout-modal').style.display = 'none';
        });
    }
}

function startWorkout() {
    if (!currentRecommendation) return;

    const modal = document.getElementById('workout-modal');
    const container = document.getElementById('active-workout-exercises');

    container.innerHTML = currentRecommendation.exercises.map((ex, i) => `
        <div class="active-exercise" data-exercise="${ex.exercise}">
            <div class="active-exercise-header">
                <label>
                    <input type="checkbox" class="exercise-done" checked>
                    ${ex.exercise}
                </label>
            </div>
            <div class="active-exercise-inputs">
                <input type="number" class="weight-input" value="${ex.target_weight}" placeholder="Weight">
                <input type="number" class="reps-input" value="${ex.target_reps}" placeholder="Reps">
                <input type="number" class="sets-input" value="${ex.target_sets}" placeholder="Sets">
            </div>
        </div>
    `).join('');

    modal.style.display = 'flex';
}

async function completeWorkout() {
    const modal = document.getElementById('workout-modal');
    const exercises = [];

    document.querySelectorAll('.active-exercise').forEach(el => {
        const done = el.querySelector('.exercise-done').checked;
        if (done) {
            const machine = el.dataset.exercise;
            const weight = parseFloat(el.querySelector('.weight-input').value) || 0;
            const reps = parseInt(el.querySelector('.reps-input').value) || 0;
            const numSets = parseInt(el.querySelector('.sets-input').value) || 0;

            const sets = [];
            for (let i = 1; i <= numSets; i++) {
                sets.push({ set_number: i, weight_lbs: weight, reps: reps, rpe: 7 });
            }

            exercises.push({ machine, muscle_group: 'unknown', sets });
        }
    });

    try {
        const response = await fetch('/api/complete-workout', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                recommendation_id: currentRecommendation?.id,
                exercises: exercises,
                session_type: currentRecommendation?.focus?.toLowerCase() || 'general',
                duration_minutes: 45
            })
        });

        const result = await response.json();

        modal.style.display = 'none';

        // Show feedback
        if (result.adherence && !result.adherence.followed) {
            alert(`Workout logged! You skipped: ${result.adherence.skipped.join(', ')}`);
        } else {
            alert('Workout completed!');
        }

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
        navigator.serviceWorker.register('/sw.js')
            .then(reg => console.log('Service Worker registered'))
            .catch(err => console.log('Service Worker registration failed:', err));
    }
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
