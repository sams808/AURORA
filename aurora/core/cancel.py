"""Cancelling long core work (stack reduction, hypercube loading, reports) from the GUI, without Qt.

The core loops call progress(i, n, message) once per energy or entry. A job wraps that callback with cancellable():
once the job's token is cancelled, the next progress call raises Cancelled, the loop unwinds, and the caches (written
after the loop) are never written half-way.
"""
from __future__ import annotations

import threading
from typing import Callable

Progress = Callable[..., None]


class Cancelled(BaseException):
    """Raised inside a job when its token was cancelled. Never an error: the job simply stops.

    A BaseException (like KeyboardInterrupt), so the `except Exception` blocks of the core loops let it through."""


class CancelToken:
    """A thread-safe cancel flag shared by the GUI (cancel) and the worker (check)."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise Cancelled()


def cancellable(progress: Progress | None, token: CancelToken) -> Progress:
    """A progress callback that raises Cancelled once `token` is cancelled, then forwards to `progress`."""

    def cb(i, n, msg: str = "") -> None:
        token.check()
        if progress is not None:
            progress(i, n, msg)

    return cb
