"""The dialogs of the Help menu besides the manuals: Keyboard shortcuts (Ctrl+/), Report a problem… (with Copy
diagnostics), Licences… and Run self-test… (aurora --selftest in a background job, then a summary with Copy and
Open folder)."""
from __future__ import annotations

import datetime as dt
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTabWidget,
                               QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget)

from aurora import APP_NAME, __version__, resources_dir
from aurora.app.help_text import explain_buttons

ISSUES_URL = "https://github.com/sams808/AURORA/issues"
LOG_TAIL = 200                       # lines of the log copied into the diagnostics


# ----------------------------------------------------------------------------- keyboard shortcuts
def shortcut_rows(window) -> list[tuple[str, str, str, list]]:
    """[(section, action text, shortcut text, [actions])] for every QAction with a shortcut in the window, grouped by
    section (shortcuts.add's section, else the action's own), identical entries merged."""
    from aurora.app import shortcuts
    rows: dict[tuple[str, str, str], list] = {}
    for a in window.findChildren(QAction):
        seqs = [s for s in a.shortcuts() if not s.isEmpty()]
        if not seqs:
            continue
        section = shortcuts.section_of(a)
        text = a.text().replace("&", "").strip() or a.toolTip() or "(unnamed)"
        keys = ", ".join(s.toString(QKeySequence.NativeText) for s in seqs)
        rows.setdefault((section, text, keys), []).append(a)
    order = {s: i for i, s in enumerate(("File", "Edit", "View", "Go to", "Help", "General"))}
    return [(s, t, k, acts) for (s, t, k), acts in sorted(rows.items(), key=lambda kv: (order.get(kv[0][0], 50),
                                                                                         kv[0][0]))]


class ShortcutsDialog(QDialog):
    """Help > Keyboard shortcuts: generated from the window's actions, so it is never out of date."""

    def __init__(self, window, parent: QWidget | None = None):
        super().__init__(parent or window)
        self.setWindowTitle(f"{APP_NAME} keyboard shortcuts")
        self.rows = shortcut_rows(window)
        lay = QVBoxLayout(self)
        self.table = QTableWidget(len(self.rows), 3)
        self.table.setHorizontalHeaderLabels(["Section", "Action", "Shortcut"])
        self.table.setAccessibleName("keyboard shortcuts")
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        for r, (section, text, keys, _acts) in enumerate(self.rows):
            for c, v in enumerate((section, text, keys)):
                self.table.setItem(r, c, QTableWidgetItem(v))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table, 1)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        explain_buttons(box)
        lay.addWidget(box)
        self.resize(560, 560)

    def text(self) -> str:
        return "\n".join(f"{s}\t{t}\t{k}" for s, t, k, _a in self.rows)


# ----------------------------------------------------------------------------- report a problem
def _dir_size(path: Path, limit: int = 20000) -> int:
    total = 0
    try:
        for i, p in enumerate(path.rglob("*")):
            if i > limit:
                break
            if p.is_file():
                total += p.stat().st_size
    except OSError:
        pass
    return total


def tilde_home(text: str) -> str:
    """The home folder written as '~' in either slash style (case-insensitive on Windows), so a report pasted into the
    public issues does not carry the account name. A longer name that only starts like the home folder stays."""
    home = str(Path.home())
    if len(home.strip("\\/")) < 3:                  # '/' or 'C:\': nothing to hide
        return text
    flags = re.IGNORECASE if os.name == "nt" else 0
    for h in sorted({home, home.replace("\\", "/"), home.replace("/", "\\")}, key=len, reverse=True):
        text = re.sub(re.escape(h) + r"(?![\w.-])", "~", text, flags=flags)
    return text


