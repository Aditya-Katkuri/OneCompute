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
