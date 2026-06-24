# Design

Visual system for the OneCompute fleet console (`src/dashboard/index.html`). A single
self-contained page — system is expressed as CSS custom properties in `:root`.

## Theme

A premium **light** system on a **warm terracotta** identity. Soft off-white surfaces, crisp
cards with hairline borders and gentle shadows, high-contrast ink text, and ONE disciplined
signature accent (terracotta) with a teal companion for GPU / measured-throughput cues. Chosen
for projector legibility and trust with Microsoft stakeholders. Committed identity — preserve it.

## Color (tokens)

| Role | Token | Value |
|---|---|---|
| Page background | `--bg` / `--bg-2` | `#faf6f4` / `#f1edeb` |
| Surface / soft surface | `--surface` / `--surface-soft` | `#fffdfc` / `#f6f1ee` |
| Border / strong | `--border` / `--border-strong` | `rgba(33,33,33,.10)` / `.20` |
| Ink / muted / soft | `--text` / `--text-muted` / `--text-soft` | `#212121` / `#4a4642` / `#6b6b6b` |
| Accent (brand) | `--accent` | `#c63f12` (terracotta) |
| Accent-2 (GPU / measured) | `--accent-2` | `#1f6f6a` (teal) |
| Brand gradient (bars/buttons only) | `--brand-grad` | `linear-gradient(135deg,#c63f12,#ff4e1b)` |

**State colors** (AA-contrast tints on white): idle `--idle #1f7a4d`, busy `--busy #2d5fd0`,
yielded `--yield #a85d06`, pending = `--accent`, blacklisted `--danger #b8341a`.

Strategy: **Restrained** — tinted neutrals + one accent. The gradient is used only on *fills*
(progress/race bars, primary buttons, the logo mark) — **never as text** (`background-clip:text`
is banned here; emphasis is by weight and scale). State is carried by full tinted borders + a
tinted background + a text label, never a side-stripe.

## Typography

- **Sans (UI/body/data):** `--sans` = `"Hanken Grotesk", "Segoe UI", system-ui, …` — embedded as
  base64 woff2 (weights 400/500/600/700) for offline fidelity.
- **Serif (wordmark + tagline only):** `--serif` = `"DM Serif Display", Georgia, …` — embedded
  (400 + 400 italic).
- Fixed rem-ish scale (product register), tabular-nums on all metrics. Hero figure
  `clamp(56px,9vw,104px)` weight 850, solid `--accent`.

## Components

- **Cards** (`--radius 18px`, hairline border, `--shadow`/`--shadow-soft`).
- **Worker tile:** device-type icon (laptop / dev box / GPU rig) + truncating monospace id + CPU/GPU
  chip; state tag pill; credits; CPU/GPU/Free-RAM metrics; a live CPU+GPU usage sparkline; and, when
  pending, a device-code + Approve block. State shown via full tinted border (+ bg tint for
  yielded/pending/blacklisted).
- **Launcher cards:** icon (terracotta = non-AI, teal = AI), title, `category · kind`, blurb,
  outline launch button.
- **Result panels:** title + kind + `completed/total` tiles, progress bar (scaleX, turns green when
  done), and a per-kind body — `<canvas>` Mandelbrot (band-by-band), optimizer score bars, AI
  prompt→completion cards, or a synthetic-data table.
- **Activity event:** full border + faint background tinted by event type (`--ev`), uppercase
  colored type label.
- Buttons have default/hover/active/disabled; bars animate with `transform: scaleX()` (never width).

## Layout

`max-width 1340px`, generous padding. Sections stack: header → hero (harvested + ceiling) → 4 stat
cards → workload launcher → workload results → fleet (`auto-fill minmax(232px,1fr)` tiles, top
aligned) + aside (ghost-bar race, workloads-run tally, activity feed). Responsive: hero/stats/layout
collapse to single column ≤1080px; stats to 1-col and header stacks ≤560px.

## Motion

Intentional and state-bound, 150–480ms, easing `cubic-bezier(0.22,1,0.36,1)`. Tile flip on yield,
busy sheen sweep, live-dot pulse, count-up tweens, result-panel + launcher-card entrances, a
one-shot ring when a workload completes, and an ambient ripple/drift background. **All motion is
neutralized under `prefers-reduced-motion: reduce`** (global override).
