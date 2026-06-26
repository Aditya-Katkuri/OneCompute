# NightShift T4 research dossier - trust, verification, and rewards

## 1. How to use this

Use this as the T4 decision memo, not as a shopping list. For the 1-2 day PoC, implement only the decisions marked **PoC**: local Ed25519 manifest signing; worker-side signature/hash refusal; one deterministic challenge task; server-side result acceptance; and credits as `accepted_units × class_weight`, with `class_weight` assigned by the orchestrator. Treat cosign/OIDC, hardware attestation, adaptive replication, fuzzy FP comparators, zk proofs, and sophisticated credit formulas as roadmap unless a workload forces them.

Deeper notes:

- [Floating-point determinism and comparators](fp-determinism.md)
- [Signing, roots of trust, and manifest integrity](signing-roots.md)
- [Verification, anti-cheat, and rewards](verification-rewards.md)

## 2. Executive summary - 5 highest-impact learning areas, ranked

1. **Verification must be workload-aware, with FP tolerance as the hidden make-or-break.** IEEE 754 operations round, operation order changes answers, and FMA performs one rounding where separate multiply/add performs two; NVIDIA explicitly shows CPU and GPU can both be IEEE-correct while returning different low-order bits [9]. **NightShift feature:** exact equality only for deterministic integer/string challenge jobs; roadmap numerical jobs need per-kind absolute/relative tolerance, NaN policy, and maybe embedding/logit-distance comparators.
2. **Signed manifests are the PoC trust root; cosign/OIDC and TPM/Pluton are production roots.** Ed25519 has small keys/signatures, high performance, no per-signature random nonce requirement, and 128-bit-classical security [1]; Python `cryptography` exposes a direct `sign()`/`verify()` API whose verifier raises on invalid signatures [2]. **NightShift feature:** local Ed25519 over canonical manifest bytes plus `code_sha256`/`input_sha256`; roadmap maps signer identity to corporate OIDC and hardware-backed keys.
3. **Challenge/ringer tasks beat claimed FLOPS for a hackathon.** Golle and Mironov's ringer idea inserts indistinguishable tasks with known answers to make cheating economically unattractive [15]. BOINC-style validation and adaptive replication are the mature roadmap, but the demo only needs one hardcoded challenge and irreversible blacklist/forfeit. **NightShift feature:** server injects `challenge`, verifies `y = x*x + 1`, credits only accepted units.
4. **Sybil and benchmark gaming are incentive failures, not just security bugs.** io.net publicly reported ~1.8M fake GPU connections trying to spoof availability/rewards [19]. Enterprise identity and managed-device signals reduce Sybil surface: Entra Conditional Access combines user/device/risk signals [20], and Intune compliance can gate access on managed device state [21]. **NightShift feature:** one corporate identity + one registered device/node + server-assigned `class_weight`; never pay on self-claimed TOPS.
5. **Credit formulas must reward validated useful work, not theoretical capacity.** BOINC CreditNew documents why peak-FLOPS and claimed-FLOPS systems are unfair/gamable, and notes only validated jobs feed credit normalization [23]. Folding@home requires a passkey, at least 10 eligible work units, 80%+ successful returns, and return before timeout for quick-return bonus [24]. Render uses multi-factor node scoring across work, bandwidth, GPU model, and uptime [25]. **NightShift feature:** PoC stays simple (`accepted_units × class_weight`); roadmap adds reliability/uptime factors and probation after validation data exists.

## 3. Compute <-> hardware <-> software interconnection map for trust + verification

