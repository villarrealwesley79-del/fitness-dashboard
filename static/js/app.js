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
        updateAlerts(data.alerts);
        updateMuscleGroups(data.muscles);
        updateExercises(data.exercises);
        updateNextWorkout(data.next_workout);

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
                const source = status.source ? `Source: ${status.source}` : '';
                noteEl.textContent = source;
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
        setVal('sleep-last-night-score', lastNight.sleep_score);
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

        // Render 7-day trend chart
        if (data.trend_data && data.trend_data.length > 0) {
            renderSleepTrendChart(data.trend_data);
        }

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
            const factors = [];
            const rf = data.readiness_factors;
            
            // ACWR
            if (rf.acwr) {
                const acwrClass = rf.acwr.risk === 'optimal' ? 'positive' : 
                                  rf.acwr.risk === 'high' ? 'negative' : 'neutral';
                factors.push(`<div class="reasoning-factor ${acwrClass}">
                    <span class="icon">📈</span>
                    <span>ACWR: ${rf.acwr.acwr.toFixed(2)} (${rf.acwr.risk})</span>
                </div>`);
            }
            
            // Sleep Debt
            if (rf.sleep_debt) {
                const sleepClass = rf.sleep_debt.status === 'good' ? 'positive' : 
                                   rf.sleep_debt.status === 'severe' ? 'negative' : 'neutral';
                factors.push(`<div class="reasoning-factor ${sleepClass}">
                    <span class="icon">😴</span>
                    <span>Sleep debt: ${rf.sleep_debt.debt_hours.toFixed(1)}h (${rf.sleep_debt.status})</span>
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

    if (!container) return;

    if (currentHistoryFilter === 'workouts') {
        const allWorkouts = historyData.workouts || [];
        const workouts = filterByDateRange(allWorkouts);
        totalEl.textContent = workouts.length;
        totalLabel.textContent = 'Total Workouts';
        const totalVolume = workouts.reduce((sum, w) => sum + (w.total_volume || 0), 0);
        volumeEl.textContent = (totalVolume / 1000).toFixed(0) + 'K';
        volumeLabel.textContent = 'Total Volume';
        renderWorkoutHistory(workouts);
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

function renderWorkoutHistory(workouts) {
    const container = document.getElementById('history-container');

    if (workouts.length === 0) {
        container.innerHTML = '<p class="empty-state">No workouts logged yet</p>';
        return;
    }

    container.innerHTML = workouts.map((w) => `
        <div class="history-card" onclick="toggleHistoryDetail(this)">
            <div class="history-header">
                <div class="history-date">${formatDate(w.date)}</div>
                <div class="history-type">${w.session_type}</div>
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

    container.innerHTML = currentRecommendation.exercises.map((ex, i) => `
        <div class="active-exercise" data-exercise="${ex.exercise}">
            <div class="active-exercise-header">
                <label>
                    <input type="checkbox" class="exercise-done" checked>
                    ${ex.exercise}
                </label>
            </div>
            <div class="active-exercise-inputs">
                <div class="active-exercise-input-group">
                    <label>Weight (lbs)</label>
                    <input type="number" class="weight-input" value="${ex.target_weight}" placeholder="Weight">
                </div>
                <div class="active-exercise-input-group">
                    <label>Reps</label>
                    <input type="number" class="reps-input" value="${ex.target_reps}" placeholder="Reps">
                </div>
                <div class="active-exercise-input-group">
                    <label>Sets</label>
                    <input type="number" class="sets-input" value="${ex.target_sets}" placeholder="Sets">
                </div>
            </div>
        </div>
    `).join('');

    modal.style.display = 'flex';
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
