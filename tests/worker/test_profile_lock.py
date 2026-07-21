from __future__ import annotations

import pytest

from worker.profile_lock import ProfileInUseError, ProfileLock


def test_profile_lock_blocks_a_second_observer_and_releases_cleanly(tmp_path) -> None:
    profile = tmp_path / "usage_profile.json"
    first = ProfileLock(profile)
    second = ProfileLock(profile)

    first.acquire()
    try:
        with pytest.raises(ProfileInUseError):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()
