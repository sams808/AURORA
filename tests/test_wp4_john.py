"""I27: John Bussey-compatible save (_full_reduced.csv, _absorption.csv, _polygon_roi.json / _zone.json,
_gui_settings.json): his column order and header keys, his numbers, files that load back in Compare."""
import json
import os
from pathlib import Path

import numpy as np
import pytest

from aurora.core import testdata
from aurora.core.compare import load_spectra, read_csv_table
from aurora.core.constants import DETS, N_CH
from aurora.core.export import (ABSORPTION_COLUMNS, JOHN_COLUMNS, john_frame, polygon_mask, roi_polygon, safe_label,
                                write_john, zone_pixels)
from aurora.core.io.config import ScanSettings, Zone
from aurora.core.io.raw import read_point_dir
from aurora.core.model import Project
from aurora.core.stack import StackData

from local_paths import john_gui

# John Bussey's GUI folder (tests/local_paths.py); when absent, a folder that does not exist, so its tests skip
LEGACY = john_gui() or Path(__file__).resolve().parents[1] / "legacy" / "CLS_GUI_JohnBussey"
JOHN_STACK = "070810_BO3_LiSO3F_LR_fluorine_k_stack"
JOHN_LABEL = "BO3_LiSO3F_LR_fluorine_k"
WINDOWS = {"sdd1": [60, 74], "sdd2": [61, 75], "sdd3": [62, 76], "sdd4": [63, 77]}


@pytest.fixture
def fake(fake_project):
    proj = Project.discover(fake_project)
    scan = proj.find(testdata.STACK)
    sd = StackData.load(proj, scan)
    # 2 x 2 pixels: columns x = -0.5, -0.25 and rows y = 5.0, 5.25 of the 4 x 3 grid
    zone = Zone(id=1, label="1 alpha", group="Group 1", rects=[[-0.5, -0.25, 5.0, 5.25]])
    return proj, scan, sd, zone


def _bad_points(path) -> np.ndarray:
    """The bad_point column as booleans (read_csv_table reads 'True' / 'False' as NaN)."""
    lines = [ln for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("#")]
    i = lines[0].split(",").index("bad_point")
    return np.array([ln.split(",")[i] == "True" for ln in lines[1:]])


def _expected_means(scan, pix, windows):
    rows = []
    for e, p in scan.energy_dirs:
        pd = read_point_dir(p)
        rows.append([float(np.mean(pd.sdd[k][pix, w[0]:w[1]].sum(axis=1).astype(float))) for k, w in
                     enumerate(windows[d] for d in DETS)] + [float(np.mean(pd.mcc[pix, 0])), float(np.mean(pd.mcc[pix, 3]))])
    return np.array(rows)


