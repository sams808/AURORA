"""The hypercube loader and its maps / spectra / exports on the synthetic project."""
import numpy as np
import pytest

from aurora.core import testdata
from aurora.core.constants import N_CH
from aurora.core.hypercube import Hypercube, export_pymca_4d, export_pymca_xanes, pca_kmeans
from aurora.core.io.raw import read_point_dir
from aurora.core.model import Project
from aurora.core.stack import StackData


def test_hypercube_matches_raw_and_reduced(fake_project):
    proj = Project.discover(fake_project)
    scan = proj.find(testdata.STACK)
    hc = Hypercube.load(proj, scan)
    assert hc.n_energies == 5 and hc.n_pix == 12 and hc.valid.all() and hc.cubes[0].shape == (5, 12, N_CH)
    # cached on the second load (memmap)
    hc2 = Hypercube.load(proj, scan)
    assert isinstance(hc2.cubes[0], np.memmap) and np.array_equal(hc2.cubes[2][3], hc.cubes[2][3])
    # the cube equals the raw file of one energy
    pd = read_point_dir(scan.energy_dirs[2][1])
    assert np.array_equal(hc.cubes[1][2], pd.sdd[1])
    # a window map equals the reduced 'total XRF' ROI of StackData (channels 5..200)
    sd = StackData.load(proj, scan)
    k = sd.labels.index("total XRF")
    m = hc.window_map(None, 5, 200, energy_index=1, per_i0=False)
    assert np.allclose(m, sd.roi_pix_det[1, :, k, :].sum(axis=1))
    xa = hc.xanes(None, 5, 200, pix=[0, 1, 2], per_i0=True)
    ref = sd.roi_pix_det[:, :3, k, :].sum(axis=(1, 2)) / sd.i0[:, :3].sum(axis=1)
    assert np.allclose(xa, ref)
    # summed spectrum peaks near channel 65 + detector index
    sp = hc.spectrum(3)
    assert abs(int(np.argmax(sp)) - 68) <= 1
    assert hc.channel_axis(0)[10] == pytest.approx(100.0)            # no calibration: 10 eV per channel
    assert hc.window_channels(0, 600, 700) == (60, 70)
    rgb = hc.rgb(None, [(60, 70), (70, 80), (5, 200)])
    assert rgb.shape == (12, 3) and rgb.min() >= 0 and rgb.max() <= 1
    M = hc.pixel_matrix(None, 60, 75)
    assert M.shape == (12, 5)
    cr = pca_kmeans(M, hc.E, n_components=2, n_clusters=2)
    assert cr.labels.shape == (12,) and set(cr.labels) <= {0, 1} and cr.centres.shape == (2, 5)


def test_pymca_exports(fake_project, tmp_path):
    import h5py
    proj = Project.discover(fake_project)
    hc = Hypercube.load(proj, proj.find(testdata.STACK))
    p = export_pymca_xanes(hc, tmp_path / "x_PCA-CA.h5", None, 60, 75)
    with h5py.File(p) as h:
        assert h["entry/measurement/average"].shape == (3, 4, 5)        # (y, x, energy) on the 4 x 3 grid
        assert list(h["entry/measurement"].attrs["axes"]) == ["y", "x", "energy"]
        assert h["entry/measurement/energy"].shape == (5,) and "sdd1" in h["entry/measurement"]
    q = export_pymca_4d(hc, tmp_path / "x_Elemental_PyMca.h5")
    with h5py.File(q) as h:
        assert h["entry/full_4d_measurement/sdd_sum_4d"].shape == (3, 4, 5, N_CH)
        assert h["entry/xrf_measurement/sdd_xrf_stack_3d"].shape == (3, 4, N_CH)
        assert h["entry/xanes_measurement/sdd1"].shape == (3, 4, 5) and h["energy"].shape == (5,)


def _beam_loss_cube():
    """21 energies over a step edge at 690 eV, 12 pixels, I0 at 5 % of its median at 695 eV (beam loss); the counts
    follow I0, so / I0 is flat except where the monitor collapses."""
    rng = np.random.default_rng(0)
    E = np.arange(680.0, 701.0, 1.0)
    nE, npx = E.size, 12
    step = 1 + (E >= 690)
    i0 = np.full((nE, npx), 2.5)
    i0[15] *= 0.05
    cubes = []
    for _d in range(4):
        c = np.zeros((nE, npx, N_CH), np.uint16)
        for e in range(nE):
            lam = 200 * step[e] * (i0[e, 0] / 2.5) * np.exp(-0.5 * ((np.arange(N_CH) - 65) / 3.0) ** 2) + 2
            c[e] = rng.poisson(lam, size=(npx, N_CH))
        cubes.append(c)
    return Hypercube(name="t", E=E, x=np.arange(npx) * 0.1, y=np.zeros(npx), cubes=cubes, i0=i0, tey=i0 * 0.2,
                     valid=np.ones(nE, bool)), cubes, i0


