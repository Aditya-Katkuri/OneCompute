"""Classification-aware, fail-closed device-tier routing policy (pure stdlib).

In the harvest phase a real workload carries a *data classification* (how sensitive its input is)
and a device carries a *trust tier* (how much the fleet operator trusts that machine). A job may
only be assigned to a device whose tier is high enough for the job's classification, so
high-sensitivity data never lands on a low-trust device.

Two invariants make this a real security control rather than a hint:

1. The device trust tier is **assigned server-side** and defaults to the lowest tier. It is NEVER
   read from the worker's self-report, mirroring the existing rule that credit is metered on the
   job's actual GPU requirement and never on a worker's self-reported ``has_gpu``. A rogue worker
   therefore cannot claim a high tier to attract confidential data.
2. Routing **fails closed**. An unknown/misspelled classification OR an unknown tier denies the
   route. New sensitivity levels or typos never silently downgrade to "allow".

This module is intentionally pure-stdlib (no pydantic, no I/O) and ``may_route`` never raises, so it
is safe to call inside the scheduler's hot path. See docs/routing-policy.md.
"""

from __future__ import annotations

# Ordered device trust tiers, LOW to HIGH. Rank == index, so a higher tier compares greater.
#   untrusted           - unmanaged / BYOD / unknown posture (the fail-closed default)
#   managed             - enrolled + managed device (e.g. Intune-managed, compliant)
#   sanctioned          - managed and explicitly cleared for sensitive internal data
#   confidential_compute- hardware-isolated / TEE-backed, cleared for the most restricted data
TIERS: tuple[str, ...] = ("untrusted", "managed", "sanctioned", "confidential_compute")

# Ordered data classifications, LOW to HIGH sensitivity. Rank == index.
CLASSIFICATIONS: tuple[str, ...] = ("public", "internal", "confidential", "restricted")

# The fail-closed default tier for a device the server has not explicitly elevated.
DEFAULT_TRUST_TIER: str = "untrusted"
# The conservative default classification for a job that does not declare one.
DEFAULT_CLASSIFICATION: str = "internal"

# Minimum device tier required to run a job of each classification. A device must be at least this
# tier (by rank) to be eligible. Keep every classification mapped, or routing for it fails closed.
_MIN_TIER_FOR_CLASSIFICATION: dict[str, str] = {
    "public": "untrusted",
    "internal": "managed",
    "confidential": "sanctioned",
    "restricted": "confidential_compute",
}


def is_valid_tier(trust_tier: str) -> bool:
    """True only for a known device trust tier."""
    return trust_tier in TIERS


def is_valid_classification(classification: str) -> bool:
    """True only for a known data classification."""
    return classification in CLASSIFICATIONS


def required_tier_for(classification: str) -> str | None:
    """The minimum device tier a job of ``classification`` requires, or ``None`` if the
    classification is unknown (caller must treat ``None`` as deny)."""
    return _MIN_TIER_FOR_CLASSIFICATION.get(classification)


def may_route(classification: str, trust_tier: str) -> bool:
    """Return True only if a device at ``trust_tier`` may run a job classified ``classification``.

    FAIL CLOSED: an unknown/misspelled classification OR an unknown tier returns False (deny). This
    is a pure function and never raises, so it is safe on the scheduler hot path.
    """
    # Non-string (or otherwise unhashable) input is never a known key -> deny, without raising.
    if not isinstance(classification, str) or not isinstance(trust_tier, str):
        return False
    required = _MIN_TIER_FOR_CLASSIFICATION.get(classification)
    if required is None:
        return False  # unknown classification -> deny
    if trust_tier not in TIERS:
        return False  # unknown/self-reported-garbage tier -> deny
    return TIERS.index(trust_tier) >= TIERS.index(required)
