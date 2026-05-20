/**
 * dashboard.js — Data layer for Fitness Dashboard
 * Wires all frontend element IDs to their backend API endpoints.
 * Previously, the HTML had placeholder "--" values and no JS to populate them.
 */

(function () {
  "use strict";

  // ── Helpers ──────────────────────────────────────────────────────────
  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
  function fmt(v, suffix) { return v != null ? (suffix ? v + suffix : v) : "--"; }
  function fmtNum(v, dec) { return v != null ? Number(v).toFixed(dec ?? 0) : "--"; }

  function el(id) { var e = $(id); return e; }

  function setText(id, text) { var e = $(id); if (e) e.textContent = text; }
  function setHTML(id, html) { var e = $(id); if (e) e.innerHTML = html; }
  function show(id) { var e = $(id); if (e) e.style.display = ""; }
  function hide(id) { var e = $(id); if (e) e.style.display = "none"; }

  var goalLabels = {strength:"Strength",hypertrophy:"Hypertrophy",endurance:"Endurance",weight_loss:"Weight Loss",strength_hypertrophy:"Strength + Hypertrophy",hypertrophy_endurance:"Hypertrophy + Endurance",toning:"Toning",weight_loss_toning:"Weight Loss + Toning"};

  function ago(dateStr) {
    if (!dateStr) return "";
    var d = new Date(dateStr);
    if (isNaN(d)) return dateStr;
    var ms = Date.now() - d.getTime();
    if (ms < 0) return "just now"; // clock drift guard
    var mins = Math.floor(ms / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return mins + "m ago";
    var hrs = Math.floor(mins / 60);
    if (hrs < 24) return hrs + "h ago";
    var days = Math.floor(hrs / 24);
    if (days === 1) return "yesterday";
    return days + "d ago";
  }

  // Expose to other IIFE bundles (e.g. app.js) without polluting globals.
  window.__dashHelpers = window.__dashHelpers || {};
  window.__dashHelpers.ago = ago;

  // ── Dashboard Tab ───────────────────────────────────────────────────
  function loadDashboard() {
    fetch("/api/dashboard").then(function(r) { return r.json(); }).then(function(data) {
      // Readiness gauge
      var readiness = data.headline && data.headline.avg_readiness;
      setText("readiness-gauge-score", fmtNum(readiness, 0));

      // Headline KPIs
      if (data.headline) {
        setText("total-sets", fmt(data.headline.total_sets));
        setText("progression", fmt(data.headline.improving) + " improving");
        setText("readiness", fmtNum(readiness, 1));
        setText("sessions", fmt(data.headline.sessions_this_week, " this week"));
        setText("current-streak-headline", fmt(data.headline.current_streak, " 🔥"));
      }

      // Recomp command center
      if (data.recomp_command) {
        var sig = data.recomp_command.signal || "--";
        var el = $("recomp-signal");
        if (el) {
          el.textContent = sig;
          el.className = "recomp-badge " + (sig === "TRAIN" ? "badge-train" : sig === "RECOVER" ? "badge-recover" : "badge-moderate");
        }
        setText("recomp-reason", data.recomp_command.reason || "");
      }

      // Goal banner
      if (data.body_stats) {
        setText("goal-weight-current", fmt(data.body_stats.latest_weight, " lbs"));
        var gw = data.body_stats.latest_weight ? (data.body_stats.latest_weight - 175).toFixed(1) : "--";
        setText("goal-weight-remaining", gw > 0 ? gw + " lbs" : "at goal!");
        if (data.body_stats.latest_body_fat != null) {
          setText("goal-bf-current", data.body_stats.latest_body_fat.toFixed(1) + "%");
          var gbf = (data.body_stats.latest_body_fat - 18).toFixed(1);
          setText("goal-bf-remaining", gbf > 0 ? gbf + "%" : "at goal!");
        }
        setText("body-weight", fmtNum(data.body_stats.latest_weight, 1));
      }

      // Readiness factors
      if (data.readiness_factors) {
        setText("oura-readiness", fmtNum(readiness, 0));
        var rf = data.readiness_factors;
      }
      // hrv_trend is at top level of /api/dashboard response, not inside readiness_factors
      var dashHrv = data.hrv_trend;
      if (dashHrv && dashHrv !== "unknown") setText("oura-hrv", dashHrv);

      // Streak
      if (data.headline) {
        var streak = data.headline.current_streak || 0;
        setText("streak-count", streak);
        setText("streak-meta", streak > 0 ? streak + " day streak" : "No streak");
      }

      // Next workout
      if (data.next_workout) {
        var nw = data.next_workout;
        window.currentRecommendation = nw;
        renderDashboardWorkout(nw);
      }

      // Recovery card (Oura data on dashboard)
      loadOuraRecovery();
      loadRecommendation();
      loadVitalsMini();

    }).catch(function (err) {
      console.error("[dashboard] Failed to load dashboard:", err);
    });
  }

  // ── Oura Recovery (Dashboard card) ──────────────────────────────────
  function loadOuraRecovery() {
    fetch("/api/oura/status").then(r => r.json()).then(data => {
      if (data.error) return;
      // Wire temperature deviation and HRV trend from Oura status
      if (data.temperature_deviation != null) setText("oura-temp", (data.temperature_deviation > 0 ? "+" : "") + data.temperature_deviation.toFixed(1) + "°");
      if (data.hrv_trend && data.hrv_trend !== "unknown") setText("oura-hrv", data.hrv_trend);
      // /api/oura/status upserts today's metrics into the DB on first call
      // of the day. Re-fetch vitals so the mini and full panels pick up the
      // freshly-synced values instead of staying on "--".
      loadVitalsMini();
      if (typeof loadVitals === "function") loadVitals();
    }).catch(function () {});
  }

  function loadRecommendation() {
    fetch("/api/recommendation/smart").then(r => r.json()).then(data => {
      setText("readiness-gauge-status", (data.recommendation || "--").toUpperCase());
      // Override headline.avg_readiness (which is a workout-volume metric) with the real Oura readiness
      var realReadiness = data.effective_readiness != null ? data.effective_readiness : data.readiness;
      if (realReadiness != null) {
        setText("readiness-gauge-score", Math.round(realReadiness));
        setText("oura-readiness", Math.round(realReadiness));
        setText("readiness", realReadiness.toFixed(1));
      }
      var note = [];
      if (data.effective_readiness != null) note.push("Readiness " + data.effective_readiness);
      if (data.hrv_trend && data.hrv_trend !== "unknown") {
        note.push("HRV " + data.hrv_trend);
        setText("oura-hrv", data.hrv_trend);
      }
      if (data.suggested_workout) note.push(data.suggested_workout);
      setText("readiness-gauge-note", note.join(" · ") || data.reason || "");

      // Color the readiness gauge
      var gauge = $("readiness-gauge");
      if (gauge) {
        gauge.className = "readiness-gauge " +
          (data.recommendation === "intensity" ? "readiness-high" :
           data.recommendation === "recovery" ? "readiness-low" : "readiness-moderate");
      }
    }).catch(function () {});
  }

  // ── Mini vitals on dashboard ────────────────────────────────────────
  function loadVitalsMini() {
    fetch("/api/vitals").then(function(r) { return r.json(); }).then(function(data) {
      if (data.weight) {
        setText("vitals-mini-weight", fmtNum(data.weight.current_lbs, 1));
      }
      if (data.heart_rate) {
        setText("vitals-mini-rhr", data.heart_rate.resting_bpm != null ? fmtNum(data.heart_rate.resting_bpm, 0) + " bpm" : "--");
      }
      if (data.sleep) {
        var hrs = data.sleep.avg_7d_hours;
        setText("vitals-mini-sleep", hrs != null ? Number(hrs).toFixed(1) + "h" : "--");
      }
      if (data.activity) {
        var st = data.activity.steps_today;
        setText("vitals-mini-steps", st != null ? Number(st).toLocaleString() : "--");
      }

      // Recovery card (Oura/vitals section on dashboard)
      if (data.activity) {
        setText("oura-steps", fmtNum(data.activity.steps_today, 0));
        setText("oura-activity-score", fmt(data.activity.active_calories_today));
        setText("vitals-steps", data.activity.steps_today != null ? Number(data.activity.steps_today).toLocaleString() : "--");
        setText("vitals-active-cal", data.activity.active_calories_today != null ? fmtNum(data.activity.active_calories_today, 0) : "--");
      }
      if (data.heart_rate) {
        setText("oura-rhr", data.heart_rate.resting_bpm != null ? fmtNum(data.heart_rate.resting_bpm, 0) + " bpm" : "--");
        setText("vitals-rhr", data.heart_rate.resting_bpm != null ? fmtNum(data.heart_rate.resting_bpm, 0) + " bpm" : "--");
      }
      function applySleep(ln) {
        if (!ln) return;
        var dur = ln.total_hours || ln.duration_hours || (ln.total_sleep_min ? ln.total_sleep_min / 60 : 0);
        if (dur) {
          setText("oura-sleep-total", fmtNum(dur, 1) + "h");
          setText("oura-sleep-duration", fmtNum(dur, 1) + "h");
        }
        if (ln.deep_sleep_min != null) setText("oura-sleep-deep", fmtNum(ln.deep_sleep_min / 60, 1) + "h");
        else if (ln.deep_min != null) setText("oura-sleep-deep", fmtNum(ln.deep_min / 60, 1) + "h");
        else if (ln.deep_hours != null) setText("oura-sleep-deep", fmtNum(ln.deep_hours, 1) + "h");
        if (ln.rem_sleep_min != null) setText("oura-sleep-rem", fmtNum(ln.rem_sleep_min / 60, 1) + "h");
        else if (ln.rem_min != null) setText("oura-sleep-rem", fmtNum(ln.rem_min / 60, 1) + "h");
        else if (ln.rem_hours != null) setText("oura-sleep-rem", fmtNum(ln.rem_hours, 1) + "h");
        if (ln.light_sleep_min != null) setText("oura-sleep-light", fmtNum(ln.light_sleep_min / 60, 1) + "h");
        else if (ln.light_min != null) setText("oura-sleep-light", fmtNum(ln.light_min / 60, 1) + "h");
        else if (ln.light_hours != null) setText("oura-sleep-light", fmtNum(ln.light_hours, 1) + "h");
        if (ln.awake_min != null) setText("oura-sleep-awake", fmtNum(ln.awake_min / 60, 1) + "h");
        else if (ln.awake_hours != null) setText("oura-sleep-awake", fmtNum(ln.awake_hours, 1) + "h");
        var score = (ln.quality_score != null ? ln.quality_score : ln.sleep_score);
        if (score != null && score !== 0) setText("oura-sleep-score", score);
      }
      if (data.sleep && data.sleep.last_night) {
        applySleep(data.sleep.last_night);
      }
      // Fallback: fetch sleep-summary if dashboard has no sleep data or sleep score
      var needsSleep = !data.sleep || !data.sleep.last_night
        || !((data.sleep.last_night.quality_score || data.sleep.last_night.sleep_score));
      if (needsSleep) {
        fetch("/api/oura/sleep-summary").then(r => r.json()).then(sdata => {
          if (sdata.last_night) applySleep(sdata.last_night);
        }).catch(() => {});
      }
      if (data.source) {
        var src = data.source;
        var srcNote = [];
        if (src.activity) srcNote.push("Activity: " + src.activity);
        if (src.sleep) srcNote.push("Sleep: " + src.sleep);
        if (src.body) srcNote.push("Body: " + src.body);
        setText("oura-recovery-note", srcNote.join(" | "));
      }
    }).catch(function () {});
  }

  // ── Vitals Tab ──────────────────────────────────────────────────────
  function loadVitals() {
    fetch("/api/vitals").then(r => r.json()).then(data => {
      // Weight card
      if (data.weight) {
        setText("vitals-weight-current", fmtNum(data.weight.current_lbs, 1) + " lbs");
        if (data.weight.change_7d != null) {
          var c7 = data.weight.change_7d;
          setText("vitals-weight-change", (c7 > 0 ? "+" : "") + fmtNum(c7, 1) + " lbs (7d)");
        }
        if (data.weight.body_fat_pct != null) {
          setText("vitals-weight-bodyfat", fmtNum(data.weight.body_fat_pct, 1) + "% BF");
        }
        // Sparkline chart
        drawSparkline("vitals-weight-chart", (data.weight.trend_7d || []).map(p => p.weight_lbs), "#4361ee");
        drawDetailChart("vitals-weight-detail-chart", (data.weight.trend_30d || []).map(p => ({ x: p.date, y: p.weight_lbs })), "Weight (lbs)");
      }

      // Heart rate card
      if (data.heart_rate) {
        setText("vitals-hr-resting", data.heart_rate.resting_bpm != null ? fmtNum(data.heart_rate.resting_bpm, 0) + " bpm" : "--");
        setText("vitals-hr-average", data.heart_rate.average_bpm != null ? fmtNum(data.heart_rate.average_bpm, 0) + " bpm avg" : "--");
        var hrZone = data.heart_rate.resting_bpm == null ? "--" : data.heart_rate.resting_bpm < 60 ? "Athlete" : data.heart_rate.resting_bpm < 70 ? "Good" : "Elevated";
        setText("vitals-hr-zone", hrZone);
        drawSparkline("vitals-hr-chart", (data.heart_rate.trend_7d || []).map(p => p.resting), "#e040fb");
        drawDetailChart("vitals-hr-detail-chart", (data.heart_rate.trend_7d || []).map(p => ({ x: p.date, y: p.resting })), "RHR (bpm)");
      }

      // Sleep card
      if (data.sleep) {
        var hrs = data.sleep.avg_7d_hours;
        setText("vitals-sleep-duration", data.sleep.last_night ? fmtNum(data.sleep.last_night.total_hours || data.sleep.last_night.duration_hours || (data.sleep.last_night.total_sleep_min ? data.sleep.last_night.total_sleep_min / 60 : 0), 1) + "h" : "--");
        setText("vitals-sleep-avg", hrs != null ? "avg " + Number(hrs).toFixed(1) + "h" : "");
        if (data.sleep.quality_score != null) {
          setText("vitals-sleep-quality", Math.round(data.sleep.quality_score || data.sleep.sleep_score));
        }
        // Sleep bar segments
        if (data.sleep.last_night) {
          var ln = data.sleep.last_night;
          setBarPct("sleep-seg-deep", ln.deep_pct || ln.deep_hours / (ln.total_hours || 1) * 100);
          setBarPct("sleep-seg-rem", ln.rem_pct || ln.rem_hours / (ln.total_hours || 1) * 100);
          setBarPct("sleep-seg-light", ln.light_pct || ln.light_hours / (ln.total_hours || 1) * 100);
          setBarPct("sleep-seg-awake", ln.awake_pct || ln.awake_hours / (ln.total_hours || 1) * 100);
        }
      }

      // Activity card
      if (data.activity) {
        var steps = data.activity.steps_today;
        setText("vitals-steps-today", steps != null ? Number(steps).toLocaleString() : "--");
        setText("vitals-active-calories", data.activity.active_calories_today != null ? fmtNum(data.activity.active_calories_today, 0) : "--");
        setText("vitals-active-minutes", data.activity.active_minutes_today != null ? fmtNum(data.activity.active_minutes_today, 0) : "--");
        setText("vitals-steps-avg", data.activity.steps_avg_7d != null ? fmtNum(data.activity.steps_avg_7d, 0) : "--");
        // Progress ring
        var ring = $("vitals-steps-ring");
        if (ring) ring.style.setProperty("--progress", Math.min(100, Math.round(((steps || 0) / 8000) * 100)) + "%");
      }
    }).catch(function (err) {
      console.error("[vitals] Failed:", err);
    });
  }

  function setBarPct(id, pct) {
    var e = $(id);
    if (e) e.style.width = Math.max(0, Math.min(100, pct || 0)) + "%";
  }

  // ── Analytics Tab ───────────────────────────────────────────────────
  function loadAnalytics() {
    // Insights
    fetch("/api/insights").then(function(r) { return r.json(); }).then(function(data) {
      var container = $("insights-container");
      if (!container || !data.insights) return;
      container.innerHTML = data.insights.map(function (ins) {
        var iconHTML = ins.icon ? '<span class="material-icons insight-icon ' + esc(ins.type || "info") + '">' + esc(ins.icon) + '</span>' : '';
        return '<div class="glass-card insight-card">' +
          iconHTML +
          '<div class="insight-content">' +
          (ins.title ? '<div class="insight-title">' + esc(ins.title) + '</div>' : '') +
          (ins.detail ? '<div class="insight-detail">' + esc(ins.detail) + '</div>' : '') +
          '</div></div>';
      }).join("");
    }).catch(function () {});

    // Adherence / consistency
    fetch("/api/adherence").then(function(r) { return r.json(); }).then(function(data) {
      // API returns flat keys; map to consistency display
      var streak = data.current_streak || 0;
      var longest = data.longest_streak || 0;
      var weeklyAvg = data.weekly_avg || data.avg_per_week || 0;
      var pct = data.adherence_rate != null ? data.adherence_rate : 0;
      // Also support nested data.consistency if backend adds it later
      if (data.consistency) {
        streak = data.consistency.current_streak || streak;
        longest = data.consistency.longest_streak || longest;
        weeklyAvg = data.consistency.weekly_avg || weeklyAvg;
        pct = data.consistency.consistency_pct != null ? data.consistency.consistency_pct : pct;
      }
      setText("current-streak", fmt(streak) + " days");
      setText("longest-streak", fmt(longest) + " days");
      setText("weekly-avg", fmtNum(weeklyAvg, 1));
      setText("consistency-pct", fmtNum(pct, 0) + "%");
    }).catch(function () {});

    // Charts need /api/dashboard data for progression/volume
    loadAnalyticsCharts();

    // Personal Records
    fetch("/api/history-all").then(function(r) { return r.json(); }).then(function(data) {
      var prs = data.personal_records || {};
      var container = $("prs-container");
      if (!container) return;
      var keys = Object.keys(prs);
      if (!keys.length) { container.innerHTML = '<p class="no-data">No PRs yet. Log workouts to track personal records.</p>'; return; }
      container.innerHTML = keys.map(function(ex) {
        var pr = prs[ex];
        return '<div class="pr-card"><div class="pr-exercise">' + esc(ex) + '</div>' +
          '<div class="pr-stats">' +
          (pr.e1rm ? '<span class="pr-stat">e1RM: ' + fmt(pr.e1rm) + ' lbs</span>' : '') +
          (pr.best_weight ? '<span class="pr-stat">Best: ' + fmt(pr.best_weight) + ' lbs</span>' : '') +
          (pr.best_reps ? '<span class="pr-stat">Best reps: ' + pr.best_reps + '</span>' : '') +
          '</div></div>';
      }).join("");
    }).catch(function () {});

    // Push/Pull balance
    fetch("/api/history-all").then(function(r) { return r.json(); }).then(function(data) {
      var analytics = data.analytics || {};
      var pp = analytics.push_pull;
      var canvas = document.getElementById("pushPullChart");
      if (!canvas) return;
      // Hide loading state
      var loadingEl = canvas.parentElement.querySelector('.chart-loading-state');
      if (loadingEl) loadingEl.style.display = 'none';
      if (!pp || (!pp.push_volume && !pp.pull_volume)) {
        hide("pushPullChart"); show("pushpull-empty");
        return;
      }
      // Simple push/pull bar
      var pushVol = pp.push_volume || 0;
      var pullVol = pp.pull_volume || 0;
      var total = pushVol + pullVol || 1;
      var ctx = canvas.getContext('2d');
      if (!ctx) return;
      var w = canvas.parentElement.clientWidth || 300;
      var h = 160;
      canvas.width = w; canvas.height = h;
      // Draw bars
      ctx.fillStyle = 'rgba(16, 185, 129, 0.7)';
      ctx.fillRect(20, 20, (w - 40) * (pushVol / total), 40);
      ctx.fillStyle = 'rgba(99, 102, 241, 0.7)';
      ctx.fillRect(20, 80, (w - 40) * (pullVol / total), 40);
      // Labels
      ctx.fillStyle = '#fff'; ctx.font = '14px sans-serif';
      ctx.fillText('Push: ' + Math.round(pushVol).toLocaleString() + ' lbs', 30, 48);
      ctx.fillText('Pull: ' + Math.round(pullVol).toLocaleString() + ' lbs', 30, 108);
      ctx.fillStyle = 'rgba(255,255,255,0.4)'; ctx.font = '11px sans-serif';
      var ratio = pullVol > 0 ? (pushVol / pullVol).toFixed(1) : 'N/A';
      ctx.fillText('Push/Pull ratio: ' + ratio + ':1', 20, 140);
    }).catch(function () {});
  }

  function loadAnalyticsCharts() {
    fetch("/api/dashboard").then(function(r) { return r.json(); }).then(function(data) {
      if (data.exercises && data.exercises.length) {
        clearChartLoading("progressChart");
        drawProgressChart(data.exercises);
      } else {
        hide("progressChart"); show("progress-empty");
      }
      if (data.muscles && data.muscles.length) {
        clearChartLoading("volumeChart");
        drawVolumeChart(data.muscles);
      } else {
        hide("volumeChart"); show("volume-empty");
      }
    }).catch(function () {});

    // Body weight trend chart
    fetch("/api/vitals").then(function(r) { return r.json(); }).then(function(data) {
      clearChartLoading("weightChart");
      if (data.weight && data.weight.trend_30d && data.weight.trend_30d.length > 1) {
        drawDetailChart("weightChart", data.weight.trend_30d.map(function(p) { return { x: p.date, y: p.weight_lbs }; }), "Weight (lbs)");
      } else {
        hide("weightChart"); show("weight-empty");
      }
    }).catch(function () {});

    // Recomp trend
    fetch("/api/vitals").then(function(r) { return r.json(); }).then(function(data) {
      clearChartLoading("recompChart");
      if (data.weight && data.weight.trend_30d && data.weight.trend_30d.length > 1 && data.weight.body_fat_pct != null) {
        drawDetailChart("recompChart", data.weight.trend_30d.map(function(p) { return { x: p.date, y: p.weight_lbs }; }), "Weight Trend");
      } else {
        hide("recompChart"); show("recomp-empty");
      }
    }).catch(function () {});
  }

  function clearChartLoading(canvasId) {
    var canvas = document.getElementById(canvasId);
    if (canvas && canvas.parentElement) {
      var loading = canvas.parentElement.querySelector('.chart-loading-state');
      if (loading) loading.style.display = 'none';
    }
  }

  // ── Body Tab ────────────────────────────────────────────────────────
  function loadBody() {
    // Vitals-based KPIs
    fetch("/api/vitals").then(function(r) { return r.json(); }).then(function(data) {
      if (!data.weight) return;
      var container = $("body-summary");
      if (!container) return;
      var items = [];
      if (data.weight.current_lbs != null) items.push(kpiCard("Weight", fmtNum(data.weight.current_lbs, 1) + " lbs"));
      if (data.weight.body_fat_pct != null) items.push(kpiCard("Body Fat", fmtNum(data.weight.body_fat_pct, 1) + "%"));
      if (data.weight.change_7d != null) items.push(kpiCard("7d Change", (data.weight.change_7d > 0 ? "+" : "") + fmtNum(data.weight.change_7d, 1) + " lbs"));
      if (data.weight.change_30d != null) items.push(kpiCard("30d Change", (data.weight.change_30d > 0 ? "+" : "") + fmtNum(data.weight.change_30d, 1) + " lbs"));
      container.innerHTML = items.join("");

      // Weight trend chart from vitals (may be empty)
      if (data.weight.trend_30d && data.weight.trend_30d.length > 1) {
        drawDetailChart("bodyWeightTrendChart", data.weight.trend_30d.map(function(p) { return { x: p.date, y: p.weight_lbs }; }), "Weight (lbs)");
      }
    }).catch(function () {});

    // Body history — richer data source with 170 entries
    fetch("/api/body-history").then(function(r) { return r.json(); }).then(function(data) {
      var history = data.history || [];
      if (!history.length) return;

      // If vitals trend_30d was empty, draw chart from body-history instead
      var weightData = history.filter(function(h) { return h.weight_lbs != null; });
      if (weightData.length > 1) {
        var canvas = document.getElementById("bodyWeightTrendChart");
        if (canvas && (!_charts["bodyWeightTrendChart"])) {
          drawDetailChart("bodyWeightTrendChart",
            weightData.map(function(h) { return { x: h.date, y: h.weight_lbs }; }),
            "Weight (lbs)");
        }
      }

      // Body composition chart (lean vs fat mass over time)
      var bfData = history.filter(function(h) { return h.weight_lbs != null && h.body_fat_pct != null; });
      if (bfData.length > 1) {
        drawDetailChart("bodyCompositionChart",
          bfData.map(function(h) {
            return { x: h.date, y: h.weight_lbs * (1 - h.body_fat_pct / 100) };
          }),
          "Lean Mass (lbs)");
      }

      // Update summary with trend info
      var container = $("body-summary");
      if (container && data.trend) {
        var trendLabel = data.trend === "increasing" ? "📈 Weight trending up" :
                         data.trend === "decreasing" ? "📉 Weight trending down" : "➡️ Weight stable";
        container.innerHTML += kpiCard("Trend", trendLabel);
      }

      // Add measurement cards if we have circumference data
      var latest = history[0];
      if (latest) {
        var measures = [];
        if (latest.waist_in) measures.push(kpiCard("Waist", latest.waist_in + '"'));
        if (latest.chest_in) measures.push(kpiCard("Chest", latest.chest_in + '"'));
        if (latest.hip_in) measures.push(kpiCard("Hip", latest.hip_in + '"'));
        if (latest.neck_in) measures.push(kpiCard("Neck", latest.neck_in + '"'));
        if (latest.arm_in) measures.push(kpiCard("Arm", latest.arm_in + '"'));
        if (latest.thigh_in) measures.push(kpiCard("Thigh", latest.thigh_in + '"'));
        if (measures.length && container) {
          container.innerHTML += measures.join("");
        }
      }
    }).catch(function() {});

    // Sleep consistency
    fetch("/api/oura/sleep-summary").then(function(r) { return r.json(); }).then(function(data) {
      var el = $("sleep-summary");
      if (!el) return;
      if (data.trend_data && data.trend_data.length) {
        var totalMin = 0;
        data.trend_data.forEach(function(d) { totalMin += (d.duration_min || 0); });
        var avgH = totalMin / data.trend_data.length / 60;
        var consistency = data.consistency || {};
        var bedtimeVar = consistency.bedtime_variance_min || "--";
        el.innerHTML = '<div class="glass-card" style="padding:16px;text-align:center;">' +
          '<div style="font-size:24px;font-weight:700;">' + fmtNum(avgH, 1) + 'h</div>' +
          '<div style="color:var(--text-secondary);margin-bottom:8px;">Avg Sleep (7d)</div>' +
          '<div style="font-size:12px;color:var(--text-secondary);">Bedtime variance: ' + fmt(bedtimeVar) + ' min</div>' +
          '</div>';
      } else if (data.last_night) {
        var ln = data.last_night;
        el.innerHTML = '<div class="glass-card" style="padding:16px;text-align:center;">' +
          '<div style="font-size:24px;font-weight:700;">' + fmtNum(ln.duration_hours || (ln.total_sleep_min ? ln.total_sleep_min / 60 : 0), 1) + 'h</div>' +
          '<div style="color:var(--text-secondary);">Last Night · Score ' + (ln.quality_score || ln.sleep_score || "--") + '</div>' +
          '</div>';
      }
    }).catch(function () {});
  }

  function kpiCard(label, value) {
    return '<div class="kpi-card" style="display:flex;flex-direction:column;gap:4px;padding:12px;border:1px solid #334155;border-radius:8px;"><span class="kpi-value" style="font-size:20px;font-weight:700;color:#e2e8f0;">' + esc(value) + '</span><span class="kpi-label" style="font-size:11px;text-transform:uppercase;letter-spacing:0.5px;color:#94a3b8;">' + esc(label) + '</span></div>';
  }

  function exerciseName(ex) {
    return ex.exercise || ex.name || ex.machine || "Exercise";
  }

  function exerciseMuscle(ex) {
    return ex.muscle || ex.muscle_group || "";
  }

  function exerciseSets(ex) {
    return ex.target_sets || ex.sets || 3;
  }

  function exerciseReps(ex) {
    return ex.target_reps || ex.reps || 10;
  }

  function exerciseWeight(ex) {
    var w = ex.target_weight;
    if (w == null) w = ex.weight;
    if (w == null) w = ex.weight_lbs;
    return w == null ? 0 : w;
  }

  function renderSetRows(ex, idx) {
    var sets = parseInt(exerciseSets(ex), 10) || 3;
    var reps = parseInt(exerciseReps(ex), 10) || 10;
    var weight = exerciseWeight(ex);
    var html = "";
    for (var i = 0; i < sets; i++) {
      html += '<div class="set-row" data-set-index="' + i + '">' +
        '<label>Set ' + (i + 1) + '</label>' +
        '<input type="number" inputmode="decimal" class="set-weight" value="' + esc(weight) + '" placeholder="lbs" aria-label="Weight for set ' + (i + 1) + '">' +
        '<input type="number" inputmode="numeric" class="set-reps" value="' + esc(reps) + '" placeholder="reps" aria-label="Reps for set ' + (i + 1) + '">' +
        '<label class="set-done"><input type="checkbox" class="set-complete"> Done</label>' +
        '</div>';
    }
    return html;
  }

  function renderWorkoutActions(targetId) {
    var el = $(targetId);
    if (!el) return;
    el.innerHTML = '<button id="start-workout-btn" type="button" class="start-workout-btn" onclick="startWorkoutFromRecommendation()">▶ Start Workout</button>';
  }

  // ── Exercise swap ─────────────────────────────────────────────────
  window.openSwap = function(exerciseName, muscle, idx) {
    var modal = document.getElementById('swap-modal');
    var nameEl = document.getElementById('swap-exercise-name');
    var listEl = document.getElementById('alternatives-list');
    if (!modal || !nameEl || !listEl) return;
    nameEl.textContent = 'Replacement for: ' + exerciseName + (muscle ? ' (' + muscle + ')' : '');
    listEl.innerHTML = '<p style="color:#94a3b8;">Loading alternatives…</p>';
    modal.style.display = 'flex';
    var mg = (muscle || '').toLowerCase().replace(/\s+/g,'_');
    fetch('/api/exercises/alternatives/' + encodeURIComponent(mg), { credentials: 'include' })
      .then(function(r){ return r.json(); })
      .then(function(d){
        var alts = (d.alternatives || []).map(function(alt) { return typeof alt === "string" ? alt : alt.name; }).filter(Boolean);
        if (!alts.length) { listEl.innerHTML = '<p style="color:#94a3b8;">No alternatives found for ' + esc(muscle) + '.</p>'; return; }
        listEl.innerHTML = alts.map(function(alt){
          return '<button class="swap-option" onclick="performSwap(' + idx + ',\'' + esc(alt) + '\')">' + esc(alt) + '</button>';
        }).join('');
      })
      .catch(function(){ listEl.innerHTML = '<p style="color:#f87171;">Failed to load alternatives.</p>'; });
  };

  window.performSwap = function(idx, newExercise) {
    fetch('/api/workout/swap', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ workout_index: 0, exercise_index: idx, new_exercise_name: newExercise })
    }).then(function(r){ return r.json(); }).then(function(d){
      if (d.success || d.status === 'ok' || d.status === 'success') {
        closeModal();
        _loadedTabs.workout = false;
        if (d.recommendation) {
          window.currentRecommendation = d.recommendation;
          renderDashboardWorkout(d.recommendation);
          loadWorkout_renderExercises(d.recommendation);
        } else if (typeof loadWorkout === 'function') loadWorkout();
        if (typeof showToast === 'function') showToast('Swapped to ' + newExercise);
      } else {
        var msg = (d.error && (d.error.message || d.error)) || d.message || 'Swap failed';
        if (typeof showToast === 'function') showToast(msg, true);
      }
    }).catch(function(){ if (typeof showToast === 'function') showToast('Network error', true); });
  };

  function renderDashboardWorkout(nw) {
    setText("today-workout-title", nw.focus || nw.name || "Workout");
    var durMin = nw.estimated_minutes || nw.duration_min || parseInt(nw.estimated_duration, 10) || "--";
    var durLabel = durMin + " min";
    var rpeVal = nw.rpe || nw.rpe_target || (nw.exercises && nw.exercises.length ? nw.exercises[0].rpe_target : null) || "--";
    setText("today-workout-focus", nw.focus || "");
    setText("today-workout-duration", durLabel);
    setText("today-workout-rpe", rpeVal === "--" ? "" : "RPE " + rpeVal);
    setText("today-workout-notes", nw.notes || "");
    var exList = $("dashboard-workout-exercises");
    if (exList && nw.exercises && nw.exercises.length) {
      exList.innerHTML = nw.exercises.map(function (ex, idx) {
        var s = exerciseSets(ex);
        var r = exerciseReps(ex);
        var w = exerciseWeight(ex);
        var m = exerciseMuscle(ex);
        var n = exerciseName(ex);
        return '<div class="workout-exercise-card" data-index="' + idx + '">' +
          '<div class="workout-exercise-header">' +
          '<span class="workout-exercise-name">' + esc(n) + '</span>' +
          '<span class="workout-exercise-meta">' + esc(m) + '</span>' +
          '<button class="workout-swap-btn" onclick="openSwap(\'' + esc(n) + '\',\'' + esc(m) + '\',' + idx + ')" title="Swap exercise">🔄 Swap</button>' +
          '</div>' +
          '<div class="workout-exercise-details">' +
          '<span>' + s + '×' + r + '</span>' +
          (w != null ? '<span>' + fmt(w) + ' lbs</span>' : '') +
          '</div></div>';
      }).join("");
    }
    renderWorkoutActions("dashboard-workout-actions");
  }

  window.startWorkoutFromRecommendation = function() {
    var rec = window.currentRecommendation;
    if (!rec || !rec.exercises || !rec.exercises.length) {
      if (typeof showToast === 'function') showToast('Workout is still loading', true);
      return;
    }
    var modal = document.getElementById('workout-modal');
    var title = document.getElementById('modal-workout-name');
    var exercisesContainer = document.getElementById('active-workout-exercises');
    if (!modal || !exercisesContainer) return;
    if (title) title.textContent = rec.focus || rec.name || 'Active Workout';
    exercisesContainer.innerHTML = rec.exercises.map(function(ex, idx) {
      var n = exerciseName(ex);
      var m = exerciseMuscle(ex);
      return '<div class="active-exercise" data-index="' + idx + '">' +
        '<div class="active-exercise-header">' +
        '<div><h4>' + esc(n) + '</h4><div class="exercise-target">' + esc(m) + ' · ' + exerciseSets(ex) + '×' + exerciseReps(ex) + ' · ' + fmt(exerciseWeight(ex)) + ' lbs</div></div>' +
        '<button class="workout-swap-btn" onclick="openSwap(\'' + esc(n) + '\',\'' + esc(m) + '\',' + idx + ')" title="Swap exercise">🔄 Swap</button>' +
        '</div>' +
        '<div class="sets-container">' + renderSetRows(ex, idx) + '</div>' +
        '</div>';
    }).join('');
    modal.style.display = 'flex';
  };

  window.completeWorkoutFromRecommendation = function() {
    var rec = window.currentRecommendation || {};
    var rows = Array.prototype.slice.call(document.querySelectorAll('#active-workout-exercises .active-exercise'));
    var exercises = rows.map(function(row, idx) {
      var ex = (rec.exercises || [])[idx] || {};
      var sets = Array.prototype.slice.call(row.querySelectorAll('.set-row')).map(function(setRow) {
        return {
          weight_lbs: parseFloat((setRow.querySelector('.set-weight') || {}).value) || 0,
          reps: parseInt((setRow.querySelector('.set-reps') || {}).value, 10) || 0,
          completed: !!(setRow.querySelector('.set-complete') || {}).checked
        };
      }).filter(function(s) { return s.completed || s.reps > 0; });
      return {
        machine: exerciseName(ex),
        muscle_group: exerciseMuscle(ex) || 'unknown',
        sets: sets,
        notes: ''
      };
    }).filter(function(ex) { return ex.sets.length; });
    if (!exercises.length) {
      if (typeof showToast === 'function') showToast('Log at least one set first', true);
      return;
    }
    fetch('/api/complete-workout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        recommendation_id: rec.id,
        session_type: rec.focus || rec.name || 'recommended',
        duration_minutes: rec.estimated_minutes || parseInt(rec.estimated_duration, 10) || 45,
        exercises: exercises
      })
    }).then(function(r) { return r.json(); }).then(function(d) {
      if (d.status === 'success') {
        document.querySelectorAll('.modal').forEach(function(m) { m.style.display = 'none'; });
        if (typeof showToast === 'function') showToast('Workout logged');
        _loadedTabs.history = false;
        if (window.switchTab) window.switchTab('history');
      } else {
        var msg = (d.error && (d.error.message || d.error)) || d.message || 'Workout log failed';
        if (typeof showToast === 'function') showToast(msg, true);
      }
    }).catch(function() { if (typeof showToast === 'function') showToast('Network error', true); });
  };
  window.completeWorkout = window.completeWorkoutFromRecommendation;

  window.syncOura = function() {
    var btn = document.getElementById('oura-sync-btn');
    var status = document.getElementById('oura-sync-status');
    if (!btn) return;
    btn.disabled = true;
    var originalText = btn.textContent;
    btn.textContent = '⏳ Syncing…';
    if (status) status.textContent = 'Requesting last 30 days from Oura API…';
    fetch('/api/oura/sync-sleep', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ days_back: 30 })
    }).then(function(r){ return r.json(); }).then(function(d){
      btn.disabled = false;
      btn.textContent = originalText;
      if (d.status === 'success') {
        if (status) status.textContent = '✅ Synced from ' + (d.synced_from || 'last 30 days') + ' · ' + ((d.latest_records||[]).length) + ' records';
        if (typeof showToast === 'function') showToast('Oura sync complete');
        // Refresh dashboard KPIs
        if (typeof loadDashboard === 'function') loadDashboard();
      } else {
        if (status) status.textContent = '⚠️ ' + (d.message || 'Sync failed');
        if (typeof showToast === 'function') showToast('Sync failed: ' + (d.message || 'unknown'), true);
      }
    }).catch(function(err){
      btn.disabled = false;
      btn.textContent = originalText;
      if (status) status.textContent = '⚠️ Network error: ' + err;
      if (typeof showToast === 'function') showToast('Network error', true);
    });
  };

  window.closeModal = function() {
    var modals = document.querySelectorAll('.modal');
    modals.forEach(function(m){ m.style.display = 'none'; });
  };

  // ── Chart Drawing ───────────────────────────────────────────────────
  var _charts = {};

  function drawSparkline(canvasId, values, color) {
    var canvas = $(canvasId);
    if (!canvas || typeof Chart === "undefined") return;
    if (_charts[canvasId]) _charts[canvasId].destroy();
    if (!values || values.length < 2) return;
    _charts[canvasId] = new Chart(canvas.getContext("2d"), {
      type: "line",
      data: {
        labels: values.map(function (_, i) { return i; }),
        datasets: [{
          data: values,
          borderColor: color || "#4361ee",
          backgroundColor: "transparent",
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.3
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { x: { display: false }, y: { display: false } }
      }
    });
  }

  function drawDetailChart(canvasId, points, label, extraDatasets) {
    var canvas = $(canvasId);
    if (!canvas || typeof Chart === "undefined") return;
    if (_charts[canvasId]) _charts[canvasId].destroy();
    if (!points || points.length < 2) {
      canvas.parentElement && (canvas.parentElement.style.display = "none");
      return;
    }
    var datasets = [{
      label: label || "Value",
      data: points.map(function (p) { return p.y; }),
      borderColor: "#4361ee",
      backgroundColor: "rgba(67,97,238,0.1)",
      fill: true,
      tension: 0.3
    }];
    if (extraDatasets) datasets = datasets.concat(extraDatasets);
    _charts[canvasId] = new Chart(canvas.getContext("2d"), {
      type: "line",
      data: {
        labels: points.map(function (p) { return p.x ? p.x.slice(5) : ""; }),
        datasets: datasets
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: "#ccc" } } },
        scales: {
          x: { ticks: { color: "#999" } },
          y: { ticks: { color: "#999" } }
        }
      }
    });
  }

  function drawProgressChart(exercises) {
    if (!exercises || !exercises.length) return;
    var canvas = $("progressChart");
    if (!canvas || typeof Chart === "undefined") return;
    if (_charts.progressChart) _charts.progressChart.destroy();
    // Top 6 exercises by current e1rm
    var top = exercises.slice(0, 6);
    _charts.progressChart = new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: {
        labels: top.map(function (e) { return e.exercise; }),
        datasets: [{
          label: "Current e1RM",
          data: top.map(function (e) { return e.current_e1rm; }),
          backgroundColor: top.map(function (e) { return e.status_color === "green" ? "#22c55e" : e.status_color === "yellow" ? "#f59e0b" : "#ef4444"; }),
          borderRadius: 4
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { x: { ticks: { color: "#ccc" } }, y: { ticks: { color: "#999" } } }
      }
    });
    // Hide loading state
    var parent = canvas.closest("[data-analytics-panel]");
    if (parent) parent.querySelector(".chart-loading-state") && (parent.querySelector(".chart-loading-state").style.display = "none");
  }

  function drawVolumeChart(muscles) {
    if (!muscles || !muscles.length) return;
    var canvas = $("volumeChart");
    if (!canvas || typeof Chart === "undefined") return;
    if (_charts.volumeChart) _charts.volumeChart.destroy();
    _charts.volumeChart = new Chart(canvas.getContext("2d"), {
      type: "doughnut",
      data: {
        labels: muscles.map(function (m) { return m.muscle; }),
        datasets: [{
          data: muscles.map(function (m) { return m.sets; }),
          backgroundColor: ["#4361ee", "#e040fb", "#22c55e", "#f59e0b", "#06b6d4", "#8b5cf6", "#ef4444", "#14b8a6", "#f97316", "#6366f1"]
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: "right", labels: { color: "#ccc" } } }
      }
    });
    var parent = canvas.closest("[data-analytics-panel]");
    if (parent) parent.querySelector(".chart-loading-state") && (parent.querySelector(".chart-loading-state").style.display = "none");
  }

  // ── Form Handlers ───────────────────────────────────────────────────
  function wireForms() {
    // Workout form
    var wf = $("workout-form");
    if (wf) wf.addEventListener("submit", function (e) {
      e.preventDefault();
      var payload = {
        exercise: $("workout-exercise").value,
        weight: parseFloat($("workout-weight").value),
        reps: parseInt($("workout-reps").value),
        rpe: parseFloat($("workout-rpe").value),
        date: new Date().toISOString().slice(0, 10)
      };
      fetch("/api/add-workout", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })
        .then(r => r.json()).then(data => {
          if (data.status === "ok" || data.success) { showToast("Set logged!"); wf.reset(); }
          else showToast(data.error || "Failed to log set", true);
        }).catch(() => showToast("Network error", true));
    });

    // RPE slider
    var rpeSlider = $("workout-rpe");
    if (rpeSlider) rpeSlider.addEventListener("input", function () { setText("rpe-value", this.value); });

    // Cardio form
    var cf = $("cardio-form");
    if (cf) cf.addEventListener("submit", function (e) {
      e.preventDefault();
      var payload = {
        type: $("cardio-type").value,
        duration_min: parseFloat($("cardio-duration").value),
        avg_hr: parseInt($("cardio-hr").value) || null,
        intensity: parseInt($("cardio-intensity").value),
        notes: $("cardio-notes").value
      };
      fetch("/api/add-cardio", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })
        .then(r => r.json()).then(data => {
          if (data.status === "ok" || data.success) { showToast("Cardio logged!"); cf.reset(); }
          else showToast(data.error || "Failed to log cardio", true);
        }).catch(() => showToast("Network error", true));
    });

    // Cardio intensity slider
    var ciSlider = $("cardio-intensity");
    if (ciSlider) ciSlider.addEventListener("input", function () { setText("cardio-intensity-value", this.value); });

    // Soreness form
    var sf = $("soreness-form");
    if (sf) sf.addEventListener("submit", function (e) {
      e.preventDefault();
      var payload = {
        muscle: $("soreness-muscle").value,
        soreness_level: parseInt($("soreness-level").value),
        notes: $("soreness-notes").value
      };
      fetch("/api/add-soreness", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })
        .then(r => r.json()).then(data => {
          if (data.status === "ok" || data.success) { showToast("Soreness logged!"); sf.reset(); }
          else showToast(data.error || "Failed to log soreness", true);
        }).catch(() => showToast("Network error", true));
    });

    // Soreness slider
    var slSlider = $("soreness-level");
    if (slSlider) slSlider.addEventListener("input", function () { setText("soreness-value", this.value); });

    // Recovery form (sauna)
    var rf = $("sauna-form");
    if (rf) rf.addEventListener("submit", function (e) {
      e.preventDefault();
      var payload = {
        type: $("recovery-type").value,
        duration_min: parseFloat($("recovery-duration").value),
        temp: parseInt($("recovery-temp").value) || null,
        notes: $("recovery-notes").value
      };
      fetch("/api/add-recovery", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })
        .then(r => r.json()).then(data => {
          if (data.status === "ok" || data.success) { showToast("Recovery logged!"); rf.reset(); }
          else showToast(data.error || "Failed to log recovery", true);
        }).catch(() => showToast("Network error", true));
    });

    // Body form
    var bf = $("body-form");
    if (bf) bf.addEventListener("submit", function (e) {
      e.preventDefault();
      var payload = {
        weight_lbs: parseFloat($("body-weight-input").value),
        body_fat_pct: parseFloat($("body-fat").value) || null,
        neck: parseFloat($("body-neck").value) || null,
        waist: parseFloat($("body-waist").value) || null,
        chest: parseFloat($("body-chest").value) || null,
        hips: parseFloat($("body-hips").value) || null,
        notes: $("body-notes").value
      };
      fetch("/api/add-body-measurement", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })
        .then(r => r.json()).then(data => {
          if (data.status === "ok" || data.success) { showToast("Body measurement saved!"); bf.reset(); }
          else showToast(data.error || "Failed to save measurement", true);
        }).catch(() => showToast("Network error", true));
    });

    // Navy calculator button
    var navyBtn = $("navy-calc-btn");
    if (navyBtn) navyBtn.addEventListener("click", function () {
      var waist = parseFloat(($("body-waist") || {}).value);
      var neck = parseFloat(($("body-neck") || {}).value);
      var height = 69; // 5'9" default — could pull from settings
      if (!waist || !neck) { showToast("Enter waist and neck measurements", true); return; }
      var bf = 86.010 * Math.log10(waist - neck) - 70.041 * Math.log10(height) + 36.76;
      var bfInput = $("body-fat");
      if (bfInput && bf > 0 && bf < 60) bfInput.value = bf.toFixed(1);
      showToast("Estimated BF: " + bf.toFixed(1) + "%");
    });
  }

  // ── Toast ────────────────────────────────────────────────────────────
  function showToast(msg, isError) {
    var t = document.createElement("div");
    t.className = "toast " + (isError ? "toast-error" : "toast-success");
    t.textContent = msg;
    t.style.cssText = "position:fixed;bottom:100px;left:50%;transform:translateX(-50%);padding:10px 20px;border-radius:8px;z-index:9999;font-size:14px;color:#fff;background:" + (isError ? "#ef4444" : "#22c55e") + ";";
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 3000);
  }

  // ── Vitals card expand/collapse ──────────────────────────────────────
  function wireVitalsCards() {
    document.querySelectorAll(".vitals-card-toggle").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var panel = btn.closest(".vitals-card").querySelector(".vitals-detail-panel");
        if (panel) panel.classList.toggle("open");
        var expanded = btn.getAttribute("aria-expanded") === "true";
        btn.setAttribute("aria-expanded", !expanded);
      });
    });
  }

  // ── Settings tab ─────────────────────────────────────────────────────
  function loadSettings() {
    fetch("/api/settings").then(function(r) { return r.json(); }).then(function(data) {
      // Sessions per week slider
      var slider = $("sessions-target");
      var val = $("target-value");
      if (slider) slider.value = data.sessions_per_week_target || data.sessions_per_week || 3;
      if (val) val.textContent = slider ? slider.value : 3;

      // Training goals
      renderGoals(data);

      // Time options (workout duration)
      renderTimeOptions(data);

      // Protocols
      renderProtocols(data);

      // Recovery / deload
      renderDeload(data);

      // Injury risk
      renderInjuryRisk(data);

      // Baseline weights
      renderBaselines(data);

      // Data buttons
      wireDataButtons();
    }).catch(function(err) { console.error("[settings]", err); });
  }

  function renderGoals(data) {
    var container = $("goals-container");
    if (!container) return;
    var goals = data.available_goals || [];
    var current = data.training_goal || "";
    container.innerHTML = goals.map(function(g) {
      var active = g.value === current ? " goal-card active" : "";
      return '<div class="goal-card' + active + '" data-goal="' + esc(g.value) + '">' +
        '<div class="goal-name">' + esc(g.name) + '</div>' +
        '<div class="goal-desc">' + esc(g.description) + '</div>' +
        '</div>';
    }).join('');

    container.querySelectorAll('.goal-card').forEach(function(card) {
      card.addEventListener('click', function() {
        var goal = card.dataset.goal;
        container.querySelectorAll('.goal-card').forEach(function(c) { c.classList.remove('goal-card active'); });
        card.classList.add('goal-card active');
        fetch("/api/settings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ training_goal: goal })
        }).then(function() {
         showToast("Goal updated: " + goal.replace(/_/g, ' '));
        }).catch(function() {});
      });
    });
  }

  function renderTimeOptions(data) {
    var container = $("time-options-container");
    if (!container) return;
    var opts = data.time_options || [];
    var current = data.available_time_minutes || 60;
    container.innerHTML = opts.map(function(o) {
      var active = o.value === current ? " time-option active" : "";
      return '<div class="time-opt' + active + '" data-minutes="' + o.value + '">' +
        '<div class="time-opt-label">' + esc(o.label) + '</div>' +
        '<div class="time-opt-desc">' + esc(o.description) + '</div>' +
        '</div>';
    }).join('');

    container.querySelectorAll('.time-opt').forEach(function(opt) {
      opt.addEventListener('click', function() {
        var mins = parseInt(opt.dataset.minutes, 10);
        container.querySelectorAll('.time-opt').forEach(function(o) { o.classList.remove('time-option active'); });
        opt.classList.add('time-option active');
        fetch("/api/settings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ available_time_minutes: mins })
        }).then(function() {
         showToast("Duration set to " + mins + " min");
        }).catch(function() {});
      });
    });
  }

  function renderProtocols(data) {
    var container = $("protocols-container");
    if (!container) return;
    var gd = data.goal_details || {};
    if (!gd.name) { container.innerHTML = '<p class="no-data">Select a training goal first.</p>'; return; }
    container.innerHTML =
      '<div class="protocol-card"><strong>Goal:</strong> ' + esc(gd.name) + '</div>' +
      '<div class="protocol-card"><strong>Rep Range:</strong> ' + (gd.rep_range ? gd.rep_range[0] + '-' + gd.rep_range[1] : '--') + '</div>' +
      '<div class="protocol-card"><strong>RPE Target:</strong> ' + (gd.rpe_target || '--') + '</div>' +
      '<div class="protocol-card"><strong>Sets/Exercise:</strong> ' + (gd.sets_per_exercise || '--') + '</div>' +
      '<div class="protocol-card"><strong>Rest:</strong> ' + esc(gd.rest_minutes || '--') + ' min</div>' +
      '<div class="protocol-card"><strong>Intensity:</strong> ' + (gd.intensity_pct || '--') + '% 1RM</div>';
  }

  function renderDeload(data) {
    var container = $("deload-container");
    if (!container) return;
    var ft = data.fatigue_threshold || 72;
    container.innerHTML =
      '<div class="status-item"><strong>Fatigue Threshold:</strong> ' + ft + '</div>' +
      '<div class="status-item"><strong>Status:</strong> Normal training</div>';
  }

  function renderInjuryRisk(data) {
    var container = $("injury-risk-container");
    if (!container) return;
    var eqLabel = data.equipment_preference || 'machines_only';
    (data.equipment_options || []).forEach(function(e) {
      if (e.value === data.equipment_preference) eqLabel = e.label;
    });
    container.innerHTML =
      '<div class="status-item"><strong>Equipment:</strong> ' + esc(eqLabel) + '</div>' +
      '<div class="status-item"><strong>Risk Level:</strong> Low</div>';
  }

  function renderBaselines(data) {
    var container = $("baseline-container");
    if (!container) return;
    var vl = data.volume_landmarks || {};
    var keys = Object.keys(vl);
    if (!keys.length) { container.innerHTML = '<p class="no-data">No baseline data yet.</p>'; return; }
    container.innerHTML = keys.map(function(muscle) {
      var v = vl[muscle];
      return '<div class="baseline-row"><span class="baseline-muscle">' + esc(muscle) + '</span>' +
        '<span class="baseline-val">MEV ' + (v.mev || '--') + ' / MAV ' + (v.mav_min || '--') + '-' + (v.mav_max || '--') + ' / MRV ' + (v.mrv || '--') + '</span></div>';
    }).join('');

    var saveBtn = $("save-baseline-btn");
    if (saveBtn) {
      saveBtn.addEventListener('click', function() {
       showToast("Baselines saved");
      });
    }
  }

  function wireDataButtons() {
    var exportBtn = $("export-backup-btn");
    if (exportBtn) {
      exportBtn.addEventListener('click', function() {
        fetch("/api/backup").then(function(r) { return r.json(); }).then(function(data) {
          var blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
          var a = document.createElement("a"); a.href = URL.createObjectURL(blob);
          a.download = "fitness-backup.json"; a.click();
        }).catch(function() {showToast("Export failed"); });
      });
    }
    var importBtn = $("import-backup-btn");
    var importInput = $("import-backup-input");
    if (importBtn && importInput) {
      importBtn.addEventListener('click', function() { importInput.click(); });
      importInput.addEventListener('change', function() {
        var file = importInput.files[0];
        if (!file) return;
        var reader = new FileReader();
        reader.onload = function(e) {
          try {
            var data = JSON.parse(e.target.result);
            fetch("/api/backup", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) })
              .then(function() {showToast("Backup restored"); })
              .catch(function() {showToast("Restore failed"); });
          } catch(err) {showToast("Invalid backup file"); }
        };
        reader.readAsText(file);
      });
    }
    var exportAllBtn = $("export-all-btn");
    if (exportAllBtn) {
      exportAllBtn.addEventListener('click', function() {
        fetch("/api/history").then(function(r) { return r.json(); }).then(function(data) {
          var md = "# Fitness Data Export\n\n";
          (data.workouts || []).forEach(function(w) {
            md += "## " + (w.session_type || "Workout") + " - " + w.date + "\n";
            md += "Sets: " + (w.total_sets || 0) + " | Volume: " + (w.total_volume || 0) + " lbs\n\n";
          });
          var blob = new Blob([md], { type: "text/markdown" });
          var a = document.createElement("a"); a.href = URL.createObjectURL(blob);
          a.download = "fitness-export.md"; a.click();
        });
      });
    }
  }

  function wireSettings() {
    var slider = $("sessions-target");
    if (slider) slider.addEventListener("input", function () {
      setText("target-value", this.value);
    });
    if (slider) slider.addEventListener("change", function () {
      fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sessions_per_week_target: parseInt(slider.value) })
      }).then(function() {showToast("Sessions updated"); }).catch(function () {});
    });
  }

  // ── Tab-aware loading ────────────────────────────────────────────────
  var _loadedTabs = {};

  // ── Workout (Next) Tab ──────────────────────────────────────────────────
  function loadWorkout() {
    fetch("/api/dashboard").then(r => r.json()).then(data => {
      var nw = data.next_workout;
      if (!nw) return;
      window.currentRecommendation = nw;

      var _workoutName = nw.focus || nw.name || "Workout";
      setText("workout-focus", _workoutName);
      setText("next-workout-title", _workoutName);
      // Also populate the standalone tab indicator labels (template uses these)
      var _rpe = nw.rpe || nw.rpe_target || (nw.exercises && nw.exercises.length ? nw.exercises[0].rpe_target : null) || "--";
      setText("rpe-value", _rpe === "--" ? "--" : "RPE " + _rpe);
      var wkDurMin = nw.estimated_minutes || nw.duration_min || parseInt(nw.estimated_duration, 10) || "--";
      setText("workout-duration", wkDurMin + " min");
      setText("workout-goal", goalLabels[nw.goal] || nw.goal_name || nw.goal || "");

      // Smart recommendation
      fetch("/api/recommendation/smart").then(r => r.json()).then(reco => {
        var recoText = (reco.recommendation || "--").toUpperCase();
        setText("smart-reco", recoText + " — " + (reco.suggested_workout || ""));
        setText("readiness-summary", "Readiness " + (reco.effective_readiness || reco.readiness || "--"));

        // Reasoning factors
        var factors = [];
        if (reco.readiness_factors) {
          var rf = reco.readiness_factors;
          if (rf.acwr && rf.acwr.message) factors.push("ACWR: " + rf.acwr.message);
          if (rf.sleep_debt && rf.sleep_debt.message) factors.push("Sleep: " + rf.sleep_debt.message);
          if (rf.recovery_bonus && rf.recovery_bonus.message) factors.push("Recovery: " + rf.recovery_bonus.message);
        }
        if (reco.hrv_trend && reco.hrv_trend !== "unknown") factors.push("HRV trend: " + reco.hrv_trend);
        if (reco.weather && reco.weather.temp_f) factors.push("Weather: " + reco.weather.temp_f + "°F");

        var reasoningEl = $("reasoning-factors");
        if (reasoningEl && factors.length) {
          reasoningEl.innerHTML = factors.map(function(f) { return "<div>• " + esc(f) + "</div>"; }).join("");
          var reasoningWrap = $("recommendation-reasoning");
          if (reasoningWrap) reasoningWrap.style.display = "";
        }
      }).catch(function() {});

      renderWorkoutActions("next-workout-actions");

      // Exercises list - template uses "today-workout-exercises"
      var exContainer = $("next-workout-exercises") || $("workout-exercises");
      if (exContainer && nw.exercises && nw.exercises.length) {
        exContainer.innerHTML = nw.exercises.map(function(ex, idx) {
          var _muscle = ex.muscle || ex.muscle_group || "";
          return '<div class="workout-exercise-card" data-index="' + idx + '">' +
            '<div class="workout-exercise-header">' +
            '<span class="workout-exercise-num">' + (idx + 1) + '. </span>' +
            '<span class="workout-exercise-name">' + esc(ex.exercise || ex.name) + '</span>' +
            '<span class="workout-exercise-meta-badge">' + esc(_muscle) + '</span>' +
            '<button class="workout-swap-btn" onclick="openSwap(\'' + esc(ex.exercise || ex.name) + '\',\'' + esc(_muscle) + '\',' + idx + ')" title="Swap exercise">🔄</button>' +
            '</div>' +
            '<div class="workout-exercise-details">' +
            '<span>' + (ex.target_sets || ex.sets || "--") + ' × ' + (ex.target_reps || ex.reps || "--") + '</span>' +
            '<span>' + fmt(ex.target_weight || ex.weight) + ' lbs</span>' +
            '<span>RPE ' + (ex.rpe_target || ex.rpe || "--") + '</span>' +
            '</div>' +
            (ex.rationale ? '<div class="workout-exercise-rationale">' + esc(ex.rationale) + '</div>' : '') +
            '</div>';
        }).join("");
      }

      // Cardio section
      if (nw.cardio) {
        var cardioSection = $("cardio-section");
        var cardioContainer = $("cardio-container");
        if (cardioSection && cardioContainer) {
          cardioSection.style.display = "";
          var c = nw.cardio;
          cardioContainer.innerHTML =
            '<div class="cardio-type">' + esc(c.type || "") + '</div>' +
            '<div class="cardio-detail">' + (c.duration_minutes || "--") + ' min · ' + esc(c.intensity || "") + '</div>' +
            '<div class="cardio-zone">' + esc(c.zone || "") + ' · ' + esc(c.heart_rate_range || "") + '</div>' +
            (c.technique ? '<div class="cardio-technique">' + esc(c.technique) + '</div>' : '') +
            (c.reason ? '<div class="cardio-reason">' + esc(c.reason) + '</div>' : '');
        }
      }

      // Muscles to avoid
      if (nw.muscles_to_avoid && nw.muscles_to_avoid.length) {
        var avoidSection = $("avoid-section");
        var avoidContainer = $("avoid-container");
        if (avoidSection && avoidContainer) {
          avoidSection.style.display = "";
          avoidContainer.innerHTML = nw.muscles_to_avoid.map(function(m) {
            return '<div class="avoid-muscle">⚠️ ' + esc(m) + '</div>';
          }).join("");
        }
      }

      // Wire reasoning toggle
      var reasoningToggle = $("reasoning-toggle");
      if (reasoningToggle) {
        reasoningToggle.onclick = function() {
          var factors = $("reasoning-factors");
          if (factors) {
            var expanded = reasoningToggle.getAttribute("aria-expanded") === "true";
            reasoningToggle.setAttribute("aria-expanded", !expanded);
            factors.style.display = expanded ? "none" : "";
          }
        };
      }

      // Expose updateNextWorkout for swap sheet integration
      window.updateNextWorkout = function(updatedWorkout) {
        window.currentRecommendation = updatedWorkout;
        loadWorkout_renderExercises(updatedWorkout);
      };
    }).catch(function(err) { console.error("[workout]", err); });
  }

  function loadWorkout_renderExercises(nw) {
    var exContainer = $("next-workout-exercises") || $("workout-exercises");
    if (exContainer && nw.exercises && nw.exercises.length) {
      exContainer.innerHTML = nw.exercises.map(function(ex, idx) {
        var n = exerciseName(ex);
        var m = exerciseMuscle(ex);
        return '<div class="workout-exercise-card" data-index="' + idx + '">' +
          '<div class="workout-exercise-header">' +
          '<span class="workout-exercise-name">' + esc(n) + '</span>' +
          '<span class="workout-exercise-meta">' + esc(m) + '</span>' +
          '<button class="workout-swap-btn" onclick="openSwap(\'' + esc(n) + '\',\'' + esc(m) + '\',' + idx + ')" title="Swap exercise">🔄 Swap</button>' +
          '</div>' +
          '<div class="workout-exercise-details">' +
          '<span>' + exerciseSets(ex) + ' × ' + exerciseReps(ex) + '</span>' +
          '<span>' + fmt(exerciseWeight(ex)) + ' lbs</span>' +
          '</div></div>';
      }).join("");
    }
  }

  // ── History Tab ─────────────────────────────────────────────────────────
  var _historyFilter = "workouts";
  var _historyRange = "all";
  var _historyData = null;

  function loadHistory() {
    fetch("/api/history-all").then(function(r) { return r.json(); }).then(function(data) {
      _historyData = data;
      renderHistoryKPIs(data);
      renderHistoryHeatmap(data);
      renderHistoryList(data);
      renderProgressiveOverload(data);
      wireHistoryFilters();
    }).catch(function(err) { console.error("[history]", err); });
  }

  function renderHistoryKPIs(data) {
    var ws = data.workouts || [];
    setText("history-total", ws.length);
    var totalVol = 0;
    ws.forEach(function(w) { totalVol += (w.total_volume || 0); });
    setText("history-volume", totalVol > 1000 ? (totalVol / 1000).toFixed(1) + "k" : totalVol);
  }

  function renderHistoryHeatmap(data) {
    var el = $("history-heatmap");
    if (!el) return;
    var ws = data.workouts || [];
    var dateCounts = {};
    ws.forEach(function(w) {
      var d = w.date ? w.date.substring(0, 10) : "";
      if (d) dateCounts[d] = (dateCounts[d] || 0) + 1;
    });
    // Build a 7-column (Sun-Sat) grid for the last 16 weeks
    var today = new Date();
    var html = '<div class="heatmap-grid">';
    for (var i = 111; i >= 0; i--) {
      var d = new Date(today); d.setDate(d.getDate() - i);
      var key = d.toISOString().substring(0, 10);
      var count = dateCounts[key] || 0;
      var lvl = count === 0 ? 0 : count <= 1 ? 1 : count <= 2 ? 2 : 3;
      html += '<div class="heatmap-cell level-' + lvl + '" title="' + key + ': ' + count + ' workouts"></div>';
    }
    html += '</div>';
    el.innerHTML = html;
  }

  function renderHistoryList(data) {
    var container = $("history-container");
    if (!container) return;

    if (_historyFilter === "workouts") {
      var ws = applyDateRange(data.workouts || []);
      if (!ws.length) { container.innerHTML = '<p class="no-data">No workout history found.</p>'; return; }
      container.innerHTML = ws.map(function(w) {
        var exHTML = (w.exercises || []).map(function(ex) {
          var sets = ex.sets || [];
          var setSummary = sets.map(function(s) { return fmt(s.weight_lbs) + "\u00d7" + s.reps; }).join(", ");
          return '<div class="history-exercise-row"><strong>' + esc(ex.machine || "?") + '</strong> <span class="meta">' + (ex.muscle_group || "") + '</span> <span class="sets">' + sets.length + ' sets</span>' + (setSummary ? ' <span class="weights">' + setSummary + '</span>' : '') + '</div>';
        }).join('');
        return '<div class="history-card"><div class="history-card-header"><h4>' + esc(w.session_type || "Workout") + '</h4><span class="history-date">' + esc(w.date || "") + '</span></div>' +
          '<div class="history-meta">' + (w.total_sets || 0) + ' sets \u00b7 ' + fmt(w.total_volume) + ' lbs vol \u00b7 ' + (w.duration_minutes || "--") + ' min</div>' +
          (exHTML ? '<div class="history-exercises">' + exHTML + '</div>' : '') +
          (w.notes ? '<div class="history-notes">' + esc(w.notes) + '</div>' : '') +
          '</div>';
      }).join('');
    } else if (_historyFilter === "cardio") {
      var cs = applyDateRange(data.cardio || []);
      if (!cs.length) { container.innerHTML = '<p class="no-data">No cardio history found.</p>'; return; }
      container.innerHTML = cs.map(function(c) {
        return '<div class="history-card"><div class="history-card-header"><h4>' + esc(c.activity_type || "Cardio") + '</h4><span class="history-date">' + esc(c.date || "") + '</span></div>' +
          '<div class="history-meta">' + (c.duration_minutes || "--") + ' min \u00b7 ' + esc(c.intensity || "") + (c.avg_heart_rate ? ' \u00b7 ' + c.avg_heart_rate + ' bpm' : '') + '</div>' +
          (c.notes ? '<div class="history-notes">' + esc(c.notes) + '</div>' : '') +
          '</div>';
      }).join('');
    } else if (_historyFilter === "recovery") {
      var rs = applyDateRange(data.recovery || []);
      if (!rs.length) { container.innerHTML = '<p class="no-data">No recovery history found.</p>'; return; }
      container.innerHTML = rs.map(function(r) {
        return '<div class="history-card"><div class="history-card-header"><h4>Recovery</h4><span class="history-date">' + esc(r.date || "") + '</span></div>' +
          '<div class="history-meta">' + esc(r.modality || r.type || "") + (r.duration_minutes ? ' \u00b7 ' + r.duration_minutes + ' min' : '') + '</div>' +
          '</div>';
      }).join('');
    }
  }

  function applyDateRange(items) {
    if (_historyRange === "all") return items;
    var days = parseInt(_historyRange, 10);
    if (isNaN(days)) return items;
    var cutoff = new Date(); cutoff.setDate(cutoff.getDate() - days);
    var cutoffStr = cutoff.toISOString().substring(0, 10);
    return items.filter(function(item) {
      return (item.date || "").substring(0, 10) >= cutoffStr;
    });
  }

  function renderProgressiveOverload(data) {
    var section = $("progressive-overload-section");
    var container = $("progressive-overload-container");
    if (!section || !container) return;
    var prs = data.personal_records || {};
    var keys = Object.keys(prs);
    if (!keys.length) { section.style.display = "none"; return; }
    section.style.display = "";
    container.innerHTML = keys.map(function(ex) {
      var pr = prs[ex];
      return '<div class="overload-card"><strong>' + esc(ex) + '</strong>' +
        (pr.e1rm ? ' <span class="pr-val">e1RM: ' + fmt(pr.e1rm) + ' lbs</span>' : '') +
        (pr.best_weight ? ' <span class="pr-val">Best: ' + fmt(pr.best_weight) + ' lbs</span>' : '') +
        '</div>';
    }).join('');
  }

  function wireHistoryFilters() {
    // Date range buttons
    document.querySelectorAll('.date-range-btn').forEach(function(btn) {
      btn.addEventListener('click', function() {
        _historyRange = btn.dataset.range || 'all';
        document.querySelectorAll('.date-range-btn').forEach(function(b) { b.classList.toggle('active', b === btn); });
        if (_historyData) renderHistoryList(_historyData);
        // Show/hide custom date range
        var custom = $("custom-date-range");
        if (custom) custom.style.display = _historyRange === "custom" ? "" : "none";
      });
    });

    // History filter tabs (workouts/cardio/recovery)
    document.querySelectorAll('.history-filter-btn').forEach(function(btn) {
      btn.addEventListener('click', function() {
        _historyFilter = btn.dataset.filter || 'workouts';
        document.querySelectorAll('.history-filter-btn').forEach(function(b) { b.classList.toggle('active', b === btn); });
        if (_historyData) renderHistoryList(_historyData);
      });
    });

    // Apply custom date range
    var applyBtn = $("apply-date-range");
    if (applyBtn) {
      applyBtn.addEventListener('click', function() {
        if (!_historyData) return;
        var from = ($("date-from") || {}).value;
        var to = ($("date-to") || {}).value;
        if (from || to) {
          var items = (_historyFilter === "workouts" ? _historyData.workouts : _historyFilter === "cardio" ? _historyData.cardio : _historyData.recovery) || [];
          if (from) items = items.filter(function(i) { return (i.date || "") >= from; });
          if (to) items = items.filter(function(i) { return (i.date || "") <= to + "z"; });
          var container = $("history-container");
          if (container) container.innerHTML = items.length ? items.length + " results" : '<p class="no-data">No results in this range.</p>';
        }
      });
    }

    // Export button
    var exportBtn = $("export-btn");
    if (exportBtn) {
      exportBtn.addEventListener('click', function() {
        fetch("/api/history").then(function(r) { return r.json(); }).then(function(data) {
          var md = "# Workout History\n\n";
          (data.workouts || []).forEach(function(w) {
            md += "## " + (w.session_type || "Workout") + " - " + w.date + "\n";
            md += "Sets: " + (w.total_sets || 0) + " | Volume: " + (w.total_volume || 0) + " lbs | Duration: " + (w.duration_minutes || "?") + " min\n\n";
            (w.exercises || []).forEach(function(ex) {
              md += "- " + (ex.machine || "?") + ": ";
              md += (ex.sets || []).map(function(s) { return fmt(s.weight_lbs) + "x" + s.reps; }).join(", ");
              md += "\n";
            });
            md += "\n";
          });
          var blob = new Blob([md], { type: "text/markdown" });
          var a = document.createElement("a"); a.href = URL.createObjectURL(blob);
          a.download = "workout-history.md"; a.click();
        });
      });
    }
  }

  // ── Apple Health Tab ─────────────────────────────────────────────────────
  function loadHealth() {
    // Status + Summary
    fetch("/api/apple-health/summary").then(function(r) { return r.json(); }).then(function(d) {
      // Status card
      var statusText = $("ah-status-text");
      var statusBadge = $("ah-status-badge");
      if (statusText) statusText.textContent = d.data_source || "Apple Health";
      if (statusBadge) {
        var total = d.workouts_total || 0;
        statusBadge.textContent = total > 0 ? "✅ " + total + " workouts" : "⏳ No data";
        statusBadge.style.background = total > 0 ? "rgba(16,185,129,0.2)" : "rgba(255,255,255,0.1)";
        statusBadge.style.color = total > 0 ? "var(--success, #10b981)" : "var(--muted, #888)";
      }

      // Summary cards
      setText("ah-workouts-total", d.workouts_total || 0);
      setText("ah-workouts-7d", "last 7d: " + (d.workouts_7d || 0));
      setText("ah-avg-sleep", d.avg_sleep_7d ? d.avg_sleep_7d.toFixed(1) + "h" : "—");
      setText("ah-sleep-days", "days: " + (d.sleep_total_days || 0));
      setText("ah-avg-steps", d.avg_steps_7d ? Math.round(d.avg_steps_7d).toLocaleString() : "—");
      setText("ah-steps-days", "days: " + (d.steps_total_days || 0));

      // Vitals summary
      var rhr = d.latest_rhr != null ? d.latest_rhr.toFixed(0) : "—";
      var hrv = d.latest_hrv != null ? d.latest_hrv.toFixed(0) : "—";
      setText("ah-vitals", rhr + " / " + hrv);
    }).catch(function(err) { console.error("[health-summary]", err); });

    // Workouts table
    fetch("/api/apple-health/workouts").then(function(r) { return r.json(); }).then(function(d) {
      var tbody = $("ah-workouts-body");
      if (!tbody) return;
      var ws = d.workouts || [];
      if (!ws.length) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:20px;color:var(--text-secondary,#94a3b8);">No Apple Health workouts. Sync via Health Auto Export app or POST to /api/apple-health/sync</td></tr>';
        return;
      }
      tbody.innerHTML = ws.slice(0, 20).map(function(w) {
        return '<tr>' +
          '<td style="padding:6px 8px;">' + esc(w.date || "") + '</td>' +
          '<td style="padding:6px 8px;">' + esc(w.activity || w.workoutActivity || "?") + '</td>' +
          '<td style="text-align:right;padding:6px 8px;">' + (w.duration_min || w.duration || "—") + '</td>' +
          '<td style="text-align:right;padding:6px 8px;">' + (w.kcal || "—") + '</td>' +
          '</tr>';
      }).join("");
    }).catch(function() {});

    // Sleep chart
    fetch("/api/apple-health/sleep").then(function(r) { return r.json(); }).then(function(d) {
      var container = $("ah-sleep-chart");
      if (!container) return;
      var items = d.sleep || [];
      if (!items.length) { container.innerHTML = '<div style="color:var(--text-secondary,#94a3b8);padding:20px;text-align:center;width:100%;">No sleep data. Sync Apple Health data to see sleep trends.</div>'; return; }
      renderBarChart(container, items.slice(-30), function(s) { return s.hours || s.value || 0; }, function(s) { return s.date || ""; }, "h", 12);
    }).catch(function() {});

    // Steps chart
    fetch("/api/apple-health/steps").then(function(r) { return r.json(); }).then(function(d) {
      var container = $("ah-steps-chart");
      if (!container) return;
      var items = d.steps || [];
      if (!items.length) { container.innerHTML = '<div style="color:var(--text-secondary,#94a3b8);padding:20px;text-align:center;width:100%;">No steps data. Sync Apple Health data to see daily steps.</div>'; return; }
      renderBarChart(container, items.slice(-30), function(s) { return s.steps || s.value || 0; }, function(s) { return s.date || ""; }, "", 15000);
    }).catch(function() {});

    // Vitals table
    fetch("/api/apple-health/vitals").then(function(r) { return r.json(); }).then(function(d) {
      var tbody = $("ah-vitals-body");
      if (!tbody) return;
      var rhr = d.rhr || [];
      var hrv = d.hrv || [];
      if (!rhr.length && !hrv.length) {
        tbody.innerHTML = '<tr><td colspan="3" style="text-align:center;padding:20px;color:var(--text-secondary,#94a3b8);">No vitals data. Sync Apple Health data to see RHR/HRV.</td></tr>';
        return;
      }
      // Merge by date
      var byDate = {};
      rhr.forEach(function(r) { byDate[r.date || ""] = { date: r.date || "", rhr: r.value, hrv: null }; });
      hrv.forEach(function(h) {
        if (byDate[h.date || ""]) byDate[h.date || ""].hrv = h.value;
        else byDate[h.date || ""] = { date: h.date || "", rhr: null, hrv: h.value };
      });
      var rows = Object.values(byDate).sort(function(a, b) { return (b.date || "").localeCompare(a.date || ""); }).slice(0, 15);
      tbody.innerHTML = rows.map(function(r) {
        return '<tr>' +
          '<td style="padding:6px 8px;">' + esc(r.date) + '</td>' +
          '<td style="text-align:right;padding:6px 8px;">' + (r.rhr != null ? r.rhr.toFixed(0) : "—") + '</td>' +
          '<td style="text-align:right;padding:6px 8px;">' + (r.hrv != null ? r.hrv.toFixed(0) : "—") + '</td>' +
          '</tr>';
      }).join("");
    }).catch(function() {});
  }

  function renderBarChart(container, items, valueFn, labelFn, unit, maxVal) {
    if (!items.length) return;
    var max = maxVal;
    items.forEach(function(item) { var v = valueFn(item); if (v > max) max = v; });
    container.innerHTML = items.map(function(item) {
      var v = valueFn(item);
      var pct = max > 0 ? (v / max * 100) : 0;
      var bg = pct > 60 ? 'rgba(16,185,129,0.7)' : pct > 30 ? 'rgba(16,185,129,0.4)' : 'rgba(255,255,255,0.1)';
      return '<div style="flex:1;min-width:6px;background:' + bg + ';height:' + Math.max(pct, 2) + '%;border-radius:2px 2px 0 0;position:relative;" title="' + esc(labelFn(item)) + ': ' + fmt(v) + unit + '"></div>';
    }).join("");
  }

  function loadTabData(tabName) {
    if (_loadedTabs[tabName]) return;
    _loadedTabs[tabName] = true;
    switch (tabName) {
      case "dashboard": loadDashboard(); break;
      case "vitals": loadVitals(); break;
      case "workout": loadWorkout(); break;
      case "log-workout": break; // static form
      case "history": loadHistory(); break;
      case "analytics": loadAnalytics(); break;
      case "apple-health": loadHealth(); break;
      case "body": loadBody(); break;
      case "settings": loadSettings(); break;
    }
  }

  // ── Boot ─────────────────────────────────────────────────────────────
  function boot() {
    wireForms();
    wireVitalsCards();
    wireSettings();
    // Load dashboard immediately (it's the default active tab).
    // Also hydrate vitals because the dashboard includes the Vitals Quick card.
    loadDashboard();
    loadVitals();
    _loadedTabs.vitals = true;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  // Expose tab data loader globally for inline switchTab to call
  window.loadTabData = loadTabData;

  // Also expose as navigateToTab alias
  window.navigateToTab = window.switchTab;
})();

