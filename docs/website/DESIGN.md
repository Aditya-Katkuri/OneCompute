# Design

Visual system for the OneCompute **marketing website** (`src/website/index.html`). A single
self-contained page (zero external requests) — the system is expressed as CSS custom properties in
`:root`, with a raw-WebGL fragment-shader background. Brand register: here design IS the product.

## Theme

A premium **light** system on the OneCompute **dawn-pastel** identity (shared with the dashboard,
expressed louder here). Warm cream paper, soft drifting pastel light, deep plum ink, and a single
serif-italic beat against a clean sans. The signature is an interactive WebGL **dawn lake + sky**:
real mirror-reflective water broken by fine ripples, a cursor "shine" ripple, and a Higgsfield-
generated pastel cloudscape with dramatic near/far scale. Strategy: **Full palette** used
deliberately (the scene needs the whole pastel range), disciplined by editorial restraint
everywhere else. Committed identity — preserve it.

## Color (tokens)

| Role | Token | Value |
|---|---|---|
| Page background / paper | `--cream` / `--paper` | `#F7EFE9` / `#F3E7EE` |
| Pastel range (scene + accents) | `--lilac` `--purple` `--mauve` | `#C9B8F0` / `#B59FE3` / `#9F86C9` |
| | `--pink` `--blush` `--peach` `--coral` | `#F2B5D4` / `#F8CCDD` / `#F8D3BC` / `#E8A38C` |
| Signature accent | `--magenta` / `--magenta-ink` | `#D98FC0` (fills) / `#b23d80` (AA-safe text) |
| Ink / muted | `--plum` / `--ink` / `--muted` | `#2B2233` / `#3A2E45` / `#6b6076` |
| Unified surface (panels) | `--surface` / `--surface-line` | `rgba(243,231,238,.82)` / `rgba(43,34,51,.16)` |
| Surface shadow + inner light | `--surface-sh` | `0 18px 46px -30px rgba(43,34,51,.42), inset 0 1px 0 rgba(255,255,255,.5)` |

**Gradients** (`--grad` mauve→magenta→coral, `--grad-dark` plum→magenta→coral) are used **only on
fills** (decorative orbs, the logo, bar fills) — **never as text**. `background-clip:text` is banned:
the hero focal word and every live number are solid `--magenta-ink` / `--plum` (verified ≥4.5:1).
The panel `--surface` is one material so "frosted glass over the lake" and "tinted card on cream"
read as a single language deepening down the scroll.

## Signature — the WebGL "Waking Field"

A full-viewport `<canvas id="fluid">` (`position:absolute`, `height:100vh`, scrolls away) running a
raw GLSL fragment shader (vertex/fragment in `<script id="vert|frag">`), at render `SCALE 0.58`,
`DPR 1`, with a graceful **CSS pastel-orb fallback** if WebGL fails to compile.
- **Lake (lower 60%, horizon `H=0.6`):** real mirror reflection of the sky/clouds, Fresnel-weighted
  (strong mirror near the horizon), broken by fine high-frequency ripples + sharp specular glitter,
  desaturated so it reads as water rather than a flat pastel field.
- **Cursor = a shine ripple:** moving over the water sends an expanding ring that modulates the
  water's specular/shine in a circle (unseen.co-style), not a cartoon wave; ripples originate where
  the cursor moves and are gated to the water only.
- **Sky:** a Higgsfield-generated pastel cumulus cloudscape (`textures/clouds.jpg`, POT) drifting
  slowly left, rendered front-most with the large near clouds dipping over the lake edge for depth;
  the cursor parts a small poof that reforms (no cloud color shift).
- A **Stage 2 scroll cinematic** (`#routing`): droplets fall → become energy droplets → are pulled
  into a terminal inside a pastel cloud running live routing/harvest commands → absorbed, a
  PFLOP·h-reclaimed counter climbing. Sticky-pinned canvas particle sim, IntersectionObserver-gated.

## Logo

The OneCompute hex-cluster mark (lavender→magenta→coral), inline SVG so the page stays
self-contained.

## Typography

- **Display / serif beat:** `--serif` = `"DM Serif Display", Iowan Old Style, Georgia, serif` —
  **embedded** woff2 (400 + 400 italic). Used with restraint: one italic word per scene (`.it`,
  solid `--magenta-ink`), against the clean sans.
- **Sans (UI / body / display headings):** `--sans` / `--disp` = `"Segoe UI Variable Text/Display",
  "Segoe UI", system-ui, -apple-system, sans-serif` (system stack; Hanken Grotesk woff2 weights
  400/500/600/700 are also vendored in `vendor/fonts/` as an embeddable body option).
- **Mono (eyebrows / captions / terminal):** `--mono` = `ui-monospace, "Cascadia Mono", Consolas`.
- Base body 17px. Live numbers use `tabular-nums`. `text-wrap: balance` on h1–h3, `pretty` on prose.
  Hero display intentionally exceeds the 6rem cap as the one documented signature exception.

## Components

- **Unified surface panels** (`--surface` + `--surface-sh` + present border) for stats, the routing
  box, steps, features, the CTA — one material on both the lake and the cream body. No nested cards.
- **Buttons:** primary (filled) + ghost; magnetic hover (fine-pointer only), `:focus-visible` ring.
- **Custom cursor:** a plum ring + dot that smoothly follows, tightens on click, grows on
  interactive elements (fine-pointer + hover only; native cursor otherwise).
- **Reactive headline:** hero/section headings split into per-letter spans that lift/scale toward
  the cursor; entrance via GSAP letter stagger.
- **Count-ups + bar fills:** ScrollTrigger-driven, `tabular-nums`, reduced-motion sets final value.

## Layout

`max-width 1280px` content wrap over the fixed-then-scrolling WebGL field. Sections stack:
hero → `#routing` cinematic → `#problem` → `#shift` → `#how` → `#yield` → `#trust` → `#ceiling` →
`#cta` → footer. Full-bleed scenes break out via `width:100vw; margin-left:calc(50% - 50vw)`.
Semantic z-index scale: field `0` → wrap `2` → nav `40` → grain/vignette `94–96` → cursor `120` →
skip-link `130`. Responsive down to mobile; headings tested for overflow at every breakpoint.

## Motion

GSAP (vendored: `gsap.min.js` + `ScrollTrigger.min.js`) + the WebGL rAF loop. Ease-out
(`--ease-out cubic-bezier(.22,1,.36,1)`, plus an overshoot `--ease` for playful beats); no bounce on
reveals. Scroll reveals **enhance an already-visible default** (`.reveal` is visible by default; the
hidden start is scoped to `html.js` so it never ships blank). **Full `prefers-reduced-motion` path:**
the WebGL field renders one static frame, GSAP entrances are skipped, the cinematic shows a composed
still, ambient CSS animations are neutralized. The field render also pauses once scrolled past the
hero and when the tab is hidden (perf).

## QA notes (no in-session browser)

Validated via `impeccable detect` (stable **4** justified: cream-palette = CEO brand, numbered
markers = the 01–05 narrative, clipped-overflow ×2 = decorative field/orbs), a headless GLSL→JS
**shader sampler** (kept in lock-step with the shader; checks lake/sky distinctness + legibility),
and Higgsfield reference-image matching. Self-contained: **0 external requests** (vendored GSAP +
fonts + the cloudscape texture).
