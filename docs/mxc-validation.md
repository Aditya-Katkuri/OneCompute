# MXC launch-path validation harness

Audience: OneCompute engineering and the Chief-of-Staff threat-model owner.
Status: test-only harness. No production behavior changes.

OneCompute's T3 isolation seam prefers Microsoft Execution Containers (MXC) as
its OS-enforced boundary, but that path is **fail-closed and inert** until a real
`wxc-exec` runtime is present: with no runtime installed, `mxc_available()`
returns `False` and `active_boundary()` reports `subprocess+jobobject`. The
existing MXC tests (`tests/isolation/test_mxc_runner.py`,
`tests/isolation/test_mxc_policy.py`) mock the runtime at a high level, so the
*real* launch protocol in `src/isolation/mxc.py` has never been exercised
end-to-end.

This harness closes that gap with a conforming **stub** `wxc-exec`
(`tests/isolation/fake_wxc_exec.py`). Pointing `ONECOMPUTE_MXC_EXE` at the stub
lets us drive the genuine probe and the genuine `_run_mxc` launch path against a
runtime that answers the exact contract, so that when a real `wxc-exec` preview
lands we already have a proven integration and a red/green oracle for protocol
drift.

## 1. How to run it

```powershell
$env:UV_LINK_MODE = 'copy'
uv sync --extra dev
uv run pytest tests/isolation/test_mxc_validation.py -v
```

The tests are Windows-only (`sys.platform == "win32"`) because the launch path
depends on Windows Job Objects for kill-on-close; they self-skip elsewhere. The
harness uses whatever interpreter runs pytest (via `sys.executable`), so it is
self-consistent regardless of which Python drives the suite.

## 2. The `wxc-exec` protocol the stub emulates

Everything below is reverse-engineered from `src/isolation/mxc.py`. The stub is a
faithful minimum: it answers precisely what these functions require and nothing
more.

### 2.1 Runtime resolution

`_find_mxc_exe()` resolves the executable from `ONECOMPUTE_MXC_EXE` (an explicit
override, via `_resolve_executable_override`), else `MXC_BIN_DIR` /
well-known install dirs (`_candidate_mxc_exes`), else `wxc-exec[.exe]` on `PATH`.
The harness sets `ONECOMPUTE_MXC_EXE` to a generated `wxc-exec.cmd` shim that
invokes `python fake_wxc_exec.py %*`. A `.cmd` is directly launchable by
`subprocess.run([...])` / `subprocess.Popen([...])` on Windows, which is how
`mxc.py` invokes the runtime.

### 2.2 Probe (`mxc_available` -> `_probe_runtime`)

`_probe_runtime()` treats the runtime as usable only if **all four** of these
succeed:

1. **Health document** - `wxc-exec --probe` exits 0 and prints JSON that
   `_probe_payload_is_supported()` accepts. The stub prints a document with a
   supported `processContainer` tier, `needsDaclAugmentation: false`, empty
   `warnings`, and no "host preparation" / "unsupported" text
   (`_probe_has_supported_tier`, `_probe_has_process_container_support`).
2. **Policy dry-run** - `wxc-exec --dry-run --config-base64 <b64>` exits 0 and
   emits no blocking warning (`_probe_policy_dry_run`,
   `_probe_output_has_blocking_warning`). The stub validates that the config
   decodes and exits 0 silently.
3. **Filesystem denial** - `wxc-exec --config-base64 <b64>` where the config's
   `process.commandLine` is a `python -c` script that tries to read and delete a
   marker file staged *outside* the sandbox's writable/read-only roots. The probe
   requires `read_denied` and `delete_denied` to be `true` and the marker to
   still exist (`_probe_filesystem_denial`). The stub enforces this for real (see
   section 3).
4. **Kill semantics** - `wxc-exec --config-base64 <b64>` runs a heartbeat loop;
   the OneCompute side assigns the runtime process to a Job Object and calls
   `_stop_mxc`, then asserts the process died within `_STOP_DEADLINE_S` and the
   heartbeat stopped advancing (`_probe_kill_semantics`). The stub wraps its
   child in its own kill-on-job-close Job Object so the child dies the instant the
   stub is terminated.

The probe container IDs all contain the substring `probe`
(`onecompute-probe`, `onecompute-fs-probe`, `onecompute-kill-probe`), which the
stub uses to keep the probe healthy even when workload-failure injection is armed
(section 4).

### 2.3 Run (`_run_mxc` -> `start_mxc` -> `build_mxc_command` / `build_mxc_config`)

For a real job, `_run_mxc` stages input/payload/writable dirs (via
`runner._stage_mxc_layout`), builds a config with
`build_mxc_config`, and launches `wxc-exec --config-base64 <b64>` under a Job
Object (`_popen_mxc`). The config the stub consumes contains:

- `process.commandLine` = `python -m jobkit <input_dir/in.json> <work_dir/out.json>`
  (Windows-quoted by `subprocess.list2cmdline`),
