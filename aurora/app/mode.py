"""Easy / Advanced mode.

Easy mode (the default on first start) is the basic processing path of John Bussey's SGM XAS GUI: open a beamtime,
draw zones, reduce, check, compare, fit with LCF, build the report. Advanced mode shows every option.

Perspectives mark what is advanced; the registry hides it in Easy mode and shows it again in Advanced mode:

    from aurora.app.mode import mark_advanced, mark_advanced_tab, on_mode_changed, is_advanced
    mark_advanced(self.deconv_box, self.roi_combo)        # widgets (wrap related controls in one container)
    mark_advanced_tab(self.tabs, self.itfa_tab)            # a tab of a QTabWidget
    mark_advanced_column(self.table, 4, 5, 6)              # columns of a table or tree view
    mark_advanced_row(form, self.k_spin)                   # a QFormLayout row (label and field)
    mark_easy(self.fixed_values_line)                      # shown only in Easy (fixed values, read-only)
    on_mode_changed(self, lambda adv: ...)                 # anything else (captions)

Invariants: a switch never writes the session, never writes any setting other than 'advanced_mode' and never changes
a widget value; hidden widgets keep their values (a value set in Advanced stays in force in Easy). Do not mark a widget
whose visibility is also driven by other logic: put it in a container and mark the container. Mark a widget once it has
a parent (a parentless widget would open as a window when shown).

EASY_DEFAULTS is the one table of the values Easy mode uses (zone-scan settings, Compare rows, Fit, Report);
easy_scan_settings(scan, flags) builds a ScanSettings from it.
"""
from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QObject, QPoint, Qt, Signal, Slot
from PySide6.QtWidgets import QMenu, QTabWidget, QWidget

from aurora.core.constants import DESPIKE_K, I0_COL, TEY_COL

SETTING = "advanced_mode"

# Values Easy mode uses. 'setting:<key>' = the Settings default of that key, read when the value is needed.
EASY_DEFAULTS: dict[str, dict] = {
    "zone_scan": {
        "fluo_norm": "setting:fluo_norm",       # Settings default (i0jump)
        "tey_norm": "setting:tey_norm",         # Settings default (i0jump)
        "tey": True,
        "sdd": [True, True, True, True],        # minus the SDDs flagged saturated (flags.merged_flags)
        "despike": True,
        "k": "setting:despike_k",               # Settings despike_k (constants.DESPIKE_K)
        "i0_source": "own",                     # the good-I0 mean when the scan's I0 is flagged invalid
        "pre": None,                            # automatic
        "post": None,                           # automatic
        "line": "main",                         # the holder's main line: sgm_report summary, else lines.main_line
        "i0_channel": I0_COL,                   # mcc ch1 (Au mesh)
        "tey_channel": TEY_COL,                 # mcc ch4
        "aggregation": "zone sum",              # John Bussey used the mean, which gives the same / I0 ratio
        "signal": "default",                    # lines.default_signal(scan, summary): PFY, or IPFY from the summary
    },
    "compare": {
        "norm": "larch", "pre": (-20.0, -5.0), "post": (15.0, 35.0),    # eV from E0
        "flatten": True, "e0": "derivative maximum", "spacing": 0.6, "scale": 1.0, "labels": True,
    },
    "fit": {"method": "lcf", "sum_to_one": True, "allow_negative": False, "bootstrap": 200, "fit_range": "compare x range"},
    "report": {"groups": "all", "appendix_a": True, "appendix_b": True, "preset": "final", "dpi": 600},
}
EASY_DEFAULTS_K_FALLBACK = DESPIKE_K

# perspectives shown only in Advanced mode (MainWindow marks them; plain strings, no Qt objects at module level)
_ADVANCED_PERSPECTIVES: set[str] = set()


class ModeNotifier(QObject):
    changed = Signal(bool)


def notifier() -> ModeNotifier | None:
    app = QCoreApplication.instance()
    if app is None:
        return None
    n = app.findChild(ModeNotifier, "aurora_mode_notifier")
    if n is None:
        n = ModeNotifier(app)
        n.setObjectName("aurora_mode_notifier")
    return n


def is_advanced() -> bool:
    from aurora.app.settings_dialog import get
    return bool(get(SETTING))


