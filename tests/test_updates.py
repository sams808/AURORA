"""Opt-in update check against a local http.server standing in for GitHub (AURORA_UPDATE_URL): a newer tag gives a
notice naming it, the same or an older tag gives nothing, the setting off sends no request at start, and a server
that is down gives no dialog and one INFO log line."""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QMainWindow  # noqa: E402

from aurora import __version__  # noqa: E402
from aurora.app import updates  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.server.hits.append((self.path, self.headers.get("User-Agent", "")))
        body = json.dumps(self.server.payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def github(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.hits = []
    srv.payload = {}
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    monkeypatch.setenv("AURORA_UPDATE_URL", f"http://127.0.0.1:{srv.server_address[1]}/repos/sams808/AURORA/releases/latest")
    yield srv
    srv.shutdown()
    srv.server_close()


def release(tag: str) -> dict:
    return {"tag_name": tag, "html_url": f"https://github.com/sams808/AURORA/releases/tag/{tag}",
            "body": f"## AURORA {tag}\n\n- what changed", "draft": False, "prerelease": False,
            "assets": [{"name": f"AURORA-{tag.lstrip('v')}-windows-x64-setup.exe", "browser_download_url": "https://x/setup.exe"},
                       {"name": f"AURORA-{tag.lstrip('v')}-macos-arm64.dmg", "browser_download_url": "https://x/arm.dmg"},
                       {"name": f"AURORA-{tag.lstrip('v')}-macos-x86_64.dmg", "browser_download_url": "https://x/intel.dmg"}]}


def wait(checker, timeout=10.0):
    t0 = time.monotonic()
    while not checker.done and time.monotonic() - t0 < timeout:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    QCoreApplication.processEvents()
    return checker.done


def bump(v: str, i: int, d: int) -> str:
    parts = [int(x) for x in v.split(".")]
    parts[i] += d
    return ".".join(str(max(x, 0)) for x in parts)


def test_versions_compare_as_integer_tuples():
    assert updates.version_tuple("v0.10.2") == (0, 10, 2) and updates.version_tuple("0.4.0-rc1") == (0, 4, 0)
    assert updates.is_newer("v0.10.0", "0.9.9") and not updates.is_newer("v0.3.0", "0.3.0")
    assert not updates.is_newer("v0.2.9", "0.3.0") and updates.is_newer("v1.0", "0.99.99")
    assert not updates.is_newer("latest", "0.3.0")


def test_a_newer_tag_gives_a_notice(qapp, github):
    newer = "v" + bump(__version__, 0, 1)
    github.payload = release(newer)
    win = QMainWindow()
    checker = updates.check_now(win)
    assert wait(checker)
    assert len(github.hits) == 1 and github.hits[0][1] == f"AURORA/{__version__}"
    assert checker.info["newer"] and checker.info["version"] == newer.lstrip("v")
    dlg = win.update_dialog
    assert dlg.isVisible() and newer.lstrip("v") in dlg.windowTitle() and "what changed" in dlg.text()
    assert newer.lstrip("v") in win.statusBar().currentMessage()
    dlg.close()
    win.deleteLater()


@pytest.mark.parametrize("which", ["same", "older"])
def test_the_same_or_an_older_tag_gives_nothing(qapp, github, which):
    tag = "v" + (__version__ if which == "same" else bump(__version__, 1, -1) if __version__.split(".")[1] != "0"
                 else "0.0.1")
    github.payload = release(tag)
    win = QMainWindow()
    checker = updates.check_now(win, quiet=True)
    assert wait(checker)
    assert checker.info is not None and not checker.info["newer"]
    assert getattr(win, "update_dialog", None) is None and win.statusBar().currentMessage() == ""
    win.deleteLater()


def test_with_the_setting_off_nothing_is_asked_at_start(qapp, github):
    from aurora.app.settings import settings
    from aurora.app.settings_dialog import get
    github.payload = release("v99.0.0")
    win = QMainWindow()
    assert get("update_check") is False
    assert updates.maybe_check_at_start(win) is None
    for _ in range(50):
        QCoreApplication.processEvents()
        time.sleep(0.002)
    assert github.hits == []
    s = settings(); s.setValue("update_check", True); s.sync()           # on: one request, then not again that day
    checker = updates.maybe_check_at_start(win)
    assert checker is not None and wait(checker) and len(github.hits) == 1
    assert updates.maybe_check_at_start(win) is None and len(github.hits) == 1
    win.update_dialog.close()
    win.deleteLater()


def test_a_server_that_is_down_gives_no_dialog_and_one_info_line(qapp, monkeypatch, caplog):
    with socket.socket() as sk:                       # a port nobody listens on
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    monkeypatch.setenv("AURORA_UPDATE_URL", f"http://127.0.0.1:{port}/latest")
    win = QMainWindow()
    with caplog.at_level("INFO", logger="aurora.updates"):
        checker = updates.check_now(win, quiet=True)
        assert wait(checker, 15)
    assert checker.info is None and getattr(win, "update_dialog", None) is None
    lines = [r for r in caplog.records if r.name == "aurora.updates"]
    assert len(lines) == 1 and lines[0].levelname == "INFO" and "update check failed" in lines[0].getMessage()
    win.deleteLater()


class _NoRelease(BaseHTTPRequestHandler):
    """GitHub's answer to releases/latest while a repository has only tags or draft releases."""
    def do_GET(self):  # noqa: N802
        body = json.dumps({"message": "Not Found", "status": "404"}).encode("utf-8")
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_no_published_release_is_said_calmly(qapp, monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _NoRelease)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("AURORA_UPDATE_URL", f"http://127.0.0.1:{srv.server_address[1]}/repos/sams808/AURORA/releases/latest")
    try:
        win = QMainWindow()
        checker = updates.check_now(win)
        assert wait(checker)
        assert checker.info is not None and not checker.info["published"] and not checker.info["newer"]
        msg = win.statusBar().currentMessage()
        assert "no AURORA release is published" in msg and __version__ in msg and "could not" not in msg
        assert "404" not in msg and getattr(win, "update_dialog", None) is None
        win.deleteLater()
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_asset_for_this_platform(monkeypatch):
    rel = release("v1.2.3")["assets"]
    monkeypatch.setattr(updates.sys, "platform", "win32")
    assert updates.platform_asset(rel)["name"].endswith("windows-x64-setup.exe")
    monkeypatch.setattr(updates.sys, "platform", "darwin")
    monkeypatch.setattr(updates.platform, "machine", lambda: "arm64")
    assert updates.platform_asset(rel)["name"].endswith("macos-arm64.dmg")
    monkeypatch.setattr(updates.platform, "machine", lambda: "x86_64")
    assert updates.platform_asset(rel)["name"].endswith("macos-x86_64.dmg")