| Layer | What can go wrong | Hardware link | Software/control link | NightShift response |
|---|---|---|---|---|
| **Manifest integrity** | MITM or compromised queue mutates code, input, limits, or sandbox policy | TPM/Pluton/Secure Boot can protect device identity and boot state, but are too much for PoC [4][5][6] | Ed25519/cosign verify signatures before run [1][2][7] | **PoC:** local Ed25519; verify signature + `code_sha256` + `input_sha256`. **Roadmap:** cosign keyless OIDC bundle and transparency log [7][8]. |
| **Execution integrity** | Worker skips work or returns fabricated output | Heterogeneous CPU/GPU speed makes “it finished fast” meaningless | Challenge/ringer jobs and server-side verification detect cheating [15] | **PoC:** one deterministic challenge. **Roadmap:** adaptive replication and reputation. |
| **Numerical result integrity** | Correct GPU result differs by low-order FP bits from CPU reference | IEEE 754 rounding, FMA availability, reduction order, denormals, library kernels, and GPU/CPU differences [9][10][11][12] | Compiler flags can contract/reassociate FP operations; ML frameworks warn CPU/GPU reproducibility is not guaranteed [11][13] | **PoC:** avoid FP for challenge. **Roadmap:** tolerance-aware comparators with explicit per-workload policy [14]. |
| **Identity/Sybil resistance** | A participant registers fake workers or inflated GPUs | TPM/Pluton can bind keys to devices; compliance/health attestation proves managed posture [4][5][21] | Entra Conditional Access uses user/device/risk signals for policy decisions [20] | **PoC:** server-issued worker IDs and class weights. **Roadmap:** SSO device binding and attested hardware keys. |
| **Rewards ledger** | Worker credits itself or optimizes for fake benchmark scores | Hardware claims and peak FLOPS can be wrong or spoofed [23] | BOINC/Folding/Render all put reward logic on validated work/reliability, not blind self-report [23][24][25] | **PoC:** ledger write only after verifier accepts; formula is deterministic and server-side. |

The deepest hardware/software trap is **floating-point determinism**. IEEE 754 standardizes formats and operations, but the exact program the hardware executes still depends on operation order, FMA contraction, compiler flags, and library kernel choice. NVIDIA's floating-point guide shows `(A+B)+C` and `A+(B+C)` can round differently, and FMA can produce a different but more accurate answer than separate multiply then add [9]. MSVC documents that `/fp:contract` may generate FMA and that `/fp:fast` may reorder/combine/simplify operations, producing observably different rounding behavior [11]. PyTorch warns that fully reproducible results are not guaranteed across releases/platforms and may differ between CPU and GPU even with identical seeds [13]. Therefore NightShift must not define validation as bitwise equality for floating-point workloads.

## 4. Deep dives

### Area 1 - FP determinism and tolerance-aware verification

**What it is.** Floating-point computation is a contract between numeric format, hardware instruction set, compiler, math library, and framework scheduler. IEEE 754 defines basic binary formats and requires operations such as add, multiply, divide, square root, and fused multiply-add, but rounding means algebraic identities do not automatically hold in finite precision [9].

**Why high-impact for NightShift.** NightShift wants heterogeneous CPU/GPU/NPU workers. A verifier that compares output bytes will falsely reject honest GPU workers or falsely prefer a single architecture. NVIDIA's guide demonstrates that operation order alone changes rounded results and that FMA produces `rn(x*y+z)` with one rounding instead of `rn(rn(x*y)+z)` with two [9]. MSVC and GCC both expose FP contraction/compiler options that affect whether FMA or reassociation may happen [10][11]. PyTorch warns CPU and GPU executions can be non-reproducible even under identical seeds [13].

**Feature/decision.** **PoC:** make `challenge` integer/deterministic and verify exactly. **Roadmap:** add `ComparatorPolicy` to manifests: `exact`, `json_exact`, `float_close(abs_tol, rel_tol)`, `vector_l2`, `topk_overlap`, and `custom_hash`. Python's `math.isclose`/PEP 485 gives the baseline symmetric rule `abs(a-b) <= max(rel_tol * max(abs(a), abs(b)), abs_tol)` and emphasizes absolute tolerance near zero [14]. See [fp-determinism.md](fp-determinism.md).

### Area 2 - Manifest signing and roots of trust

