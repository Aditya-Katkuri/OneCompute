# OneCompute Pilot — Volunteer Consent & Quick-Start (one page)

Thanks for lending your spare compute. Please read this, then opt in.

## What this is
OneCompute runs **company batch work** (e.g. test/eval jobs) on your machine's **spare capacity**
— the headroom that's sitting unused even while you work — and pays you **points** for the verified
work it completes. It is **opt-in** and you stay in control.

## What it will and won't do
- ✅ Runs work only in your machine's **learned spare headroom**, and **instantly steps aside**
  (sub-second) when **your own** apps need the CPU/GPU. You should notice **no slowdown**.
- ✅ **Never runs on battery** — only when you're plugged in.
- ✅ Each job runs **sandboxed**; it **cannot see your files** and jobs are **wiped** when they finish.
- ✅ It learns a usage pattern to size the headroom — that profile is **stored only on your machine**
  and **never uploaded**.
- ❌ It does **not** read your files, keystrokes, screen, or browsing.
- ❌ It does **not** run when you've opted out or closed it.

## Your controls
- **Opt out instantly:** press **Ctrl-C** in the agent window (or uninstall) — it stops immediately.
- **Caps & schedule:** ask the pilot lead to set CPU caps / hours / "work-hours-only" if you prefer.
- **Plugged-in only** is on by default.

## Quick start (the pilot lead will confirm the URL for you)
1. Confirm your machine can reach the orchestrator:
   ```powershell
   curl https://<orchestrator-url>/healthz      # should print {"ok": true}
   ```
2. Start the worker (signed `.exe` the pilot lead sends you, **or** from source):
   ```powershell
   # Signed exe (preferred on managed machines):
   .\onecompute-worker.exe --url https://<orchestrator-url>

   # ...or from source (user-space, no admin):
   $env:PYTHONPATH = "src"
   uv run python -m worker --url https://<orchestrator-url> --governor adaptive
   ```
3. Leave it running. It harvests headroom and yields when you get busy. **Ctrl-C to stop anytime.**

## What you get
Points for **verified** work your machine contributed, visible on the live dashboard
(`https://<orchestrator-url>/`). GPU-capable machines earn at a higher rate.

## Questions / issues
Tell the pilot lead immediately if you see any antivirus warning, a slowdown, or anything odd —
we'll stop your machine first and investigate. Your participation is voluntary and you can withdraw
at any time.

---
**I consent to participate in the OneCompute pilot on the terms above.**

Name: ________________________  Machine: ________________  Date: __________  Signature: ____________
