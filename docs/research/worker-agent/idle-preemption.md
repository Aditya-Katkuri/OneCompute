# Deep dive: idle detection and instant yield

Citation numbers match `README.md`.

## Build the idle detector as a state machine

NightShift should not ask “is the keyboard idle?” It should ask whether *all* admission gates are satisfied:

```text
idle_enough = input_idle_ms >= threshold_ms
session_ok = unlocked AND desktop_ready
power_ok = ac_power AND not battery_saver
util_ok = cpu_below_cap AND (no_gpu OR gpu_below_cap)
run_allowed = idle_enough AND session_ok AND power_ok AND util_ok
```

`GetLastInputInfo` provides the last input event time, but only for the calling session [1]. Therefore a Windows service in session 0 is the wrong place to make the final idle decision. WTS notifications add lock/unlock events that are faster and less ambiguous than waiting for the idle timer to roll forward [2][3]. Power notifications and `GetSystemPowerStatus` cover the “never on battery” rule with both event and snapshot paths [4][5][6].

## Yield latency budget

For the demo, the latency budget should be split:

- input/presence detection: ~0-250 ms if polling `GetLastInputInfo` at 250 ms and handling lock/unlock events immediately;
- worker state flip: ~single-digit ms in process;
- runner cooperation: bounded by chunk size, target <250 ms;
- kill path: close/terminate Job Object, then report yielded [8][10].

The runner should still check `should_yield()` because it makes graceful chunk boundaries possible. But the visual guarantee comes from the T3 Job Object: closing the final handle with kill-on-close terminates the process tree, and `TerminateJobObject` cannot be postponed by child processes [8][10].

## Practical PoC checklist

- Start worker from the logged-in desktop session, not Task Scheduler “run whether user is logged on or not.”
- Fail closed on any idle-signal exception.
- Debounce resume-to-run: after a yield, require a fresh idle window before pulling new work.
- Send heartbeat state: `idle`, `on_ac`, `locked`, `gpu_busy`, `yield_reason`, and `last_yield_ms`.
- Demo script should include mouse move and lock/unlock, because WTS and input idle prove different paths [1][3].