**What it is.** Signing binds “what to run” to a trusted publisher. Ed25519 is a strong PoC primitive because RFC 8032 calls out high performance, no unique random value per signature, side-channel resilience, and 32-byte public keys/64-byte signatures for Ed25519 [1]. The `cryptography` library directly supports `Ed25519PrivateKey.sign()` and `Ed25519PublicKey.verify()` [2].

**Why high-impact for NightShift.** The worker is executing code on an employee device. Before sandboxing matters, the worker must know it is running the exact manifest the orchestrator intended. Cosign is the production-shaped answer: it signs containers and blobs, supports keyless OIDC identity, can bundle signature/certificate/transparency inclusion data, and can use KMS providers including Azure Key Vault [7][8]. Rekor provides an immutable, tamper-resistant ledger for supply-chain metadata [8]. TPM/Pluton/Secure Boot extend the root down to hardware/boot state, but require enterprise deployment and are roadmap [4][5][6].

**Feature/decision.** **PoC:** canonical JSON -> Ed25519 signature -> worker verifies signature and hashes before run; flipped byte refuses. **Roadmap:** cosign `sign-blob` bundle for manifests, signer identity constrained to corporate OIDC, key in Azure Key Vault or hardware-backed store, and optional Rekor-style audit. See [signing-roots.md](signing-roots.md).

### Area 3 - Result verification: ringer tasks, replication, and zk roadmap

**What it is.** In open or semi-trusted distributed compute, the hard problem is proving the worker actually performed useful work. Golle and Mironov's “uncheatable distributed computations” introduces decoy/ringer tasks with known answers; cheaters who skip work risk detection because ringers are indistinguishable from real units [15]. BOINC adds mature validation/quorum and CreditNew mechanisms; its credit system explicitly notes peak FLOPS can be wrong and cheaters can falsify device attributes or elapsed time [23].

**Why high-impact for NightShift.** NightShift's demo needs a visible “caught a cheater” beat. Ringers are cheaper than blanket replication and map directly to the existing `challenge` runner contract. Adaptive replication is useful later for new/low-trust nodes and high-value jobs, but is cut from the PoC.

**Feature/decision.** **PoC:** inject one deterministic challenge; if wrong, blacklist and forfeit. **Roadmap:** reputation score, probabilistic challenge rate, adaptive replication on suspicious/high-value work, and cryptographic verifiable-computation experiments. Pinocchio shows public verifiable computation with tiny proofs and fast verification for compiled computations [17], while Proofs of Useful Work formalizes the desire to make otherwise wasteful proof work useful but remains roadmap-level for NightShift [18]. See [verification-rewards.md](verification-rewards.md).

### Area 4 - Anti-Sybil identity and hardware-backed claims

**What it is.** Sybil resistance prevents one actor from pretending to be many workers; hardware-backed identity prevents easy copying of device credentials. TPMs can generate/store/limit cryptographic keys and store boot measurements; keys can be made unavailable outside the TPM [4]. Pluton integrates a secure subsystem into the SoC and provides hardware root of trust, secure identity, attestation, and cryptographic services [5]. Secure Boot makes firmware verify signatures of boot software before handing control to the OS [6].

**Why high-impact for NightShift.** Rewards create adversarial incentives. io.net's 2024 incident showed attackers attempting ~1.8M fake GPU connections to spoof availability and rewards [19]. NightShift's internal corporate context is a huge advantage: Entra Conditional Access can combine user, device, app, location, and risk signals [20], and Intune compliance can mark noncompliant devices and integrate with Conditional Access [21].

**Feature/decision.** **PoC:** one registered worker ID, server-assigned `class_weight`, and ledger credits only after verifier acceptance. **Roadmap:** Entra-authenticated worker enrollment, one employee/device policy, device compliance gate, TPM/Pluton-backed private key, and attested capability sampling.

### Area 5 - Incentive and credit mechanism design

**What it is.** Incentives define what participants optimize. BOINC's CreditNew history is directly relevant: claimed credit based on peak performance was unfair, actual-FLOPs reporting was not universal and did not prevent cheating under single replication, and the newer system normalizes/caps using validated jobs [23]. Folding@home's QRB gates bonus points on passkey, at least 10 eligible WUs, 80%+ successful returns, and before-timeout return [24]. Render scores nodes using compute work, bandwidth, GPU model, and uptime with explicit weights [25].

