"""I35: each detector's own emission window in every Maps sum, and SDD ticks for the sum.

The four SDDs have their own gain and offset (HANDOFF 2.3: sdd1 10.058 +3.9; sdd2 9.398 -3.6; sdd3 9.852 -1.3;
sdd4 10.090 +7.9 eV per channel), so 640-710 eV is channels 63-71 on SDD1 but 68-76 on SDD2. AURORA 0.3 converted the
eV window with SDD1's calibration and applied those channels to all four detectors."""
import json

import numpy as np
import pytest

from aurora.core import testdata
from aurora.core.constants import DETS
from aurora.core.hypercube import Hypercube, describe_windows, export_pymca_xanes
from aurora.core.io.config import Calibration
from aurora.core.model import Project

GAINS = {"sdd1": {"gain": 10.058, "offset": 3.9}, "sdd2": {"gain": 9.398, "offset": -3.6},
         "sdd3": {"gain": 9.852, "offset": -1.3}, "sdd4": {"gain": 10.090, "offset": 7.9}}
E_LO, E_HI = 640.0, 710.0


@pytest.fixture
def cube(fake_project):
    proj = Project.discover(fake_project)
    return Hypercube.load(proj, proj.find(testdata.STACK), calibration=Calibration(data=GAINS))


def test_each_sdd_gets_its_own_channels(cube):
    w = cube.ev_windows(E_LO, E_HI)
    assert w[0] == (63, 71) and w[1] == (68, 76)                      # the measured statement of the item
    assert len({w[d] for d in range(4)}) == 4                          # four distinct windows
    assert describe_windows(w).startswith("SDD1 ch 63-70 · SDD2 ch 68-75")
    # every accepted form gives the same windows
    import functools
    assert cube.resolve_windows({DETS[d]: w[d] for d in w}) == w
    assert cube.resolve_windows(functools.partial(cube.window_channels, e_lo=E_LO, e_hi=E_HI)) == w
    assert cube.resolve_windows(None, 60, 70) == {d: (60, 70) for d in range(4)}
    assert cube.resolve_windows(2, 60, 70) == {2: (60, 70)}


@pytest.mark.parametrize("ei", [2, None], ids=["at one energy", "averaged"])
@pytest.mark.parametrize("per_i0", [False, True], ids=["counts", "per I0"])
def test_the_sum_map_is_the_sum_of_the_detectors_own_windows(cube, ei, per_i0):
    w = cube.ev_windows(E_LO, E_HI)
    m = cube.window_map(w, energy_index=ei, per_i0=per_i0)
    singles = sum(cube.window_map(d, *w[d], energy_index=ei) for d in range(4))
    if per_i0:
        i0 = cube.i0[ei] if ei is not None else np.nanmean(cube.i0[cube.valid], axis=0)
        singles = singles / i0
    assert np.allclose(m, singles)
    # AURORA 0.3: SDD1's channels applied to all four detectors
    old = cube.window_map(None, *w[0], energy_index=ei, per_i0=per_i0)
    assert not np.allclose(m, old)


def test_three_ticked_sdds_equal_the_sum_of_their_single_maps(cube):
    w = cube.ev_windows(E_LO, E_HI)
    three = {d: w[d] for d in (0, 1, 3)}
    m = cube.window_map(three, energy_index=1)
    assert np.allclose(m, sum(cube.window_map(d, *w[d], energy_index=1) for d in (0, 1, 3)))
    # the same windows drive the XANES, the pixel matrix and the RGB composite
    xa = cube.xanes(three, per_i0=False)
    assert np.allclose(xa[1], m.sum())
    M = cube.pixel_matrix(three, per_i0=False)
    assert np.allclose(M[:, 1], m)
    rgb = cube.rgb([three, {0: w[0]}, {1: w[1]}], energy_index=1)
    assert rgb.shape == (cube.n_pix, 3) and 0 <= rgb.min() and rgb.max() <= 1


def test_pymca_xanes_stack_carries_four_windows(cube, tmp_path):
    import h5py
    w = cube.ev_windows(E_LO, E_HI)
    p = export_pymca_xanes(cube, tmp_path / "x_PCA-CA.h5", w)
    with h5py.File(p, "r") as h:
        meas = h["entry/measurement"]
        rois = [tuple(int(v) for v in meas[d].attrs["channel_roi"]) for d in DETS]
        assert rois == [w[d] for d in range(4)] and len(set(rois)) == 4
        assert [tuple(meas.attrs[f"channel_roi_{d}"]) for d in DETS] == rois
        assert [s.decode() if isinstance(s, bytes) else s for s in meas.attrs["sdds"]] == list(DETS)
        # each detector's dataset holds its own window's counts / I0
        v = np.where(cube.valid)[0]
        for d in range(4):
            col = cube.window_map(d, *w[d], energy_index=int(v[2]), per_i0=True)
            assert np.allclose(np.sort(meas[DETS[d]][:, :, 2].ravel()), np.sort(col.astype(np.float32)), rtol=1e-6)


