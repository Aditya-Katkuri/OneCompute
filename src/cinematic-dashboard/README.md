# OneCompute — Cinematic Launch Film

A hardcoded, video-optimized cut of the OneCompute fleet console, built for a product-launch
clip. One self-contained page that **auto-plays a ~27-second camera move** on load:

1. **Open** on the dashboard — the harvested-credits hero ticks up.
2. **Scroll down** to the connected devices (laptops, a GPU rig, a dev box — all idle).
3. **Zoom in**; a giant cursor glides in and **clicks "Run AI job."**
4. A **giant arrow shoots up out of the dashboard** and **splits into many wires** that link a
   constellation of **workload nodes**.
5. **Scroll across the nodes** — each shows a live status for a different piece of the job
   (rendering a band, inferring a batch, optimizing a slice, synthesizing rows…).
6. The nodes **flip to verified / credited**, then resolve to the **OneCompute** end card.

Big, bold, legible type; only the beats that make OneCompute unique; the cream dashboard floats
in a deep-plum film world where the nodes glow. On the committed dawn-pastel brand (same logo,
fonts, and `--accent #b23d80` as the product dashboard).

## View it
Open `index.html` in **Chrome or Edge** (a GPU browser). It plays automatically. Resize the
window — the 1920×1080 frame scales to fit.

## Remove the overlay text
The captions are removable two ways:
- Open with **`index.html?captions=0`**, or
- Set **`CAPTIONS = false`** near the top of the `<script>` in `index.html`.

## Record the clip
**Easiest:** open `index.html` in Chrome/Edge and screen-record the ~27 s (Windows: `Win+Alt+R`).

**Automated MP4** (frame-accurate, any fps):
```bash
pip install playwright && playwright install chromium
python record.py                 # 30 fps onecompute-launch.mp4 (needs ffmpeg on PATH)
python record.py --fps 60
python record.py --no-captions   # bake it without the overlay text
```
`record.py` seeks the timeline frame-by-frame, so the output is perfectly smooth regardless of
machine speed. Run it on a normal desktop (it uses the GPU browser).

## Files
- `index.html` — the whole film (self-contained except the two vendored files below).
- `vendor/gsap.min.js` — GSAP (the animation engine).
- `vendor/fonts.css` — embedded Hanken Grotesk + DM Serif (reused from the product dashboard).
- `record.py` — the frame-accurate recorder.

## Editing the story
- **Camera shots** live in the `SHOT = {…}` object (each is `frame(worldX, worldY, scale)`).
- **The workload nodes** (names + statuses) live in the `NODES = […]` array.
- **Captions** are added per beat via `caption(tl, atTime, KICKER, "Sentence.")`.
- **Timings** are the numbers at the end of each `tl.to(…, atTime)` on the master GSAP timeline.

## Note on Higgsfield
The brief mentioned the Higgsfield MCP. Higgsfield is an AI image/video generator — it can't
render this exact UI choreography (a launch button, arrows splitting into labeled workload
nodes); it would hallucinate the interface. This film is built deterministically with GSAP so it
is pixel-accurate and on-brand. Higgsfield could later generate **b-roll / title plates** to
composite *behind* this film — that needs its CLI binary reinstalled (`npm i -g @higgsfield/cli`)
and `higgsfield auth login`.
