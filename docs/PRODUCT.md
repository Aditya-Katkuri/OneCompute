# Product

## Register

product

## Users

The **OneCompute fleet console** is the operator-facing dashboard for NightShift — the
internal idle-compute grid. Two audiences, one screen:
- **Demo audience** (Microsoft stakeholders / hackathon judges) watching a 4–5 minute live
  demo on a projector. They must instantly trust what they see.
- **Fleet operators** connecting/approving employee devices, launching example workloads, and
  watching harvested work, device usage, and the instant-yield beat in real time.

Context of use: a live, projected, time-boxed demo. Legibility and trust beat density.

## Product Purpose

Make the truth of the system *land*: harvested compute is real, measured honestly, and
privacy-preserving. The console shows the fleet (devices + live CPU/GPU usage), lets an operator
approve a newly-joined device (device-code gate), launch the four example workloads (2 non-AI:
fractal render, param-sweep optimize; 2 AI: batch inference, synthetic data) across the fleet,
and watch per-tile outputs assemble (a Mandelbrot filling in band-by-band, AI completions, a
synthetic-data table, optimizer scores) alongside the honest "measured harvested throughput vs
the 1.8-ExaOPS theoretical ceiling" framing. Success = a green, legible end-to-end demo that
makes a skeptical viewer trust the system.

## Brand Personality

Clean, trustworthy, honest, premium-but-restrained. Confident without hype. Voice is plain and
exact ("The honest number."). It should feel like a serious internal tool, not a marketing page.

## Anti-references

- AI-slop SaaS dashboards: gradient text, side-stripe accent cards, neon-on-near-black, the
  big-number-with-gradient hero template.
- Over-animated, "alive" dashboards where motion is decoration rather than state.
- Vanity metrics: any headline throughput number shown *without* the honest measured figure and
  the disclosed theoretical ceiling beside it.

## Design Principles

1. **The demo path is sacred.** Every visual decision is judged by whether it makes the 4–5 minute
   demo more reliable and more legible on a projector.
2. **Honest measurement, always paired.** The measured harvested throughput sits next to (never
   instead of) the 1.8-ExaOPS ceiling; both are labeled for what they are.
3. **Earned familiarity.** Read like Linear/Stripe and disappear into the task; no invented
   affordances for standard actions.
4. **State legible at a glance.** Device and job state are conveyed by a consistent color + label
   vocabulary (idle/busy/yielded/pending/blacklisted), not by decoration.
5. **Projector-first legibility.** High contrast, no gradient text, embedded fonts so the typography
   is identical offline.

## Accessibility & Inclusion

WCAG AA contrast for text; `prefers-reduced-motion` fully honored (all entrance/ambient motion is
neutralized); `aria-live` regions for the fleet, events, and results; controls are real buttons,
keyboard-operable; state is never conveyed by color alone (every state also carries a text label).
