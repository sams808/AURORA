"""WP5 I30: the Compare library is saved with the project (library.json + library_spectra.npz), configs round-trip
zone traces (sidecar of arrays), John Bussey's keys and moved folders (Locate…)."""
import json
import shutil
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtWidgets import QFileDialog  # noqa: E402

from aurora.core.compare import TraceRequest, ZoneRef, load_config, sidecar_path  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def _window_with_zone(qapp, project_root: Path):
    from aurora.app.main import MainWindow
    from aurora.core import testdata
    from aurora.core.model import Project
    win = MainWindow()
    proj = Project.discover(project_root)
    win.home.set_project(proj)
    ses = win.session
    sid = ses.stack_id(proj.find(testdata.STACK))
    sd = ses.stack(sid)
    ses.touch()
    ses.select_stack(sid)
    x0, x1, y0, y1 = sd.bbox
    z = ses.new_zone("zone one")
    z.rects.append([x0, x1, y0, y1])
    ses.touch()
    ses.select_zone(z.id)
    qapp.processEvents()
    return win, sid


def test_zone_traces_survive_a_restart(qapp, fake_project):
    from aurora.app.main import MainWindow
    from aurora.core.model import Project
    win, sid = _window_with_zone(qapp, fake_project)
    lib = win.library
    assert win.reduce.send_to_compare() == 2
    rows = [r.to_json() for r in lib.requests]
    arrays = {k: (s.E.copy(), s.y.copy()) for k, s in lib.spectra.items()}
    zr = [s.meta.get("zone") for s in lib.spectra.values()]
    assert all(isinstance(z, ZoneRef) and z.sid == sid and z.zone >= 1 for z in zr)
    assert {z.signal for z in zr} == {"fluo", "TEY"} and all(z.holder == "N5" for z in zr)
    assert "09:27" in lib.requests[0].label and "alpha_beta" in lib.requests[0].label      # HH:MM and the scan's short label
    win.session._save_timer.stop()
    win.close()                                                    # the pending library save is written at close
    lib_file = fake_project / ".aurora" / "library.json"
    assert lib_file.is_file() and (fake_project / ".aurora" / "library_spectra.npz").is_file()
    win2 = MainWindow()
    win2.home.set_project(Project.discover(fake_project))
    lib2 = win2.library
    assert [r.to_json() for r in lib2.requests] == rows
    assert set(lib2.spectra) == set(arrays)
    for k, (E, y) in arrays.items():
        assert np.allclose(lib2.spectra[k].E, E) and np.allclose(lib2.spectra[k].y, y, equal_nan=True)
    assert all(isinstance(s.meta.get("zone"), ZoneRef) for s in lib2.spectra.values())
    # nothing is written when nothing changed
    mtime = lib_file.stat().st_mtime_ns
    assert lib2.flush() is False and lib_file.stat().st_mtime_ns == mtime
    win2.session._save_timer.stop()
    win2.close()


def test_config_round_trip_with_the_sidecar(qapp, fake_project, tmp_path):
    from aurora.app.library import Library
    win, _sid = _window_with_zone(qapp, fake_project)
    lib = win.library
    win.reduce.send_to_compare()
    cfg = tmp_path / "configs" / "zone_config.json"
    cfg.parent.mkdir()
    written = lib.save(cfg, {"spacing": 0.4})
    assert written == [cfg, sidecar_path(cfg)] and sidecar_path(cfg).name == "zone_config_spectra.npz"
    before = [nr.y for _r, _s, nr in lib.processed()]
    fresh = Library()
    missing, extra = fresh.load(cfg)
    assert len(fresh.requests) == 2 and len(fresh.spectra) == 2 and not missing and extra["spacing"] == 0.4
    after = [nr.y for _r, _s, nr in fresh.processed()]
    assert all(np.allclose(a, b, equal_nan=True) for a, b in zip(before, after))
    win.session._save_timer.stop()
    win.close()