def test_written_files_follow_johns_layout(fake, tmp_path):
    proj, scan, sd, zone = fake
    st = ScanSettings(stack=scan.name, line=sd.main, roi=dict(WINDOWS), despike_mode="coherent")
    sid = f"{scan.day}_{scan.name}"
    paths = write_john(zone, sid, sd, st, project=proj, scan=scan, holder="holderA")
    out = proj.root / "processed" / "reduced" / "1 alpha"
    assert [p.name for p in paths] == ["1 alpha_full_reduced.csv", "1 alpha_absorption.csv", "1 alpha_polygon_roi.json",
                                       "1 alpha_gui_settings.json"] and all(p.parent == out for p in paths)
    head, cols, data = read_csv_table(paths[0])
    assert cols == JOHN_COLUMNS and len(data) == len(scan.energy_dirs)
    keys = [h[2:].split(":", 1)[0] for h in head]
    john_keys = ["sample_label", "source_h5", "survey_h5", "scan_name", "sdd_roi_channels", "use_sdd_calibration",
                 "mcc_i0_channel", "mcc_tey_channel", "spatial_reduction", "roi_points", "bad_point_reference", "spike_z",
                 "processing", "reduced_with"]
    assert keys[:len(john_keys)] == john_keys
    assert "# roi_points: 4 / 12" in head and any(h.startswith("# reduced_with: AURORA ") for h in head)
    assert f"# sdd_roi_channels: {json.dumps(WINDOWS)}" in head and "# spatial_reduction: mean" in head
    pix = zone_pixels(zone, sd)
    assert len(pix) == 4
    exp = _expected_means(scan, pix, WINDOWS)
    c = {n: data[:, i] for i, n in enumerate(cols)}
    assert not _bad_points(paths[0]).any()                                # nothing flagged on the synthetic stack
    for k, d in enumerate(DETS):
        assert np.array_equal(c[d], exp[:, k]), d                         # the mean of the window channels, every energy
        assert (c[f"{d}_roi_lo"] == WINDOWS[d][0]).all() and (c[f"{d}_roi_hi"] == WINDOWS[d][1]).all()
    assert np.array_equal(c["I0"], exp[:, 4]) and np.array_equal(c["TEY"], exp[:, 5])
    assert np.allclose(c["sdd_total"], exp[:, :4].sum(axis=1), rtol=1e-12) and np.allclose(c["sdd_mean"], exp[:, :4].mean(axis=1))
    h2, cols2, data2 = read_csv_table(paths[1])
    assert cols2 == ABSORPTION_COLUMNS and h2 == head and np.array_equal(data2[:, 1], c["sdd_total"])
    # the rectangle as his polygon, widened by AURORA's tolerance: his strict test takes the same 4 pixels
    poly = json.loads(paths[2].read_text(encoding="utf-8"))
    assert len(poly) == 4 and sorted(np.where(polygon_mask(poly, sd.x, sd.y))[0]) == sorted(pix)
    gui = json.loads(paths[3].read_text(encoding="utf-8"))
    assert list(gui)[:7] == ["rois", "params", "stack_h5", "survey_h5", "spike_z", "drop_energy_ranges", "drop_indices"]
    assert gui["rois"] == WINDOWS and gui["params"] == {"i0": "ch1", "mode": "mean", "tey": "ch4"}
    assert gui["aurora"]["roi_points"] == [4, 12] and gui["aurora"]["settings"]["dsm"] == "coherent"
    # spike_z is John's robust-z threshold of his 'Detect spikes' (None when off): AURORA never runs that detector,
    # so his key says None and AURORA's de-spike (a MAD multiple) has keys of its own
    assert gui["spike_z"] is None and "# spike_z: None" in head
    assert gui["aurora"]["despike_k"] == 6.0 and "# despike: coherent, k = 6 (no spikes found)" in head
    # the files load back in Compare as sdd-scaled, sdd_total and TEY
    assert {s.signal for s in load_spectra(paths[0])} == {"sdd-scaled", "sdd_total", "TEY"}
    assert load_spectra(paths[0])[0].label == "1 alpha"
    # never overwritten silently, never into raw/ or a scan folder
    with pytest.raises(FileExistsError):
        write_john(zone, sid, sd, st, project=proj, scan=scan)
    assert write_john(zone, sid, sd, st, project=proj, scan=scan, overwrite=True) == paths
    with pytest.raises(PermissionError):
        write_john(zone, sid, sd, st, proj.raw / testdata.DAY / "x", "1 alpha", project=proj, scan=scan)
    with pytest.raises(PermissionError):
        write_john(zone, sid, sd, st, scan.path / "out", "1 alpha", project=proj, scan=scan)


