# OneCompute — Design Harness (how we make it beautiful)

> The operating doctrine for UI/UX work in this repo: how we plan, build, critique, and ship
> interfaces that look *designed*, not generated. It is the design counterpart to
> [`harness.md`](./harness.md) (how the org runs) and is wired to the tools in
> [`design-toolkit.md`](./design-toolkit.md) (what's installed and how to invoke it).
> Product intent lives in [`idea.md`](./idea.md); the shared engineering doctrine is
> [`.github/copilot-instructions.md`](../.github/copilot-instructions.md).
>
> Standards below are drawn from researched best practice (Refactoring UI, Material 3 motion,
> WCAG 2.2, web.dev/MDN performance) and from our installed skills (`frontend-design`,
> `impeccable`, `ui-ux-pro-max`). Every standard is written as a **checkable number or rule** so
> the design gate is a test, not an opinion.

---

## 0. The bar — the completeness standard (read first)

**The marginal cost of completeness is near zero. Do the whole thing, and do it right.** This is the
standard every pass is held to; the self-improvement loop in §2 and the gates in §6–§7 exist to *reach*
it, not to approximate it. Self-improvement here means each pass closes its own gaps before it is handed
up — never shipping a draft and calling the rest "later."

- **Ship the finished product, not a plan to build it.** When something is asked for, the deliverable is
  the working, integrated, verified result — implemented, tested, and documented — not a proposal, a
  stub, or a "phase 1."
- **The permanent fix over the workaround, every time.** Never present a workaround when the real fix is
  within reach. Never leave a dangling thread when tying it off takes five more minutes.
- **Never "table it for later"** when the permanent solve is reachable now. Time, fatigue, and complexity
  are not excuses — they are the reason the standard exists.
- **Search before building. Test before shipping. Ship the complete thing.** Reuse what already exists,
  verify it actually works (impeccable + the headless sampler + reference-match + review, per §7), and
  only then call it done.
- **The target isn't "good enough" — it's "holy shit, that's done."** Aim to genuinely impress, not to
  politely satisfy. If a reasonable person would still see a loose end, it is not finished.

When in doubt, **boil the ocean** — completeness is cheap; regret is not.

---

## 1. The thesis: distinctive on purpose

The bar is one sentence: **if someone could look at the screen and say "AI made that" without
doubt, it has failed.** Beauty here is not decoration laid on at the end — it is a series of
deliberate, defensible choices about palette, type, structure, and motion that are specific to
*this* subject (idle compute woken at dawn; fleets of machines; workloads routed like water).

Three looks are the current AI-default cluster — avoid them unless a brief explicitly asks:
1. warm cream bg (~`#F4F1EA`) + high-contrast serif display + terracotta accent;
2. near-black bg + one acid-green / vermilion accent;
3. broadsheet layout — hairline rules, zero radius, dense newspaper columns.

**The two-altitude reflex check** (run on every direction before building):
- **First-order:** could someone guess the theme + palette from the *category* alone? If yes, it's
  the first training reflex — rework the scene sentence and color strategy.
- **Second-order:** could someone guess the aesthetic from *category + obvious anti-reference*
  ("AI tool that's *not* SaaS-cream → editorial-typographic")? If yes, it's the trap one tier down.
  Rework until **both** answers are non-obvious.

Spend boldness in one place (the **signature** element), keep everything around it quiet, and —
per Chanel — remove one accessory before shipping.

---

## 2. The design loop (plan → build → critique → polish → ship)

Design is a loop, not a stamp, and it nests inside the org's quality gates from `harness.md`
(G0 self → G1 staff → G2 integration → G3 CEO). Every UI unit clears the **Design Gate (Gd)**
before it can pass G1.

```
  BRIEF ──▶ ① PLAN ──▶ ② ASSETS ──▶ ③ BUILD ──▶ ④ CRITIQUE ──▶ ⑤ POLISH ──▶ Gd ──▶ G1
 (intent)   tokens     reference     code w/      detector +     final pass    design   staff
            +signature  images        motion       review loop                  gate    review
                ▲                                      │
                └──────────── bounce w/ specific gaps ─┘
```

| Step | What happens | Primary tool | Output to clear the step |
|---|---|---|---|
| ① Plan | Pin subject, audience, the page's one job. Compose a 4–6 color token system, 2–3 type roles, a layout concept (ASCII wireframe), and **one signature**. Run the two-altitude reflex check. | `frontend-design` (taste) + `ui-ux-pro-max` search (palette/type/style libraries) | A named token plan that survives the reflex check. |
| ② Assets | Generate only the hero assets the plan calls for (textures, reference shots, a GLB). | **Higgsfield CLI** (`generate`, `upscale`, `remove_background`, 3D) | Asset saved in-project + a reference image we can `view` and color-sample. |
| ③ Build | Write production CSS/JS to the plan exactly. Motion is part of the build, not bolted on. | `gsap` patterns (vendored) | Working code, self-contained, reduced-motion path present. |
| ④ Critique | Run the detector; run a `rubber-duck` / `code-review` pass; for generative/WebGL, run the headless sampler. | `impeccable detect` + sampler + review subagents | Zero unjustified anti-patterns; metrics in range. |
| ⑤ Polish | Final quality pass: contrast, spacing rhythm, focus states, breakpoints, copy. | `impeccable` `polish`/`audit` doctrine | The Gd checklist (§6) all green. |

Loop ④↔⑤ until clean. Bounce with **specific** gaps ("contrast 3.9:1 on `.cap`", not "make it nicer").

---

## 3. The toolkit — and exactly when to reach for each

Full install/invocation detail is in [`design-toolkit.md`](./design-toolkit.md). When to use what:

| Tool | Reach for it when… | How (here) | Leave it off when… |
|---|---|---|---|
| **frontend-design** (skill) | Any new surface or redesign — to set palette, type, and the signature so it isn't templated. | Auto-fires on the brief; apply its plan-then-critique two-pass before coding. | Never — it's the taste floor on every UI task. |
| **ui-ux-pro-max** (skill) | Conventional UI — dashboard, admin, landing, forms, charts — where a curated palette/font/chart library beats hand-rolling. | `python ~/.copilot/skills/ui-ux-pro-max/scripts/search.py "<query>" --domain <product\|style\|typography\|color\|landing\|chart\|ux> -n 3` | Bespoke WebGL / one-of-a-kind motion — it overlaps `frontend-design` and costs ~17k tokens/fire. |
| **gsap** (skill + vendored lib) | Any motion: page-load sequence, scroll-triggered scene, hover micro-interactions, pinned cinematic. | Vendored at `src/website/vendor/gsap/`. Lead with ScrollTrigger (`scrub`, `pin`); wrap everything in `gsap.matchMedia()` for reduced-motion. | Static surfaces, or where CSS transitions are enough — don't pull GSAP for a hover. |
| **impeccable** (skill + CLI) | The **QA gate** on every HTML/CSS file, and as a command vocabulary (`polish`, `audit`, `critique`, `animate`, `colorize`, `typeset`, `layout`, `quieter`, `bolder`…). | `impeccable detect "src/<file>"` → exit 0 / `[]` = clean; non-zero lists anti-patterns. Strip giant base64 to a temp copy first (it chokes on huge inline data). | Backend-only work. |
| **Higgsfield** (CLI) | The page needs real visual assets — textures, hero photography, reference shots, or a GLB mesh. | `higgsfield generate create <model> --prompt "…" --aspect_ratio 16:9 --wait` (`nano_banana_2`, `gpt_image_2`, …). 3D: image → `remove_background` → `generate_3d`. | The self-contained Fleet Console — keep generated raster/video off it; assets belong on the marketing site. |

**Default high-value sequence for a motion-heavy page:** `frontend-design` (plan) → `ui-ux-pro-max`
(pull palette/type if conventional) → Higgsfield (hero assets) → build with `gsap` →
`impeccable detect` inline → `impeccable audit`/`polish` at the end.

> Naming truth (don't claim otherwise): there is **no MCP server** in this repo. Higgsfield and
> `impeccable` are global npm CLIs; the skills live in `~/.copilot/skills/`; GSAP is vendored
> because our pages ship as single no-external-dependency files.

---

## 4. Design standards (the checkable numbers)

### 4.1 Color & contrast
- **Contrast is non-negotiable:** body text **≥ 4.5:1**; large text (≥18px, or bold ≥14px) **≥ 3:1**;
  placeholders also **4.5:1**. The #1 AI failure is muted gray body on a tinted near-white — bump
  toward ink. Gray text on a colored bg → use a darker shade of the bg's own hue, or a transparency
  of the text color.
- **Use OKLCH** for new ramps. Pick a **color strategy** before colors: *Restrained* (tinted
  neutrals + one accent ≤10%) · *Committed* (one color 30–60% of surface) · *Full palette* (3–4
  named roles) · *Drenched* (the surface IS the color).
- Define **semantic tokens** (bg, surface, ink, muted, accent, error…), never raw hex in components.
- **Banned default:** the warm cream/sand/beige body bg (OKLCH L 0.84–0.97, C<0.06, hue 40–100) and
  its tell token names (`--cream`, `--sand`, `--paper`, `--bone`, `--linen`…). *(OneCompute's
  `--cream #F7EFE9` is a CEO-specified brand exception — keep it justified, don't spread it.)*

### 4.2 Typography
- **Scale, don't guess:** a real type scale (e.g. 12 · 14 · 16 · 20 · 24 · 32 · 40 · 48 · 64) with
  base body **16px**, line-height **1.4–1.6** body / tighter for display.
- **Pair on a contrast axis** (serif + sans, geometric + humanist) or one family in multiple
  weights. Never two similar-but-not-identical sans.
- Body line length **65–75ch**. Display clamp **max ≤ 6rem (~96px)**. Display letter-spacing
  **floor ≥ −0.04em**. Use `text-wrap: balance` on h1–h3, `text-wrap: pretty` on long prose.
- Tabular figures (`font-variant-numeric: tabular-nums`) on any live/animated number so it doesn't jitter.

### 4.3 Spacing & layout
- One **spacing scale** (multiples of 4/8). Vary spacing for rhythm; larger gaps signal grouping.
  Give elements room to breathe — whitespace is structure, not waste.
- **Flexbox for 1D, Grid for 2D.** Responsive grid without breakpoints:
  `repeat(auto-fit, minmax(280px, 1fr))`.
- **Cards are the lazy answer** — use only when truly the best affordance; **nested cards are always
  wrong.** Structure (numbers, eyebrows, dividers) must *encode something true*, not decorate.
- Build a **semantic z-index scale** (dropdown → sticky → modal-backdrop → modal → toast → tooltip).
  Never `999` / `9999`.

### 4.4 Motion (Material-3-aligned timings)
- **Durations:** micro-interactions **120–180ms** (hover, toggle); UI transitions **200–400ms**
  (menus, dialogs); large/page changes **400–700ms**. Keep similar actions the same duration.
- **Easing:** ease-out with exponential curves (quart/quint/expo) for things coming to rest;
  ease-in-out for interface transitions. **No bounce, no elastic.** `linear` only for progress.
- **Choreography:** stagger entrances, parent→child; animate the key element first; never animate
  everything at once. The tell is the *uniform reflex* (one identical entrance on every section),
  not motion itself — each reveal should fit what it reveals.
- **Scroll reveals must enhance an already-visible default.** Don't gate content visibility on a
  class-triggered transition — it never fires on hidden tabs/headless renders and ships blank.
- **`prefers-reduced-motion` is mandatory.** Every animation needs a reduce alternative (crossfade
  or instant). Wrap motion in `gsap.matchMedia()`. Reduced ≠ no motion for everyone — it's a path.
- **Premium materials beyond transform/opacity:** blur, backdrop-filter, clip-path, mask, glow are
  fair game *when they materially improve the effect and stay smooth.*

### 4.5 Interaction & feedback
- **Targets:** ≥ **44×44px** touch (HIG/Material), absolute floor **24×24px** (WCAG 2.2), **≥8px**
  apart. Every action gets **immediate** visible feedback (hover/press/loading/validation).
- **Visible keyboard focus** on every interactive element — never remove focus rings.
- Affordances must look interactive. Don't rely on hover alone; no instant (0ms) state changes.
- `position:absolute` dropdowns inside `overflow:hidden/auto` get clipped — use native
  `<dialog>`/popover, `position:fixed`, or a portal to escape the stacking context.

### 4.6 Performance budget
- **Animate `transform` and `opacity` only** (compositor-only). Never animate `width/height/top/left`.
- **60fps** = frame budget **< 16.67ms**. Drive JS animation with `requestAnimationFrame`; keep
  per-frame main-thread work minimal; avoid layout thrashing (don't read layout right after writing).
- **Core Web Vitals:** **INP < 200ms**, **LCP < 2.5s**, **CLS < 0.1** (reserve space; fixed sizes).
- `will-change: transform, opacity` **sparingly** — only on elements about to animate; too many GPU
  layers hurts.
- **WebGL/canvas:** draw only what changed, batch draw calls, minimize state changes, reuse textures.
  Render below native res when the field is soft (we run the lake at SCALE≈0.58, DPR 1).
- Our pages are **self-contained** — vendor assets locally; **zero external requests** at runtime.

### 4.7 Accessibility (WCAG 2.2 AA floor)
Keyboard-operable everything · visible focus · programmatic labels on all controls · errors
described **in text** (not color alone) · target size ≥24×24 · accessible auth (no puzzle-only
CAPTCHA) · consistent help · timeout warnings · respect reduced motion. Don't convey meaning by
color alone (add icon/text), especially in charts.

---

## 5. Absolute bans (match-and-refuse)

If you're about to write one of these, **rewrite the element with different structure**:

- **Side-stripe borders** — `border-left/right` > 1px as a colored accent on cards/alerts. → full
  border, bg tint, leading number/icon, or nothing.
- **Gradient text** — `background-clip:text` over a gradient. → one solid color; emphasis via weight/size.
- **Glassmorphism by default** — decorative blur/glass. → rare and purposeful, or nothing.
- **The hero-metric template** — big number + small label + supporting stats + gradient accent.
- **Identical card grids** — same-size icon+heading+text cards repeated endlessly.
- **Tiny tracked uppercase eyebrow above every section**, and **numbered markers (01/02/03) as
  default scaffolding** — numbers earn their place only when the section *is* a real sequence.
- **Text that overflows its container** — test heading copy at every breakpoint; the viewport is
  part of the design.
- **Two similar fonts**, **gray-on-gray**, **emoji as icons**, **body text < 12px**.

`impeccable detect` is the automated enforcer of most of these. A finding is either fixed or
**justified in writing** (e.g. our `--cream` is the CEO brand exception; one deliberate numbered
sequence is voice). "Justified" must name the reason, not wave it away.

---

## 6. Definition of Done for design (the Gd checklist)

A UI unit is done when **all** of these are true (this is the gate, run it before G1):

- [ ] **Distinctive** — passes the two-altitude reflex check; has exactly one signature.
- [ ] **On-brief / on-brand** — every color and type decision derives from the token plan; aligns with `idea.md`.
- [ ] **Contrast** — body ≥4.5:1, large ≥3:1, placeholders ≥4.5:1 (verified, not eyeballed).
- [ ] **Type** — scale + pairing on a contrast axis; line length 65–75ch; display ≤6rem, ≥−0.04em.
- [ ] **Layout** — spacing scale; semantic z-index; no nested cards; structure encodes meaning.
- [ ] **Motion** — durations in band; ease-out (no bounce); intentional choreography; **reduced-motion path present and tested**; reveals enhance an already-visible default.
- [ ] **Interaction** — targets ≥44px (≥24 floor); visible focus; immediate feedback on every action.
- [ ] **Performance** — transform/opacity only; rAF; 60fps; CLS<0.1; self-contained (0 external refs).
- [ ] **Accessibility** — keyboard-operable; labels; errors in text; color never the only signal.
- [ ] **Responsive** — no overflow/horizontal scroll at any breakpoint down to mobile.
- [ ] **Copy** — end-user voice, active, consistent vocabulary; empty/error states give direction.
- [ ] **Detector** — `impeccable detect` clean, or every finding individually justified.

---

## 7. How we QA design **without a browser** (our reality)

This session has no Playwright / screenshotting tool, so "looks right" is verified indirectly.
The four-part substitute (use as many as apply):

1. **`impeccable detect`** — the deterministic design gate over the file. Clean (exit 0) or each
   finding justified. This catches the slop families in §5 mechanically.
2. **Headless sampler** — for generative/WebGL surfaces, port the GLSL/draw logic to a small JS
   script (`session files/sample-shader.mjs`) and **measure structure** (sky/water averages, cloud
   coverage %, luma legibility range, element distinctness). *Keep it in lock-step with the shader —
   mirror every shader edit into the sampler or the metrics lie.*
3. **Higgsfield reference-image match** — generate the look you're targeting, **`view` the image**,
   color-sample it, and tune tokens/shader to the sampled values. The one way to "see" a target here.
4. **Review subagents** — `rubber-duck` for logic/perf/structural bugs (it caught the `H` variable
   collision), `code-review` for correctness. Plus a manual pass against the §6 checklist.

State the constraint honestly in status: *not pixel-verified in-session; validated via detector +
sampler metrics + reference match + review.*

---

## 8. Files in this harness

| File | Purpose |
|---|---|
| `docs/design-harness.md` | **this file** — the design doctrine, standards, gate, and QA loop |
| `docs/design-toolkit.md` | what's installed (skills/CLIs/GSAP) and the exact commands to invoke |
| `docs/website/PRODUCT.md` + `docs/website/DESIGN.md` | impeccable **brand-register** context for the marketing website (`src/website/`). Read these when designing the website. |
| `docs/PRODUCT.md` + `docs/DESIGN.md` | impeccable **product-register** context for the dashboard / Fleet Console (`src/dashboard/`). |
| `docs/harness.md` | how the agent org runs (G0–G3 gates this design loop nests into) |
| `docs/idea.md` | canonical product intent the design must keep aligning to |
| `.github/copilot-instructions.md` | shared engineering doctrine (auto-loaded) |
| `.github/skills/impeccable/` + `.github/hooks/` | project-installed impeccable skill + design-detector hook (`/impeccable <cmd>`) |
| session `files/sample-shader.mjs` | the headless QA sampler for the WebGL lake |
| session `files/design-system.md` | the live OneCompute token plan (palette + type + motion) |
