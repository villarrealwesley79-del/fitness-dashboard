---
version: 1
name: Fitness — Daily Rhythm
description: The app's visual system (PER-1234). Adapted from an analysis of Oura's design language — warm paper, white cards, ink text, one action blue — trimmed to the tokens the app actually uses, with system fonts in place of the proprietary faces.

colors:
  paper: "#f7f1e8"      # page canvas, header
  card: "#ffffff"       # cards, sheets, fields, nav
  ink: "#1c1b1a"        # primary text, primary (non-workout) buttons, selection, focus ring
  stone: "#4a4741"      # secondary text, axis labels
  forest: "#224228"     # trend lines and bars
  action: "#2a72de"     # Start Workout / Complete Workout only
  hairline: "#e1dcd3"   # card borders, dividers, gridlines
  rule: "#cfc7ba"       # field and secondary-button outlines
  tint: "#efe7da"       # quiet selected surface
  good: "#2b6a3c"       # fresh / up / ok
  warn: "#7a4f00"       # stale-ish / caution / retry
  error: "#b42d1a"      # source #e33a22 darkened to pass AA on white and paper

typography:
  display:    { fontFamily: "Georgia, serif", fontSize: 34px (40px from 640px), fontWeight: 400 }   # greeting only
  title:      { fontFamily: "Georgia, serif", fontSize: 30px, fontWeight: 400 }                    # tab titles
  card-title: { fontFamily: sans, fontSize: 16px, fontWeight: 700 }
  body:       { fontFamily: sans, fontSize: 16px, fontWeight: 400, lineHeight: 1.45 }
  small:      { fontFamily: sans, fontSize: 12–13px }                                               # sources, meta
  sans: '"Helvetica Neue", Helvetica, Arial, system-ui, sans-serif'

spacing: { base: 8px, scale: [4, 8, 12, 16, 24, 32] }

rounded:
  card: 24px
  card-sm: 16px      # glance and metric tiles
  tag: 8px           # non-interactive labels (tags, chips, freshness)
  button: 999px      # functional buttons only
---

# Daily Rhythm

A warm training journal: a serif greeting gives character; clean sans-serif numbers on white cards over a cream canvas keep today's decision easy to scan.

## Rules

- **Flat.** No gradients, glow, blur, gradient text or elevation shadows. Cards separate from the canvas by a 1px hairline.
- **One blue.** `action` is reserved for the primary workout action (Start Workout, Complete Workout). Every other primary button is ink. Blue is never used for text (it is 4.1:1 on paper).
- **Pills are buttons.** Only functional buttons get the full radius. Tags, freshness chips and badges use 8px corners.
- **Sentence case.** No uppercase transforms or wide tracking. Acronyms (HRV, RPE) stay as they are.
- **Status by word and colour.** good / warn / error colours always sit beside words; tints are the same hue at 8% with a 35–40% line.
- **Charts.** Trend lines and bars are forest; gridlines hairline; axis text stone; no area fills. The readiness gauge keeps three bands (good, `#9a6a00`, error), each at least 3:1 on white. Chart colours are applied from CSS over the SVG attributes app.js sets.
- **Focus.** A 2px ink outline with a 2px offset on `:focus-visible` everywhere. `html` scroll padding keeps focused controls above the fixed nav.
- **Targets.** Buttons, fields, selects and summaries are at least 44px tall; primary buttons 48px.
- **Motion.** Short colour transitions only; `prefers-reduced-motion` turns transitions and animations off.
- **Theme.** Light only. The old dark theme was replaced, not kept as a variant.

## Layout

- Phone first: 16px gutters, 24px card padding, 16px between cards. Content column max 560px, centred on desktop.
- Bottom nav: eight word-only destinations on white, the current one in bold ink with a 2px ink underline.

## Contrast (WCAG, solid tokens)

| Pair | Ratio |
|---|---|
| ink on paper / card | 15.3 / 17.2 |
| stone on paper / card | 8.2 / 9.3 |
| white on action | 4.61 |
| error on paper / card | 5.6 / 6.3 |
| good on paper / card | 5.8 / 6.5 |
| warn on paper / card | 6.4 / 7.1 |
