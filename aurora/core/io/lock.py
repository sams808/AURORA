"""One AURORA at a time edits a project: the lock file <root>/.aurora/session.lock.

The lock says who has the project open for editing: {'pid', 'host', 'user', 'app', 'since', 'time', 'token'} (JSON,
times in seconds since the epoch, so a move to another time zone changes nothing). The first AURORA to open a project
takes the lock; a second one (another process on this computer, or another computer on a shared folder) finds it live
and opens the session read-only, naming the holder. A lock is stale, and taken over, when its process is gone (same
computer) or when its holder has not refreshed it for LOCK_STALE_S (a holder refreshes it every LOCK_REFRESH_S). The
holder checks that the lock is still its own before every save, so a session whose lock was taken over stops saving
instead of overwriting the other one's work.

Within one process the lock is shared: several Sessions of one process on one project (tests, the self-test) hold the
same lock, and the last release removes the file.
"""
from __future__ import annotations

import datetime as dt
import errno
import getpass
import json
import logging
import os
import socket
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("aurora.io")

LOCK_NAME = "session.lock"
LOCK_REFRESH_S = 120                    # the holder rewrites the lock's time this often
LOCK_STALE_S = 600                      # a lock not refreshed for this long is taken over (a crashed or asleep holder)
UNREADABLE_GRACE_S = 10                 # an unreadable lock younger than this is being written by its creator: live
_HELD: dict[str, "ProjectLock"] = {}    # locks held by this process, by normalised path


def _host() -> str:
    try:
        return socket.gethostname() or "?"
    except OSError:
        return "?"


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:                   # noqa: BLE001  no login name (a service account): not needed to lock
        return ""


def pid_alive(pid: int) -> bool:
    """Whether a process with this id runs on this computer (never sends it a signal: on Windows os.kill(pid, 0)
    would terminate it)."""
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        h = k32.OpenProcess(0x1000, False, int(pid))         # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5             # access denied: it exists; invalid parameter: it does not
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return True
            return code.value == 259                         # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except OSError:                     # EPERM: it exists, owned by someone else
        return True
    return True


@dataclass
class LockInfo:
    """The content of a lock file."""
    pid: int = 0
    host: str = ""
    user: str = ""
    app: str = ""
    since: float = 0.0
    time: float = 0.0
    token: str = ""

    @classmethod
    def read(cls, path: Path) -> "LockInfo | None":
        """The lock in the file, None when it is missing or unreadable."""
        try:
            d = json.loads(Path(path).read_bytes().decode("utf-8-sig"))
            return cls(pid=int(d.get("pid", 0)), host=str(d.get("host", "")), user=str(d.get("user", "")),
                       app=str(d.get("app", "")), since=float(d.get("since", 0)), time=float(d.get("time", 0)),
                       token=str(d.get("token", "")))
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def to_bytes(self) -> bytes:
        return json.dumps({"pid": self.pid, "host": self.host, "user": self.user, "app": self.app,
                           "since": self.since, "time": self.time, "token": self.token}, indent=1).encode("utf-8")

    def here(self) -> bool:
        return self.host.lower() == _host().lower()

    def stale(self, now: float | None = None) -> bool:
        """The holder is gone: its process ended (this computer) or it has not refreshed the lock for LOCK_STALE_S."""
        now = time.time() if now is None else now
        if self.here() and not pid_alive(self.pid):
            return True
        return now - self.time > LOCK_STALE_S

    def describe(self) -> str:
        """'AURORA 0.4.0, process 1234 of jdoe on LAB-PC, since 2026-11-10 14:02'."""
        if not self.pid:
            return "another AURORA"
        who = f"process {self.pid}" + (f" of {self.user}" if self.user else "") + f" on {self.host or '?'}"
        since = dt.datetime.fromtimestamp(self.since).strftime("%Y-%m-%d %H:%M") if self.since else "?"
        return f"{self.app or 'AURORA'}, {who}, since {since}"