**Why high-impact for NightShift.** If we pay on claimed FLOPS, workers will optimize claims. If we pay only on validated useful work, the economically rational path is to run assigned jobs correctly. For the hackathon, simple and legible beats sophisticated and fragile.

**Feature/decision.** **PoC:** `credits = accepted_units × class_weight`, where `accepted_units` is server-accepted result units and `class_weight` is assigned at registration by the orchestrator (GPU=5, CPU=1). **Roadmap:** reliability/uptime multiplier, probation for new nodes, capped daily earning, challenge pass-rate factor, and resource scarcity weights only after enough validation history exists.

## 5. Direct implications for OUR implementation

### PoC decisions to build now

- **Canonical manifest signing:** serialize the frozen manifest deterministically; sign the bytes with Ed25519; include `signature` and `public_key_id`; verify before runner starts [1][2].
- **Hash refusal:** verify `code_sha256` and `input_sha256` against the manifest before run; a flipped manifest byte or altered payload must hard-fail before execution.
- **Challenge task:** keep the existing contract `challenge: input {"x": int} -> {"y": x*x + 1}`; server knows the answer and blacklists/forfeits on mismatch [15].
- **Server-authoritative rewards:** only T1/T4 verifier writes ledger; workers submit results, never credits. Formula: `credits = accepted_units × class_weight`; use server-side GPU/CPU class weights from `/register`, never `benchmarked_tops` [23].
- **Comparator policy:** exact equality only for challenge and deterministic `data.transform`. Add a backlog item for tolerance-aware numerical comparison before any FP workload is judged for rewards [9][13][14].
- **Audit event:** append `job_id`, `worker_id`, manifest hash, result hash, accepted/rejected, credits, and reason; optional prev-hash chain if claiming tamper-evidence.

### Roadmap decisions to document, not build in the PoC

- **cosign/OIDC/Rekor:** move from local Ed25519 to identity-bound signatures and transparency bundles when enterprise egress/SSO wiring is ready [7][8].
- **TPM/Pluton-backed worker keys:** bind worker identity to a non-exportable key and optionally attest device health [4][5][21].
- **Adaptive replication/reputation:** replicate new/suspect/high-value work; reduce redundancy for reliable internal nodes after challenge pass history.
- **zk/verifiable computation:** track Pinocchio/SNARK/STARK/zkML only for sensitive high-value workloads where verifier cost and proof generation cost make sense [17][18].
- **CreditNew-like fairness:** add uptime/reliability/scarcity only after the simple ledger works and cannot be gamed by self-report [23][24][25].

## 6. Pitfalls & open questions

1. **Bitwise FP validation will punish honest workers.** Exact byte equality is valid for the PoC challenge; it is unsafe for GPU/CPU numerical outputs [9][13].
2. **FMA/compiler flags can change answers even on the same source.** MSVC `/fp:contract` and GCC `-ffp-contract` affect FMA generation [10][11]. Pin the environment or compare with tolerance.
3. **NaN/Inf/zero need explicit policy.** `math.isclose` treats NaN as not close to anything and infinities as close only to themselves [14]; NightShift should make that explicit in comparator policy.
4. **Rewards leak through side channels.** If “GPU=5” is too coarse, CPU-only users may feel underpaid or GPU users may farm easy units; if too complex, it becomes gameable. Start simple, then use validated history.
5. **Identity is not hardware attestation.** Corporate SSO tells who enrolled; TPM/Pluton/Intune tells more about which managed device is running [4][5][20][21]. Decide how much is necessary before production.
6. **Ringers are probabilistic.** A cheater can pass if not sampled; increase challenge rate for new/suspect nodes and high-reward periods.
7. **Cosign keyless requires operational plumbing.** OIDC, Fulcio/certificates, Rekor, registry/blob storage, and egress are production-grade but not free [7][8].
8. **zk proofs are not a hackathon shortcut.** Pinocchio-style proofs are promising, but proof generation/tooling and circuit constraints make them roadmap unless the workload is specifically designed for it [17].

