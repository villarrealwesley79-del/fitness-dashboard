# 🏋️‍♀️ Gym Access Instructions

## Before You Leave for the Gym

1. **Start the app:**
   ```bash
   cd /Users/wesleyv/clawd/fitness-dashboard
   ./start_for_gym.sh
   ```

2. **Copy the URL:**
   - ngrok will show something like: `https://abc123.ngrok.io`
   - This is your gym URL - bookmark it on your phone

3. **Keep your Mac awake and connected to WiFi**

## At the Gym

- Open the ngrok URL on your phone (cellular works!)
- Log your workouts as usual
- Data syncs back to your Mac

## When You're Done

- Press `Ctrl+C` in the terminal to stop
- Your workout data is saved locally

---

**Quick Commands:**
- Home use: `./start_fitness_dashboard.sh` (localhost only)
- Gym use: `./start_for_gym.sh` (creates public URL)