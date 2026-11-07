"""The Qt shell, offscreen."""
import pytest

pytest.importorskip("PySide6")

from aurora.core.model import Project  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def test_main_window_and_home(qapp, fake_project):
    from aurora.app.main import MainWindow
    win = MainWindow()
    assert win.stack.count() == 8 and win.rail_buttons["home"].isChecked()
    proj = Project.discover(fake_project)
    win.home.set_project(proj)
    qapp.processEvents()
    tree = win.home.tree
    assert tree.topLevelItemCount() == 1
    day_item = tree.topLevelItem(0)
    assert day_item.childCount() == 1                       # one holder
    holder_item = day_item.child(0)
    assert holder_item.childCount() == 4                    # survey + 3 stacks
    names = sorted(holder_item.child(i).text(0) for i in range(4))
    assert names[0].endswith("_survey")
    tree.setCurrentItem(holder_item.child(1))
    qapp.processEvents()
    assert "Metadata" in win.home.details.toPlainText()
    assert "holders" in win.crumb.text()
    win.show_perspective("reduce")
    assert win.rail_buttons["reduce"].isChecked()
    win.close()


def test_about_dialog(qapp):
    from aurora.app.about import AboutDialog, CREDITS
    d = AboutDialog()
    assert "John Bussey" in CREDITS and "James J. Dynes" in CREDITS and "Sami Soudani" in CREDITS
    assert d.windowTitle().startswith("About")


def test_home_opens_project_in_worker(qapp, fake_project):
    from PySide6.QtCore import QEventLoop, QTimer
    from aurora.app.home import HomePerspective
    home = HomePerspective()
    loop = QEventLoop()
    got = []
    home.project_opened.connect(lambda p: (got.append(p), loop.quit()))
    QTimer.singleShot(10000, loop.quit)
    home.open_project(fake_project)
    loop.exec()
    assert got and len(got[0].scans) == 4
    assert home.tree.topLevelItemCount() == 1
    home.shutdown()
    home.deleteLater()
    qapp.processEvents()


def test_distcheck():
    from aurora.cli import main
    assert main(["--distcheck"]) == 0
