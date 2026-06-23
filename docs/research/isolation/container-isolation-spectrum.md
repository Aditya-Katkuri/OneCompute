# Container isolation spectrum

## Spectrum

| Tier | Mechanism | Isolation value | Cost |
|---|---|---|---|
| Docker/native containers | Namespaces, cgroups, capabilities, seccomp/AppArmor profile, daemon policy [5] | Fast, practical, good filesystem/network/resource confinement when configured safely | Shared kernel; daemon and mounts are dangerous |
| gVisor | User-space application kernel (`Sentry`) intercepts Linux system calls [6][7] | Reduces direct host-kernel syscall exposure | Compatibility/performance tradeoff for syscall-heavy workloads |
| Kata Containers | OCI containers in lightweight VMs [8] | VM isolation with container workflow | More startup/resource overhead and platform integration work |

## NightShift decision

Docker-per-job is the PoC default because it can be configured immediately with `--network none`, read-only mounts, `--rm`, CPU/memory caps, and no admin-heavy Windows feature enablement. Do not expose the Docker daemon to job code; Docker documents that daemon control and host-root mounts are equivalent to high host privilege [5].

## Feature ideas

- Policy compiler from manifest to Docker args.
- Preflight that rejects host-root, Docker socket, writable input, privileged mode, and network unless explicitly allowed.
- Roadmap labels: `sandbox=docker`, `sandbox=gvisor`, `sandbox=microvm`.

## Sources

Uses README sources [5], [6], [7], [8].