def test_fixed_bins_sum_mode_and_other_zones(fake, tmp_path):
    proj, scan, sd, zone = fake
    sid = f"{scan.day}_{scan.name}"
    st = ScanSettings(stack=scan.name, line="total XRF", despike=False)
    fr = john_frame(zone, sid, sd, st, project=proj, scan=scan)            # the stack's fixed bins, no raw read
    pix = zone_pixels(zone, sd)
    k = sd.roi_index("total XRF")
    assert fr.windows == {d: (5, 200) for d in DETS} and "fixed bins" in fr.source
    assert np.allclose(fr.det, sd.roi_pix_det[:, pix, k, :].astype(float).mean(axis=1), rtol=1e-12)
    frs = john_frame(zone, sid, sd, st, project=proj, scan=scan, mode="sum")
    assert np.allclose(frs.det, fr.det * 4, rtol=1e-12) and np.allclose(frs.i0, fr.i0 * 4)
    # a zone that is not one polygon or one rectangle: _zone.json, which says so
    z2 = Zone(id=2, label="2 beta / mixed", rects=[[-0.5, -0.25, 5.0, 5.25]], paint=[[0.25, 5.5, 0.25, 0.25]])
    assert roi_polygon(z2) is None and safe_label(z2.label) == "2 beta _ mixed"
    paths = write_john(z2, sid, sd, st, tmp_path / "out", z2.label, project=proj, scan=scan)
    zj = json.loads(paths[2].read_text(encoding="utf-8"))
    assert paths[2].name == "2 beta _ mixed_zone.json" and "not one polygon" in zj["_comment"] and zj["zone"]["paint"]
    assert "# roi_points: 5 / 12" in read_csv_table(paths[0])[0]
    # a polygon zone: its vertices are his polygon, strict membership
    z3 = Zone(id=3, label="tri", polys=[[[-0.6, 4.9], [0.1, 4.9], [-0.6, 5.6]]])
    assert roi_polygon(z3) == [[-0.6, 4.9], [0.1, 4.9], [-0.6, 5.6]] and len(zone_pixels(z3, sd)) > 0
    # the pixels saved are the zone's members (Reduce's own rule): an erase rectangle inside a polygon stays erased
    from aurora.core.zones import members
    z4 = Zone(id=4, label="erased", polys=[[[-0.7, 4.8], [0.3, 4.8], [0.3, 5.7], [-0.7, 5.7]]],
              erects=[[-0.5, -0.5, 5.0, 5.0]])
    pix4 = zone_pixels(z4, sd)
    assert np.array_equal(pix4, members(z4, sd.x, sd.y, sd.pitch))
    whole = zone_pixels(Zone(id=5, label="whole", polys=z4.polys), sd)
    assert len(pix4) == len(whole) - 1


def test_masked_and_incomplete_energies_are_flagged(fake, tmp_path):
    proj, scan, sd, zone = fake
    sid = f"{scan.day}_{scan.name}"
    sd2 = StackData(**{**sd.__dict__})
    sd2.i0 = sd.i0.copy(); sd2.roi_pix_det = sd.roi_pix_det.copy()
    sd2.i0[2] *= 0.05                                                     # beam loss at one energy
    sd2.roi_pix_det[3, :, :, 1] = np.nan                                  # SDD2 missing at another
    st = ScanSettings(stack=scan.name, line="total XRF", despike=False)
    fr = john_frame(zone, sid, sd2, st, project=proj, scan=scan)
    assert fr.low_ev == [float(sd.E[2])] and np.isnan(fr.i0[2]) and fr.bad[2] and np.isfinite(fr.det[2]).all()
    assert fr.incomplete_ev == [float(sd.E[3])] and np.isnan(fr.total[3]) and fr.bad[3] and np.isnan(fr.det[3, 1])
    paths = write_john(zone, sid, sd2, st, tmp_path / "o", "x", project=proj, scan=scan)
    head = read_csv_table(paths[0])[0]
    assert any(h.startswith("# i0_masked: 686.00 eV") for h in head) and any(h.startswith("# incomplete: 689.00 eV") for h in head)


