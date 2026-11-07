"""Colour tokens (docs/DESIGN.md) and the Qt style sheet built from them."""
from __future__ import annotations

TOKENS = {
    "night": "#0A1430",
    "rail": "#101E4A",
    "rail_text": "#C9D6F0",
    "aurora": "#38E0C2",
    "action": "#0E7C6B",
    "action_dark": "#0A5F52",
    "violet": "#A78BFA",
    "warn": "#F2A65A",
    "paper": "#F3F5F9",
    "card": "#FFFFFF",
    "ink": "#15213B",
    "muted": "#5B6B8C",
    "line": "#DDE3EE",
    "ok_bg": "#E3F8F3", "ok_fg": "#0A5F52",
    "warn_bg": "#FFF1DD", "warn_fg": "#8A4B00",
    "bad_bg": "#FDE2E2", "bad_fg": "#9B1C1C",
    "muted_bg": "#E4E9F2",
}

STATUS_COLOURS = {
    "complete": ("ok_bg", "ok_fg"),
    "survey": ("ok_bg", "ok_fg"),
    "in_progress": ("warn_bg", "warn_fg"),
    "test": ("muted_bg", "muted"),
    "aborted": ("bad_bg", "bad_fg"),
    "empty": ("bad_bg", "bad_fg"),
    "unreadable": ("bad_bg", "bad_fg"),
    "other": ("muted_bg", "muted"),
}

UI_FONT = "Inter, 'Segoe UI', 'SF Pro Text', 'Helvetica Neue', Arial, sans-serif"
MONO_FONT = "'JetBrains Mono', Consolas, 'SF Mono', Menlo, monospace"


def qss() -> str:
    t = TOKENS
    return f"""
QMainWindow, QDialog {{ background: {t['paper']}; }}
QWidget {{ color: {t['ink']}; font-size: 13px; }}
QWidget#topbar {{ background: {t['night']}; }}
QWidget#topbar QLabel {{ color: #F4FFF8; }}
QLabel#brand {{ font-weight: 700; letter-spacing: 2px; font-size: 15px; color: #F4FFF8; }}
QLabel#crumb {{ color: #9FB4D8; }}
QWidget#rail {{ background: {t['rail']}; }}
QToolButton#railbtn {{ color: {t['rail_text']}; background: transparent; border: 0; border-radius: 10px;
    padding: 8px 4px; font-size: 11px; min-width: 76px; }}
QToolButton#railbtn:hover {{ background: #1A2A5E; }}
QToolButton#railbtn:checked {{ background: {t['aurora']}; color: {t['night']}; font-weight: 600; }}
QFrame#card {{ background: {t['card']}; border: 1px solid {t['line']}; border-radius: 12px; }}
QLabel#cardtitle {{ font-weight: 600; }}
QLabel#h1 {{ font-size: 18px; font-weight: 600; }}
QLabel#muted {{ color: {t['muted']}; }}
QPushButton {{ background: {t['card']}; border: 1px solid #CBD3E1; border-radius: 8px; padding: 6px 12px; }}
QPushButton:hover {{ border-color: {t['action']}; }}
QPushButton#primary {{ background: {t['action']}; color: white; border: 0; font-weight: 600; }}
QPushButton#primary:hover {{ background: {t['action_dark']}; }}
QPushButton#primary:disabled {{ background: #9FB4B0; }}
QTreeView, QTableView, QTextBrowser, QListView {{ background: {t['card']}; border: 1px solid {t['line']};
    border-radius: 8px; alternate-background-color: #F8FAFD; }}
QTreeView::item {{ padding: 3px 2px; }}
QHeaderView::section {{ background: {t['paper']}; color: {t['muted']}; border: 0; border-bottom: 1px solid {t['line']};
    padding: 4px 6px; font-weight: 500; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {t['card']}; border: 1px solid #CBD3E1; border-radius: 8px;
    padding: 4px 8px; min-height: 22px; }}
QProgressBar {{ border: 1px solid {t['line']}; border-radius: 6px; background: {t['card']}; text-align: center; height: 14px; }}
QProgressBar::chunk {{ background: {t['aurora']}; border-radius: 6px; }}
QStatusBar {{ background: {t['paper']}; color: {t['muted']}; }}
QSplitter::handle {{ background: {t['paper']}; }}
QMenuBar {{ background: {t['paper']}; }}
QToolTip {{ background: {t['night']}; color: #F4FFF8; border: 0; padding: 4px 6px; }}
"""


def chip_style(kind: str) -> str:
    bg, fg = STATUS_COLOURS.get(kind, ("muted_bg", "muted"))
    return (f"background: {TOKENS[bg]}; color: {TOKENS[fg]}; border-radius: 9px; padding: 2px 8px; "
            f"font-weight: 500; font-size: 12px;")
