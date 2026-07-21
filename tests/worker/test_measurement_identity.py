import errno
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from worker.measurement_identity import load_or_create_measurement_id, validate_measurement_id


def test_measurement_identity_is_stable_and_hostname_free(tmp_path: Path) -> None:
    path = tmp_path / "observer-id"

    first = load_or_create_measurement_id(path=path)
    second = load_or_create_measurement_id(path=path)

    assert first == second
    assert first.startswith("observer-")
    assert path.read_text(encoding="utf-8").strip() == first


def test_explicit_measurement_alias_is_validated_without_persisting(tmp_path: Path) -> None:
    path = tmp_path / "observer-id"

    assert load_or_create_measurement_id(requested="fleet-devbox-017", path=path) == "fleet-devbox-017"
    assert not path.exists()


def test_concurrent_identity_creation_publishes_one_complete_value(tmp_path: Path) -> None:
    path = tmp_path / "observer-id"

    with ThreadPoolExecutor(max_workers=12) as pool:
        identities = list(pool.map(lambda _index: load_or_create_measurement_id(path=path), range(24)))

    assert len(set(identities)) == 1
    assert path.read_text(encoding="utf-8").strip() == identities[0]


def test_identity_creation_falls_back_when_hard_links_are_unsupported(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "observer-id"

    def unsupported_link(_source, _destination):
        raise OSError(errno.EOPNOTSUPP, "hard links unsupported")

    monkeypatch.setattr("worker.measurement_identity.os.link", unsupported_link)

    identity = load_or_create_measurement_id(path=path)

    assert identity.startswith("observer-")
    assert path.read_text(encoding="utf-8").strip() == identity


def test_incomplete_identity_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "observer-id"
    path.write_text("observer-partial", encoding="utf-8")

    with pytest.raises(ValueError, match="incomplete"):
        load_or_create_measurement_id(path=path)


def test_concurrent_fallback_identity_creation_still_has_one_winner(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "observer-id"

    def unsupported_link(_source, _destination):
        raise OSError(errno.EOPNOTSUPP, "hard links unsupported")

    monkeypatch.setattr("worker.measurement_identity.os.link", unsupported_link)

    with ThreadPoolExecutor(max_workers=12) as pool:
        identities = list(pool.map(lambda _index: load_or_create_measurement_id(path=path), range(24)))

    assert len(set(identities)) == 1
    assert path.read_text(encoding="utf-8").strip() == identities[0]


@pytest.mark.parametrize("value", ["short", "has spaces", "../escape", "x" * 65])
def test_invalid_measurement_alias_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        validate_measurement_id(value)
