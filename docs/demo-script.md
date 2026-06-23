# NightShift demo script

## 4–5 minute run-of-show

1. **Idle fleet (0:00–0:35)** — Open the dashboard at `/`. Show worker tiles, CPU/GPU badges, zero or current credits, and the live `/state` status. Set the frame: NightShift harvests opt-in idle machines and credits only accepted useful work.

2. **Fan-out vs ghost bar (0:35–1:40)** — Submit the CPU fan-out `data.transform` workload. Narrate the tiles flipping idle → busy, completed jobs rising, and total credits ticking. Point to the grey **1 machine baseline** ghost bar and the live **fleet observed** bar: the claim is measured wall-clock throughput for this run, not theoretical TOPS.

3. **“And it also does AI” (1:40–2:25)** — Submit the `ai.batch_infer` prompt slices. Explain that each worker gets a small independent prompt batch through the SDK when keys are present, with a disclosed fallback if no provider key is configured. Keep this secondary: the CPU fan-out is the reliable throughput beat.

4. **Instant yield (2:25–3:05)** — Trigger foreground activity / yield. Call out the tile changing to amber and the work being requeued instead of fighting the user. The money shot: the employee stays in control.

5. **Caught a cheater (3:05–3:35)** — Run the challenge/ringer beat. A wrong result is rejected, the worker is blacklisted, and points are forfeited. Emphasize that rewards are based on verified results, not self-reported performance.

6. **Isolation proof (3:35–4:15)** — Show the sandbox or Docker proof: no host profile access / no network / wiped state. Be precise: CPU isolation is real in the demo path; GPU PoC control uses host-side Job Objects and stronger confidential GPU isolation is roadmap.

7. **Close: measured vs ceiling (4:15–5:00)** — End on two separate numbers: the theoretical Copilot+ NPU ceiling (for example, 40,000 devices × ~45 TOPS ≈ 1.8 ExaOPS peak INT8) and today’s measured harvested throughput from the live demo. The pitch: NightShift is delay-tolerant internal batch capacity, not magic free ExaOPS.

