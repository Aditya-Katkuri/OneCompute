# MXC isolation backend for OneCompute T3

Audience: OneCompute engineering. Status: design guidance, no code in this change.

This document describes how to add a Microsoft Execution Containers (MXC) backend to the existing T3 isolation seam. It is intentionally conservative: MXC is public preview, the public repository warns that current profiles are overly permissive, and the implementation must fail closed when a required policy cannot be enforced.

## 1. What MXC is

Microsoft announced Microsoft Execution Containers (MXC) at Build 2026 as an early-preview, policy-driven execution layer for agents on Windows and WSL. The Windows platform posts describe MXC as the developer control surface for a composable sandbox, where an app declares what an agent can access, such as files and network, and Windows enforces those constraints at runtime through containment primitives. The MXC policy spec describes the OS primitive layer as kernel-level enforcement. See the Build post, the Windows platform security post, and the MXC policy spec:

- https://blogs.windows.com/windowsdeveloper/2026/06/02/build-2026-furthering-windows-as-the-trusted-platform-for-development/
- https://blogs.windows.com/windowsdeveloper/2026/06/02/windows-platform-security-for-ai-agents/
- https://github.com/microsoft/mxc/blob/main/docs/sandbox-policy/v1/policy.md

The public MXC repository describes MXC as a sandboxed code execution system for untrusted code, model output, plugins, and tools, with multiple containment backends behind a unified JSON configuration schema and TypeScript SDK. It lists Windows, Linux, and macOS support, plus Windows backends including `processcontainer`, `windows_sandbox`, `wslc`, `microvm`, `hyperlight`, and `isolation_session`. See:

- https://github.com/microsoft/mxc
- https://github.com/microsoft/mxc/blob/main/sdk/README.md
- https://github.com/microsoft/mxc/blob/main/docs/schema.md

The target guarantees relevant to OneCompute are:

1. Files outside the declared policy are not writable, and should not be visible when they are not explicitly exposed. MXC policy has `filesystem.readwritePaths`, `filesystem.readonlyPaths`, and `filesystem.deniedPaths`; its policy spec says omitted filesystem policy means no filesystem access beyond the sandbox root, and the SDK README states default deny applies to filesystem, network, and UI policy. See:
   - https://github.com/microsoft/mxc/blob/main/docs/sandbox-policy/v1/policy.md
   - https://github.com/microsoft/mxc/blob/main/sdk/README.md
2. Unauthorized delete and rename should be blocked by making the per-job work directory the only writable path. Anything outside that directory is absent, read-only, or explicitly denied. This is the concrete policy mechanism OneCompute will depend on, not an application-level check.
3. Privilege elevation should be blocked by running the job under least privilege, with no extra capabilities, no user-session authority, and no job-time elevation. The Windows platform post says process isolation constrains files and network outside policy, while session isolation separates the sandboxed process from the human user's desktop, clipboard, UI, input devices, and active sessions. It also says Windows can assign a local ID or Entra-backed identity to attribute container activity to a principal. See:
   - https://blogs.windows.com/windowsdeveloper/2026/06/02/windows-platform-security-for-ai-agents/
   - https://blogs.windows.com/windowsdeveloper/2026/06/02/build-2026-furthering-windows-as-the-trusted-platform-for-development/
4. `wxc-exec.exe` must not elevate during job execution. The MXC host preparation doc says privileged setup lives in `wxc-host-prep.exe`, while `wxc-exec.exe` never elevates itself. See:
   - https://github.com/microsoft/mxc/blob/main/docs/host-prep.md

Preview caveat: the public MXC README says this is early preview, current SDK-generated policies can be overly permissive, and no MXC profiles should be treated as security boundaries currently. The README also says denied paths are not yet supported on Windows. That means our implementation must validate the effective policy and fail closed. If the Windows preview cannot enforce a requirement we need, `mxc_available()` must report false or `_run_mxc` must raise an MXC infrastructure error before the job starts.

## 2. How OpenClaw uses MXC