def test_sdd_total_is_the_sum_of_the_written_detector_columns(fake, monkeypatch):
    """John's sdd_total is sdd1 + sdd2 + sdd3 + sdd4 on every row. A spike that only the coherent rule's sum test finds
    is interpolated in the detector columns it is the sum of, so the row still adds up (and is bad)."""
    from aurora.core import export
    proj, scan, sd, zone = fake
    sid = f"{scan.day}_{scan.name}"
    real = export.build_traces

    def with_a_sum_spike(sums, st, *a, **k):
        t = real(sums, st, *a, **k)
        t.spikes["sum"] = [2]                     # what coherent_despike reports for an outlier of the sum alone
        return t

    monkeypatch.setattr(export, "build_traces", with_a_sum_spike)
    st = ScanSettings(stack=scan.name, line="total XRF", despike_mode="coherent")
    fr = john_frame(zone, sid, sd, st, project=proj, scan=scan)
    assert fr.bad[2]
    assert np.allclose(fr.total, fr.det.sum(axis=1), rtol=1e-12) and np.allclose(fr.mean, fr.total / 4, rtol=1e-12)
    # interpolated between its neighbours in every ticked detector, the others untouched
    expect = 0.5 * (fr.det_measured[1] + fr.det_measured[3])
    assert np.allclose(fr.det[2], expect, rtol=1e-12) and np.array_equal(fr.det[[0, 1, 3, 4]], fr.det_measured[[0, 1, 3, 4]])
    st2 = ScanSettings(stack=scan.name, line="total XRF", despike_mode="coherent", sdd=[True, False, True, True])
    fr2 = john_frame(zone, sid, sd, st2, project=proj, scan=scan)
    assert fr2.det[2, 1] == fr2.det_measured[2, 1]                        # SDD2 is not in the sum
    assert np.allclose(fr2.total, fr2.det[:, [0, 2, 3]].sum(axis=1), rtol=1e-12)


def test_fixed_bins_keep_the_energies_the_stack_left_out(fake_project):
    """sgm_report's reduction leaves an incomplete map out entirely (I0 and TEY too); John Bussey's GUI reads each
    detector file on its own and keeps what was measured. The John file on the fixed bins (the Easy default) reads
    such an energy from its raw folder, by his rule; without the raw folders the energy is empty, bad and named."""
    proj = Project.discover(fake_project)
    scan = proj.find(testdata.STACK)
    e2, p2 = scan.energy_dirs[2]
    f = Path(p2) / "sdd2_0.bin"
    np.fromfile(f, dtype=np.uint32)[:6 * N_CH].tofile(f)                  # SDD2 stopped after 6 of 12 pixels
    sd = StackData.load(proj, scan)
    assert not sd.valid[2] and np.isnan(sd.i0[2]).all()                   # the stack left the energy out
    zone = Zone(id=1, label="1 alpha", rects=[[-0.5, -0.25, 5.0, 5.25]])
    sid = f"{scan.day}_{scan.name}"
    st = ScanSettings(stack=scan.name, line="total XRF", despike=False)          # the total XRF bins: 5:200
    fr = john_frame(zone, sid, sd, st, project=proj, scan=scan)
    assert "fixed bins" in fr.source
    pix = zone_pixels(zone, sd)
    mcc = np.loadtxt(Path(p2) / "mcc_flyer_0.csv", delimiter=",", ndmin=2)
    w = {d: (5, 200) for d in DETS}
    for k, d in enumerate(DETS):
        a = np.fromfile(Path(p2) / f"{d}_0.bin", dtype=np.uint32)
        a = a.reshape(-1, N_CH)
        have = pix[pix < len(a)]
        assert fr.det[2, k] == float(np.mean(a[have, w[d][0]:w[d][1]].sum(axis=1).astype(float))), d
    assert fr.i0[2] == float(np.mean(mcc[pix, 0])) and fr.tey[2] == float(np.mean(mcc[pix, 3]))
    assert np.isfinite(fr.total[2]) and not fr.bad[2] and fr.incomplete_ev == []
    assert "1 energy the stack left out read from the raw folders" in fr.source
    # every other energy is the stack's own (fixed bins), unchanged
    other = [i for i in range(len(sd.E)) if i != 2]
    k = sd.roi_index("total XRF")
    assert np.array_equal(fr.det[other], np.nanmean(sd.roi_pix_det[:, pix, k, :].astype(float), axis=1)[other])
    # no raw folders: the energy stays empty, and is bad and named in the header
    fr2 = john_frame(zone, sid, sd, st, project=proj, scan=None)
    assert np.isnan(fr2.det[2]).all() and np.isnan(fr2.i0[2]) and fr2.bad[2] and fr2.incomplete_ev == [float(e2)]


