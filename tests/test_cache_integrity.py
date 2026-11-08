"""Reduced-stack caches checked against the ROI bins and the energy folders; damaged caches rebuilt from raw (I49)."""
import json
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from aurora.core import testdata
from aurora.core.io.cache import CACHE_NAME, ReducedCache, windows_from_signature
from aurora.core.model import Project
from aurora.core.stack import StackData, project_windows


def _demo(tmp_path: Path) -> Path:
    from aurora.cli import cmd_demo
    root = tmp_path / "demo beamtime"
    cmd_demo(root)
    return root


def _load(root: Path, **kw):
    proj = Project.discover(root)                       # discovery re-reads config/roi_bins.json
    msgs: list[str] = []
    sd = StackData.load(proj, proj.find(testdata.STACK), progress=lambda i, n, m: msgs.append(m), **kw)
    return proj, sd, msgs


def _aurora_cache(proj: Project) -> Path:
    scan = proj.find(testdata.STACK)
    return proj.root / ".aurora" / "cache" / scan.day / scan.name / CACHE_NAME


def _edit_bins(root: Path, fn) -> None:
    p = root / "config" / "roi_bins.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    fn(d)
    p.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")


def _fresh(root: Path) -> StackData:
    """A reduction from raw with the current bins, ignoring and then restoring every cache."""
    proj = Project.discover(root)
    cache = _aurora_cache(proj)
    keep = cache.read_bytes() if cache.exists() else None
    if keep is not None:
        cache.unlink()
    try:
        sd = StackData.load(proj, proj.find(testdata.STACK))
        assert sd.source == "reduced from raw"
        return sd
    finally:
        if keep is not None:
            cache.write_bytes(keep)


def _f_sums(sd: StackData) -> np.ndarray:
    return sd.roi_pix_det[:, :, sd.labels.index("F Kα"), :].sum(axis=2, dtype=float)


def test_edited_roi_bins_re_reduce_and_say_why(tmp_path):
    root = _demo(tmp_path)
    _proj, sd0, _ = _load(root)
    assert sd0.source == "reduced from raw" and "Na Kα" not in sd0.labels
    _proj, sd1, _ = _load(root)
    assert sd1.source.startswith("aurora cache") and not sd1.notes[:1] == ["ROI bins changed since this cache: re-reducing"]
    old_f = _f_sums(sd1)

    def edit(d):                     # a narrower F Kα window and a new line, as a user editing roi_bins.json would
        d["F Kα"] = {"sdd1": [64, 70], "sdd2": [65, 71], "sdd3": [66, 72], "sdd4": [67, 73], "line_ev": d["F Kα"]["line_ev"]}
        d["Na Kα"] = {"sdd1": [100, 108], "sdd2": [100, 108], "sdd3": [100, 108], "sdd4": [100, 108]}
    _edit_bins(root, edit)

    _proj, sd2, msgs = _load(root)
    assert sd2.source == "reduced from raw"
    assert sd2.notes[0] == "ROI bins changed since this cache: re-reducing"
    assert msgs and all(m.startswith("ROI bins changed since this cache · ") for m in msgs), msgs[:2]
    assert "Na Kα" in sd2.labels and sd2.labels[-1] == "total XRF"
    fresh = _fresh(root)
    assert fresh.labels == sd2.labels
    np.testing.assert_allclose(_f_sums(sd2), _f_sums(fresh), rtol=1e-6, equal_nan=True)   # float32 precision
    assert not np.allclose(_f_sums(sd2), old_f, equal_nan=True)     # the old cache's F Kα was a different window

    _proj, sd3, msgs3 = _load(root)                                # the rewritten cache now matches the bins
    assert sd3.source.startswith("aurora cache") and "Na Kα" in sd3.labels and not msgs3
    np.testing.assert_allclose(_f_sums(sd3), _f_sums(fresh), rtol=1e-6, equal_nan=True)