def test_maps_leave_out_the_low_i0_energies_like_the_reduce_traces(tmp_path):
    """HANDOFF 5.2 / MANUAL 4.5: an energy whose I0 is below 20 % of its median is left out of every / I0 trace. The Maps
    XANES, the pixel matrix of the clusters and both PyMca exports follow the rule of the Reduce traces (build_traces on
    the same pixels) and of sgm_report (I0 mean over the map below 0.2 x its median: masked)."""
    import h5py
    from aurora.core.io.config import ScanSettings
    from aurora.core.spectra import build_traces
    from aurora.core.zones import ZoneSums
    hc, cubes, i0 = _beam_loss_cube()
    i0m = np.nanmean(i0, axis=1)                                                    # sgm_report v2.5, _finish_reduce
    legacy = (i0m > 0) & (i0m < 0.2 * np.nanmedian(i0m))
    assert np.array_equal(hc.i0_low(), legacy) and np.where(legacy)[0].tolist() == [15]
    w = {d: (60, 71) for d in range(4)}
    det = np.column_stack([cubes[d][:, :, 60:71].sum(axis=(1, 2)) for d in range(4)]).astype(float)
    t = build_traces(ZoneSums(E=hc.E, n_pix=hc.n_pix, i0=i0.sum(axis=1), tey=i0.sum(axis=1) * 0.2, det={"F Kα": det},
                              main="F Kα"), ScanSettings(line="F Kα", fluo_norm="i0", despike=False))
    x = hc.xanes(w, per_i0=True)
    assert np.array_equal(np.isnan(x), t.masked["i0_low"])                         # the same energy as Reduce
    assert np.allclose(x / np.nanmedian(x[hc.E >= 690]), t.sum / np.nanmedian(t.sum[hc.E >= 690]), equal_nan=True)
    assert np.isnan(hc.xanes(w, pix=[0, 3, 7], per_i0=True)[15])                 # a ROI: judged on its own I0
    assert np.isfinite(hc.xanes(w, per_i0=False)).all()                           # raw counts keep every energy
    assert hc.pixel_matrix(w, per_i0=True).shape == (12, 20) and hc.matrix_energies(True).tolist() == [
        e for e in range(21) if e != 15]
    assert hc.pixel_matrix(w, per_i0=False).shape == (12, 21)
    p = export_pymca_xanes(hc, tmp_path / "x_PCA-CA.h5", w, per_i0=True)
    with h5py.File(p) as h:
        assert 695.0 not in h["entry/measurement/energy"][()] and h["entry/measurement/sum"].shape[2] == 20
        assert h["entry/measurement"].attrs["i0_low_ev"].tolist() == [695.0]
    for per_i0, n in ((True, 20), (False, 21)):
        q = export_pymca_4d(hc, tmp_path / f"x{per_i0}_Elemental_PyMca.h5", per_i0)
        with h5py.File(q) as h:
            assert h["energy"].shape == (n,) and (695.0 in h["energy"][()]) is not per_i0
    # the Reduce stack map / I0 (a mean of the per-energy maps) leaves the beam-loss energy out too
    rpd = np.stack([cubes[d][:, :, 60:71].sum(axis=2) for d in range(4)], axis=-1)[:, :, None, :].astype(np.float32)
    sd = StackData(id="d_t", name="t", day="d", E=hc.E, x=hc.x, y=hc.y, i0=i0, tey=i0 * 0.2, roi_pix_det=rpd,
                   valid=np.ones(21, bool), labels=["F Kα"], main="F Kα", edge_ev=690.0)
    a = rpd[:, :, 0, :].sum(axis=2) / i0
    keep = (hc.E >= 690) & ~legacy
    assert np.allclose(sd.map_values("F Kα", mode="above", per_i0=True), a[keep].mean(axis=0))
    assert (a[hc.E >= 690].mean(axis=0) / a[keep].mean(axis=0) - 1).mean() > 0.01     # the dark counts / 5 % I0
    raw = rpd[:, :, 0, :].sum(axis=2)
    assert np.allclose(sd.map_values("F Kα", mode="above", per_i0=False), raw[hc.E >= 690].mean(axis=0))
