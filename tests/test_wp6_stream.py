"""I36: the hypercube is built on disk, not in RAM: streamed one energy at a time into memory maps, uint16 when the
counts allow, the disk space checked first, cancellable without leftovers, and the PyMca exports written per slab.

The references are the AURORA 0.3 loader and exports (in RAM), copied below from commit 3691729."""
import json
import tracemalloc
from pathlib import Path

import numpy as np
import pytest

from aurora.core import hypercube as hcmod
from aurora.core import testdata
from aurora.core.cancel import Cancelled
from aurora.core.constants import DETS, I0_COL, N_CH, TEY_COL
from aurora.core.hypercube import (CacheSpaceError, Hypercube, cube_bytes, export_pymca_4d, export_pymca_xanes,
                                   paint_squares)
from aurora.core.io.h5 import xy
from aurora.core.io.raw import read_point_dir, stack_energy_dirs
from aurora.core.model import Project

N_E, NX, NY = 30, 30, 30                      # 30 energies x 900 pixels
BIG = "120000_bigmap_f_k_stack"
HOT = "130000_hotmap_f_k_stack"


# ----------------------------------------------------------------------------- AURORA 0.3 (reference)
def v03_load(project, scan):
    """The 0.3 loader without its cache: four full (nE, n_pix, 256) uint32 cubes in RAM."""
    x, y = xy(scan.h5)
    edirs = scan.energy_dirs or stack_energy_dirs(scan.path)
    nE, npts = len(edirs), len(x)
    cubes = [np.zeros((nE, npts, N_CH), np.uint32) for _ in DETS]
    i0 = np.full((nE, npts), np.nan); tey = np.full((nE, npts), np.nan)
    valid = np.zeros(nE, bool); skipped = []
    for i, (e, p) in enumerate(edirs):
        try:
            pd = read_point_dir(p)
        except (OSError, ValueError) as err:
            skipped.append((e, str(err))); continue
        if not pd.complete or pd.n_pix < npts:
            skipped.append((e, f"incomplete map ({pd.n_mcc}/{npts} points)")); continue
        for d in range(len(DETS)):
            cubes[d][i] = pd.sdd[d][:npts]
        i0[i] = pd.mcc[:npts, I0_COL]; tey[i] = pd.mcc[:npts, TEY_COL]
        valid[i] = True
    return cubes, i0, tey, valid, skipped


def v03_export_4d(hc, path, per_i0=True, title=""):
    import h5py
    ix, iy, nx, ny = hcmod._snap_grid(hc.x, hc.y, hc.pitch)
    v = np.where(hc.valid)[0]
    with h5py.File(path, "w") as h:
        h.create_dataset("energy", data=hc.E[v])
        ent = h.create_group("entry"); ent.create_dataset("title", data=title or hc.name)
        xrf = ent.create_group("xrf_measurement"); xan = ent.create_group("xanes_measurement"); full = ent.create_group("full_4d_measurement")
        xrf_sum = np.zeros((ny, nx, N_CH), np.float32); xan_sum = np.zeros((ny, nx, v.size), np.float32)
        full_sum = np.zeros((ny, nx, v.size, N_CH), np.float32)
        for d in range(len(DETS)):
            cube4 = np.zeros((ny, nx, v.size, N_CH), np.float32)
            for j, e in enumerate(v):
                block = np.asarray(hc.cubes[d][e]).astype(np.float32)
                if per_i0:
                    with np.errstate(all="ignore"):
                        block = block / np.where(hc.i0[e] > 0, hc.i0[e], np.nan)[:, None]
                        block = np.nan_to_num(block)
                cube4[iy, ix, j, :] = block
            xrf.create_dataset(DETS[d], data=cube4.sum(axis=2))
            xan.create_dataset(DETS[d], data=cube4.sum(axis=3))
            full.create_dataset(DETS[d], data=cube4)
            xrf_sum += cube4.sum(axis=2); xan_sum += cube4.sum(axis=3); full_sum += cube4
        xrf.create_dataset("sdd_xrf_stack_3d", data=xrf_sum)
        xan.create_dataset("sdd_xanes_stack_3d", data=xan_sum)
        full.create_dataset("sdd_sum_4d", data=full_sum)
    return Path(path)