class ProjectLock:
    """The lock of one project held by this process (shared by its Sessions on that project)."""

    def __init__(self, path: Path, info: LockInfo):
        self.path = Path(path)
        self.info = info
        self.users = 0

    @staticmethod
    def _key(path: Path) -> str:
        try:
            p = Path(path).resolve()
        except OSError:
            p = Path(os.path.abspath(path))
        return os.path.normcase(str(p))

    @classmethod
    def acquire(cls, root: Path, app: str = "AURORA") -> "tuple[ProjectLock | None, LockInfo | None]":
        """Take the lock of the project at `root`: (the lock, None), or (None, the live holder's LockInfo) when another
        AURORA holds it. Raises OSError when the lock cannot be written (a protected or read-only folder)."""
        from aurora.core.io.safety import assert_writable
        path = Path(root) / ".aurora" / LOCK_NAME
        key = cls._key(path)
        held = _HELD.get(key)
        if held is not None and held.still_ours():
            held.users += 1
            return held, None
        assert_writable(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        mine = LockInfo(pid=os.getpid(), host=_host(), user=_user(), app=app, since=now, time=now,
                        token=uuid.uuid4().hex)
        for _attempt in range(3):
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            except FileExistsError:
                other = LockInfo.read(path)
                if other is None:
                    try:
                        age = now - path.stat().st_mtime
                    except FileNotFoundError:
                        continue                            # released meanwhile: try again
                    if age < UNREADABLE_GRACE_S:            # its creator is writing it now
                        return None, LockInfo(time=now)
                    log.info("%s is unreadable and %.0f s old: taken over", path, age)
                elif not other.stale(now) and not (other.here() and other.pid == os.getpid()):
                    return None, other
                else:
                    log.info("stale lock of %s taken over (%s)", path, other.describe())
                try:
                    cls._replace(path, mine.to_bytes())
                except PermissionError:                     # held open by its holder refreshing it: not stale
                    return None, other or LockInfo(time=now)
                if (LockInfo.read(path) or LockInfo()).token != mine.token:
                    other = LockInfo.read(path)
                    if other is not None:
                        return None, other                  # another AURORA took it over at the same moment
                    continue
                break
            else:
                try:
                    os.write(fd, mine.to_bytes())
                finally:
                    os.close(fd)
                break
        else:
            raise OSError(errno.EAGAIN, "the lock file could not be created", str(path))
        lock = cls(path, mine)
        lock.users = 1
        _HELD[key] = lock
        log.info("lock %s taken (%s)", path, mine.describe())
        return lock, None

    @staticmethod
    def _replace(path: Path, data: bytes) -> None:
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

    def holder(self) -> LockInfo | None:
        """What the lock file says now (None: missing or unreadable)."""
        return LockInfo.read(self.path)

    def still_ours(self) -> bool:
        info = self.holder()
        return info is not None and info.token == self.info.token

    def reclaim(self) -> bool:
        """The lock file went missing (deleted by hand, or released by a session that had taken it over): create it
        again. False when another AURORA holds it now."""
        from aurora.core.io.safety import assert_writable
        try:
            assert_writable(self.path)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            return self.still_ours()
        except OSError as err:
            log.info("lock %s not created again (%s)", self.path, err)
            return False
        try:
            self.info.time = time.time()
            os.write(fd, self.info.to_bytes())
        finally:
            os.close(fd)
        _HELD[self._key(self.path)] = self
        return True

    def refresh(self) -> bool:
        """Rewrite the lock's time (the holder is alive). False when the lock is no longer this process's."""
        if not self.still_ours():
            return False
        self.info.time = time.time()
        try:
            from aurora.core.io.safety import assert_writable
            assert_writable(self.path)          # a folder protected since the lock was taken is never written
            self._replace(self.path, self.info.to_bytes())
        except OSError as err:                  # held open for a moment by a reader: the next refresh writes it
            log.info("lock %s not refreshed (%s)", self.path, err)
        return True

    def release(self) -> None:
        """One Session of this process lets the project go; the last one removes the lock file (when still ours) and
        the .aurora folder when the lock was all it held."""
        self.users -= 1
        if self.users > 0:
            return
        key = self._key(self.path)
        if _HELD.get(key) is self:
            del _HELD[key]
        from aurora.core.io.safety import is_protected
        if not self.still_ours() or is_protected(self.path):
            return
        try:
            self.path.unlink()
            log.info("lock %s released", self.path)
        except OSError as err:
            log.info("lock %s not removed (%s)", self.path, err)
            return
        try:
            self.path.parent.rmdir()            # only when empty: a project only looked at keeps no .aurora folder
        except OSError:
            pass