- `process.cwd` = the writable work dir,
- `process.env` = `PYTHONPATH=<payload>/src`, `PYTHONDONTWRITEBYTECODE=1`,
  `ONECOMPUTE_ISOLATION=mxc`, and the job identity vars,
- `filesystem.readonlyPaths` = `[input_dir, payload_dir]`,
  `filesystem.readwritePaths` = `[work_dir]`.

The stub launches the staged command line with that cwd and env, applies its
best-effort filesystem policy, and exits with the child's return code. `_run_mxc`
then reads `work_dir/out.json` and returns the parsed result, exactly as it would
with a real runtime. The stub substitutes the leading `python` token with its own
`sys.executable` so the staged job runs against a working stdlib on the host (a
real runtime would supply `python` from the container image).

### 2.4 Teardown

`_best_effort_mxc_teardown` calls `wxc-exec --teardown <id>` / `--stop <id>` with
a 50 ms timeout. The stub accepts both verbs and exits 0.

## 3. Filesystem enforcement in the stub

The probe's filesystem check (section 2.2, item 3) cannot pass unless the runtime
genuinely denies out-of-policy access, so the stub emulates enforcement in the
child process with a `sys.addaudithook` policy shim, imported via a generated
`sitecustomize` on the child's `PYTHONPATH`. The hook denies `open` (read/write),
`os.remove` / `os.unlink` / `os.rmdir`, and `os.rename` / `os.replace` for paths
that are reachable inside the **container root** (the common ancestor of the
declared roots) but fall outside the declared `readonlyPaths` / `readwritePaths`.
Writes to read-only roots are denied; writes to the writable root are allowed.