def v03_export_xanes(hc, path, det, lo, hi, per_i0=True):
    import h5py
    ix, iy, nx, ny = hcmod._snap_grid(hc.x, hc.y, hc.pitch)
    v = np.where(hc.valid)[0]
    with h5py.File(path, "w") as h:
        meas = h.create_group("entry").create_group("measurement")
        dets = range(len(DETS)) if det is None else [det]
        total = np.zeros((ny, nx, v.size), np.float32)
        for d in dets:
            arr = np.zeros((ny, nx, v.size), np.float32)
            for j, e in enumerate(v):
                col = np.asarray(hc.cubes[d][e])[:, lo:hi].sum(axis=1, dtype=np.float64)
                if per_i0:
                    with np.errstate(all="ignore"):
                        col = np.where(hc.i0[e] > 0, col / hc.i0[e], 0.0)
                arr[iy, ix, j] = col
            meas.create_dataset(DETS[d], data=arr)
            total += arr
        meas.create_dataset("sum", data=total)
        meas.create_dataset("average", data=total / max(len(list(dets)), 1))
    return Path(path)


def _datasets(path):
    import h5py
    out = {}
    with h5py.File(path, "r") as h:
        h.visititems(lambda name, obj: out.__setitem__(name, obj[()]) if isinstance(obj, h5py.Dataset) else None)
    return out