// ── Manual log tab glue (strength/cardio/recovery) ─────────────────────
(function () {
  function $(id) { return document.getElementById(id); }
  function today() { return new Date().toISOString().slice(0, 10); }
  function result(html, ok) {
    var el = $('log-workout-result');
    if (el) el.innerHTML = '<div style="color:' + (ok ? '#22c55e' : '#ef4444') + ';padding:8px;">' + html + '</div>';
  }
  function toNum(v, fallback) {
    if (v === '' || v == null) return fallback;
    var n = Number(v);
    return Number.isFinite(n) ? n : fallback;
  }
  function currentExerciseMeta(name) {
    var opt = document.querySelector('#log-exercise option[value="' + CSS.escape(name || '') + '"]');
    return opt ? { muscle: opt.dataset.muscle || 'unknown' } : { muscle: 'unknown' };
  }

  window.switchLogType = function (type) {
    document.querySelectorAll('.log-type-btn').forEach(function (btn) {
      btn.classList.toggle('active', btn.dataset.logType === type);
    });
    document.querySelectorAll('.log-panel').forEach(function (panel) {
      panel.classList.toggle('active', panel.id === 'log-' + type + '-panel');
    });
  };

  window.populateExerciseDropdown = function () {
    var select = $('log-exercise');
    if (!select || select.dataset.loaded === 'true') return;
    fetch('/api/exercises', { credentials: 'include' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var groups = {};
        (data.exercises || []).forEach(function (ex) {
          var muscle = ex.muscle || 'other';
          (groups[muscle] = groups[muscle] || []).push(ex);
        });
        var html = '<option value="">Select exercise…</option>';
        Object.keys(groups).sort().forEach(function (muscle) {
          html += '<optgroup label="' + muscle.replace(/_/g, ' ') + '">';
          groups[muscle].forEach(function (ex) {
            html += '<option value="' + ex.name.replace(/&/g, '&amp;').replace(/"/g, '&quot;') + '" data-muscle="' + muscle + '">' + ex.name + '</option>';
          });
          html += '</optgroup>';
        });
        select.innerHTML = html;
        select.dataset.loaded = 'true';
      })
      .catch(function () { result('Could not load exercise dropdown.', false); });
  };

  window.submitWorkout = function (e) {
    e.preventDefault();
    var f = e.target;
    var name = f.exercise.value;
    var meta = currentExerciseMeta(name);
    var sets = Math.max(1, parseInt(f.sets.value || '1', 10));
    var reps = Math.max(1, parseInt(f.reps.value || '1', 10));
    var weight = toNum(f.weight.value, 0);
    var rpe = toNum(f.rpe.value, 7);
    var payload = {
      session_type: 'manual strength',
      duration_minutes: Math.max(1, sets * 4),
      notes: f.notes.value || '',
      exercises: [{
        machine: name,
        muscle_group: meta.muscle,
        sets: Array.from({ length: sets }, function (_, i) {
          return { set_number: i + 1, weight_lbs: weight, reps: reps, rpe: rpe };
        })
      }]
    };
    fetch('/api/complete-workout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(payload)
    }).then(function (r) { return r.json(); }).then(function (res) {
      if (res.status === 'success') {
        result('✅ Strength logged.', true);
        f.reset();
        seedLogDates();
        if (window.loadTabData) window.loadTabData('history');
      } else {
        result('❌ ' + ((res.error && (res.error.message || res.error)) || res.message || 'Error logging strength'), false);
      }
    }).catch(function () { result('❌ Error logging strength.', false); });
  };

  window.submitCardio = function (e) {
    e.preventDefault();
    var f = e.target;
    var payload = {
      date: f.date.value || today(),
      activity_type: f.activity_type.value,
      duration_minutes: parseInt(f.duration_minutes.value, 10),
      avg_heart_rate: f.avg_heart_rate.value ? parseInt(f.avg_heart_rate.value, 10) : null,
      intensity: parseInt(f.intensity.value || '5', 10),
      notes: f.notes.value || ''
    };
    fetch('/api/add-cardio', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify(payload) })
      .then(function (r) { return r.json(); }).then(function (res) {
        if (res.status === 'success') { result('✅ Cardio logged.', true); f.reset(); seedLogDates(); }
        else result('❌ ' + ((res.error && (res.error.message || res.error)) || res.message || 'Error logging cardio'), false);
      }).catch(function () { result('❌ Error logging cardio.', false); });
  };

  window.submitRecovery = function (e) {
    e.preventDefault();
    var f = e.target;
    var payload = {
      date: f.date.value || today(),
      recovery_type: f.recovery_type.value,
      duration_minutes: parseInt(f.duration_minutes.value, 10),
      temperature: f.temperature.value ? parseInt(f.temperature.value, 10) : null,
      notes: f.notes.value || ''
    };
    fetch('/api/add-recovery', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify(payload) })
      .then(function (r) { return r.json(); }).then(function (res) {
        if (res.status === 'success') { result('✅ Recovery logged.', true); f.reset(); seedLogDates(); }
        else result('❌ ' + ((res.error && (res.error.message || res.error)) || res.message || 'Error logging recovery'), false);
      }).catch(function () { result('❌ Error logging recovery.', false); });
  };

  function seedLogDates() {
    ['log-date', 'cardio-date', 'recovery-date'].forEach(function (id) {
      var input = $(id);
      if (input && !input.value) input.value = today();
    });
  }

  function bootLogTab() {
    seedLogDates();
    window.populateExerciseDropdown();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bootLogTab);
  else bootLogTab();
})();
