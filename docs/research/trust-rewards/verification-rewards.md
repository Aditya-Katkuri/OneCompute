# Verification, anti-cheat, and rewards

## Ringer tasks and challenge verification

Golle and Mironov's “Uncheatable Distributed Computations” is the right mental model for NightShift: insert dummy/ringer tasks with server-known answers so cheating becomes risky even when most work is not redundantly checked [15]. For the PoC, one hidden deterministic challenge is enough to create the demo beat: a `--cheat` worker returns the wrong answer, the verifier rejects it, the worker is blacklisted, and credits are forfeited.

## Replication and reputation roadmap

BOINC's history shows the production path: validation, host reputation, and adaptive redundancy. Its CreditNew document explains why peak FLOPS and claimed work are problematic: GPU/CPU peak formulas can be wrong, elapsed time and device attributes can be falsified, and only validated jobs should affect credit statistics [23]. NightShift should borrow the principle, not the full machinery, for the hackathon.

Roadmap replication policy:

- New worker: high challenge rate and occasional replication.
- Trusted worker: low challenge rate, no routine replication.
- High-value job: replicate or add stronger comparator.
- Failed challenge: irreversible demo blacklist and points forfeiture.
- Suspicious drift: probation and higher challenge rate.

## Anti-Sybil and identity

The io.net incident is the cautionary tale: attackers attempted about 1.8M fake GPU connections to spoof availability/rewards [19]. NightShift has an enterprise advantage. Entra Conditional Access can combine user, device, app, location, and risk signals [20]. Intune compliance can require managed devices to meet policy and can feed Conditional Access decisions [21]. TPM/Pluton-backed keys can later make worker credentials non-exportable [4][5].

## Rewards design

PoC formula:

```text
credits = accepted_units × class_weight
```

Where:

- `accepted_units` is counted only after verifier acceptance.
- `class_weight` is assigned by the server at registration, e.g. CPU=1, GPU=5.
- Worker-submitted `benchmarked_tops`, elapsed time, or device strings are display/debug signals, not payment inputs.

Why this is right: BOINC documents the pitfalls of peak-FLOPS and claimed-FLOPS credit [23]. Folding@home's QRB adds reliability gates such as passkey, 10 eligible work units, 80%+ successful returns, and timeout return [24]. Render uses a multi-factor formula over compute work, bandwidth, GPU model, and uptime [25]. NightShift can add those later, but only after a simple accepted-work ledger exists.

## Sources

- [15] Golle and Mironov, “Uncheatable Distributed Computations.” https://www.microsoft.com/en-us/research/publication/uncheatable-distributed-computations/
- [19] io.net Sybil attack summary. https://cryptonews.com/news/io-net-ceo-details-recent-sybil-attack-and-new-security-measures/
- [20] Microsoft Entra Conditional Access. https://learn.microsoft.com/en-us/entra/identity/conditional-access/overview
- [21] Microsoft Intune device compliance. https://learn.microsoft.com/en-us/intune/device-security/compliance/overview
- [23] BOINC CreditNew. https://raw.githubusercontent.com/wiki/BOINC/boinc/CreditNew.md
- [24] Folding@home QRB qualifications. https://foldingathome.org/faqs/points/bonus-points/what-are-the-qualifications-for-the-qrb/
- [25] Render reward mechanism. https://medium.com/render-token/compute-client-node-reward-mechanism-update-6b867e348030
