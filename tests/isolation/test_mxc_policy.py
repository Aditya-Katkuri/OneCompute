import json
from pathlib import Path

from contracts import Limits
from isolation.mxc_policy import (
    build_policy,
    denies_delete_outside,
    permits_write,
    policy_to_json,
    write_policy,
)


def test_policy_filesystem_is_default_deny(tmp_path: Path):
    work_dir = tmp_path / "job"
    policy = build_policy(work_dir, Limits())
    fs = policy["filesystem"]

    assert policy["schema"] == "mxc-policy/v1"
    assert policy["default"] == "deny"
    assert fs["default"] == "deny"
    assert fs["work_dir"] == str(work_dir.resolve())
    assert fs["payload_dir"] == str((work_dir / "src").resolve())

    payload_rule = next(rule for rule in fs["rules"] if rule["path"] == fs["payload_dir"])
    assert payload_rule["access"] == ["read"]
    assert payload_rule["allow_delete"] is False
    assert payload_rule["allow_rename"] is False

    read_write_rules = [rule for rule in fs["rules"] if "write" in rule["access"]]
    assert len(read_write_rules) == 1
    assert read_write_rules[0]["path"] == fs["work_dir"]
    assert read_write_rules[0]["allow_delete"] is True
    assert read_write_rules[0]["allow_rename"] is True

    outside_rule = next(
        rule for rule in fs["deny_rules"] if rule["scope"] == "outside_work_dir"
    )
    assert outside_rule["effect"] == "deny"
    assert outside_rule["access"] == ["delete", "rename"]


def test_policy_blocks_protected_locations(tmp_path: Path):
    policy = build_policy(tmp_path / "job", Limits())
    protected_rule = next(
        rule for rule in policy["filesystem"]["deny_rules"] if rule["scope"] == "protected_locations"
    )

    kinds = {location["kind"] for location in protected_rule["locations"]}
    assert {"home", "user_profile", "network_share"}.issubset(kinds)
    assert protected_rule["effect"] == "deny"
    assert protected_rule["access"] == ["read", "write", "delete", "rename"]


def test_predicates_enforce_work_dir_boundary(tmp_path: Path):
    work_dir = tmp_path / "job"
    payload_file = work_dir / "src" / "module.py"
    inside_file = work_dir / "out.json"
    outside_file = tmp_path / "outside" / "victim.txt"
    policy = build_policy(work_dir, Limits())

    assert denies_delete_outside(policy, outside_file) is True
    assert denies_delete_outside(policy, inside_file) is False
    assert permits_write(policy, inside_file) is True
    assert permits_write(policy, outside_file) is False
    assert permits_write(policy, payload_file) is False


def test_privileges_deny_elevation_and_admin(tmp_path: Path):
    policy = build_policy(tmp_path / "job", Limits())

    assert policy["privileges"] == {
        "elevation": "deny",
        "allow_new_privileges": False,
        "run_as": "low_privilege",
        "allow_admin": False,
    }


def test_network_mapping_uses_limits_and_override(tmp_path: Path):
    assert build_policy(tmp_path / "default", Limits())["network"] == {
        "default": "deny",
        "mode": "none",
        "allowed": False,
    }
    assert build_policy(tmp_path / "host", Limits(network="host"))["network"] == {
        "default": "deny",
        "mode": "host",
        "allowed": True,
    }
    assert build_policy(tmp_path / "override-allow", Limits(), allow_network=True)["network"][
        "mode"
    ] == "host"
    assert build_policy(
        tmp_path / "override-deny",
        Limits(network="host"),
        allow_network=False,
    )["network"]["mode"] == "none"


def test_job_id_is_honored_or_generated_stably(tmp_path: Path):
    explicit = build_policy(tmp_path / "explicit", Limits(), job_id="job-123")
    generated_once = build_policy(tmp_path / "generated", Limits())
    generated_twice = build_policy(tmp_path / "generated", Limits())

    assert explicit["job_id"] == "job-123"
    assert explicit["identity"]["job_id"] == "job-123"
    assert explicit["identity"]["principal_type"] == "job"
    assert explicit["identity"]["kind"] == "onecompute-job"
    assert generated_once["job_id"].startswith("onecompute-job-")
    assert generated_once["job_id"] == generated_twice["job_id"]


def test_policy_json_and_write_round_trip(tmp_path: Path):
    policy = build_policy(tmp_path / "job", Limits(), job_id="job-123")
    serialized = policy_to_json(policy)
    output_path = tmp_path / "nested" / "policy.json"

    assert json.loads(serialized) == policy
    assert serialized == policy_to_json(policy)
    assert write_policy(output_path, policy) == output_path
    assert json.loads(output_path.read_text(encoding="utf-8")) == policy
