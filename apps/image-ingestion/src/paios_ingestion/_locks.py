"""Cross-platform exclusive advisory file locks (flock on POSIX, msvcrt on Windows).

Liveness is inferred from lock ownership, never from PIDs: the OS drops a lock
when its holder exits, and probing a lock cannot signal or terminate anything.
"""
from __future__ import annotations

import os
import time
from typing import BinaryIO

if os.name == "nt":
    import msvcrt

    def try_lock(handle: BinaryIO) -> bool:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def unlock(handle: BinaryIO) -> None:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl

    def try_lock(handle: BinaryIO) -> bool:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def unlock(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def lock(handle: BinaryIO, timeout: float = 30.0) -> None:
    """Blocking exclusive lock with a bounded wait."""
    deadline = time.monotonic() + timeout
    while not try_lock(handle):
        if time.monotonic() > deadline:
            raise TimeoutError("timed out waiting for a cache lock")
        time.sleep(0.01)