OpenClaw is an open-source personal AI assistant and gateway. Its own README says OpenClaw can run tools, sessions, channels, and skills, and its security model warns that default tools run on the host for the `main` session while non-main sessions can be configured to run inside sandboxes. See:

- https://github.com/openclaw/openclaw

Microsoft's Windows Build post and Windows platform security post say OpenClaw now runs its Windows node and gateway contained on Windows by using MXC. The Build post also says OpenClaw has a Windows companion app for setup, and that Microsoft is continuing to make OpenClaw run securely on Windows. See:

- https://blogs.windows.com/windowsdeveloper/2026/06/02/build-2026-furthering-windows-as-the-trusted-platform-for-development/
- https://blogs.windows.com/windowsdeveloper/2026/06/02/windows-platform-security-for-ai-agents/

The `microsoft/openclaw-dev` sample is the complementary cloud pattern. Its Microsoft Learn page says the sample deploys OpenClaw to an ephemeral cloud sandbox, while local OpenClaw on Windows should use MXC, described there as a policy-driven runtime that contains the OpenClaw node and gateway on Windows. See:

- https://learn.microsoft.com/en-us/samples/microsoft/openclaw-dev/openclaw-dev/
- https://github.com/microsoft/openclaw-dev

The pattern to mirror is not OpenClaw's product shape. The pattern is: put the agent runtime behind an OS-enforced policy boundary, make the writable surface explicit and disposable, keep identity and audit separate from the human user, and keep a fallback story for hosts where the preferred isolation surface is absent.

### What OneCompute puts in the sandbox (job, not agent)

MXC was built to contain agents (OpenClaw-class assistants that autonomously run tools, hold sessions, and act on the host). OneCompute's contained principal is deliberately much smaller: a single lightweight, deterministic job, `python -m jobkit <in> <out>`, which main defines as "a signed, sandboxed unit of work with declared resource needs" (idea.md section 6). The non-AI job executors are pure-stdlib (architecture.md), so the sandboxed process has no autonomous tool use, no interactive UI, no network by default, no persistence, and it exits when the one job finishes. In OneCompute's own vocabulary the long-running "worker agent" is a separate process that pulls jobs, learns the idle profile, and reports results; it never itself runs inside the MXC sandbox. So OneCompute is a strictly more constrained containment case than the agents MXC targets, which is a strength of the story. Throughout this document, the sandboxed principal is a job/worker identity for audit, not an autonomous-agent identity.

## 3. Public MXC policy model and SDK surface

Confirmed public surface:

| Area | Confirmed facts | Sources |
| --- | --- | --- |
| Config | MXC uses JSON configuration with `version`, `containerId`, `containment`, `lifecycle`, `process`, `filesystem`, `network`, backend-specific sections, and experimental sections. Current stable schema is listed as `0.7.0-alpha`; dev schema is `0.8.0-dev`. | https://github.com/microsoft/mxc/blob/main/docs/schema.md, https://github.com/microsoft/mxc/blob/main/schemas/schema-version.json |
| Filesystem | `readwritePaths` allow read and write, `readonlyPaths` allow read only, and `deniedPaths` block access. Omitted policy is default deny in the policy spec. The README caveat says denied paths are not yet supported on Windows preview. | https://github.com/microsoft/mxc/blob/main/docs/sandbox-policy/v1/policy.md, https://github.com/microsoft/mxc |
| Network | Policy supports default block or allow, outbound controls, local network controls, allowed and blocked host lists, and proxy settings. The SDK README says Windows host allow and block lists are not implemented, so on Windows we can rely on default block for `Limits.network == "none"`, but cannot claim host filtering for allow mode. | https://github.com/microsoft/mxc/blob/main/sdk/README.md, https://github.com/microsoft/mxc/blob/main/docs/sandbox-policy/v1/policy.md |
| UI | Policy supports GUI/window access, clipboard, and input injection controls. SDK README says UI is blocked by default for 0.5.0 and later. | https://github.com/microsoft/mxc/blob/main/sdk/README.md, https://github.com/microsoft/mxc/blob/main/docs/sandbox-policy/v1/policy.md |
| Backends | Windows default is `processcontainer` on Windows 11 24H2 and later. Experimental Windows backends include `windows_sandbox`, `wslc`, `microvm`, `hyperlight`, and `isolation_session`; experimental backends require the `experimental` spawn option or `--experimental` CLI flag. | https://github.com/microsoft/mxc, https://github.com/microsoft/mxc/blob/main/sdk/README.md |
| SDK | `@microsoft/mxc-sdk` exposes `getPlatformSupport`, `createConfigFromPolicy`, `spawnSandboxFromConfig`, `spawnSandbox`, `spawnSandboxAsync`, policy discovery helpers, and state-aware lifecycle APIs. | https://github.com/microsoft/mxc/blob/main/sdk/README.md |
| Native run | Windows can run `wxc-exec.exe config.json`, `wxc-exec.exe --config-base64 <base64>`, `wxc-exec.exe --debug config.json`, and `--dry-run` validation. The SDK resolves binaries and uses `--config-base64`; `getPlatformSupport()` uses `wxc-exec --probe` on Windows. | https://github.com/microsoft/mxc, https://github.com/microsoft/mxc/blob/main/sdk/README.md, https://github.com/microsoft/mxc/blob/main/sdk/src/platform.ts, https://github.com/microsoft/mxc/blob/main/sdk/src/helper.ts |
| State-aware lifecycle | The SDK exposes `provisionSandbox`, `startSandbox`, `execInSandboxAsync`, `stopSandbox`, and `deprovisionSandbox`; the SDK README says this is currently implemented for `isolation_session` on Windows. | https://github.com/microsoft/mxc/blob/main/sdk/README.md |
| Diagnostics | MXC has debug logging and ETW diagnostics. Diagnostic output includes policy, sandbox spec, process lifecycle, identity, exit code, and timing. | https://github.com/microsoft/mxc/blob/main/docs/diagnostics.md |

Assumed for this preview design, to be verified during implementation:

- The one-shot `processcontainer` backend can be killed reliably by terminating the `wxc-exec.exe` parent process, or by an exposed teardown path keyed by `containerId`. Do not ship the MXC backend until a long-running job proves sub-second yield kills the sandboxed process tree.
- `processContainer.leastPrivilege: true` plus empty capabilities maps to the Windows least-privilege behavior we need for OneCompute. Confirm with `--debug` or diagnostics.
- The final GA policy will have a stable way to express explicit deny of delete and rename outside a writable work directory. In the current Windows preview, do not rely on `deniedPaths` alone because the README says denied paths are not yet supported on Windows.
- The OSS MXC config does not appear to expose a durable Entra agent identity field. `containerId` and log metadata can tag jobs now. Real Entra-backed identity belongs to the roadmap with Agent 365 or session isolation.

## 4. Mapping to the OneCompute isolation seam

Current contract to preserve:

- `src\isolation\runner.py` exposes `run_in_isolation(kind, input, limits, should_yield, host_side)`, `start_in_isolation(kind, input, limits)`, `active_boundary()`, and `JobHandle`.
- `run_in_isolation` writes `in.json` and `out.json`, stages the stdlib-only `jobkit` payload, runs the best available backend, polls every about 20 ms for `should_yield()`, and returns `{"yielded": true, "results": []}` when preempted.
- Docker is used only when `docker_available()` confirms the daemon responds. Docker infrastructure errors fall back to subprocess plus Job Object; job-level errors and timeouts do not silently re-run on a weaker boundary.
- `active_boundary()` reports the real default boundary: currently `"docker"` or `"subprocess+jobobject"`.
- `JobHandle.kill()` kills the real boundary, not just a client process. Docker kills the named container. Job Object closes the kernel handle.
- `host_side=True` currently forces the on-host subprocess plus Job Object path for GPU jobs, `ai.*` jobs, and manifests requesting `job_object` because Docker cannot see CUDA and the slim container intentionally does not receive AI provider credentials.

