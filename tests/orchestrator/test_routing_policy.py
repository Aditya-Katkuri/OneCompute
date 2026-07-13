"""Unit tests for the fail-closed, classification-aware routing policy (pure function).

Covers the full (classification x tier) matrix and the fail-closed behavior for unknown
classifications and unknown tiers. See src/orchestrator/routing_policy.py and docs/routing-policy.md.
"""

import pytest

from orchestrator.routing_policy import (
    CLASSIFICATIONS,
    DEFAULT_CLASSIFICATION,
    DEFAULT_TRUST_TIER,
    TIERS,
    is_valid_classification,
    is_valid_tier,
    may_route,
    required_tier_for,
)

# The intended decision matrix, written out explicitly (not derived from the module) so a
# regression in the mapping is caught. Rows are classifications, columns are device tiers;
# True == "a device at this tier may run a job of this classification".
EXPECTED = {
    "public":       {"untrusted": True,  "managed": True,  "sanctioned": True,  "confidential_compute": True},
    "internal":     {"untrusted": False, "managed": True,  "sanctioned": True,  "confidential_compute": True},
    "confidential": {"untrusted": False, "managed": False, "sanctioned": True,  "confidential_compute": True},
    "restricted":   {"untrusted": False, "managed": False, "sanctioned": False, "confidential_compute": True},
}


def test_tiers_and_classifications_are_ordered_low_to_high():
    assert TIERS == ("untrusted", "managed", "sanctioned", "confidential_compute")
    assert CLASSIFICATIONS == ("public", "internal", "confidential", "restricted")
    assert DEFAULT_TRUST_TIER == "untrusted"      # fail-closed default device tier
    assert DEFAULT_CLASSIFICATION == "internal"   # conservative default job classification


@pytest.mark.parametrize("classification", CLASSIFICATIONS)
@pytest.mark.parametrize("trust_tier", TIERS)
def test_full_matrix_matches_intended_policy(classification, trust_tier):
    assert may_route(classification, trust_tier) is EXPECTED[classification][trust_tier]


def test_high_sensitivity_never_lands_on_a_low_trust_device():
    # The core security invariant: restricted/confidential data must not route to untrusted/managed.
    assert may_route("restricted", "untrusted") is False
    assert may_route("restricted", "managed") is False
    assert may_route("restricted", "sanctioned") is False
    assert may_route("confidential", "untrusted") is False
    assert may_route("confidential", "managed") is False
    # ...and only the top tier clears restricted data.
    assert may_route("restricted", "confidential_compute") is True


@pytest.mark.parametrize("trust_tier", [*TIERS, "bogus", ""])
def test_unknown_classification_always_denies(trust_tier):
    # An unknown/misspelled classification fails closed for EVERY tier, even the highest.
    for bad in ("secret", "PUBLIC", "top-secret", "", "internal ", "confidental"):
        assert may_route(bad, trust_tier) is False


@pytest.mark.parametrize("classification", [*CLASSIFICATIONS, "secret", ""])
def test_unknown_tier_always_denies(classification):
    # An unknown/self-reported-garbage tier fails closed for EVERY classification, even "public".
    for bad in ("trusted", "MANAGED", "confidential-compute", "", "root"):
        assert may_route(classification, bad) is False


def test_both_unknown_denies():
    assert may_route("nonsense", "nonsense") is False


def test_may_route_never_raises_on_weird_input():
    # Pure function on the scheduler hot path: must never raise, whatever it is handed.
    for weird in (None, 123, [], {}, object()):
        assert may_route(weird, "managed") is False  # type: ignore[arg-type]
        assert may_route("public", weird) is False   # type: ignore[arg-type]


def test_validity_helpers():
    assert is_valid_tier("managed") is True
    assert is_valid_tier("bogus") is False
    assert is_valid_classification("confidential") is True
    assert is_valid_classification("bogus") is False


def test_required_tier_for_maps_each_classification_and_denies_unknown():
    assert required_tier_for("public") == "untrusted"
    assert required_tier_for("internal") == "managed"
    assert required_tier_for("confidential") == "sanctioned"
    assert required_tier_for("restricted") == "confidential_compute"
    assert required_tier_for("bogus") is None