# ----------------------------------------------------------------------------- synthetic stacks
@pytest.fixture(scope="module")
def big_project(tmp_path_factory):
    """The synthetic project plus a 30-energy x 900-pixel stack (one energy folder incomplete, so one is skipped) and
    a small stack whose counts exceed 65535 at one energy."""
    root = testdata.make_fake_project(tmp_path_factory.mktemp("stream") / "beamtime")
    raw_day = root / "raw" / testdata.DAY
    rng = np.random.default_rng(11)
    x, y = testdata.snake_grid(nx=NX, ny=NY, pitch=0.05, x0=-0.75, y0=4.0)
    energies = tuple(675.0 + 0.5 * i for i in range(N_E))
    testdata._write_stack(raw_day / BIG, "bigmap_f_k", energies, N_E, x, y, rng)
    broken = sorted(p for p in (raw_day / BIG).iterdir() if p.is_dir())[7] / "mcc_flyer_0.csv"
    lines = broken.read_text(encoding="utf-8").splitlines()
    broken.write_text("\n".join(lines[:len(lines) // 2]) + "\n", encoding="utf-8")      # a map cut in half
    hx, hy = testdata.snake_grid(nx=5, ny=4)
    testdata._write_stack(raw_day / HOT, "hotmap_f_k", energies[:6], 6, hx, hy, rng)
    hot = sorted(p for p in (raw_day / HOT).iterdir() if p.is_dir())[3] / "sdd2_0.bin"
    a = np.fromfile(hot, dtype=np.uint32)
    a[5 * N_CH + 66] = 70_000                                                          # one pixel-channel above uint16
    a.tofile(hot)
    return root


def _scan(root, name):
    proj = Project.discover(root)
    return proj, proj.find(name)


def test_the_build_peak_memory_stays_below_a_tenth_of_the_cube(big_project, tmp_path):
    proj, scan = _scan(big_project, BIG)
    assert scan.n_energies == N_E and scan.n_points == NX * NY
    tracemalloc.start()
    try:
        hc = Hypercube.load(proj, scan, cache_root=tmp_path / "cache")
        _cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    size = cube_bytes(N_E, NX * NY)                        # 4 detectors x 256 channels x 4 bytes: 110.6 MB
    print(f"tracemalloc peak {peak / 1e6:.1f} MB for a {size / 1e6:.1f} MB cube ({hc.dtype})")
    assert peak < 0.10 * size
    assert all(isinstance(c, np.memmap) for c in hc.cubes) and hc.dtype == "uint16"


def test_the_streamed_cube_is_identical_to_the_0_3_loader(big_project, tmp_path):
    proj, scan = _scan(big_project, BIG)
    hc = Hypercube.load(proj, scan, cache_root=tmp_path / "cache")
    cubes, i0, tey, valid, skipped = v03_load(proj, scan)
    assert valid.sum() == N_E - 1 and len(skipped) == 1 and "incomplete map" in skipped[0][1]
    for d in range(len(DETS)):
        assert np.array_equal(np.asarray(hc.cubes[d]), cubes[d])          # uint16 cube == uint32 reference
    assert np.array_equal(hc.valid, valid) and [tuple(s) for s in hc.skipped] == [tuple(s) for s in skipped]
    assert np.allclose(hc.i0, i0, equal_nan=True) and np.allclose(hc.tey, tey, equal_nan=True)
    # the derived sums equal sums of the cube
    v = np.where(valid)[0]
    for d in range(len(DETS)):
        assert np.array_equal(hc.xrf_total[d], cubes[d][v].sum(axis=0, dtype=np.uint64))
        assert np.allclose(hc.spec_all[d][v], cubes[d][v].sum(axis=1))
    meta = json.loads((hc.cache_dir / "cube_meta.json").read_text(encoding="utf-8"))
    assert meta["dtype"] == "uint16" and meta["layout"] == hcmod.LAYOUT and meta["key"].startswith("cube|30|900|")
    # the second load opens the cache (memory-mapped) without reading the raw folders
    calls = []
    hc2 = Hypercube.load(proj, scan, progress=lambda *a: calls.append(a), cache_root=tmp_path / "cache")
    assert hc2.cache_dir == hc.cache_dir and calls == [] and np.array_equal(hc2.cubes[3][4], hc.cubes[3][4])


def test_counts_above_uint16_promote_the_cube_to_uint32(big_project, tmp_path):
    proj, scan = _scan(big_project, HOT)
    hc = Hypercube.load(proj, scan, cache_root=tmp_path / "cache")
    cubes, *_ = v03_load(proj, scan)
    assert hc.dtype == "uint32" and hc.cubes[1].dtype == np.uint32 and int(np.max(hc.cubes[1])) == 70_000
    for d in range(len(DETS)):
        assert np.array_equal(np.asarray(hc.cubes[d]), cubes[d])
    assert sorted(p.name for p in hc.cache_dir.iterdir() if p.suffix == ".npy" and p.name.startswith("cube_")) == \
        [f"cube_{d}.npy" for d in DETS]                                  # no .u16 / .u32 files left


def test_cancel_after_three_energies_leaves_nothing(big_project, tmp_path):
    proj, scan = _scan(big_project, BIG)
    seen = []

    def progress(i, n, msg=""):
        seen.append(i)
        if i == 3:
            raise Cancelled()
    cache = tmp_path / "cache"
    with pytest.raises(Cancelled):
        Hypercube.load(proj, scan, progress=progress, cache_root=cache)
    assert seen == [0, 1, 2, 3]
    assert not list(cache.rglob("cube_meta.json")) and not list(cache.rglob(".building-*"))
    assert not list(cache.rglob("*.npy"))


def test_too_little_disk_space_writes_nothing(big_project, tmp_path, monkeypatch):
    proj, scan = _scan(big_project, BIG)
    monkeypatch.setattr(hcmod.shutil, "disk_usage", lambda p: (10 ** 12, 10 ** 12 - 2_000_000_000, 2_000_000_000))
    monkeypatch.setattr(hcmod, "needed_bytes", lambda *a: 4_100_000_000)
    cache = tmp_path / "cache"
    with pytest.raises(CacheSpaceError) as ei:
        Hypercube.load(proj, scan, cache_root=cache)
    assert str(ei.value) == f"needs 4.1 GB in {cache / scan.day / scan.name}, 2.0 GB free"
    assert not cache.exists()
    # the real requirement: the uint32 cube plus its sums
    monkeypatch.undo()
    assert hcmod.needed_bytes(N_E, 900, 2) > cube_bytes(N_E, 900)


def test_a_protected_project_caches_its_cube_per_user(big_project, tmp_path, monkeypatch):
    import os
    proj, scan = _scan(big_project, HOT)
    monkeypatch.setenv("AURORA_PROTECT_DIRS", os.pathsep.join([os.environ.get("AURORA_PROTECT_DIRS", ""), str(big_project)]))
    monkeypatch.setenv(hcmod.CACHE_ENV, str(tmp_path / "user cache"))
    hc = Hypercube.load(proj, scan)
    assert str(hc.cache_dir).startswith(str(tmp_path / "user cache"))
    assert not (big_project / ".aurora" / "cache" / scan.day / scan.name).exists()
    assert Hypercube.cache_exists(proj, scan)
    hc2 = Hypercube.load(proj, scan)                                      # found again there
    assert hc2.cache_dir == hc.cache_dir


def test_a_0_3_cache_is_converted_without_the_raw_data(fake_project, tmp_path):
    proj = Project.discover(fake_project)
    scan = proj.find(testdata.STACK)
    cubes, i0, tey, valid, skipped = v03_load(proj, scan)
    hc_new = Hypercube.load(proj, scan, cache_root=tmp_path / "ref")
    key = json.loads((hc_new.cache_dir / "cube_meta.json").read_text(encoding="utf-8"))["key"]
    cdir = fake_project / ".aurora" / "cache" / scan.day / scan.name
    cdir.mkdir(parents=True)
    for d, c in zip(DETS, cubes):
        np.save(cdir / f"cube_{d}.npy", c)
    np.savez(cdir / "cube_aux.npz", i0=i0, tey=tey, valid=valid)
    (cdir / "cube_meta.json").write_text(json.dumps({"key": key, "skipped": skipped}), encoding="utf-8")
    import aurora.core.hypercube as m
    orig = m.read_point_dir
    m.read_point_dir = lambda p: (_ for _ in ()).throw(AssertionError("raw data read"))
    try:
        hc = Hypercube.load(proj, scan)
    finally:
        m.read_point_dir = orig
    assert hc.cache_dir.parent == cdir and hc.dtype == "uint16"
    for d in range(len(DETS)):
        assert np.array_equal(np.asarray(hc.cubes[d]), cubes[d])
    assert not (cdir / "cube_sdd1.npy").exists() and not (cdir / "cube_meta.json").exists()


def test_bin_sums_follow_the_roi_bins(fake_project):
    (fake_project / "config" / "roi_bins.json").write_text(json.dumps({
        "F Kα": {"sdd1": [60, 74], "sdd2": [61, 75], "sdd3": [62, 76], "sdd4": [63, 77]}}), encoding="utf-8")
    proj = Project.discover(fake_project)
    scan = proj.find(testdata.STACK)
    hc = Hypercube.load(proj, scan)
    assert hc.bin_labels == ["F Kα"] and hc.bin_sums.shape == (1, 4, 5, 12)
    assert np.array_equal(hc.bin_sums[0, 1, 2], np.asarray(hc.cubes[1][2])[:, 61:75].sum(axis=1))
    # new bins: recomputed from the cube once, stored next to it
    (fake_project / "config" / "roi_bins.json").write_text(json.dumps({
        "O Kα": {"sdd1": [46, 58], "sdd2": [46, 58], "sdd3": [46, 58], "sdd4": [46, 58]}}), encoding="utf-8")
    proj = Project.discover(fake_project)
    hc2 = Hypercube.load(proj, proj.find(testdata.STACK))
    assert hc2.bin_labels == ["O Kα"] and np.array_equal(hc2.bin_sums[0, 3, 1], np.asarray(hc2.cubes[3][1])[:, 46:58].sum(axis=1))
    meta = json.loads((hc2.cache_dir / "cube_meta.json").read_text(encoding="utf-8"))
    assert meta["bins"]["labels"] == ["O Kα"] and (hc2.cache_dir / f"bins_{meta['bins']['hash']}.npy").exists()


# ----------------------------------------------------------------------------- exports
def test_export_4d_equals_the_0_3_export(fake_project, tmp_path):
    proj = Project.discover(fake_project)
    hc = Hypercube.load(proj, proj.find(testdata.STACK))
    for per_i0 in (True, False):
        new = _datasets(export_pymca_4d(hc, tmp_path / f"new{per_i0}.h5", per_i0))
        old = _datasets(v03_export_4d(hc, tmp_path / f"old{per_i0}.h5", per_i0))
        for name, ref in old.items():
            assert name in new, name
            if not isinstance(ref, np.ndarray):                           # the title
                assert new[name] == ref, name
                continue
            assert new[name].shape == ref.shape and new[name].dtype == ref.dtype, name
            assert np.array_equal(new[name], ref), name


def test_export_xanes_equals_the_0_3_export_for_one_window(fake_project, tmp_path):
    proj = Project.discover(fake_project)
    hc = Hypercube.load(proj, proj.find(testdata.STACK))
    new = _datasets(export_pymca_xanes(hc, tmp_path / "new.h5", None, 60, 75))
    old = _datasets(v03_export_xanes(hc, tmp_path / "old.h5", None, 60, 75))
    for name in ("entry/measurement/sdd1", "entry/measurement/sdd4", "entry/measurement/sum", "entry/measurement/average"):
        assert np.array_equal(new[name], old[name]), name


def test_exports_are_chunked_per_slab_and_cancellable(big_project, tmp_path):
    import h5py
    proj, scan = _scan(big_project, BIG)
    hc = Hypercube.load(proj, scan, cache_root=tmp_path / "cache")
    out = tmp_path / "x_Elemental_PyMca.h5"
    steps = []
    export_pymca_4d(hc, out, progress=lambda i, n, msg="": steps.append((i, n)))
    assert steps[0] == (0, N_E - 1) and len(steps) == N_E - 1          # one step per valid energy
    with h5py.File(out, "r") as h:
        ds = h["entry/full_4d_measurement/sdd2"]
        assert ds.chunks is not None and ds.chunks[2:] == (1, N_CH) and ds.shape == (NY, NX, N_E - 1, N_CH)
        assert h["entry/xanes_measurement/sdd1"].chunks[2] == 1
    before = out.read_bytes()

    def stop(i, n, msg=""):
        if i == 2:
            raise Cancelled()
    for fn in (lambda: export_pymca_4d(hc, out, progress=stop),
               lambda: export_pymca_xanes(hc, out, hc.ev_windows(640, 720), progress=stop)):
        with pytest.raises(Cancelled):
            fn()
        assert out.read_bytes() == before                                # the earlier file is intact
        assert not list(tmp_path.glob(".x_Elemental_PyMca.h5.*"))       # no temporary file left


def test_paint_squares_select_exactly_their_pixels_on_a_jittered_grid():
    from aurora.core.io.config import Zone
    from aurora.core.zones import members
    rng = np.random.default_rng(3)
    x, y = testdata.snake_grid(nx=25, ny=20, pitch=0.1, x0=-1.0, y0=2.0)
    x = x + rng.normal(0, 0.01, x.size); y = y + rng.normal(0, 0.005, y.size)       # hexapod jitter
    pitch = (0.1, 0.1)
    for frac in (0.1, 0.5, 0.9):
        pix = np.sort(rng.choice(x.size, int(frac * x.size), replace=False))
        z = Zone(id=1, label="c", paint=paint_squares(x, y, pitch, pix))
        assert np.array_equal(members(z, x, y, pitch), pix)