def test_changed_energy_folders_re_reduce(tmp_path):
    root = _demo(tmp_path)
    proj, _sd, _ = _load(root)
    _load(root)
    scan = proj.find(testdata.STACK)
    last = scan.energy_dirs[-1][1]
    st = last.stat()
    os.utime(last, (st.st_atime, st.st_mtime + 120))               # a folder rewritten after the cache was made
    _proj, sd, _ = _load(root)
    assert sd.source == "reduced from raw"
    assert sd.notes[0] == "energy folders changed since this cache: re-reducing"


def _truncate_member(npz: Path, member: str) -> None:
    with zipfile.ZipFile(npz) as z:
        data = {n: z.read(n) for n in z.namelist()}
    assert member in data, list(data)
    data[member] = data[member][: len(data[member]) // 2]
    with zipfile.ZipFile(npz, "w") as z:
        for n, b in data.items():
            z.writestr(n, b)


def test_a_truncated_member_is_set_aside_and_the_stack_reduced_from_raw(tmp_path):
    root = _demo(tmp_path)
    proj, sd0, _ = _load(root)
    cache = _aurora_cache(proj)
    _truncate_member(cache, "roi_pix_det.npy")
    _proj, sd, _ = _load(root)
    assert sd.source == "reduced from raw"
    bad = list(cache.parent.glob("*.bad"))
    assert bad and bad[0].name == CACHE_NAME + ".bad"
    assert sd.notes[0].startswith("damaged cache (") and "renamed to _reduced_cache.npz.bad" in sd.notes[0]
    np.testing.assert_allclose(sd.roi_pix_det, sd0.roi_pix_det, rtol=1e-6, equal_nan=True)
    _proj, sd2, _ = _load(root)                                    # a good cache was written again
    assert sd2.source.startswith("aurora cache")


def test_garbage_and_protected_caches(tmp_path, monkeypatch):
    root = _demo(tmp_path)
    proj, _sd, _ = _load(root)
    cache = _aurora_cache(proj)
    cache.write_bytes(b"\x00" * 64)                                # not even a zip file
    _proj, sd, _ = _load(root)
    assert sd.source == "reduced from raw" and (cache.parent / (CACHE_NAME + ".bad")).exists()
    # in a protected folder the damaged file is left alone: nothing is renamed, the stack still opens
    (cache.parent / (CACHE_NAME + ".bad")).unlink()
    cache.write_bytes(b"PK\x03\x04 truncated")
    rc, problem = ReducedCache.open(cache.parent, allow_pickle=False)
    assert rc is None and "renamed" in problem
    cache.with_name(CACHE_NAME + ".bad").replace(cache)
    monkeypatch.setenv("AURORA_PROTECT_DIRS", str(cache.parent))
    rc, problem = ReducedCache.open(cache.parent, allow_pickle=False)
    assert rc is None and "left in place" in problem and cache.exists()
    assert not (cache.parent / (CACHE_NAME + ".bad")).exists()


def test_aurora_caches_need_no_pickle(tmp_path):
    root = _demo(tmp_path)
    proj, _sd, _ = _load(root)
    cache = _aurora_cache(proj)
    with np.load(cache, allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}
    assert arrays["roi_labels"].dtype.kind == "U" and "F Kα" in arrays["roi_labels"].tolist()
    assert arrays["notes"].dtype.kind == "U" and arrays["skipped"].dtype.kind == "U"
    assert str(arrays["signature"]).startswith("aurora2|")
    rc = ReducedCache.load(cache.parent, allow_pickle=False)
    assert rc is not None and rc.fmt == "aurora" and rc.windows == windows_from_signature(rc.signature)


def test_a_cache_of_aurora_0_3_is_replaced_without_a_bad_file(tmp_path):
    """0.3 wrote pickled labels and an 'aurora|' signature: re-reduced and overwritten, never called damaged."""
    root = _demo(tmp_path)
    proj, sd, _ = _load(root)
    cache = _aurora_cache(proj)
    with np.load(cache, allow_pickle=False) as z:
        a = {k: z[k] for k in z.files}
    np.savez(cache, signature=np.array("aurora|4|0|[]"), i0=a["i0"], tey=a["tey"], roi_pix_det=a["roi_pix_det"],
             roi_det=a["roi_det"], valid=a["valid"], roi_labels=np.array(sd.labels, dtype=object),
             skipped=np.array([], dtype=object), notes=np.array([], dtype=object))
    _proj, sd2, _ = _load(root)
    assert sd2.source == "reduced from raw" and sd2.notes[0] == "cache from an earlier AURORA version: re-reducing"
    assert not list(cache.parent.glob("*.bad"))
    with np.load(cache, allow_pickle=False) as z:
        assert str(z["signature"]).startswith("aurora2|")


def _sgm_report_cache(proj: Project, sd: StackData, windows: dict, with_labels: bool = True) -> Path:
    """A cache in sgm_report v2.5's format: pickled object arrays, 'v2.5|nE|mtime|{json}' signature."""
    from aurora.core.io.raw import stack_last_mtime
    scan = proj.find(testdata.STACK)
    d = proj.processed / scan.day / scan.name
    d.mkdir(parents=True, exist_ok=True)
    labels = list(windows)
    k = [sd.labels.index(l) for l in labels]
    rpd = sd.roi_pix_det[:, :, k, :]
    sig = f"v2.5|{len(scan.energy_dirs)}|{stack_last_mtime(scan.energy_dirs):.0f}|{json.dumps(windows, sort_keys=True)}"
    extra = {"roi_labels": np.array(labels, dtype=object)} if with_labels else {}
    np.savez(d / CACHE_NAME, signature=sig, i0=sd.i0, tey=sd.tey, roi_pix_det=rpd, roi_det=np.nansum(rpd, axis=1),
             valid=sd.valid, skipped=np.array([(686.0, "incomplete map (3/12 points)")], dtype=object),
             notes=np.array(["the F Kα window also collects O Kα (within ±1.5σ, unresolved by the SDD)"], dtype=object),
             **extra)
    return d / CACHE_NAME


def test_sgm_report_caches_still_load_and_other_windows_are_reported(tmp_path):
    root = _demo(tmp_path)
    proj, sd, _ = _load(root)
    _labels, win = project_windows(proj.roi_bins)
    same = {"F Kα": {d: list(b) for d, b in win["F Kα"].items()}, "total XRF": {d: list(b) for d, b in win["total XRF"].items()}}
    _sgm_report_cache(proj, sd, same)
    _proj, rep, _ = _load(root)
    assert rep.source.startswith("sgm_report cache ") and "other windows" not in rep.source
    assert rep.labels == ["F Kα", "total XRF"] and rep.window_note == "" and rep.from_report_cache
    assert "the F Kα window also collects O Kα (within ±1.5σ, unresolved by the SDD)" in rep.notes
    rc = ReducedCache.load(proj.processed / proj.find(testdata.STACK).day / testdata.STACK)
    assert rc.fmt == "sgm_report" and rc.skipped == [(686.0, "incomplete map (3/12 points)")]

    other = json.loads(json.dumps(same))
    other["F Kα"]["sdd2"] = [win["F Kα"]["sdd2"][0] + 3, win["F Kα"]["sdd2"][1] + 3]
    _sgm_report_cache(proj, sd, other)
    _proj, rep2, _ = _load(root)
    # AURORA's own cache (made by the first load) matches the project bins: it wins over the cache with other windows
    assert rep2.source.startswith("aurora cache") and rep2.window_note == ""
    _aurora_cache(proj).unlink()                     # the usual case: only sgm_report's cache exists
    _proj, rep2, _ = _load(root)
    assert rep2.source.startswith("sgm_report cache with other windows ") and rep2.from_report_cache
    assert "F Kα (sdd2" in rep2.window_note and rep2.notes[0] == rep2.window_note
    # the re-reduction offered: the project's bins, every line of roi_bins.json
    _proj, red, _ = _load(root, use_report_cache=False)
    assert not red.from_report_cache and red.labels == project_windows(proj.roi_bins)[0]
    assert red.window_note == "" and red.source == "reduced from raw"
    # ... and it sticks: the next ordinary load takes AURORA's cache, not the sgm_report one with other windows
    _proj, again, msgs = _load(root)
    assert again.source.startswith("aurora cache") and again.labels == red.labels and not msgs


def test_window_differences_wording():
    rc = SimpleNamespace(windows={"F Kα": {"sdd1": (60, 74), "sdd2": (65, 80)}, "Xx Kα": {"sdd1": (1, 2)},
                                  "total XRF": {"sdd1": (5, 200)}})
    want = {"F Kα": {"sdd1": (60, 74), "sdd2": (66, 80)}, "total XRF": {"sdd1": (4, 200)}}
    out = ReducedCache.window_differences(rc, want)
    assert out == ["F Kα (sdd2 65-79 ch, roi_bins.json 66-79 ch)", "Xx Kα (not in roi_bins.json)"]
    assert ReducedCache.window_differences(SimpleNamespace(windows=None), want) == []


def test_real_sgm_report_caches_agree_with_the_project_bins(real_project):
    """Every sgm_report cache of the real beamtime (read-only) uses roi_bins.json's windows for the lines it holds:
    none of them is announced as 'other windows'."""
    proj = Project.discover(real_project)
    _labels, win = project_windows(proj.roi_bins)
    files = sorted(proj.processed.glob("*/*/" + CACHE_NAME))
    assert files
    for f in files:
        with np.load(f, allow_pickle=True) as z:
            sig = str(z["signature"])
        w = windows_from_signature(sig)
        assert w is not None, f
        assert ReducedCache.window_differences(SimpleNamespace(windows=w), win) == [], f


# ----------------------------------------------------------------------------- the offer in the window
@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("pyqtgraph")
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def test_file_menu_offers_the_re_reduction(qapp, tmp_path):
    from aurora.app import mode, stack_cache
    from aurora.app.main import MainWindow
    root = _demo(tmp_path)
    proj, sd, _ = _load(root)
    _labels, win_ = project_windows(proj.roi_bins)
    other = {"F Kα": {d: [b[0] + 2, b[1] + 2] for d, b in win_["F Kα"].items()},
             "total XRF": {d: list(b) for d, b in win_["total XRF"].items()}}
    _sgm_report_cache(proj, sd, other)
    _aurora_cache(proj).unlink()                     # only sgm_report's cache, with other windows
    mode.set_advanced(True)
    win = MainWindow()
    offer = stack_cache.install(win)
    assert stack_cache.install(win) is offer                       # once per window
    act = offer.action
    assert act in win.menus["file"].actions() and act.text() == stack_cache.TEXT
    assert not act.isEnabled() and "open a stack in Reduce first" in act.toolTip()
    mode.set_advanced(False)
    assert not act.isVisible()                                     # an Advanced entry
    mode.set_advanced(True)
    assert act.isVisible()
    win.home.set_project(Project.discover(root))                   # Reduce reads the holder's first stack
    assert win.jobs.wait_idle(30000)
    qapp.processEvents()
    sid = win.session.stack_id(win.session.project.find(testdata.STACK))
    win.reduce.load_stack(sid)
    qapp.processEvents()
    loaded = win.session.stack_loaded(sid)
    assert loaded.source.startswith("sgm_report cache with other windows") and win.session.current_stack == sid
    assert act.isEnabled() and "F Kα (sdd1" in act.toolTip()
    assert "sgm_report cache with other windows" in win.reduce.map_hint.text()
    act.trigger()
    assert win.jobs.wait_idle(30000)
    qapp.processEvents()
    new = win.session.stack_loaded(sid)
    assert new is not loaded and not new.from_report_cache and new.labels == project_windows(proj.roi_bins)[0]
    assert not act.isEnabled() and "already uses the project ROI bins" in act.toolTip()
    assert "sgm_report cache" not in win.reduce.map_hint.text()
    mode.set_advanced(False)