Add `src\isolation\mxc.py` beside `docker.py` and `jobobject.py`. Do not replace the existing backends. The default preference for non-host-side jobs should become:

1. MXC, when `mxc_available()` is true and the job policy dry-run passes.
2. Docker, when `docker_available()` is true and MXC is absent or fails before job start with an MXC infrastructure error.
3. Subprocess plus Job Object, with the existing warning on degraded filesystem boundary.

Recommended public helpers for `mxc.py`:

- `mxc_available(*, force: bool = False) -> bool`
- `reset_mxc_probe_cache() -> None`
- `build_mxc_config(work_dir, input_dir, payload_dir, output_dir, in_name, out_name, limits, container_id) -> dict`
- `_run_mxc(in_path, out_path, work_dir, limits, should_yield) -> dict`
- `_stop_mxc(container_id, proc, job_handle=None) -> None`

`active_boundary()` should return `"mxc"` when `mxc_available()` is true for the default non-host-side path, else `"docker"` when Docker is available, else `"subprocess+jobobject"`. This keeps dashboard and demo reporting honest.

`start_in_isolation()` should choose MXC first when available, returning a `JobHandle` that knows it owns an MXC process and any teardown handle. `JobHandle.kill()` should call `_stop_mxc` for MXC jobs before falling back to process termination.

Host-side jobs: for the first PoC, keep current `host_side=True` behavior unless a job is explicitly marked MXC-capable under host-side policy. Moving GPU and AI host-side execution under MXC is roadmap work because it needs policy for provider credentials, CUDA or DirectML access, cache directories, and possibly network allow mode.

## 5. `mxc_available()` probe and fallback behavior

Mirror the Docker cached probe shape:

- No import-time cost.
- 30 second TTL, same as Docker unless tests prove a different value is needed.
- `force=True` bypasses cache for tests and dashboard refresh.
- Missing executable, probe timeout, malformed probe JSON, dry-run failure, or unsupported required policy returns `False` and never raises.
- Never run `wxc-host-prep.exe` from the worker. Host prep is installer or admin work only.

Recommended probe sequence:

1. Locate `wxc-exec.exe`.
   - First honor `MXC_BIN_DIR` using the SDK layout: `%MXC_BIN_DIR%\x64\wxc-exec.exe` or `%MXC_BIN_DIR%\arm64\wxc-exec.exe`.
   - Then try `shutil.which("wxc-exec.exe")`.
   - If we vendor the SDK binary later, add that repository-relative path deliberately.
2. Run `wxc-exec.exe --probe` with a 5 second timeout. The public SDK uses `wxc-exec --probe` for Windows support detection. Treat non-zero exit, timeout, or invalid JSON as unavailable.
3. Inspect probe warnings. If warnings say host preparation is required, do not elevate. Continue only if the dry-run below succeeds for our exact policy shape.
4. Run a bounded `--dry-run` with a minimal OneCompute policy shape: schema `0.7.0-alpha`, `containment: "process"`, `lifecycle.destroyOnExit: true`, one benign `process.commandLine`, filesystem limited to a scratch work directory, `network.defaultPolicy: "block"`, and `processContainer.leastPrivilege: true` with no capabilities.
5. Return true only when both the probe and dry-run succeed.

This probe means MXC preview absence changes nothing on current machines. The runner will keep using Docker or the existing subprocess plus Job Object fallback. MXC failures before the job starts should log a warning similar to Docker and fall back. MXC job-level failures and timeouts should propagate, not re-run on a weaker backend.

## 6. Run and kill approach

Use the native `wxc-exec.exe` path from Python for the PoC. It avoids adding Node to the worker runtime and maps cleanly to the current `subprocess.Popen` based `JobHandle` contract. The TypeScript SDK remains the reference for policy construction, probing, and future state-aware lifecycle work.

Run path:

