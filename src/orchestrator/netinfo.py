"""Best-effort LAN IPv4 discovery for the orchestrator startup banner.

Cross-platform and defensive: every code path swallows OS errors so importing or
calling this module can never raise, even on a host with no network. Used by
``python -m orchestrator`` to print the dashboard URL and the worker command for
each reachable interface.
"""

from __future__ import annotations

import socket


def _is_usable_ipv4(ip: str) -> bool:
    """True for a syntactically valid IPv4 that is neither loopback nor link-local."""
    if not ip or ip.startswith("127.") or ip.startswith("169.254."):
        return False
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    return all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)


def primary_lan_ipv4() -> str | None:
    """Return the IPv4 of the primary outbound interface, or None.

    Uses a UDP socket pointed at a public address to ask the OS which local
    interface it would route through. No packets are actually sent.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
        finally:
            sock.close()
    except OSError:
        return None
    return ip if _is_usable_ipv4(ip) else None


def lan_ipv4_addresses() -> list[str]:
    """Best-effort, de-duplicated, sorted list of this machine's LAN IPv4 addresses.

    Combines the primary-outbound-interface probe with the host's resolvable
    addresses (covers multi-homed machines). Loopback and link-local are
    excluded. Returns an empty list if nothing usable is found; never raises.
    """
    found: set[str] = set()

    primary = primary_lan_ipv4()
    if primary:
        found.add(primary)

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if _is_usable_ipv4(ip):
                found.add(ip)
    except OSError:
        pass

    return sorted(found)