def set_advanced(on: bool) -> None:
    """Write the one setting 'advanced_mode' and tell every registered widget (nothing else is written)."""
    from aurora.app.settings import settings
    s = settings()
    s.setValue(SETTING, bool(on))
    s.sync()
    n = notifier()
    if n is not None:
        n.changed.emit(bool(on))


class _Hook(QObject):
    def __init__(self, owner: QObject, fn):
        super().__init__(owner)
        self._fn = fn

    @Slot(bool)
    def fire(self, adv: bool) -> None:
        try:
            self._fn(adv)
        except RuntimeError:
            pass


def on_mode_changed(owner: QObject, fn, call_now: bool = True) -> None:
    """Call fn(advanced) on every mode change while `owner` lives (and once now unless call_now is False)."""
    n = notifier()
    if n is not None:
        n.changed.connect(_Hook(owner, fn).fire)
    if call_now:
        fn(is_advanced())


def _show(w: QWidget, on: bool) -> None:
    if on and w.parentWidget() is None:          # never pop a parentless widget up as a window
        return
    w.setVisible(on)


def mark_advanced(*widgets: QWidget) -> None:
    """Hide these widgets in Easy mode."""
    for w in widgets:
        if w is None:
            continue
        w.setProperty("aurora_advanced", True)
        on_mode_changed(w, lambda adv, ww=w: _show(ww, adv))


def mark_easy(*widgets: QWidget) -> None:
    """Show these widgets only in Easy mode (fixed values shown read-only, e.g. '± from 200 bootstrap draws')."""
    for w in widgets:
        if w is None:
            continue
        w.setProperty("aurora_easy", True)
        on_mode_changed(w, lambda adv, ww=w: _show(ww, not adv))


def mark_advanced_row(form, *fields: QWidget) -> None:
    """Hide QFormLayout rows (label and field) in Easy mode."""
    for f in fields:
        f.setProperty("aurora_advanced", True)
        lab = form.labelForField(f)
        if lab is not None:
            lab.setProperty("aurora_advanced", True)
        on_mode_changed(f, lambda adv, ff=f, fo=form: fo.setRowVisible(ff, adv))


def mark_advanced_tab(tabs: QTabWidget, page: QWidget | int) -> None:
    """Hide one tab of a QTabWidget in Easy mode (the current tab moves to the first visible one)."""
    def apply(adv: bool, t=tabs, p=page) -> None:
        i = p if isinstance(p, int) else t.indexOf(p)
        if i < 0:
            return
        t.setTabVisible(i, adv)
        if not adv and t.currentIndex() == i:
            for j in range(t.count()):
                if t.isTabVisible(j):
                    t.setCurrentIndex(j)
                    break
    if isinstance(page, QWidget):
        page.setProperty("aurora_advanced_tab", True)
    on_mode_changed(tabs, apply)


def mark_advanced_action(owner: QObject, *actions) -> None:
    """Hide menu actions in Easy mode."""
    for a in actions:
        a.setProperty("aurora_advanced", True)
        on_mode_changed(owner, lambda adv, aa=a: aa.setVisible(adv))


class _ColumnModes(QObject):
    """Per view: the columns hidden in Easy mode, plus the Advanced header menu that shows or hides any column."""

    def __init__(self, view):
        super().__init__(view)
        self.setObjectName("aurora_column_modes")
        self.view = view
        self.cols: set[int] = set()
        self.user_hidden: set[int] = set()
        header = view.header() if hasattr(view, "header") else view.horizontalHeader()
        self.header = header
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self.menu)
        on_mode_changed(self, self.apply, call_now=False)

    def apply(self, adv: bool) -> None:
        n = self.view.model().columnCount() if self.view.model() is not None else 0
        for c in range(n):
            if c in self.cols:
                self.view.setColumnHidden(c, (not adv) or c in self.user_hidden)
            elif c in self.user_hidden:
                self.view.setColumnHidden(c, adv)

    def menu_actions(self) -> QMenu:
        m = QMenu(self.view)
        model = self.view.model()
        for c in range(model.columnCount()):
            a = m.addAction(str(model.headerData(c, Qt.Horizontal) or c))
            a.setCheckable(True)
            a.setChecked(not self.view.isColumnHidden(c))
            a.toggled.connect(lambda on, cc=c: self.toggle(cc, on))
        return m

    @Slot(QPoint)
    def menu(self, pos: QPoint) -> None:
        if not is_advanced():
            return
        self.menu_actions().exec(self.header.mapToGlobal(pos))

    def toggle(self, col: int, on: bool) -> None:
        if on:
            self.user_hidden.discard(col)
        else:
            self.user_hidden.add(col)
        self.view.setColumnHidden(col, not on)