@pytest.mark.skipif(not (LEGACY / JOHN_STACK).is_dir(), reason="John Bussey's GUI fixtures (legacy/, untracked) absent")
def test_reproduces_johns_bo3_file(tmp_path, monkeypatch):
    """His BO3 stack, polygon and settings: our _full_reduced.csv has his columns, 311 rows, and his numbers. The
    fixture is read in place (a fresh copy of its 1 868 files costs 30 s of antivirus scanning on Windows) and is
    write-protected for the test through AURORA_PROTECT_DIRS; the project root, the cache and the output are tmp."""
    from aurora.core.io.safety import ENV
    monkeypatch.setenv(ENV, os.pathsep.join(filter(None, [os.environ.get(ENV, ""), str(LEGACY)])))
    from aurora.core.model import ScanInfo
    jd = LEGACY / "gui_outputs" / "reduced" / JOHN_LABEL
    work = tmp_path / "CLS GUI"
    work.mkdir()
    for name in (f"{JOHN_LABEL}_polygon_roi.json", f"{JOHN_LABEL}_gui_settings.json"):
        (work / name).write_bytes((jd / name).read_bytes())
    poly = json.loads((work / f"{JOHN_LABEL}_polygon_roi.json").read_text(encoding="utf-8"))
    gui = json.loads((work / f"{JOHN_LABEL}_gui_settings.json").read_text(encoding="utf-8"))
    scan = ScanInfo.from_dir(LEGACY / JOHN_STACK, day="2026-05-15")
    proj = Project(root=work, raw=work)
    sd = StackData.load(proj, scan)
    assert sd.n_pix == 100 and not (LEGACY / ".aurora").exists()
    st = ScanSettings(stack=scan.name, line=sd.main, roi={d: list(v) for d, v in gui["rois"].items()},
                      k=float(gui["spike_z"]), despike_mode="coherent")
    zone = Zone(id=1, label=JOHN_LABEL, polys=[poly])
    paths = write_john(zone, f"2026-05-15_{scan.name}", sd, st, work / "out", JOHN_LABEL, project=proj, scan=scan,
                       mode=gui["params"]["mode"])
    h1, c1, d1 = read_csv_table(paths[0])
    h2, c2, d2 = read_csv_table(jd / f"{JOHN_LABEL}_full_reduced.csv")
    assert c1 == c2 == JOHN_COLUMNS and len(d1) == len(d2) == 311
    assert "# roi_points: 8 / 100" in h1 and "# roi_points: 8 / 100" in h2
    for name in ("energy", *DETS, "I0", "TEY"):
        a, b = d1[:, c1.index(name)], d2[:, c2.index(name)]
        assert np.allclose(a, b, rtol=1e-12, atol=0, equal_nan=True), (name, np.where(~np.isclose(a, b, rtol=1e-12, equal_nan=True)))
    # at 704.6 eV his SDD2 file is empty: he sums the three others into sdd_total, AURORA leaves it empty and says so
    i = int(np.where(d1[:, 0] == 704.6)[0][0])
    assert np.isnan(d1[i, c1.index("sdd2")]) and np.isnan(d1[i, c1.index("sdd_total")]) and _bad_points(paths[0])[i]
    assert _bad_points(paths[0]).sum() == 1
    assert any(h.startswith("# incomplete: 704.60 eV") for h in h1)
    assert {s.signal for s in load_spectra(paths[0])} == {"sdd-scaled", "sdd_total", "TEY"}
    assert not (LEGACY / JOHN_STACK / ".aurora").exists()
