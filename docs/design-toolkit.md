# Frontend design toolkit

The actual skills, plugin, and MCP used to build a polished, motion-heavy frontend. Portable to any
project. Install once at user scope and they activate automatically across every repo on the machine.

> **This repo runs on GitHub Copilot CLI, not Claude Code.** The toolkit, workflows, and taste below
> are identical either way — only the install locations and a couple of tool names differ. The body of
> this doc is the original Claude Code writeup; **see [OneCompute setup (GitHub Copilot CLI)](#onecompute-setup-github-copilot-cli)
> at the end for the exact way these are installed in this project** (skills in `~/.copilot/skills/`,
> `impeccable` + Higgsfield as global npm CLIs, GSAP vendored locally because our pages are self-contained).

## What's in it

| Layer | Tool | What it does |
|---|---|---|
| Skill (auto-fires) | **gsap** | GSAP v3.13+ best practices: the now-free plugins (ScrollTrigger, ScrollSmoother, SplitText, MorphSVG, MotionPath, Flip, Observer), the `useGSAP()` React hook, scroll `scrub`/`pin` patterns, `gsap.matchMedia()` for responsive + reduced-motion, Next.js / SSR gotchas, performance rules. |
| Skill (auto-fires) | **frontend-design** | Aesthetic direction and taste. Pushes for distinctive, intentional design and away from templated "AI default" looks (cream + serif, near-black + acid-green, broadsheet grids). Anthropic skill, Apache-2.0. |
| Plugin | **impeccable** | Two modes: (1) a deterministic file hook that auto-flags design-quality issues as you edit (side-tab accent borders, layout-property animations, overused fonts, etc.). (2) Explicit slash commands: `/impeccable polish`, `/impeccable audit`, `/impeccable critique`. Best used as an audit pass at the end of UI work. |
| Plugin (situational) | **ui-ux-pro-max** | Design-knowledge databases: 67 styles, 161 color palettes, 57 font pairings, 25 chart types, per-framework guidance (React, Tailwind, shadcn, etc.). Heavy on tokens (~17k per fire) and overlaps with `frontend-design`, so leave disabled by default and enable when starting conventional UI work — dashboards, admin panels, marketing landing pages — where pulling from a curated palette/font/chart library beats hand-rolling. Less useful for bespoke WebGL or one-of-a-kind motion pieces. |
| MCP | **Higgsfield** | Generates the visual assets the frontend ships. `generate_image` for textures and reference shots, `generate_3d` to turn a still image into a GLB mesh you can load with three.js. Also includes `upscale_image`, `outpaint_image`, `remove_background`, `reframe` for cleanup. |

Skills install to `~/.claude/skills/`. Plugin via `claude plugin install`. MCP via Claude Code's MCP config. The two skills load after a Claude Code session restart.

## Verify the install

```bash
claude plugin list          # impeccable should be enabled
ls ~/.claude/skills/        # frontend-design, gsap
```

And check the MCP list in your Claude Code config for the Higgsfield server.

## The one project-local step

GSAP itself is a library. Install it in each project that uses the `gsap` skill:

```bash
npm i gsap @gsap/react
```

All GSAP plugins are bundled and free for commercial use (Webflow made the entire Club library free in April 2025). Lead with `useGSAP()` + ScrollTrigger (`scrub`, `pin`) for scroll-driven scenes, and always wrap motion in `gsap.matchMedia()` so `prefers-reduced-motion` users get a static path.

## Asset workflow with Higgsfield

For a 3D mesh:
1. `generate_image` with a tight prompt → reference PNG
2. (optional) `remove_background` to isolate the subject
3. `generate_3d` on that image → GLB mesh
4. Drop the GLB into `public/models/` and load via three.js `GLTFLoader`

For a flat texture (rack faces, hull skin, hero photography):
1. `generate_image` with the prompt
2. (optional) `upscale_image` to 2K/4K if you need detail at close range
3. Save into `public/textures/` and load via three.js `TextureLoader` or as a CSS background

## The high-value workflow

For a polished, opinionated frontend (especially anything with motion):

1. **frontend-design** auto-fires on the brief → produces a palette + type plan that isn't the AI default
2. **Higgsfield** (`generate_image`, `generate_3d`) makes the hero assets the design plan calls for
3. **gsap** auto-fires on motion work → guides timelines, scroll-triggered reveals, reduced-motion gating
4. **impeccable** hook flags AI-tell patterns inline as you write CSS
5. **`/impeccable audit`** at the end of the pass for a final polish review

## Notes

- `frontend-design` will flag any direction that feels templated even if the brief is loose. Trust it; if you're getting a generic look it usually means a token wasn't decided deliberately.
- `impeccable`'s most useful catches are the side-tab accent border (1px or 3px colored stripe on one side of a card) and animating layout properties like `width`/`height`. The hook suppresses after ~6 edits per file in a session — run `/impeccable audit` to refresh.
- `ui-ux-pro-max` enable/disable: `claude plugin enable ui-ux-pro-max@ui-ux-pro-max-skill` when you start a conventional UI project, `claude plugin disable ...` when you go back to bespoke work — it overlaps with `frontend-design` and the extra tokens are wasted on a one-of-a-kind piece.
- Higgsfield's `generate_3d` works best on a single subject shot from a 3/4 angle with a clean background. Generate the image first, then run `remove_background` before `generate_3d` for a cleaner mesh.

---

## OneCompute setup (GitHub Copilot CLI)

How the same toolkit is actually installed and used **in this repo**. (Maps the Claude Code instructions
above to our environment. Verified during the dashboard + marketing-site build.)

### Where things live
| Layer | Tool | Install location / invocation here |
|---|---|---|
| Skill | **frontend-design** | `~/.copilot/skills/frontend-design/` (Anthropic `SKILL.md`). Loads on a **Copilot CLI restart**. |
| Skill | **ui-ux-pro-max** | `~/.copilot/skills/ui-ux-pro-max/` with a working search CLI (below). Loads on restart. |
| Skill + CLI | **impeccable** | `~/.copilot/skills/impeccable/` **and** a global npm CLI `impeccable` (v3.1.0). |
| CLI (asset gen) | **Higgsfield** | global npm CLI `@higgsfield/cli` (v0.2.3) — installed as a terminal tool, not an MCP. |
| Library | **GSAP** | **vendored locally** at `src/website/vendor/gsap/` (`gsap.min.js` + `ScrollTrigger.min.js`). |

> Note on naming: there is **no MCP server** in this setup. Higgsfield ships here as the `@higgsfield/cli`
> terminal tool (image/video/3D/audio generation); `impeccable` is a skill **plus** a CLI, not a plugin
> with an editor hook. The substance (taste, motion guidance, asset gen, the slop detector) is identical.

### Install (one-time, user scope)
```powershell
# Node (machine had none): winget install OpenJS.NodeJS.LTS
npm install -g impeccable @higgsfield/cli      # the detector + the asset generator
# skills: clone the repos and copy the skill folder into ~/.copilot/skills/<name>/
#   frontend-design  -> anthropics/skills  (skills/frontend-design)
#   ui-ux-pro-max    -> nextlevelbuilder/ui-ux-pro-max-skill (.claude/skills/ui-ux-pro-max + src data/scripts)
#   impeccable       -> pbakaus/impeccable  (.github/skills/impeccable)
higgsfield auth login                           # interactive; needs a (paid) Higgsfield account
```

### Verify the install
```powershell
impeccable --version            # 3.1.x
higgsfield --version            # 0.2.x
Get-ChildItem ~/.copilot/skills # frontend-design, ui-ux-pro-max, impeccable (+ the built-ins)
```

### Use it here
- **Design QA gate (replaces the inline hook + `/impeccable audit`):** run the detector on any HTML/CSS
  file directly —
  ```powershell
  impeccable detect "src/dashboard/index.html"
  ```
  It exits non-zero and lists anti-patterns (tiny text, cramped padding, overused fonts, `transition: width`,
  em-dash overuse, side-tab borders, cream-palette, etc.). `[]` / exit 0 means clean. This is the gate we
  ran the OneCompute dashboard (18→clean) and site (18→6 justified) through.
- **Design intelligence (ui-ux-pro-max):** query the curated databases from the terminal —
  ```powershell
  python ~/.copilot/skills/ui-ux-pro-max/scripts/search.py "real-time monitoring dashboard" --domain style -n 3
  ```
  Domains: `product · style · typography · color · landing · chart · ux`.
- **frontend-design:** taste/aesthetic guidance — applied directly when planning palette + type (it's how we
  landed the Unseen-pastel direction instead of an AI-default look).
- **Higgsfield (assets):** `higgsfield generate create <model> --prompt "..." --aspect_ratio 16:9 --wait`
  (models: `nano_banana_2`, `gpt_image_2`, etc.; also video / 3D / audio). Save output into the project and
  reference it. **Keep generated raster/video off the self-contained Fleet Console** — it ships as a single
  no-external-deps file; generated media belongs on the marketing site.

### Project-local GSAP
Our pages are **self-contained single files**, so GSAP is **vendored** (downloaded `*.min.js` into
`src/website/vendor/gsap/`) rather than `npm i`-ed per project. For a bundler-based project, prefer
`npm i gsap @gsap/react` and lead with `useGSAP()` + ScrollTrigger, always wrapped in `gsap.matchMedia()`
so `prefers-reduced-motion` users get a static path (the dashboard + site both honor this).

---

## Operating guide — when & how to use each tool (you and your subagents)

> The canonical "how to wield the toolkit." Every command below was **run and verified on 2026-06-24**.
> The Chief of Staff and every dispatched subagent follow it. Pair it with the
> [design harness](./design-harness.md) (§3 is the at-a-glance table; this is the deep how).

### What's actually installed (verified by inventory)
| Tool | Form | Status |
|---|---|---|
| **frontend-design** | skill `~/.copilot/skills/frontend-design/` | doctrine, applied directly |
| **ui-ux-pro-max** | skill `~/.copilot/skills/ui-ux-pro-max/` + `scripts/search.py` (BM25 over CSV DBs) | runnable ✓ |
| **impeccable** | skill `~/.copilot/skills/impeccable/` + global CLI `impeccable` (3.1.0) | runnable ✓ |
| **Higgsfield** | global CLI `@higgsfield/cli` (0.2.3); binary `…/vendor/hf.exe` | restored ✓ (see note) |
| **GSAP** | **vendored library** at `src/website/vendor/gsap/` — **not a skill** | in use ✓ |

> **Correction to the writeup above:** there is **no `gsap` skill** in `~/.copilot/skills/`. GSAP is the
> vendored library plus the best-practice rules in this doc. The taste skill is `frontend-design`.
> (Design-adjacent skills also present: `excalidraw` for diagrams, `web-artifacts-builder` for HTML artifacts.)

> **If Higgsfield fails with `spawn UNKNOWN` / "binary not found":** its `hf.exe` was removed (a cleanup
> or Windows security quarantine — it happened mid-session). Restore with **`npm i -g @higgsfield/cli`**
> (re-fetches the ~8 MB binary, ~30 s). If it's removed again immediately, that's AV policy — don't
> fight it; fall back to a procedural / in-shader approach.

### The one rule: which tool for which job
| If you're… | Reach for | Not for |
|---|---|---|
| deciding *direction* (palette, type, the one signature) so it isn't an AI default | **frontend-design** — always, first | — |
| building **conventional UI** (dashboard, admin, landing, forms, charts) wanting a curated starting palette/font/style/chart | **ui-ux-pro-max** `search.py` | bespoke WebGL/one-of-a-kind pieces (it returns generic defaults) |
| writing or finishing CSS / auditing a surface | **impeccable** (`detect` = gate; command vocab = refines) | backend-only work |
| the page needs a real visual asset (texture, hero shot, 3D mesh) | **Higgsfield** | the self-contained Fleet Console; or when a **procedural** effect is more controllable (in-shader water beat a pasted texture for us) |
| any motion (scroll scene, reveal, micro-interaction) | **GSAP** (vendored) + the rules below | static surfaces; a CSS transition suffices for a hover |

### Tool cards (verified invocations)

**1 · frontend-design — taste & direction (fires first).** Doctrine, not a CLI: read its `SKILL.md` and
run the two-pass (brainstorm a *named* token plan → critique it against the brief, killing anything that
reads as a default) **before writing code**. Best context: the very first move on any surface. It is
*why* we never ship ui-ux-pro-max's defaults verbatim.

**2 · ui-ux-pro-max — curated knowledge (BM25 over real CSV DBs).** Run from its `scripts/` dir (it uses
relative imports):
```powershell
$py = "C:\Users\t-cfinney\AppData\Local\Programs\Python\Python312-arm64\python.exe"
cd "$env:USERPROFILE\.copilot\skills\ui-ux-pro-max\scripts"
& $py search.py "real-time monitoring dashboard" -d style -n 3   # domains: style·color·typography·product·ux·chart·landing·google-fonts
& $py search.py "reduced motion accessible animation" -d ux -n 2 # ux = fast a11y/interaction rule lookup
& $py search.py "data table" -s shadcn -n 3                       # per-stack: react·nextjs·vue·svelte·shadcn·threejs·tailwind…
& $py search.py "premium pastel marketing site, motion-heavy" --design-system -p "OneCompute"  # full starter system
```
Results carry keywords, AI-prompt keywords, CSS keywords, perf/a11y flags, design-system variables.
**Best context:** conventional UI where a curated library beats hand-rolling. **Verified caveat:** on a
one-of-a-kind brief it returns generic answers (it suggested editorial-black+pink / Newsreader-Roboto
for us) — treat it as a *library to draw from*, then let frontend-design make it distinctive. Token-heavy;
use deliberately, not on every edit.

**3 · impeccable — the design QA gate + refine vocabulary.**
```powershell
impeccable detect "src\website\index.html"   # exit 0 / [] = clean; exit 2 = lists anti-patterns
```
**Best context:** after *every* meaningful UI edit and as the final gate; each finding is fixed or
**justified in writing**. Our stable justified baseline on the site is **4**: `cream-palette` (CEO
`#F7EFE9` brand), `numbered-section-markers` (the 01–05 narrative), `clipped-overflow ×2` (`body` +
`.orbs`). It also names a command vocabulary (`polish·audit·critique·animate·colorize·typeset·layout·
quieter·bolder…`) — read `~/.copilot/skills/impeccable/reference/<command>.md` for that flow. Gotcha:
strip huge base64 to a temp copy before scanning (it can choke).

**4 · Higgsfield — generate the assets the plan calls for.**
```powershell
higgsfield account status
higgsfield model get nano_banana_2          # params: aspect_ratio, resolution 1k/2k/4k
higgsfield generate create nano_banana_2 --prompt "…" --aspect_ratio 21:9 --resolution 2k --wait
# also: remove_background · upscale_image · 3D (image→GLB) · video
```
Download the result URL, **downscale to a power-of-two** for WebGL textures, and `view` a small preview
to verify *before* integrating. **Best context:** photographic/painterly hero assets, textures, reference
shots, 3D meshes (the pastel cloudscape that fixed the cloud lattice came from here). **Worst context
(learned the hard way):** don't paste a flat asset where a **procedural** effect is more controllable and
interactive — the cursor-follow cloud PNG + sprite ripples looked fake and were reverted; in-shader water
+ a procedural shine ripple read better. Keep generated raster **off** the self-contained Fleet Console.

**5 · GSAP — motion (vendored).** Lead with `ScrollTrigger` (`scrub`, `pin`); wrap all motion in
`gsap.matchMedia()` so reduced-motion gets a static path; ease-out (no bounce); reveals must enhance an
**already-visible** default (never gate visibility on a class that may never fire).

### The end-to-end process (which tool at which phase)
```
BRIEF
  │  frontend-design ……… direction: named palette + type + ONE signature (kills AI defaults)
  │  ui-ux-pro-max ……… (conventional UI only) pull a starting palette/font/style/chart from the DBs
  ▼
ASSETS
  │  Higgsfield ……… only the hero assets the plan calls for (texture/shot/GLB) → view → integrate
  ▼                   …or decide a procedural / in-shader effect is the better, more interactive call
BUILD
  │  GSAP ……… motion (matchMedia + ScrollTrigger), self-contained
  ▼
CRITIQUE → POLISH (loop)
  │  impeccable detect ……… clean or justify
  │  headless sampler + reference-match + rubber-duck ……… for generative/WebGL (no browser in-session)
  ▼
SHIP — the Gd gate (design-harness §6)
```

### How subagents use the toolkit (the dispatch contract)
When the COS dispatches an Opus-4.8 design specialist, **put the toolkit in its prompt**:
- Point it at `docs/design-harness.md` (standards + the Gd gate) **and** this guide.
- Give it the **Node-PATH prepend** (`$env:Path = "C:\Program Files\nodejs;$env:APPDATA\npm;" + $env:Path`)
  and the **Python path** for `search.py`.
- Require **`impeccable detect` as its G0** self-check; report the count, fix or justify each finding.
- For generative/WebGL with **no browser**: require the **headless sampler** kept in lock-step + a
  reference match, and an honest "not pixel-verified" caveat.
- Scope it to **one region of the single `index.html`** — serialize writers, never two at once.

### Verified working (run 2026-06-24)
- `impeccable detect src/website/index.html` → 4 justified anti-patterns.
- `search.py … -d style|color|ux` and `--design-system -p OneCompute` → real structured results.
- `npm i -g @higgsfield/cli` → restored `hf.exe`; `higgsfield --version` → 0.2.3.