def diagnostics_text(window=None) -> str:
    """What to paste into a bug report: versions, OS, project, session schema, cache size, settings location, the
    credits line and the last 200 lines of the log, with the home folder written as '~' (tilde_home)."""
    from aurora.app.settings import settings
    from aurora.credits import CREDITS_SHORT
    from aurora.logsetup import log_file
    lines = [f"{APP_NAME} {__version__}" + (" (frozen)" if getattr(sys, "frozen", False) else "")]
    try:
        import h5py
        import numpy
        import PySide6
        from PySide6.QtCore import qVersion
        lines.append(f"Python {sys.version.split()[0]} · Qt {qVersion()} · PySide6 {PySide6.__version__} · "
                     f"numpy {numpy.__version__} · h5py {h5py.__version__}")
    except ImportError as err:
        lines.append(f"versions incomplete: {err}")
    lines.append(f"OS {platform.platform()} · {platform.machine()}")
    ses = getattr(window, "session", None)
    proj = getattr(ses, "project", None)
    if proj is not None:
        lines.append(f"project {proj.root}")
        zones = getattr(ses, "zones", None)
        lines.append(f"session schema {getattr(zones, 'schema', None) or 1} "
                     f"({getattr(zones, 'app', None) or 'no header: AURORA 0.3 or the zone tool'})"
                     + (" · read-only" if getattr(ses, "read_only", False) else ""))
        cache = Path(proj.root) / ".aurora" / "cache"
        lines.append(f"cache {cache}: {_dir_size(cache) / 1e6:.1f} MB" if cache.is_dir() else f"cache {cache}: none")
    else:
        lines.append("project: none open")
    lines.append(f"settings {settings().fileName()}")
    lines.append(CREDITS_SHORT)
    lines.append("")
    lf = log_file()
    try:
        tail = lf.read_text(encoding="utf-8", errors="replace").splitlines()[-LOG_TAIL:]
        lines.append(f"last {len(tail)} lines of {lf}:")
        lines += tail
    except OSError:
        lines.append(f"no log file at {lf}")
    return tilde_home("\n".join(lines))


class ReportProblemDialog(QDialog):
    def __init__(self, window, parent: QWidget | None = None):
        super().__init__(parent or window)
        self.window_ = window
        self.setWindowTitle("Report a problem")
        lay = QVBoxLayout(self)
        intro = QLabel("To report a problem, describe what you did and what happened, then paste the diagnostics "
                       "(Copy diagnostics) into the report. Attach the log file when AURORA closed unexpectedly. "
                       "Reports go to the GitHub issues of the project, which are public: the diagnostics name folders "
                       "and carry recent log lines with project, scan and sample names (your home folder is written "
                       "as ~). Read them and remove what should stay private before posting.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.intro = intro
        url = QLabel(f'<a href="{ISSUES_URL}">{ISSUES_URL}</a>')
        url.setTextFormat(Qt.RichText)
        url.setOpenExternalLinks(True)
        url.setTextInteractionFlags(Qt.TextBrowserInteraction)
        url.setAccessibleName("GitHub issues address")
        url.setToolTip("Opens the GitHub issues page in the browser (public)")
        self.url = url
        lay.addWidget(url)
        self.preview = QPlainTextEdit(diagnostics_text(window))
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("diagnostics")
        lay.addWidget(self.preview, 1)
        row = QHBoxLayout()
        self.copy_btn = QPushButton("Copy diagnostics")
        self.copy_btn.setToolTip("Copy the versions, the project, the settings location and the last log lines")
        self.copy_btn.setAccessibleName("Copy diagnostics")
        self.copy_btn.clicked.connect(self.copy)
        self.log_btn = QPushButton("Open the log folder")
        self.log_btn.setToolTip("The folder of aurora.log, to attach to the report")
        self.log_btn.setAccessibleName("Open the log folder")
        self.log_btn.clicked.connect(self._open_logs)
        row.addWidget(self.copy_btn)
        row.addWidget(self.log_btn)
        row.addStretch(1)
        lay.addLayout(row)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        explain_buttons(box)
        lay.addWidget(box)
        self.resize(720, 520)

    def copy(self) -> str:
        text = diagnostics_text(self.window_)
        QGuiApplication.clipboard().setText(text)
        self.copy_btn.setText("Copied")
        return text

    def _open_logs(self) -> None:
        from aurora.logsetup import log_dir
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir())))


# ----------------------------------------------------------------------------- licences
THIRD_PARTY_NOTE = ("Third-party notices are generated at build time (packaging/third_party_licenses.py) and ship with "
                    "the installers as THIRD_PARTY_LICENSES.txt.")


def _licence_dirs() -> list[Path]:
    out = [resources_dir() / "licenses"]
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        if base:
            out.append(Path(base))
        exe = Path(sys.executable).resolve().parent
        out += [exe, exe.parent / "Resources"]
    out.append(Path(__file__).resolve().parents[2])        # source checkout: the repository root
    return out


def licence_texts() -> list[tuple[str, str]]:
    """[(title, text)] of LICENSE, CREDITS.md and THIRD_PARTY_LICENSES.txt, from the bundle or the source checkout."""
    out = []
    for name, title in (("LICENSE", "AURORA licence"), ("CREDITS.md", "Credits"),
                        ("THIRD_PARTY_LICENSES.txt", "Third-party licences")):
        text = None
        for d in _licence_dirs():
            p = d / name
            if p.is_file():
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                    break
                except OSError:
                    continue
        if text is None and name == "THIRD_PARTY_LICENSES.txt":
            text = THIRD_PARTY_NOTE
        if text is not None:
            out.append((title, text))
    return out


class LicencesDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} licences")
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.texts = licence_texts()
        for title, text in self.texts:
            if title == "Credits":                         # CREDITS.md: a heading and a table, shown as such
                ed = QTextBrowser()
                ed.setOpenExternalLinks(True)
                ed.setMarkdown(text)
            else:
                ed = QPlainTextEdit(text)
                ed.setReadOnly(True)
            ed.setAccessibleName(title)
            self.tabs.addTab(ed, title)
        lay.addWidget(self.tabs, 1)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        explain_buttons(box)
        lay.addWidget(box)
        self.resize(720, 600)

    def text(self) -> str:
        return "\n\n".join(f"{t}\n{x}" for t, x in self.texts)


# ----------------------------------------------------------------------------- self-test
def selftest_dir() -> Path:
    from platformdirs import user_data_dir
    return Path(user_data_dir(APP_NAME, appauthor=False)) / "selftest"


def selftest_command(out: Path) -> list[str]:
    """The command that runs `aurora --selftest --out <out>` with this installation (frozen or from source)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--selftest", "--out", str(out)]
    return [sys.executable, "-m", "aurora", "--selftest", "--out", str(out)]


EXPECTED_CHECKS = 57          # checks of a full run, the progress scale (tests/test_selftest.py keeps it equal)


def run_selftest(progress, out_dir: Path | None = None) -> dict:
    """aurora.selftest in a separate process (it sets its own temporary settings, logs and Qt platform, which must not
    touch the running window). Returns the report: {version, ok, checks: [{name, ok, seconds, detail}], json, ...}."""
    folder = Path(out_dir) if out_dir else selftest_dir()
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"selftest_{dt.datetime.now():%Y%m%d_%H%M%S}.json"
    env = dict(os.environ)
    env.pop("AURORA_SETTINGS_DIR", None)
    env["PYTHONIOENCODING"] = "utf-8"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform.startswith("win") else 0
    proc = subprocess.Popen(selftest_command(out), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", env=env, creationflags=flags)
    tail: list[str] = []
    n = 0
    try:
        for line in proc.stdout:
            line = line.rstrip()
            tail = (tail + [line])[-40:]
            if line.lstrip().startswith(("ok ", "FAIL ")):
                n += 1
            progress(n, EXPECTED_CHECKS, line.strip()[:120])
        proc.wait()
    except BaseException:                      # Cancelled (or anything else): the child process goes too
        proc.kill()
        proc.wait()
        raise
    try:
        summary = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        summary = {"version": __version__, "ok": False, "checks": [], "error": "\n".join(tail)}
    summary["json"] = str(out)
    summary["returncode"] = proc.returncode
    return summary


def summary_text(summary: dict) -> str:
    checks = summary.get("checks") or []
    n_ok = sum(1 for c in checks if c.get("ok"))
    lines = [f"{APP_NAME} {summary.get('version', __version__)} self-test: {n_ok} of {len(checks)} checks ok"]
    for c in checks:
        if not c.get("ok"):
            lines.append(f"failed: {c.get('name')}: {c.get('detail', '')}")
    if not checks and summary.get("error"):
        lines.append("the self-test did not finish:")
        lines.append(str(summary["error"])[-2000:])
    if summary.get("json"):
        lines.append(f"report: {summary['json']}")
    return "\n".join(lines)


class SelftestDialog(QDialog):
    """The self-test summary with Copy and Open folder."""

    def __init__(self, summary: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.summary = summary
        self.setWindowTitle(f"{APP_NAME} self-test")
        lay = QVBoxLayout(self)
        self.view = QPlainTextEdit(summary_text(summary))
        self.view.setReadOnly(True)
        self.view.setAccessibleName("self-test summary")
        lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setToolTip("Copy the summary and the JSON report")
        self.copy_btn.setAccessibleName("Copy the self-test report")
        self.copy_btn.clicked.connect(self.copy)
        self.folder_btn = QPushButton("Open folder")
        self.folder_btn.setToolTip("The folder of the JSON report")
        self.folder_btn.setAccessibleName("Open the self-test folder")
        self.folder_btn.clicked.connect(self._open_folder)
        row.addWidget(self.copy_btn)
        row.addWidget(self.folder_btn)
        row.addStretch(1)
        lay.addLayout(row)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        explain_buttons(box)
        lay.addWidget(box)
        self.resize(640, 420)

    def text(self) -> str:
        return self.view.toPlainText()

    def copy(self) -> None:
        body = json.dumps({k: v for k, v in self.summary.items() if k != "json"}, indent=1, ensure_ascii=False)
        QGuiApplication.clipboard().setText(self.text() + "\n\n" + body)

    def _open_folder(self) -> None:
        p = self.summary.get("json")
        if p:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(p).parent)))