1. Create a per-job temp tree under the current OneCompute temp convention:
   - `input\in.json`, read-only policy.
   - `payload\src\...`, read-only policy, containing the same stdlib-only `jobkit` and `contracts.hashing` payload used by Docker.
   - `work\out.json` and scratch files, the only read-write policy path.
2. Build a unique `containerId`, for example `onecompute-<uuid12>`.
3. Build MXC config with `containment: "process"`, `lifecycle.destroyOnExit: true`, `process.cwd` set to `work`, and `process.timeout` set to `limits.timeout_s * 1000`.
4. Launch `wxc-exec.exe --config-base64 <json>` with `subprocess.Popen`, `stdout=PIPE`, `stderr=PIPE`, and a scrubbed environment.
5. Assign the `wxc-exec.exe` process to a Windows Job Object as a belt and braces kill path. Verify during implementation whether the sandboxed child remains in that tree. If MXC creates a child outside the Job Object, require an MXC teardown API before enabling the backend.
6. Poll every 20 ms, matching `_run_docker` and `_run_subprocess_with_existing_files`.

Kill path on `should_yield()` or timeout:

1. Mark outcome as yielded or timeout.
2. Call `_stop_mxc(containerId, proc, job_handle)`.
3. `_stop_mxc` closes the Job Object handle if present, sends termination to `wxc-exec.exe`, escalates to kill if the process is still alive after a short wait, and performs any MXC teardown command available for the selected backend.
4. For future `isolation_session`, use the state-aware lifecycle: `stopSandbox(sandboxId)` and `deprovisionSandbox(sandboxId)`, with the same sub-second deadline.
5. Return `{"yielded": true, "results": []}` on yield, and raise timeout on timeout.

Acceptance requirement: add a long-running job test that asks for yield immediately and proves the sandboxed process tree is gone in less than one second. If this cannot be proven on a host, `mxc_available()` should remain false for that backend.

## 7. Exact OneCompute job policy

The MXC backend should declare this policy for each non-host-side job:

```json
{
  "version": "0.7.0-alpha",
  "containerId": "onecompute-<job-id-or-uuid>",
  "containment": "process",
  "lifecycle": {
    "destroyOnExit": true,
    "preservePolicy": false
  },
  "process": {
    "commandLine": "<quoted-python> -m jobkit <input> <output>",
    "cwd": "<work-dir>",
    "env": [
      "PYTHONPATH=<payload-src>",
      "PYTHONDONTWRITEBYTECODE=1",
      "ONECOMPUTE_ISOLATION=mxc",
      "ONECOMPUTE_AGENT_ID=<worker-id-or-container-id>",
      "ONECOMPUTE_JOB_ID=<job-id>"
    ],
    "timeout": 600000
  },
  "filesystem": {
    "readonlyPaths": [
      "<input-dir>",
      "<payload-src>",
      "<exact-tool-runtime-paths-if-required>"
    ],
    "readwritePaths": [
      "<work-dir>"
    ],
    "deniedPaths": [
      "%USERPROFILE%",
      "%SystemRoot%",
      "%ProgramFiles%",
      "%ProgramFiles(x86)%",
      "\\\\*"
    ]
  },
  "network": {
    "defaultPolicy": "block"
  },
  "ui": {
    "allowWindows": false,
    "clipboard": "none",
    "allowInputInjection": false
  },
  "processContainer": {
    "leastPrivilege": true,
    "capabilities": []
  }
}
```

Policy rules:

