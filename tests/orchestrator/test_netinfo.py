import ipaddress

from orchestrator.netinfo import _is_usable_ipv4, lan_ipv4_addresses


def test_returns_sorted_list_of_plausible_ipv4_strings():
    addrs = lan_ipv4_addresses()
    assert isinstance(addrs, list)
    assert addrs == sorted(addrs)  # deterministic ordering
    for ip in addrs:
        parsed = ipaddress.ip_address(ip)
        assert parsed.version == 4
        assert not parsed.is_loopback
        assert not parsed.is_link_local


def test_is_deterministic_and_never_raises():
    first = lan_ipv4_addresses()
    second = lan_ipv4_addresses()
    assert first == second


def test_usable_ipv4_filter_rejects_loopback_and_link_local():
    assert _is_usable_ipv4("192.168.1.50")
    assert _is_usable_ipv4("10.0.0.4")
    assert not _is_usable_ipv4("127.0.0.1")
    assert not _is_usable_ipv4("169.254.10.1")
    assert not _is_usable_ipv4("")
    assert not _is_usable_ipv4("not.an.ip")
    assert not _is_usable_ipv4("256.1.1.1")
