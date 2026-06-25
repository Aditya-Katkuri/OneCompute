"""Render the OneCompute launch film to a video, frame-accurately.

Seeks the GSAP timeline frame-by-frame (so render speed never affects smoothness), screenshots
each frame, then muxes them into an MP4 with ffmpeg if it's on PATH.

Run on a normal desktop with a GPU-capable browser (any modern machine). Requires Playwright
(`pip install playwright && playwright install chromium`). Captions are baked in unless you pass
--no-captions.

    python record.py                 # 30 fps MP4 (or PNG frames if ffmpeg is missing)
    python record.py --fps 60        # smoother
    python record.py --no-captions   # remove the overlay text
"""
from __future__ import annotations

import argparse
import functools
import http.server
import shutil
import socketserver
import subprocess
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
FRAMES = HERE / "frames"
PORT = 8222


def serve():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(HERE))
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", PORT), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--out", default="onecompute-launch.mp4")
    args = ap.parse_args()

    serve()
    from playwright.sync_api import sync_playwright

    if FRAMES.exists():
        shutil.rmtree(FRAMES)
    FRAMES.mkdir()

    qs = "?paused=1" + ("&captions=0" if args.no_captions else "")
    with sync_playwright() as p:
        # headed so the GPU renders the large type + glows smoothly
        browser = p.chromium.launch(headless=False, args=["--no-sandbox", "--force-color-profile=srgb"])
        page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
        page.goto(f"http://127.0.0.1:{PORT}/index.html{qs}", wait_until="load")
        page.wait_for_timeout(800)
        duration = page.evaluate("window.__filmDuration")
        total = int(duration * args.fps) + 1
        print(f"film {duration:.1f}s -> {total} frames @ {args.fps}fps")
        for f in range(total):
            t = f / args.fps
            page.evaluate(f"window.__film.pause(); window.__film.seek({t}, false);")
            page.screenshot(path=str(FRAMES / f"f_{f:05d}.png"))
            if f % 30 == 0:
                print(f"  {f}/{total}")
        browser.close()

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        out = HERE / args.out
        subprocess.run([
            ffmpeg, "-y", "-framerate", str(args.fps), "-i", str(FRAMES / "f_%05d.png"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", str(out),
        ], check=True)
        print(f"\nDONE -> {out}")
    else:
        print(f"\nFrames in {FRAMES}. Install ffmpeg to mux an MP4, or import the frames into any editor.")


if __name__ == "__main__":
    main()
