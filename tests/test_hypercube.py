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
