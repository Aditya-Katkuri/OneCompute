"""Cross-platform process lock for one measurement profile."""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import BinaryIO


class ProfileInUseError(RuntimeError):
    """Raised when another observer already owns the profile."""


class ProfileLock:
    def __init__(self, profile_path: str | os.PathLike[str]) -> None:
        profile = Path(profile_path)
        self.path = profile.with_name(profile.name + ".lock")
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise ProfileInUseError(
                    f"another observer is already using profile {self.path.with_suffix('')}"
                ) from exc
            raise
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        finally:
            handle.close()
            self._handle = None

    def __enter__(self) -> ProfileLock:
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()
