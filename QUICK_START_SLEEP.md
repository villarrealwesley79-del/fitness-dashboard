# Oura Sleep Integration - Quick Start Guide

## 🎯 What's New

Your fitness dashboard now has **comprehensive sleep tracking** from your Oura ring!

## 📊 Where to Find It

Open your dashboard: **http://localhost:5050**

Look for the new **"Sleep Insights"** section on the Dashboard tab (below Recovery).

## 🔍 What You'll See

### Last Night Summary
- **Duration** - How long you slept (in hours)
- **Sleep Score** - Oura's quality rating (0-100)
- **7-Day Average** - Your weekly average
- **Consistency** - How regular your bedtime is (✅ Excellent / ✔️ Good / ⚠️ Fair / ❌ Poor)

### 7-Day Trend Chart
- Blue line: Sleep duration (hours)
- Purple line: Sleep score
- Shows patterns over the last week

### Sleep Stage Breakdown (Last Night)
- **Deep %** - Restorative sleep (target: 13-23%)
- **REM %** - Dream sleep (target: 20-25%)
- **Light %** - Transition sleep
- **Avg HR** - Heart rate during sleep

## 🔄 How It Updates

### Automatic (Recommended)
Sleep data syncs automatically when you load the dashboard. The Recovery section pulls from Oura's daily summary, and Sleep Insights pulls from the detailed sleep endpoint.

### Manual Sync
To force a fresh sync of the last 30 days:
```bash
curl -X POST http://localhost:5050/api/oura/sync-sleep \
  -H "Content-Type: application/json" \
  -d '{"days_back": 30}'
```

## 💡 Smart Recommendations

Your workout recommendations now factor in **sleep debt**:
- If you have **< 5 hours sleep debt** → Normal intensity
- If you have **5-6 hours sleep debt** → Reduced intensity
- If you have **> 6 hours sleep debt** → Recovery day recommended

Check the **"Next Workout"** tab to see how sleep affects your recommended training.

## 📈 Sleep Quality Metrics

Your current sleep data shows:
- **Last night:** 7.0 hours (421 minutes)
  - 24% deep sleep ✅ (within optimal range)
  - 23% REM sleep ✅ (within optimal range)
  - 88% efficiency ✅ (good quality)
- **Bedtime consistency:** ⚠️ Poor (±10.5 hours variance)
  - *Tip: Try to go to bed within 30-60 min of the same time each night*

## 🎯 Quick Actions

### View Sleep Trends
1. Open dashboard
2. Scroll to "Sleep Insights" section
3. Check the 7-day chart

### Check Sleep Debt
1. Go to "Next Workout" tab
2. Look at the smart recommendation
3. You'll see sleep debt factored in (e.g., "Sleep debt 120 min (mild)")

### Sync Latest Data
Just refresh the page (F5) or:
```bash
curl -X POST http://localhost:5050/api/oura/sync-sleep -d '{"days_back": 2}'
```

## 📱 Mobile Access

Access from your phone via:
- **LAN:** http://10.5.0.2:5050 (on same WiFi)
- **Tailscale:** Use your Tailscale URL
- **ngrok:** Run `ngrok http 5050` for cellular access

## 🔧 Troubleshooting

### Sleep data shows "--"
- Wait 30 seconds and refresh the page
- Oura API may be processing data (usually updates by 8 AM)
- Check server logs: `tail /tmp/fitness-dashboard.log`

### Consistency shows "poor"
- This means your bedtime varies a lot
- Current variance: ±630 minutes (±10.5 hours)
- Goal: Get under 60 minutes for "good" consistency

### Manual import is gone
- ✅ This is intentional! 
- Oura API is now the single source of truth
- No more CSV/JSON uploads needed

## 📖 Full Documentation

For technical details, see:
- `OURA_SLEEP_INTEGRATION.md` - Complete implementation guide
- `DEPLOYMENT_NOTES.md` - Deployment summary

---

**Enjoy your sleep insights!** 😴💤
