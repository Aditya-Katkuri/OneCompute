# Honest pitch and pitfalls

## Honest demo language

Use this close: "The headline number is a ceiling: if 40,000 Copilot+ PCs each exposed roughly 45 peak INT8 NPU TOPS, that is about 1.8 ExaOPS of theoretical NPU nameplate capacity. Our PoC did not claim that. It measured live useful work from this small CPU/GPU/SDK fleet, shown here beside the ceiling. The roadmap is to move more of that latent capacity into the measured column."

## Isolation and yield truth

Windows Sandbox supports `.wsb` controls such as networking, vGPU, mapped folders, logon command, and deletes sandbox contents when closed (https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file). Docker `--network none` creates only loopback inside the container (https://docs.docker.com/engine/network/drivers/none/). Windows Job Objects manage groups of processes and can enforce limits or terminate associated processes, but they are governance/control, not a security boundary (https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects). `GetLastInputInfo` is session-specific, so idle detection must run in the interactive session (https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getlastinputinfo).

## Enterprise acceptance truth

CISA describes cryptojacking as hijacking CPU resources and recommends monitoring abnormal sustained CPU activity, antivirus, and allow-listing (https://www.cisa.gov/news-events/news/defending-against-illicit-cryptocurrency-mining-activity). Microsoft Purview Endpoint DLP monitors and protects endpoint file activity through policies once devices are onboarded (https://learn.microsoft.com/en-us/purview/endpoint-dlp-learn-about). Therefore, production NightShift must be signed, governed, allow-listed, auditable, and policy-integrated; the hackathon should not imply bypassing endpoint controls.

## Pitfall checklist

- Do not merge theoretical NPU TOPS with measured CPU/GPU demo throughput.
- Do not credit yielded/failed/challenge-rejected units.
- Do not hide model/API warmup; pre-warm or disclose fallback.
- Do not call Job Objects a sandbox.
- Do not demo seeded dashboard data as live.
- Do not overbuild push transports; polling is enough.
- Do not claim all internal workloads are cloud-displaceable.
- Do not imply consumer GPU memory has datacenter-grade TEE isolation.

## Fallback disclosure line

"If the AI SDK path is unavailable, this beat uses a token-proportional sleep to preserve real fan-out, requeue, points, and dashboard behavior. The primary throughput measurement remains the real CPU fan-out job."