def mark_advanced_column(view, *cols: int) -> None:
    """Hide columns of a QTableView / QTableWidget / QTreeView in Easy mode. In Advanced, a header context menu shows
    or hides any column."""
    helper = view.findChild(_ColumnModes, "aurora_column_modes")
    if helper is None:
        helper = _ColumnModes(view)
    helper.cols.update(int(c) for c in cols)
    view.setProperty("aurora_advanced_columns", sorted(helper.cols))
    helper.apply(is_advanced())


def advanced_columns(view) -> list[int]:
    return list(view.property("aurora_advanced_columns") or [])


def mark_advanced_perspective(key: str) -> None:
    """A perspective shown only in Advanced mode. MainWindow hides its rail button and Go-to entry in Easy, falls back
    to Home when it becomes hidden, and answers its Ctrl+number shortcut with a status hint."""
    _ADVANCED_PERSPECTIVES.add(str(key))


def advanced_perspectives() -> set[str]:
    return set(_ADVANCED_PERSPECTIVES)


def perspective_hidden(key: str) -> bool:
    return key in _ADVANCED_PERSPECTIVES and not is_advanced()


def advanced_marked(root: QWidget) -> list[QWidget]:
    """Every widget under `root` marked advanced (used by tests and the self-test)."""
    return [w for w in root.findChildren(QWidget) if w.property("aurora_advanced")]


def easy_marked(root: QWidget) -> list[QWidget]:
    return [w for w in root.findChildren(QWidget) if w.property("aurora_easy")]


# ----------------------------------------------------------------------------- Easy defaults
def easy_value(section: str, key: str):
    """One EASY_DEFAULTS value, with 'setting:<key>' resolved from the Settings."""
    v = EASY_DEFAULTS[section][key]
    if isinstance(v, str) and v.startswith("setting:"):
        from aurora.app.settings_dialog import get
        return get(v.split(":", 1)[1])
    return v


def easy_line(scan, labels: list[str] | None = None) -> str:
    """The holder's main line for a scan: the sgm_report summary mode, else lines.main_line on the stack's labels,
    else the resonant line of the target edge."""
    from aurora.core import lines
    summary = getattr(scan, "summary", None) or {}
    mode = summary.get("mode")
    edge = getattr(scan, "edge", None)
    if labels:
        return lines.main_line(list(labels), edge, mode)
    ml = lines.line_of_mode(mode)
    if ml:
        return ml
    return lines.resonant_line(*edge) if edge else ""


def easy_scan_settings(scan, flags=(), labels: list[str] | None = None):
    """A ScanSettings built from EASY_DEFAULTS for one zone x scan: Settings normalisations and k, TEY and de-spike on,
    SDD1-4 on except the ones flagged saturated, I0 'own' (the good-I0 mean when I0 is flagged invalid), pre / post
    automatic, the holder's main line, and the default signal when the settings carry one."""
    from aurora.core import flags as fl
    from aurora.core.constants import DETS
    from aurora.core.io.config import ScanSettings
    d = EASY_DEFAULTS["zone_scan"]
    flags = list(flags or [])
    sat = fl.saturated_dets(flags)
    try:
        k = float(easy_value("zone_scan", "k"))
    except (TypeError, ValueError):
        k = EASY_DEFAULTS_K_FALLBACK
    st = ScanSettings(stack=getattr(scan, "name", "") or "", line=easy_line(scan, labels),
                      fluo_norm=str(easy_value("zone_scan", "fluo_norm")), tey=bool(d["tey"]),
                      tey_norm=str(easy_value("zone_scan", "tey_norm")), despike=bool(d["despike"]), k=k,
                      i0_source="good" if fl.i0_invalid(flags) else str(d["i0_source"]), pre=d["pre"], post=d["post"])
    st.sdd = [bool(on) and det not in sat for det, on in zip(DETS, d["sdd"])]
    if hasattr(st, "signal"):
        try:
            from aurora.core.lines import default_signal
            st.signal = default_signal(scan, getattr(scan, "summary", None) or {})
        except ImportError:                     # lines.default_signal arrives with the science package
            pass
    return st