- Deny by default. Do not add the repository root, `C:\Users`, `%USERPROFILE%`, desktop, documents, downloads, `%SystemRoot%`, Program Files, or UNC roots to readable or writable policy.
- The only writable path is the per-job work directory. Delete and rename outside that directory are therefore outside policy. A delete or rename attempt should fail because the target is not visible, is read-only, or is explicitly denied.
- The payload and input are read-only. A job can read its manifest and code payload, but cannot rewrite them during execution.
- Network is none by default: `network.defaultPolicy: "block"` for `Limits.network == "none"`. For `Limits.network == "host"`, use `defaultPolicy: "allow"` only for jobs that already requested host network under the current contract, and document that Windows preview host allow and block lists are not implemented.
- No privilege elevation. Set least privilege, keep capabilities empty, do not run host-prep during job execution, and do not forward admin tokens or user credentials.
- Identity for audit now is a job tag: `containerId` and `ONECOMPUTE_JOB_ID` in diagnostics and logs. Real Entra-backed identity (for the worker/job principal) is roadmap.
- If Windows preview cannot enforce `deniedPaths`, the backend must still not expose those paths. If the implementation cannot prove no access to sensitive roots, MXC must be treated as unavailable and the runner should fall back according to the existing order.

This policy directly satisfies the T3 isolation requirement in the form we can implement now: the job cannot delete files outside the sandbox because no outside path is writable or exposed, and it cannot elevate access because it runs with least privilege, no additional capabilities, no job-time elevation, and no human desktop/session authority.

## 8. PoC scope

Build now:

1. `src\isolation\mxc.py` with cached probe, config builder, run function, stop function, and test seams.
2. `runner.py` integration that tries MXC before Docker for non-host-side jobs.
3. `active_boundary()` returning `"mxc"` when the MXC probe and policy dry-run pass.
4. `JobHandle` support for MXC process and teardown.
5. Tests:
   - `mxc_available()` returns a bool and caches like Docker.
   - `force=True` bypasses cache.
   - Missing binary and probe failure return false.
   - Config builder exposes only input, payload, and work paths.
   - Network default maps to block for `Limits.network == "none"`.
   - MXC infrastructure error before start falls back to Docker or Job Object.
   - MXC job error and timeout do not silently re-run on weaker isolation.
   - Yield kills the MXC process tree sub-second on machines with MXC.
   - `active_boundary()` reports `"mxc"` when MXC is available, then Docker, then subprocess plus Job Object.
6. Documentation update to `docs\architecture.md` after code lands, not in this research-only change.

Do not build now:

- Do not change contracts to add MXC as a sandbox type until implementation needs it. The backend can be an internal preference first.
- Do not require Node or npm in the worker runtime for the PoC.
- Do not run `wxc-host-prep.exe` from the worker.
- Do not claim production-grade security until Microsoft removes the preview caveat and we validate the policy on target Windows builds.

## 9. Roadmap

- Real Entra-backed identity for the worker/job principal, and Agent 365 or Intune-managed policy, so audit can distinguish human activity from OneCompute job activity at the OS level.
- `isolation_session` for long-lived OneCompute worker sessions when it is available on supported Windows builds, using state-aware provision, start, exec, stop, and deprovision.
- Micro-VM tier for higher-risk jobs after MXC micro-VM support is stable enough for T3 workloads.
- WSL container tier for Linux-first workloads if it gives better Python or ML compatibility than Docker Desktop.
- GPU and AI host-side jobs under MXC, including CUDA or DirectML access, model SDKs, provider credentials, cache directories, and network policy.
- Central policy publishing and verification: same policy builder used by tests, runtime, and dashboard proof output.

## 10. Open questions for the implementer

1. What exact `wxc-exec --probe` JSON fields should OneCompute require for process containment on the target Windows build?
2. Does killing `wxc-exec.exe` terminate the sandboxed child process reliably, or do we need a state-aware teardown call even for one-shot process containers?
3. Can `processContainer.leastPrivilege` and empty capabilities be verified in diagnostics on the target machine?
4. How should we express explicit deny on Windows while the public README says denied paths are not yet supported there?
5. Which exact Python executable path is safe to expose read-only without granting broad user-profile or system-directory reads?
6. Should `Limits.network == "host"` continue to mean full outbound network under MXC, or should we introduce a narrower future contract before enabling networked MXC jobs?
7. Which Windows builds in the demo fleet have MXC binaries and OS support, and which require admin host prep that the worker must not perform?