It is deliberately **permissive about paths outside the container root** (for
example the interpreter's own runtime and the stdlib), because a real base image
would provide those. This is an emulation of the *policy contract*, not a Windows
kernel boundary.

## 4. Infrastructure-failure injection

`mxc.py` distinguishes a runtime/infra failure (`_MxcInfraError`, which lets
`run_in_isolation` fall back to Docker/subprocess) from a job-level failure
(`RuntimeError`/`TimeoutError`, which propagate so no job is silently re-run on a
weaker boundary). The classifier lives in `_looks_like_mxc_infra_error` /
`_looks_like_job_failure` (`_MXC_INFRA_MARKERS` vs `_JOB_FAILURE_MARKERS`).

To exercise the fail-closed fallback, the stub emits
`wxc-exec: execution container failed to start (simulated host preparation
failure)` and exits non-zero when a `fail-infra` marker file exists in the
control directory named by `FAKE_WXC_CONTROL`. That message matches an infra
marker and none of the job-failure markers, so `_run_mxc` raises `_MxcInfraError`.
Injection is scoped to real workload containers (IDs starting with
`onecompute-job-`); probe containers are never failed, so `mxc_available()` still
reports healthy while a workload run fails over.

`FAKE_WXC_CONTROL` is set **inside** the `.cmd` shim (not in the process
environment) on purpose: `mxc._mxc_env()` forwards only a fixed allowlist of
variables to the runtime, so an environment variable set by the test would be
stripped before reaching the stub. Baking it into the shim survives that
filtering.

## 5. Protocol assumptions (documented, not asserted by the real runtime)

Where `mxc.py` leaves a detail unspecified, the stub emulates the closest
faithful behavior the code actually requires:

- **Interpreter substitution.** `mxc.py` builds a `commandLine` beginning with the
  literal `python`. A real runtime resolves `python` inside its container; the
  stub substitutes `sys.executable` on the host. The staged command, cwd, env,
  and file layout are otherwise used verbatim.
- **Container root = common ancestor of the declared roots.** `mxc.py` does not
  hand the runtime an explicit "container root"; the stub derives it with
  `os.path.commonpath(readonlyPaths + readwritePaths)`, which for OneCompute's
  staging (`input/`, `payload/src/`, `work/` under one root) resolves to the
  staging root the out-of-policy probe marker lives directly under.
- **Kill-on-close via a nested Job Object.** The kill probe assigns the *runtime*
  process to a Job Object after launch. To guarantee the staged child dies with
  the runtime regardless of assignment timing, the stub owns a
  `KILL_ON_JOB_CLOSE` Job Object for its child; terminating the stub closes that
  handle and reaps the child.
- **Permissive outside-root reads.** See section 3. The stub does not attempt to
  hide or deny the interpreter runtime, only out-of-policy user-data paths.

## 6. What this PROVES and does NOT prove

**Proves (OneCompute-side integration):**

- The runtime-resolution path (`ONECOMPUTE_MXC_EXE` / `MXC_BIN_DIR`) locates and
  invokes a `wxc-exec` executable the way `mxc.py` expects.
- The four-part probe wiring (`_probe_runtime`) accepts a conforming runtime, so
  `mxc_available(force=True)` returns `True` and `active_boundary()` returns
  `mxc`.
- The real `_run_mxc` launch path (config build, base64 handoff, command line,
  cwd/env staging, output readback) runs an actual OneCompute CPU job
  (`challenge`, `data.transform`) to a correct, real result. The integration test
  wraps `_run_mxc` with a pass-through counter to prove the genuine launch path
  executed, rather than a mock or a fallback.
- The infra-failure classification and fail-closed fallback
  (`_MxcInfraError` -> Docker/subprocess) behave as documented, and with no
  runtime present the seam correctly reports `mxc_available() == False` and falls
  back.

**Does NOT prove (out of scope for a stub):**

- Any real Windows kernel-enforced containment. The stub's filesystem boundary is
  an in-process Python audit hook, not an OS boundary, and it is permissive about
  runtime paths. It is not a confidentiality boundary and does not enforce network
  or UI/session isolation.
- That the R15 MXC preview caveats are retired. Per `docs/mxc-sandbox.md` and the
  public MXC README, the preview still ships **overly-permissive default
  policies**, **`deniedPaths` are unsupported on Windows**, and **no MXC profile
  should be treated as a security boundary yet**. Those caveats stand unchanged;
  this harness validates OneCompute's wiring, not Microsoft's enforcement.

## 7. Swapping in a real `wxc-exec`

When a genuine preview runtime is available, point the same env var at it and drop
the stub:

```powershell
$env:ONECOMPUTE_MXC_EXE = 'C:\Program Files\Microsoft\MXC\x64\wxc-exec.exe'
# or: $env:MXC_BIN_DIR = 'C:\Program Files\Microsoft\MXC'
uv run python -c "from isolation.mxc import mxc_available; print(mxc_available(force=True))"
uv run python -c "from isolation.runner import active_boundary; print(active_boundary())"
```

If the real runtime conforms to the probe contract (section 2.2) it returns
`True` / `mxc` and `run_in_isolation` will use it with no code change. If it does
not - because a preview caveat means a required policy cannot be enforced - the
probe fails closed and OneCompute keeps its Docker / Job Object behavior. Before
trusting a real runtime as a boundary, re-validate that `deniedPaths` are honored
on Windows and that the effective policy is not overly permissive; only then
should the boundary be treated as security-relevant.

## 8. STRIDE: Elevation of privilege / sandbox-escape (boundary B2)

**Boundary B2** is the per-job isolation boundary: untrusted job code
(`python -m jobkit`) executing inside the OS-enforced sandbox versus the worker
host it runs on. The STRIDE category is **Elevation of privilege** - job code
breaking out of the sandbox to read/write the worker's files, acquire the worker
identity or credentials, or run with more privilege than the low-privilege job
principal.

- **Threat.** A malicious or compromised job manifest, or a compromised control
  plane injecting a job, attempts to escape B2: escalate privilege, mutate DACLs,
  reach paths outside its work dir, or persist. MXC's declared guarantees
  (`build_policy`: deny-by-default filesystem, single writable work dir,
  `leastPrivilege`, no new privileges, no admin, no DACL mutation) are the primary
  mitigation once a real runtime enforces them.
- **What this harness changes for the threat model.** It validates the
  OneCompute-side wiring end-to-end (probe -> policy build -> launch -> readback)
  so a conforming runtime is exercised for real. It **does not** reduce B2 risk on
  its own: the stub is not a kernel boundary. Until a real `wxc-exec` enforces the
  policy, B2 continues to rest on Docker (a real boundary) or, when neither MXC nor
  Docker is available, on the fail-closed `--require-isolation` path (which refuses
  to run rather than degrade to the unsandboxed subprocess).
- **Residual risk.** The R15 preview caveats (overly-permissive defaults,
  `deniedPaths` unsupported on Windows, not a security boundary yet) mean MXC must
  not be treated as B2's enforcement until validated against a real runtime and the
  effective policy is confirmed restrictive. The probe's fail-closed design keeps
  an unvalidated runtime from silently becoming the boundary.

### Microsoft Windows platform-security alignment

OneCompute's B2 model maps directly onto the Windows platform-security posture for
agent sandboxing described in the Build 2026 material (see `docs/mxc-sandbox.md`
for citations): an app declares what the contained principal may access (files,
network, UI) and Windows enforces it via process-isolation containment, with
session isolation separating the sandboxed process from the human user's desktop,
clipboard, input, and active sessions, and an assigned local/Entra-backed identity
attributing container activity to a principal. `build_mxc_config` expresses exactly
these controls - read-only input/payload, a single writable work dir, deny-by-
default elsewhere, `network` per `Limits`, `ui.disable` + `clipboard: none` +
`injection: false`, and `leastPrivilege` with empty `capabilities` derived from the
policy. This harness proves OneCompute emits and drives that configuration
correctly against a conforming runtime; it aligns the integration with the Windows
enforcement contract so the day a validated `wxc-exec` preview arrives, B2 can be
promoted from "wired and proven" to "kernel-enforced" without OneCompute code
changes. The Windows platform remains the intended enforcement authority for B2;
OneCompute's job is to declare least-privilege policy and fail closed until Windows
can enforce it.
