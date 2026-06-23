# Dashboard and demo patterns

## Recommended dashboard implementation

Use Streamlit or Gradio with periodic polling. Streamlit `st.fragment` can rerun independently with `run_every`, preserving the rest of the app while refreshing a live panel (https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment). Gradio `Timer` is a non-visible component that triggers events at a regular interval (https://gradio.app/api/markdown/timer). For 2-3 workers, polling `GET /state` every ~500 ms is simpler and more reliable than a custom SSE/WebSocket stack.

## Required panels

1. **Fleet tiles:** worker id, CPU/GPU badge, idle/busy/yielded/blacklisted, current slice, p95 heartbeat age.
2. **Points ticker:** accepted units × server-assigned class weight; blacklisted worker goes to zero.
3. **Throughput race:** live fan-out bar vs grey single-worker ghost baseline.
4. **Yield card:** green to amber with measured yield latency, e.g. `yielded in 0.3s`.
5. **Trust/isolation card:** challenge caught, points forfeited; sandbox/Docker isolation proof.
6. **Close card:** theoretical peak ceiling beside measured live throughput, never merged.

## Data contract discipline

`GET /state` is the dashboard truth source per NightShift frozen contracts. Seeded data is allowed only while building the UI scaffold. Final demo data must come from orchestrator/ledger state, including job progress, worker status, accepted units, yielded/requeued units, blacklist, and points.

## Demo build order

1. Seeded dashboard with all beats.
2. CPU fan-out and ghost baseline.
3. Live `GET /state` wiring.
4. AI SDK prompt-slice secondary beat.
5. Cheater/challenge tile.
6. Isolation card.
7. Scripted close with honest measured-vs-theoretical framing.
