"""Easy / Advanced mode.

Easy mode (the default on first start) is the basic processing path of John Bussey's SGM XAS GUI: open a beamtime,
draw zones, reduce, check, compare, fit with LCF, build the report. Advanced mode shows every option.

Perspectives mark what is advanced; the registry hides it in Easy mode and shows it again in Advanced mode:

    from aurora.app.mode import mark_advanced, mark_advanced_tab, on_mode_changed, is_advanced
    mark_advanced(self.deconv_box, self.roi_combo)        # widgets (wrap related controls in one container)
    mark_advanced_tab(self.tabs, self.itfa_tab)            # a tab of a QTabWidget
    on_mode_changed(self, lambda adv: ...)                 # anything else (fixed easy defaults, captions)

Do not mark a widget whose visibility is also driven by other logic: put it in a container and mark the container.
"""
from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QObject, Signal, Slot
from PySide6.QtWidgets import QTabWidget, QWidget

SETTING = "advanced_mode"


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


def mark_advanced(*widgets: QWidget) -> None:
    """Hide these widgets in Easy mode."""
    for w in widgets:
        if w is None:
            continue
        w.setProperty("aurora_advanced", True)
        on_mode_changed(w, lambda adv, ww=w: ww.setVisible(adv))


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
    on_mode_changed(tabs, apply)


def mark_advanced_action(owner: QObject, *actions) -> None:
    """Hide menu actions in Easy mode."""
    for a in actions:
        on_mode_changed(owner, lambda adv, aa=a: aa.setVisible(adv))


def advanced_marked(root: QWidget) -> list[QWidget]:
    """Every widget under `root` marked advanced (used by tests and the self-test)."""
    return [w for w in root.findChildren(QWidget) if w.property("aurora_advanced")]
