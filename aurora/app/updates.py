"""Opt-in check for a newer AURORA on GitHub (Help > Check for updates…, and once a day at start when Settings asks).

- GET https://api.github.com/repos/sams808/AURORA/releases/latest through QNetworkAccessManager (Qt's own TLS backend:
  the frozen Python carries no CA bundle). GitHub already leaves drafts and prereleases out of 'latest'.
- User-Agent AURORA/<version>, 5 s timeout; the tag is compared with __version__ as integer tuples.
- Newer: a calm status line and a dialog with the release notes, the release page and the asset for this OS and
  architecture. Up to date: a status line only. Nothing is ever downloaded or installed.
- Failures go to the log at INFO level, never to a dialog. The time of the last check is kept in the settings.
- AURORA_UPDATE_URL overrides the URL (the tests use a local http.server).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import platform
import re
import sys

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QTextBrowser, QVBoxLayout, QWidget

from aurora import APP_NAME, __version__

log = logging.getLogger("aurora.updates")

URL = "https://api.github.com/repos/sams808/AURORA/releases/latest"
TIMEOUT_MS = 5000
EVERY_S = 24 * 3600                     # at most one start-up check a day
LAST_KEY = "update_last_check"


def update_url() -> str:
    return os.environ.get("AURORA_UPDATE_URL") or URL


def version_tuple(tag: str) -> tuple[int, ...]:
    """'v0.4.1' -> (0, 4, 1); the numeric parts only ('0.4.0-rc1' -> (0, 4, 0))."""
    m = re.match(r"^\s*[vV]?(\d+(?:\.\d+)*)", str(tag or ""))
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def is_newer(tag: str, current: str = __version__) -> bool:
    a, b = version_tuple(tag), version_tuple(current)
    if not a:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def platform_asset(assets: list[dict]) -> dict | None:
    """The release asset for this OS and architecture (setup.exe on Windows, the DMG of the arch on macOS)."""
    mach = platform.machine().lower()
    arch = "arm64" if mach in ("arm64", "aarch64") else "x86_64" if sys.platform == "darwin" else "x64"
    names = [(a, str(a.get("name", "")).lower()) for a in assets or []]
    if sys.platform.startswith("win"):
        for a, n in names:
            if n.endswith("setup.exe") and ("x64" in n or "windows" in n):
                return a
        for a, n in names:
            if n.endswith(".zip") and "windows" in n:
                return a
    elif sys.platform == "darwin":
        for a, n in names:
            if n.endswith(".dmg") and arch in n:
                return a
    return None


class UpdateDialog(QDialog):
    """A newer release: its version, notes and links (nothing is downloaded)."""

    def __init__(self, info: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} {info['version']} is available")
        self.info = info
        lay = QVBoxLayout(self)
        head = QLabel(f"<b>{APP_NAME} {info['version']}</b> is available; this is {APP_NAME} {__version__}.")
        lay.addWidget(head)
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(info.get("notes") or "No release notes.")
        notes.setAccessibleName("release notes")
        lay.addWidget(notes, 1)
        links = [f'<a href="{info["page"]}">the release page</a>'] if info.get("page") else []
        if info.get("asset_url"):
            links.append(f'<a href="{info["asset_url"]}">{info.get("asset_name", "the installer")}</a>')
        if links:
            ln = QLabel("Download from " + " or ".join(links) + ". AURORA never downloads or installs anything itself.")
            ln.setOpenExternalLinks(True)
            ln.setWordWrap(True)
            lay.addWidget(ln)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        from aurora.app.help_text import explain_buttons
        explain_buttons(box)
        lay.addWidget(box)
        self.resize(560, 420)

    def text(self) -> str:
        return f"{self.windowTitle()}\n{self.info.get('notes', '')}"


class UpdateChecker(QObject):
    """One request; `finished(info | None)` on the GUI thread. info = {'version', 'tag', 'newer', 'notes', 'page',
    'asset_name', 'asset_url'}; None after a failure."""
    finished = Signal(object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.nam = QNetworkAccessManager(self)
        self.reply: QNetworkReply | None = None
        self.info: dict | None = None
        self.error = ""
        self.done = False

    def start(self, url: str | None = None) -> None:
        req = QNetworkRequest(QUrl(url or update_url()))
        req.setHeader(QNetworkRequest.UserAgentHeader, f"{APP_NAME}/{__version__}")
        req.setRawHeader(b"Accept", b"application/vnd.github+json")
        req.setTransferTimeout(TIMEOUT_MS)
        self.reply = self.nam.get(req)
        self.reply.finished.connect(self._finished)

    @Slot()
    def _finished(self) -> None:
        reply = self.reply
        try:
            if reply.error() != QNetworkReply.NoError:
                raise OSError(reply.errorString())
            data = json.loads(bytes(reply.readAll()).decode("utf-8"))
            tag = str(data.get("tag_name") or "")
            if not version_tuple(tag):
                raise ValueError(f"no version in the tag {tag!r}")
            asset = platform_asset(data.get("assets") or [])
            ver = ".".join(str(x) for x in version_tuple(tag))
            self.info = {"version": ver, "tag": tag, "newer": is_newer(tag), "notes": str(data.get("body") or ""),
                         "page": str(data.get("html_url") or ""),
                         "asset_name": str(asset.get("name", "")) if asset else "",
                         "asset_url": str(asset.get("browser_download_url", "")) if asset else ""}
        except Exception as err:                # noqa: BLE001  a failed check is never an error for the user
            self.error = str(err) or type(err).__name__
            self.info = None
            log.info("update check failed: %s", self.error)
        finally:
            reply.deleteLater()
            self.done = True
        self.finished.emit(self.info)


def _remember_check() -> None:
    from aurora.app.settings import settings
    s = settings()
    s.setValue(LAST_KEY, dt.datetime.now().isoformat(timespec="seconds"))
    s.sync()


def _status(window, text: str) -> None:
    sb = getattr(window, "statusBar", None)
    if callable(sb):
        sb().showMessage(text, 10000)


def check_now(window, quiet: bool = False) -> UpdateChecker:
    """Ask GitHub now. A newer release: a status line and a dialog (UpdateDialog, not modal). Up to date: a status line
    unless quiet. A failure: an INFO log line and, unless quiet, a status line; never a dialog."""
    checker = UpdateChecker(window)
    window.update_checker = checker

    def done(info) -> None:
        _remember_check()
        if info is None:
            if not quiet:
                _status(window, f"could not check for updates ({checker.error})")
            return
        if info["newer"]:
            _status(window, f"{APP_NAME} {info['version']} is available (this is {__version__})")
            dlg = UpdateDialog(info, window)
            window.update_dialog = dlg
            dlg.show()
        elif not quiet:
            _status(window, f"{APP_NAME} {__version__} is up to date (latest release {info['version']})")
        log.info("update check: latest %s, this %s", info["version"], __version__)

    checker.finished.connect(done)
    checker.start()
    return checker


def due(now: dt.datetime | None = None) -> bool:
    """Whether the start-up check is due (at most once a day)."""
    from aurora.app.settings import settings
    last = str(settings().value(LAST_KEY, "") or "")
    try:
        t = dt.datetime.fromisoformat(last)
    except ValueError:
        return True
    return ((now or dt.datetime.now()) - t).total_seconds() >= EVERY_S


def maybe_check_at_start(window) -> UpdateChecker | None:
    """The start-up check: only when Settings > 'check GitHub for a newer AURORA at start' is on, once a day."""
    from aurora.app.settings_dialog import get
    if not get("update_check") or not due():
        return None
    return check_now(window, quiet=True)
