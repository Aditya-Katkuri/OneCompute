# Deep dive: prior art and operating model

Citation numbers match `README.md`.

## HTCondor lessons

HTCondor execution points are explicitly owner-controlled machines where policy decides when jobs run and stop, and where machine attributes advertise both resources and state [26]. Its `ConsoleIdle` attribute reports seconds since console keyboard/mouse activity [27]. This validates NightShift's core model: employee-owned policy first, scheduler throughput second.

HTCondor also shows the right checkpoint humility. Self-checkpointing is application-specific, requires the executable to restart from its own checkpoint, and frequency depends on checkpoint write/transfer cost versus lost work [28]. Its file transfer docs distinguish normal exit output from `ON_EXIT_OR_EVICT`, where eviction-time output transfer enables resume-like behavior [29].

**NightShift PoC translation:** use chunk outputs and requeue for the demo. Real checkpointing is workload contract work, not T2 infrastructure magic.

## Folding@home lessons

Folding@home exposes simple user-facing controls: Light/Medium/Full and “While I’m working” vs “Only when idle.” Its own docs say Full increases fans/heat, and idle mode begins only after the system has not been used for several minutes [30]. This is the UX standard NightShift must meet: simple controls, visible politeness, and no surprise fan noise.

## What to copy

- Conservative default policy.
- Explicit employee control and transparent status.
- Idle admission and immediate eviction/yield on user return.
- Credit only completed/verified chunks, not claimed capacity.
- Treat churn as normal; design every job slice to be disposable.

## What not to copy for the PoC

- Long checkpoint plumbing before the yield demo.
- Complex multi-daemon production policy language.
- Any assumption that “idle” is one signal or that volunteers tolerate heat/fan surprises.