def test_john_keys_are_read_and_written(tmp_path):
    r = TraceRequest.from_json({"label": "X", "signal": "sdd-scaled", "color": "#123456", "mcr": True})
    assert r.colour == "#123456" and r.fit is True
    d = r.to_json()
    assert d["color"] == d["colour"] == "#123456" and d["mcr"] is d["fit"] is True
    cfg = tmp_path / "john_config.json"
    cfg.write_text(json.dumps({"rows": [{"label": "X", "signal": "sdd-scaled", "color": "#123456", "mcr": True,
                                         "norm": "larch", "pre": [-20, -5], "post": [15, 35], "flatten": True,
                                         "spacing_after": None, "show": True, "my_key": 3}],
                               "sources": {}, "show_diagnostics": False}), encoding="utf-8")
    reqs, _src, extra = load_config(cfg)
    assert reqs[0].colour == "#123456" and reqs[0].fit and extra == {"show_diagnostics": False}
    assert reqs[0].to_json()["my_key"] == 3                        # a key this version does not know is kept


def test_moved_folder_is_found_again_with_locate(qapp, tmp_path, monkeypatch):
    from aurora.app.compare import ComparePerspective
    from aurora.app.library import Library
    from aurora.core.testdata import write_john_reduced
    E = np.linspace(680, 720, 81)
    old = tmp_path / "csv_old"
    for name, e0 in (("A", 694.0), ("B", 698.0)):
        write_john_reduced(old / name / f"{name}_full_reduced.csv", name, E, 1 / (1 + np.exp(-(E - e0))))
    lib = Library()
    lib.add_folder(old)
    n_rows = len(lib.requests)
    cfg = tmp_path / "cfg" / "c.json"
    cfg.parent.mkdir()
    lib.save(cfg)
    d = json.loads(cfg.read_text(encoding="utf-8"))
    assert d["sources_rel"]["A"].endswith("A/A_full_reduced.csv") and Path(d["sources"]["A"]).is_absolute()
    new = tmp_path / "elsewhere" / "csv_new"
    new.parent.mkdir()
    shutil.move(str(old), str(new))
    fresh = Library()
    cmp_ = ComparePerspective(fresh)
    missing, _ = fresh.load(cfg)
    assert len(missing) == 2 and len(fresh.requests) == n_rows and not fresh.processed()
    assert "not found" in cmp_.message.text() and not cmp_.locate_btn.isHidden()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(tmp_path / "elsewhere")))
    cmp_._locate()
    assert not fresh.missing and len(fresh.processed()) == n_rows
    assert all(Path(src).is_relative_to(new) for src in fresh.sources.values())
    cmp_.deleteLater()


def test_relative_path_follows_a_moved_config_and_data(tmp_path):
    from aurora.app.library import Library
    from aurora.core.testdata import write_john_reduced
    E = np.linspace(680, 720, 81)
    root = tmp_path / "share"
    write_john_reduced(root / "data" / "A_full_reduced.csv", "A", E, 1 / (1 + np.exp(-(E - 694))))
    lib = Library()
    lib.add_folder(root / "data")
    lib.save(root / "config.json")
    moved = tmp_path / "share moved"
    shutil.move(str(root), str(moved))
    fresh = Library()
    missing, _ = fresh.load(moved / "config.json")
    assert not missing and fresh.processed()


def test_zone_row_label():
    from aurora.app.library import zone_row_label
    lab = zone_row_label("1 alpha", "N5", "2026-11-05_092758_alpha_beta_f_k_stack")
    assert "09:27" in lab and lab.startswith("1 alpha · N5") and "alpha_beta" in lab


def test_no_library_written_into_a_protected_project(qapp, fake_project, monkeypatch):
    from aurora.app.main import MainWindow
    from aurora.core.model import Project
    monkeypatch.setenv("AURORA_PROTECT_DIRS", str(fake_project))
    win = MainWindow()
    win.home.set_project(Project.discover(fake_project))
    E = np.linspace(680, 720, 41)
    win.library.add_traces("z", E, E * 0 + 1, None, source="zone 1 of 2026-11-05_092758_alpha_beta_f_k_stack")
    assert win.library.flush() is False
    assert not (fake_project / ".aurora" / "library.json").exists()
    win.session._save_timer.stop()
    win.close()