# ----------------------------------------------------------------------------- the perspective
@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("pyqtgraph")
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def _window(qapp, root, summary=None):
    from aurora.app.main import MainWindow
    (root / "config" / "sdd_calibration.json").write_text(json.dumps(GAINS), encoding="utf-8")
    proj = Project.discover(root)
    scan = proj.find(testdata.STACK)
    if summary is not None:
        scan.summary = summary
    win = MainWindow()
    win.home.set_project(proj)
    qapp.processEvents()
    hc = Hypercube.load(proj, scan)
    win.maps.set_hypercube(hc)
    win.jobs.wait_idle(10000)
    return win, hc


def test_sdd_ticks_choose_the_sum_and_the_hint_names_the_channels(qapp, fake_project):
    from aurora.core.grid import to_grid
    win, hc = _window(qapp, fake_project)
    mp = win.maps
    assert [b.text() for b in mp.sdd_boxes] == ["SDD1", "SDD2", "SDD3", "SDD4"] and mp.ticked() == (0, 1, 2, 3)
    mp.per_i0.setChecked(False)
    mp.w_lo.setValue(E_LO); mp.w_hi.setValue(E_HI)
    w = hc.ev_windows(E_LO, E_HI)
    assert mp.current_windows() == w and mp.window(1) == (68, 76)
    hint = mp.map_hint.full_text()
    for d in range(4):
        assert f"SDD{d + 1} ch {w[d][0]}-{w[d][1] - 1}" in hint
    ei = mp.energy.value()
    mp.sdd_boxes[2].setChecked(False)                           # SDD1, SDD2 and SDD4
    assert mp.ticked() == (0, 1, 3) and "SDD3" not in mp.map_hint.full_text()
    want = to_grid(hc.x, hc.y, sum(hc.window_map(d, *w[d], energy_index=ei) for d in (0, 1, 3))).img
    assert np.allclose(mp.map.image.image, want, equal_nan=True)
    # the spectrum shows the ticked detectors only, the XANES all four plus the sum of the ticked ones
    assert [c.getData()[0] is not None and len(c.getData()[0]) > 0 for c in mp._spec_curves] == [True, True, False, True]
    names = [it.name() for it in mp.xanes.plotItem.listDataItems()]
    assert names[-1] == "sum of SDD1, SDD2, SDD4" and names[2] == "SDD3 (not summed)"
    # no SDD ticked: actions that need one say why
    for b in mp.sdd_boxes:
        b.setChecked(False)
    assert not mp.cluster_btn.isEnabled() and "tick at least one SDD" in mp.cluster_btn.toolTip()
    assert "tick at least one SDD" in mp.map_hint.full_text()
    win.close()


def test_a_saturated_sdd_starts_unticked(qapp, fake_project):
    win, hc = _window(qapp, fake_project, summary={"sdd_saturated": ["sdd3"]})
    mp = win.maps
    assert [b.isChecked() for b in mp.sdd_boxes] == [True, True, False, True]
    assert mp.sdd_note.isVisibleTo(mp) and "SDD3 flagged saturated (sgm_report)" in mp.sdd_note.text()
    assert "flagged saturated" in mp.sdd_boxes[2].toolTip() and "saturated SDD3" in mp.status.full_text()
    # flagged and off by default, never dropped: one tick brings it back
    mp.sdd_boxes[2].setChecked(True)
    assert mp.ticked() == (0, 1, 2, 3)
    win.close()


def test_a_flag_from_another_source_is_honoured(qapp, fake_project):
    """Flags come from session.flags_of: the summary and every registered source (AURORA's own checks)."""
    from aurora.core.flags import Flag
    from aurora.app.main import MainWindow
    proj = Project.discover(fake_project)
    win = MainWindow()
    win.home.set_project(proj)
    win.session.add_flag_source(lambda scan: [Flag("SDD saturated: SDD1", "sdd_saturated", "AURORA check", ("sdd1",))]
                                if scan.name == testdata.STACK else [])
    win.maps.set_hypercube(Hypercube.load(proj, proj.find(testdata.STACK)))
    assert win.maps.ticked() == (1, 2, 3) and "AURORA check" in win.maps.sdd_note.text()
    win.close()
