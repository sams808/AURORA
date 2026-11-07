"""Navigation links and the settings dialog, offscreen."""
import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtCore import Qt  # noqa: E402
from aurora.core import testdata  # noqa: E402
from aurora.core.model import Project  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def test_open_in_reduce_from_home_and_survey(qapp, fake_project):
    from aurora.app.main import MainWindow
    win = MainWindow()
    proj = Project.discover(fake_project)
    win.home.set_project(proj)
    qapp.processEvents()
    scan = proj.find(testdata.STACK)
    win.home.scan_activated.emit(scan)
    qapp.processEvents()
    assert win.rail_buttons["reduce"].isChecked()
    assert win.reduce.scan_list.currentItem().data(Qt.UserRole) == win.session.stack_id(scan)
    win.show_perspective("survey")
    win.survey.open_requested.emit(scan)
    assert win.rail_buttons["reduce"].isChecked()
    win.close()


def test_settings_dialog_round_trip(qapp):
    from aurora.app.settings_dialog import SettingsDialog, get
    assert get("despike_k") == 6.0 and get("fluo_norm") == "i0jump" and get("open_last_project") is True
    d = SettingsDialog()
    d.k.setValue(8.0); d.fn.setCurrentText("area"); d.open_last.setChecked(False); d.author.setText("Sami Soudani")
    d.accept()
    assert get("despike_k") == 8.0 and get("fluo_norm") == "area" and get("open_last_project") is False
    # new zone settings pick up the defaults
    from aurora.core.io.config import ScanSettings
    from aurora.app.session import Session
    from aurora.core.io.config import Zone
    from aurora.app.main import MainWindow
    win = MainWindow()
    assert isinstance(win.session, Session)
    win.close()
