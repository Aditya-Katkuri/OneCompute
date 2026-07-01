"""Declarative MXC sandbox policies for OneCompute jobs.

This module models the Microsoft Execution Containers (MXC, Build 2026)
declarative policy concept as pure data. The policy is deny-by-default and is
intended to be consumed by a backend that asks the OS kernel to enforce it.

Important framing: the principal we place inside the sandbox is a lightweight,
deterministic OneCompute job (``python -m jobkit``), not an autonomous agent.
MXC was announced as an agent sandbox (it contains OpenCLAW-class agents); we
reuse the same OS-enforced containment for a strictly more constrained unit of
work: a short-lived compute script with no autonomous tool use, no interactive
UI, no network by default, and no persistence. Identity fields below therefore
describe a job/worker principal for audit, not an agent.

See docs/mxc-sandbox.md for the runtime enforcement design.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contracts import Limits

PathInput = str | os.PathLike[str]


@dataclass(frozen=True, slots=True)
class _AccessRule:
    path: str
    access: tuple[str, ...]
    allow_delete: bool
    allow_rename: bool
    effect: str = "allow"

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect": self.effect,
            "path": self.path,
            "access": list(self.access),
            "allow_delete": self.allow_delete,
            "allow_rename": self.allow_rename,
        }


def build_policy(
    work_dir: PathInput,
    limits: Limits,
    *,
    payload_subdir: str = "src",
    allow_network: bool | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Build a serializable, deny-by-default MXC policy for a single OneCompute job.

    ``job_id`` labels the sandboxed principal for audit. It is a job/worker
    identity (the thing in the sandbox is a ``python -m jobkit`` script), not an
    autonomous-agent identity.
    """
    work_path = _resolve_path(work_dir)
    payload_path = _payload_path(work_path, payload_subdir)
    resolved_job_id = job_id if job_id is not None else _stable_job_id(work_path)
    network_mode = _network_mode(limits, allow_network)

    rules = [
        _AccessRule(
            path=str(payload_path),
            access=("read",),
            allow_delete=False,
            allow_rename=False,
        ).to_dict(),
        _AccessRule(
            path=str(work_path),
            access=("read", "write"),
            allow_delete=True,
            allow_rename=True,
        ).to_dict(),
    ]

    return {
        "schema": "mxc-policy/v1",
        "version": 1,
        "default": "deny",
        "job_id": resolved_job_id,
        "identity": {
            "job_id": resolved_job_id,
            "kind": "onecompute-job",
            "principal_type": "job",
            "privilege": "low_privilege",
        },
        "filesystem": {
            "default": "deny",
            "work_dir": str(work_path),
            "payload_dir": str(payload_path),
            "rules": rules,
            "deny_rules": [
                {
                    "effect": "deny",
                    "scope": "outside_work_dir",
                    "path": str(work_path),
                    "access": ["delete", "rename"],
                },
                {
                    "effect": "deny",
                    "scope": "protected_locations",
                    "locations": _protected_locations(),
                    "access": ["read", "write", "delete", "rename"],
                    "except_under": str(work_path),
                },
            ],
        },
        "privileges": {
            "elevation": "deny",
            "allow_new_privileges": False,
            "run_as": "low_privilege",
            "allow_admin": False,
        },
        "network": {
            "default": "deny",
            "mode": network_mode,
            "allowed": network_mode == "host",
        },
    }


def policy_to_json(policy: dict[str, Any]) -> str:
    """Serialize a policy with deterministic formatting."""
    return json.dumps(policy, sort_keys=True, indent=2)


def write_policy(path: PathInput, policy: dict[str, Any]) -> Path:
    """Write policy JSON to path and return the destination path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(policy_to_json(policy), encoding="utf-8")
    return target


def denies_delete_outside(policy: dict[str, Any], some_path: PathInput) -> bool:
    """Return True when deleting some_path is denied because it is outside work_dir."""
    work_path = _policy_work_path(policy)
    if work_path is None:
        return True
    return not _is_same_or_child(_resolve_path(some_path), work_path)


def permits_write(policy: dict[str, Any], some_path: PathInput) -> bool:
    """Return True when policy permits writing some_path."""
    fs = policy.get("filesystem")
    if not isinstance(fs, dict):
        return False

    path = _resolve_path(some_path)
    rules = fs.get("rules")
    if not isinstance(rules, list):
        return False

    matched_rules: list[dict[str, Any]] = []
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("effect", "allow") != "allow":
            continue
        rule_path = rule.get("path")
        if not isinstance(rule_path, str):
            continue
        if _is_same_or_child(path, _resolve_path(rule_path)):
            matched_rules.append(rule)

    if not matched_rules:
        return False

    best_rule = max(matched_rules, key=lambda rule: len(str(rule.get("path", ""))))
    access = best_rule.get("access", [])
    return isinstance(access, list) and "write" in access


def _resolve_path(path: PathInput) -> Path:
    candidate = Path(path).expanduser()
    try:
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return candidate.absolute()


def _payload_path(work_path: Path, payload_subdir: str) -> Path:
    requested = Path(payload_subdir or "src")
    payload_path = _resolve_path(work_path / requested)
    if _is_same_or_child(payload_path, work_path):
        return payload_path

    fallback_name = requested.name or "src"
    return _resolve_path(work_path / fallback_name)


def _stable_job_id(work_path: Path) -> str:
    digest = uuid.uuid5(uuid.NAMESPACE_URL, f"onecompute:mxc:{work_path}").hex[:12]
    return f"onecompute-job-{digest}"


def _network_mode(limits: Limits, allow_network: bool | None) -> str:
    if allow_network is not None:
        return "host" if allow_network else "none"
    return "host" if getattr(limits, "network", "none") == "host" else "none"


def _policy_work_path(policy: dict[str, Any]) -> Path | None:
    fs = policy.get("filesystem")
    if not isinstance(fs, dict):
        return None
    work_dir = fs.get("work_dir")
    if not isinstance(work_dir, str):
        return None
    return _resolve_path(work_dir)


def _is_same_or_child(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        candidate_text = _comparison_text(candidate)
        root_text = _comparison_text(root).rstrip("\\/")
        if candidate_text == root_text:
            return True
        return candidate_text.startswith(f"{root_text}{os.sep}")


def _comparison_text(path: Path) -> str:
    text = str(path)
    if os.name == "nt":
        return text.replace("/", "\\").casefold()
    return text


def _protected_locations() -> list[dict[str, str]]:
    locations = [
        {"kind": "home", "path": _safe_path_string(Path.home())},
        {"kind": "user_profile", "path": _safe_path_string(Path.home())},
        {"kind": "network_share", "path": r"\\*"},
    ]

    if os.name == "nt":
        system_candidates = (
            ("system_root", os.environ.get("SystemRoot") or r"C:\Windows"),
            ("program_files", os.environ.get("ProgramFiles") or r"C:\Program Files"),
            (
                "program_files_x86",
                os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)",
            ),
        )
    else:
        system_candidates = (
            ("system_root", "/"),
            ("system_config", "/etc"),
            ("system_bin", "/usr"),
        )

    for kind, raw_path in system_candidates:
        locations.append({"kind": kind, "path": _safe_path_string(Path(raw_path))})

    return locations


def _safe_path_string(path: Path) -> str:
    try:
        return str(path.expanduser().resolve(strict=False))
    except (OSError, RuntimeError):
        return str(path.expanduser().absolute())
