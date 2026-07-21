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


@pytest.mark.parametrize("value", ["short", "has spaces", "../escape", "x" * 65])
def test_invalid_measurement_alias_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        validate_measurement_id(value)
