# Fitness Dashboard Vision

Status: Draft for owner review  
Last updated: 2026-05-17

## Product Direction

Fitness Dashboard should become the owner's daily training and food command center: one place that decides what to do today, explains why, tracks what actually happened, watches what the owner eats, and learns from the result.

The app should not be a generic fitness tracker or a manual calorie spreadsheet. It should be a personal coach interface built around the owner's real training history, recovery data, body-composition targets, food intake, equipment preferences, and available time. The best version feels like opening one trusted briefing before a workout or meal, then closing the loop with almost no friction.

## Ideal End State

The ideal end state is a mobile-first coaching app that answers five questions quickly:

- What should I do today?
- Why is that the right session?
- What should I avoid because of soreness, recovery, fatigue, sleep, or recent load?
- What changed because of what I ate today?
- Did I execute the plan?
- What should change next time?

In that end state, the dashboard is not only showing metrics. It is turning those metrics into specific decisions. Oura, Apple Health, workout history, soreness, body measurements, food logs, nutrition targets, and training goals all feed a deterministic recommendation engine. The AI layer explains and adapts that plan, but it does not replace the safety rails or core prescription logic.

## Product Principles

1. The app should be decision-first. A user should not need to interpret ten charts before knowing whether to train hard, train light, recover, or adjust the plan.
2. The deterministic engine remains the source of truth. LLM output can translate, explain, analyze, and propose intent patches, but Python validates and applies the actual plan.
3. The workout execution flow matters more than the planning screen. Starting, modifying, logging, and completing a workout on mobile should be fast and reliable.
4. Wearable sync status must be honest. "Connected" or "synced" should mean there is recent evidence, not just old files on disk.
5. The app should remember enough context to reduce repeated manual work: equipment preference, common substitutions, fatigue patterns, soreness history, and completed-set performance.
6. Food logging should be low-friction. The owner should be able to snap a photo of popcorn, a meal, or a snack and get an estimated entry without typing everything by hand.
7. Trust beats magic. Any AI adjustment should show what changed, what was refused, what is estimated, and why.

## Target User Experience

The first screen should show a daily command brief: readiness, recovery signal, recommended session, avoid list, and the main reason behind the recommendation. The primary action should be starting the workout.

During a workout, the app should behave like a coach clipboard. It should prefill the recommended sets, reps, weights, rest guidance, and RPE target. The user should be able to swap an exercise, adjust sets, log performance, and complete the session without leaving the active workout flow.

After a workout, the app should summarize what happened: volume, intensity, missed targets, PR signals, fatigue impact, adherence to the recommendation, and the next likely adjustment. The user should not need to manually connect the dots between training load, soreness, sleep, and body trend.

For food, the owner should be able to snap a picture, optionally add a short note like "movie theater popcorn" or "half the bag," and let the app estimate calories, protein, carbs, fat, sodium, and confidence. When confidence is low, the app should ask for a quick correction instead of pretending the estimate is exact. Once accepted, the day's remaining calories/macros and coaching recommendation should update automatically.

## Product Pillars

### Daily Coaching

The app should generate a daily recommendation from training history, soreness, recovery, sleep, body goals, equipment preference, available time, and recent adherence.

### Food-Aware Coaching

The app should treat food intake as a live input to the day. A popcorn snack, high-protein meal, missed meal, or calorie-heavy dinner should adjust remaining macros, body-recomposition guidance, and training context without making the owner manually recalculate anything.

### Workout Execution

The app should support starting a recommended workout, modifying it safely, logging sets on mobile, preserving changes during swaps, and saving a complete session with stable IDs.

### Recovery and Readiness

Oura and Apple Health should provide the recovery context. Readiness, HRV, sleep debt, resting heart rate, steps, activity, and recent workouts should influence intensity and avoid-list decisions.

### Body Recomposition

The app should connect training output, food intake, weight, body-fat trend, nutrition targets, and goal progress. It should make body-composition status visible without turning the app into a tedious manual calorie tracker.

### History and Analysis

The app should make past work usable: workout history, volume by muscle, e1RM trends, adherence, soreness patterns, body trend, and AI workout analysis should all help answer what is working.

### Reliable Integrations

Wearable and AI integrations should fail gracefully. If Oura, Apple Health, or LM Studio is unavailable, the app should show the degraded state clearly and keep the deterministic plan usable.

## North Star Outcome

The owner can open the app before training or eating, trust the recommended plan, log food by photo, execute workouts on mobile, and see the next adjustment without manually reconciling multiple apps or spreadsheets.

## Success Measures

- The owner can start a workout from the daily recommendation in under one minute.
- The active workout flow preserves all logged inputs during exercise swaps and modal transitions.
- Wearable sync status reflects recent backend evidence.
- Photo food logs update daily calories/macros with confidence labels and editable estimates.
- The recommendation explains the training decision in plain language.
- Completed workouts update history, readiness/adherence views, and future recommendations.
- Food intake can adjust the same-day plan, such as remaining macros, recovery guidance, workout intensity context, or body-recomposition warning state.
- The app remains useful when the AI layer is unreachable.

## Assumptions

- The app is primarily for one owner, not a public multi-tenant product.
- Mobile use is the primary workflow, especially during workouts.
- Oura should remain the preferred sleep/recovery source, while Apple Health should remain the preferred workout/activity source when recent data is available.
- The current dark analytical design direction is still desired.
- Body recomposition, strength progression, and recovery-aware training are all core goals.
- Food photo recognition will produce estimates, not verified nutrition facts, so the app needs confidence, review, and correction states.

## Unknowns For Owner Review

- Should the ideal end state include a native iOS app, or should the PWA remain the main surface?
- Should food photo capture use a phone camera PWA flow first, or a native iOS shortcut/app flow?
- Should the app auto-adjust only calories/macros from food, or also adjust workout recommendations when the day is under-fueled, over target, or low protein?
- Should the app support multiple users later, or stay single-owner indefinitely?
- Should coaching optimize primarily for fat loss, lean gain, strength, consistency, or a rotating phase-based goal?
- What level of automation is acceptable for plan changes: suggest-only, one-tap apply, or auto-apply within rails?
