# CPU virtualization and Windows Sandbox

## Why this matters

NightShift's strongest visual isolation beat is a CPU job in a disposable Windows Sandbox: launch, attempt `C:\Users` access, fail, close, state gone. Microsoft documents Windows Sandbox as a lightweight isolated desktop using hardware-based virtualization, a separate kernel, and disposable state [1].

## Compute <-> hardware <-> software chain

1. **Hardware:** CPU virtualization and platform security capabilities allow the Microsoft hypervisor to interpose between host and guest [3].
2. **Hypervisor:** Hyper-V is a type-1 hypervisor, and Windows Sandbox relies on it for kernel isolation rather than a same-kernel process sandbox [1][3].
3. **Guest OS:** Sandbox starts a clean Windows instance; host-installed apps are absent, and closing the sandbox deletes files/software/state [1].
4. **Policy surface:** `.wsb` controls networking, mapped folders, vGPU, memory, clipboard, and LogonCommand [2].
5. **NightShift runner:** use LogonCommand to run the job, read-only mapped inputs, narrow output path, and `Networking=Disable` [2].

## PoC guidance

- Treat Windows Sandbox as an upside CPU-only demo beat, not the default path.
- Build a single pre-warmed Sandbox flow if the demo PC supports the feature and admin/elevation/reboot are already solved.
- Always disable network and clipboard unless the manifest explicitly requires them.
- Do not map writable host folders except a single output drop with controlled file names and sizes.

## Roadmap

For higher assurance, investigate Hyper-V-isolated containers or microVM-style execution once install/admin friction is acceptable. Keep the PoC on Docker + one Sandbox proof.

## Sources

Uses README sources [1], [2], [3], [4].