## 7. Sources

[1] RFC 8032, “Edwards-Curve Digital Signature Algorithm (EdDSA).” https://datatracker.ietf.org/doc/html/rfc8032

[2] Python `cryptography` Ed25519 signing documentation. https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/

[3] Sigstore cosign signing containers. https://docs.sigstore.dev/cosign/signing/signing_with_containers/

[4] Microsoft, Trusted Platform Module Technology Overview. https://learn.microsoft.com/en-us/windows/security/hardware-security/tpm/trusted-platform-module-overview

[5] Microsoft, Microsoft Pluton security processor. https://learn.microsoft.com/en-us/windows/security/hardware-security/pluton/microsoft-pluton-security-processor

[6] Microsoft, Secure Boot. https://learn.microsoft.com/en-us/windows-hardware/design/device-experiences/oem-secure-boot

[7] Sigstore cosign signing blobs/files. https://docs.sigstore.dev/cosign/signing/signing_with_blobs/

[8] Sigstore Rekor overview. https://docs.sigstore.dev/logging/overview/

[9] NVIDIA, Floating Point and IEEE 754 / FMA guide. https://docs.nvidia.com/cuda/floating-point/index.html

[10] GCC optimize options, `-ffp-contract`. https://gcc.gnu.org/onlinedocs/gcc/Optimize-Options.html

[11] Microsoft MSVC `/fp` floating-point behavior. https://learn.microsoft.com/en-us/cpp/build/reference/fp-specify-floating-point-behavior?view=msvc-170

[12] C++ reference, `std::fma`. https://en.cppreference.com/cpp/numeric/math/fma

[13] PyTorch reproducibility notes. https://docs.pytorch.org/docs/2.12/notes/randomness.html

[14] Python `math.isclose` and PEP 485. https://docs.python.org/3/library/math.html#math.isclose ; https://peps.python.org/pep-0485/

[15] Philippe Golle and Ilya Mironov, “Uncheatable Distributed Computations,” Microsoft Research. https://www.microsoft.com/en-us/research/publication/uncheatable-distributed-computations/ ; PDF: https://www.microsoft.com/en-us/research/wp-content/uploads/2001/04/dist.pdf

[16] BOINC CreditNew raw wiki. https://raw.githubusercontent.com/wiki/BOINC/boinc/CreditNew.md

[17] Microsoft Research, “Pinocchio: Nearly Practical Verifiable Computation.” https://www.microsoft.com/en-us/research/publication/pinocchio-nearly-practical-verifiable-computation/

[18] IACR ePrint 2017/203, “Proofs of Useful Work.” https://eprint.iacr.org/2017/203

[19] Cryptonews summary of io.net CEO Sybil attack postmortem. https://cryptonews.com/news/io-net-ceo-details-recent-sybil-attack-and-new-security-measures/

[20] Microsoft Entra Conditional Access overview. https://learn.microsoft.com/en-us/entra/identity/conditional-access/overview

[21] Microsoft Intune device compliance overview. https://learn.microsoft.com/en-us/intune/device-security/compliance/overview

[22] Sigstore cosign key management overview. https://docs.sigstore.dev/cosign/key_management/overview/

[23] BOINC CreditNew discussion of peak FLOPS, validation, normalization, and cheating. https://raw.githubusercontent.com/wiki/BOINC/boinc/CreditNew.md

[24] Folding@home QRB qualifications. https://foldingathome.org/faqs/points/bonus-points/what-are-the-qualifications-for-the-qrb/

[25] Render Network compute client node reward mechanism update. https://medium.com/render-token/compute-client-node-reward-mechanism-update-6b867e348030

[26] Salad earning FAQ. https://support.salad.com/faq/jobs/how-can-i-earn-more-with-salad/
