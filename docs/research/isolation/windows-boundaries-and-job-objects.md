# Windows boundaries and Job Objects

## Boundary map

- **Security boundary:** Hyper-V/Windows Sandbox, AppContainer/Win32 App Isolation, VM boundaries, and other Microsoft-recognized trust separations [9][13][14].
- **Resource governance:** Job Objects can group, limit, account for, and terminate process trees [10][11][12].
- **Policy hardening:** Defender ASR, Purview DLP, code signing, and Intune deployment help the agent pass enterprise controls rather than bypass them [30][31].

## Job Object details for T2/T3

A job object affects all associated processes; child processes are associated by default unless breakaway settings are used [10]. `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` is the key instant-yield primitive: closing the last handle terminates associated processes [11]. CPU rate control uses `CpuRate` as percentage times 100, so 20% is 2000 [12].

## Implementation implications

- Expose a handle/close function to T2, not just a PID.
- Do not allow breakaway unless a very specific workload requires it.
- Layer Job Objects outside Docker/Sandbox launchers where possible and inside Sandbox for the Windows beat if needed.
- On yield, close the handle first, then report yielded/requeue.

## Sources

Uses README sources [9], [10], [11], [12], [13], [14], [30], [31].
