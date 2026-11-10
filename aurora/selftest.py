"""Deep self-test: `aurora --selftest [--out FILE.json] [--platform offscreen|native] [--keep] [--no-gui]`.

Runs every core path and every perspective on a synthetic beamtime in a temporary folder (spaces and accents in its
path on purpose), in the installed or frozen application itself, so a missing module, plugin or data file in a
build shows up here and not on a colleague's computer. Settings and logs go to the temporary folder: the user's real
settings are never touched.

Every check is named, timed and isolated: a failure is recorded with its traceback and the run goes on. The JSON
report holds version, build, platform, machine, python, qt, qt_platform, native, cwd, credits and checks
[{name, ok, seconds, detail}]; the exit code is the number of failed checks (0 = every check passed).

matplotlib's SVG backend is used by the report check only (the report writes an SVG beside every figure), so a build
that lacks it fails exactly that check. The other figure checks write PNG and PDF.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import platform
import shutil
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

import numpy as np

STACK_B = "095500_CaF2_f_k_stack"      # a second complete stack over the same grid (session checks)


class _Runner:
    def __init__(self):
        self.results: list[dict] = []

    def check(self, name: str, fn) -> object:
        t0 = time.perf_counter()
        try:
            detail = fn()
            self.results.append({"name": name, "ok": True, "seconds": round(time.perf_counter() - t0, 3),
                                 "detail": "" if detail is None else str(detail)[:400]})
            _say(f"  ok    {name}  {'' if detail is None else str(detail)[:120]}")
            return detail
        except Exception as err:                       # noqa: BLE001  every failure is reported, none stops the run
            self.results.append({"name": name, "ok": False, "seconds": round(time.perf_counter() - t0, 3),
                                 "detail": f"{type(err).__name__}: {err}"[:400], "traceback": traceback.format_exc()})
            _say(f"  FAIL  {name}  {type(err).__name__}: {err}")
            return None

    @property
    def failed(self) -> int:
        return sum(not r["ok"] for r in self.results)

    @property
    def ok(self) -> bool:
        return self.failed == 0


def _say(text: str) -> None:
    """Print a progress line; a console that cannot encode a character never stops the run."""
    if sys.stdout is None:
        return
    try:
        print(text, flush=True)
    except (UnicodeEncodeError, OSError, ValueError):
        try:
            print(text.encode("ascii", "replace").decode("ascii"), flush=True)
        except (OSError, ValueError):
            pass


@contextlib.contextmanager
def _quiet_console():
    """Expected warnings (a damaged cache made on purpose) go to the log file only, not to the console."""
    import logging
    hs = [h for h in logging.getLogger().handlers if getattr(h, "_aurora_console", False)]
    levels = [h.level for h in hs]
    for h in hs:
        h.setLevel(logging.CRITICAL + 1)
    try:
        yield
    finally:
        for h, lv in zip(hs, levels):
            h.setLevel(lv)


def _edge(E, e0, width, white):
    return 0.2 + 0.002 * (E - E[0]) + 1.0 / (1 + np.exp(-(E - e0) / width)) + white * np.exp(-((E - e0 - 2) / 1.5) ** 2)


def _write_john_csv(path: Path, label: str, E, y) -> None:
    rows = "\n".join(f"{e},{10 * v},60,83,{20 * v},60,83,{5 * v},60,83,{5 * v},60,83,2.0,{0.5 * v},{40 * v},{10 * v},False"
                     for e, v in zip(E, y))
    path.write_text(f"# sample_label: {label}\nenergy,sdd1,sdd1_roi_lo,sdd1_roi_hi,sdd2,sdd2_roi_lo,sdd2_roi_hi,sdd3,sdd3_roi_lo,"
                    "sdd3_roi_hi,sdd4,sdd4_roi_lo,sdd4_roi_hi,I0,TEY,sdd_total,sdd_mean,bad_point\n" + rows, encoding="utf-8")


def _add_stack_b(root: Path) -> None:
    """A second complete stack of holder N5 over the same 4 x 3 grid (written like the synthetic beamtime's own)."""
    from aurora.core import testdata
    x, y = testdata.snake_grid()
    testdata._write_stack(root / "raw" / testdata.DAY / STACK_B, "CaF2_f_k", testdata.ENERGIES,
                          len(testdata.ENERGIES), x, y, np.random.default_rng(11), created="Thu Nov  5 09:55:00 2026")


def run(out: Path | None = None, qpa: str | None = None, keep: bool = False, gui: bool = True,
        native: bool = False) -> int:
    """Run every check; returns the number of failed checks (0 = all passed).

    native=True runs the GUI checks on the platform's own windowing system (Cocoa, Windows, xcb/wayland) and never
    falls back to offscreen; the JSON records the platform Qt actually used."""
    from aurora import __version__
    from aurora.credits import CREDITS
    cwd = os.getcwd()                                   # '/' when macOS LaunchServices (Finder, `open`) started us
    # resolved: the temporary folder can be an alias of itself (C:\Users\RUNNER~1 on Windows, /var -> /private/var on
    # macOS), and the checks compare paths with the project's resolved root
    base = Path(tempfile.mkdtemp(prefix="aurora selftest é ")).resolve()
    os.environ["AURORA_SETTINGS_DIR"] = str(base / "settings")
    os.environ["AURORA_LOG_DIR"] = str(base / "logs")
    if qpa:
        os.environ["QT_QPA_PLATFORM"] = qpa
    elif not native and "QT_QPA_PLATFORM" not in os.environ and gui:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen" and not os.environ.get("QT_QPA_FONTDIR"):
        fonts = _system_font_dir()                      # offscreen Qt draws no text without a font folder
        if fonts:
            os.environ["QT_QPA_FONTDIR"] = fonts
    r = _Runner()
    build = _build_info().get("describe", "")
    _say(f"AURORA {__version__}{f' (build {build})' if build else ''} self-test in {base}")
    ctx: dict = {}
    _core_checks(r, ctx, base)
    if gui:
        _gui_checks(r, ctx, base)

    summary = {
        "app": "AURORA", "version": __version__, "ok": r.ok, "frozen": bool(getattr(sys, "frozen", False)),
        "python": sys.version.split()[0], "platform": platform.platform(), "machine": platform.machine(),
        "qt": _qt_version(), "qt_platform": ctx.get("qt_platform") or os.environ.get("QT_QPA_PLATFORM", ""),
        "native": native, "folder": str(base), "cwd": cwd, "build": _build_info(), "credits": CREDITS,
        "checks": r.results,
    }
    try:
        import PySide6
        summary["pyside6"] = PySide6.__version__
    except ImportError:
        pass
    n_ok = sum(c["ok"] for c in r.results)
    _say(f"self-test {'PASSED' if r.ok else 'FAILED'}: {n_ok}/{len(r.results)} checks")
    for c in r.results:
        if not c["ok"]:
            _say(f"  failed: {c['name']}: {c['detail']}")
    if out is not None:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
        _say(f"report written to {out}")
    if not keep:
        import logging
        for h in list(logging.getLogger().handlers):
            if getattr(h, "_aurora", False):
                logging.getLogger().removeHandler(h)
                h.close()
        shutil.rmtree(base, ignore_errors=True)
    return min(r.failed, 255)


def _system_font_dir() -> str | None:
    """The folder of the system fonts, flat enough for Qt's offscreen font database (None when there is none)."""
    if sys.platform.startswith("win"):
        cands = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
    elif sys.platform == "darwin":
        cands = [Path("/System/Library/Fonts"), Path("/Library/Fonts")]
    else:
        cands = [Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/dejavu"), Path("/usr/share/fonts/TTF")]
    for c in cands:
        try:
            if c.is_dir() and any(c.glob("*.tt[fc]")):
                return str(c)
        except OSError:
            continue
    return None


def _qt_version() -> str:
    try:
        from PySide6.QtCore import qVersion
        return qVersion()
    except ImportError:
        return ""


def _build_info() -> dict:
    """aurora/_build.json of a frozen build (version, git describe, commit, date, library versions); {} from source."""
    import aurora
    try:
        return json.loads((Path(aurora.__file__).resolve().parent / "_build.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ============================================================================ core (no Qt)
def _core_checks(r: _Runner, ctx: dict, base: Path) -> None:
    from aurora.core import testdata

    # ------------------------------------------------------------------ what a frozen build must carry
    def imports():
        import h5py, matplotlib, platformdirs, scipy, xraydb  # noqa: E401,F401
        import scipy.optimize, scipy.signal, scipy.cluster.vq, scipy.ndimage, scipy.linalg, scipy.interpolate  # noqa: E401,F401
        import matplotlib.backends.backend_agg, matplotlib.backends.backend_pdf  # noqa: E401,F401
        # matplotlib.backends.backend_svg is loaded by name when a figure is saved as SVG: the report check covers it
        return (f"numpy {np.__version__}, scipy {scipy.__version__}, h5py {h5py.__version__}, "
                f"matplotlib {matplotlib.__version__}, xraydb {getattr(xraydb, '__version__', '?')}")
    r.check("imports a frozen build must carry", imports)

    def xray_database():
        import sqlite3
        import sqlalchemy
        import xraydb
        db = xraydb.get_xraydb()
        engine = getattr(db, "engine", None)
        e = xraydb.xray_edge("F", "K").energy
        assert round(float(e), 1) == 696.7, e
        line = xraydb.xray_lines("F", "K")
        assert line, "no F K emission lines"
        return (f"F K edge {e:.1f} eV, {len(line)} F K lines, sqlite {sqlite3.sqlite_version}, "
                f"SQLAlchemy {sqlalchemy.__version__} ({type(engine).__name__})")
    r.check("X-ray database: F K edge 696.7 eV (sqlite, SQLAlchemy)", xray_database)

    def logging_ok():
        import logging
        from aurora.logsetup import setup_logging
        p = setup_logging()
        logging.getLogger("aurora.selftest").info("self-test log line %s", os.getpid())
        for h in logging.getLogger().handlers:
            with contextlib.suppress(Exception):
                h.flush()
        text = Path(p).read_text(encoding="utf-8", errors="replace")
        assert f"self-test log line {os.getpid()}" in text, f"the line did not reach {p}"
        return str(p)
    r.check("log file is writable", logging_ok)

    # ------------------------------------------------------------------ projects (WP1)
    def demo():
        from aurora.cli import cmd_demo
        from aurora.core.model import Project
        root = base / "demo beamtime"
        with contextlib.redirect_stdout(io.StringIO()):
            cmd_demo(root)
        proj = Project.discover(root)
        statuses = sorted(s.status for s in proj.scans)
        assert statuses == ["aborted", "complete", "survey", "test"], statuses
        ctx["proj"] = proj
        return f"{len(proj.scans)} scans, statuses {statuses}"
    r.check("demo beamtime and discovery", demo)

    def loose_and_midnight():
        from aurora.core.model import Project
        root = testdata.make_fake_project(base / "share" / "CLS GUI", loose=True)
        p = Project.discover(root)
        assert p.loose and p.root == root.resolve(), (p.loose, p.root)
        assert all(s.day == testdata.DAY for s in p.scans), sorted({s.day for s in p.scans})
        assert [h.name for d in p.days for h in d.holders] == ["N5"]
        h5 = next((root / testdata.STACK).glob("*.h5"))
        assert Project.discover(h5).root == p.root
        # a stack recorded after midnight, before the next survey, stays with its holder
        mid = base / "midnight beamtime"
        rng = np.random.default_rng(11)
        x, y = testdata.snake_grid()
        testdata._write_survey(mid / "raw" / "2026-11-05" / "233000_holderA_survey", "holderA", "233000", rng)
        late = "000500_NaF_f_k_stack"
        testdata._write_stack(mid / "raw" / "2026-11-06" / late, "NaF_f_k", testdata.ENERGIES,
                              len(testdata.ENERGIES), x, y, rng, created="Fri Nov  6 00:05:00 2026")
        (mid / "processed").mkdir(parents=True, exist_ok=True)
        (mid / "config").mkdir(parents=True, exist_ok=True)
        pm = Project.discover(mid)
        hs = [h for d in pm.days for h in d.holders]
        st = pm.find(late)
        assert [h.name for h in hs] == ["N5"] and late in [s.name for s in hs[0].stacks], [h.name for h in hs]
        assert st.day == "2026-11-06" and st.holder == "N5" and st.continues == "2026-11-05", (st.day, st.continues)
        return f"loose folder: {len(p.scans)} scans in {p.root.name}; {late} continues holder N5 of 2026-11-05"
    r.check("loose scan folders and holders across midnight", loose_and_midnight)

    def summaries():
        from aurora.core import overview
        from aurora.core.io.h5 import file_day
        from aurora.core.names import energy_regions, parse_timestamp
        proj = ctx["proj"]
        st = proj.find(testdata.STACK)
        assert file_day(st.h5) == testdata.DAY, file_day(st.h5)
        assert parse_timestamp("Thu Nov  5 09:27:59 2026").isoformat() == "2026-11-05T09:27:59"
        assert energy_regions([680, 683, 686, 689, 692]) == [(680.0, 692.0, 3.0)]
        rows = dict(overview.scan_rows(st, proj, incomplete=[]))
        assert rows["time per map"] == "6 s per map", rows.get("time per map")
        assert rows["energy regions"] == "680–692 eV in 3 eV steps", rows.get("energy regions")
        assert overview.incomplete_energies(st) == []
        prow = dict(overview.project_rows(proj))
        assert any("holder" in k.lower() for k in prow), list(prow)
        return f"{len(rows)} scan rows, {len(prow)} project rows, energy regions '{rows['energy regions']}'"
    r.check("scan summaries, timestamps and energy regions", summaries)

    def grid_of_zones():
        from aurora.core.propose import Pad, grid_cells, snap_to_pads, zones_from_grid
        zs = zones_from_grid((0, 4, 0, 2), 2, 4, labels=list("abcdefgh"))
        assert len(zs) == 8 and [z.label for z in zs] == list("abcdefgh")
        x0, x1, y0, y1 = zs[0].rects[0]
        assert abs((x0 + x1) / 2 - 0.5) < 1e-9 and abs((y0 + y1) / 2 - 1.5) < 1e-9, zs[0].rects
        turned = zones_from_grid((0, 4, 0, 2), 2, 4, rotation=180, labels=list("abcdefgh"))
        assert [z.label for z in turned] == list("hgfedcba")
        cells = grid_cells((0, 2, 0, 2), 2, 2, labels=["tl", "tr", "bl", "br"])
        pads = [Pad(1, 0.2, 0.6, 1.3, 1.7, 9, 1.0), Pad(2, 1.1, 1.9, 1.1, 1.9, 25, 1.0), Pad(3, 1.4, 1.6, 0.4, 0.6, 9, 1.0)]
        kept, skipped = snap_to_pads(cells, pads, pitch=(0.1, 0.1))
        assert [c.label for c in kept] == ["tl", "tr", "br"] and [c.label for c in skipped] == ["bl"]
        return "8 zones in reading order, rotation 180 reverses the labels, a cell without a pad is skipped"
    r.check("grid of zones from a plate layout", grid_of_zones)

    # ------------------------------------------------------------------ stacks, caches, survey
    def stack():
        from aurora.core.stack import StackData
        proj = ctx["proj"]
        sd = StackData.load(proj, proj.find(testdata.STACK))
        sd2 = StackData.load(proj, proj.find(testdata.STACK))          # second read: from the cache
        assert sd.source == "reduced from raw" and sd2.source.startswith("aurora cache"), (sd.source, sd2.source)
        assert sd.n_pix == sd2.n_pix and np.allclose(sd.i0, sd2.i0, equal_nan=True)
        ctx["sd"] = sd
        return f"{sd.n_energies} energies x {sd.n_pix} px from {sd.source}, then {sd2.source[:40]}"
    r.check("stack reduction and cache", stack)

    def cache_integrity():
        import zipfile
        from aurora.cli import cmd_demo
        from aurora.core.io.cache import CACHE_NAME
        from aurora.core.model import Project
        from aurora.core.stack import StackData
        root = base / "cache beamtime"
        with contextlib.redirect_stdout(io.StringIO()):
            cmd_demo(root)

        def load():
            proj = Project.discover(root)
            with _quiet_console():                    # the damaged-cache warnings are expected here
                return proj, StackData.load(proj, proj.find(testdata.STACK))
        proj, sd0 = load()
        _proj, sd1 = load()
        assert sd0.source == "reduced from raw" and sd1.source.startswith("aurora cache"), (sd0.source, sd1.source)
        scan = proj.find(testdata.STACK)
        cache = root / ".aurora" / "cache" / scan.day / scan.name / CACHE_NAME
        with np.load(cache, allow_pickle=False) as z:                # AURORA's caches need no pickle
            n_members = len([z[k] for k in z.files])
        # an edited roi_bins.json re-reduces the stack and says why
        p = root / "config" / "roi_bins.json"
        bins = json.loads(p.read_text(encoding="utf-8"))
        bins["F Kα"] = {"sdd1": [64, 70], "sdd2": [65, 71], "sdd3": [66, 72], "sdd4": [67, 73], "line_ev": bins["F Kα"]["line_ev"]}
        bins["Na Kα"] = {"sdd1": [100, 108], "sdd2": [100, 108], "sdd3": [100, 108], "sdd4": [100, 108]}
        p.write_text(json.dumps(bins, indent=1, ensure_ascii=False), encoding="utf-8")
        _proj, sd2 = load()
        assert sd2.source == "reduced from raw" and sd2.notes[:1] == ["ROI bins changed since this cache: re-reducing"], \
            (sd2.source, sd2.notes[:1])
        assert "Na Kα" in sd2.labels, sd2.labels
        # a truncated member: the cache is set aside as .bad and the stack reduced from raw again
        with zipfile.ZipFile(cache) as zin:
            items = {n: zin.read(n) for n in zin.namelist()}
        member = next(n for n in items if n.startswith("roi_pix_det"))
        items[member] = items[member][: len(items[member]) // 2]
        with zipfile.ZipFile(cache, "w") as zout:
            for n, data in items.items():
                zout.writestr(n, data)
        _proj, sd3 = load()
        assert sd3.source == "reduced from raw", sd3.source
        assert (cache.parent / (CACHE_NAME + ".bad")).exists(), sorted(q.name for q in cache.parent.iterdir())
        _proj, sd4 = load()
        assert sd4.source.startswith("aurora cache"), sd4.source
        return f"{n_members} members without pickle; edited ROI bins re-reduced; a truncated cache set aside as .bad"
    r.check("reduced-stack caches checked and rebuilt from raw", cache_integrity)

    def survey():
        from aurora.core.survey import SurveyData
        proj = ctx["proj"]
        sv = SurveyData.load(next(s for s in proj.scans if s.kind == "survey"), proj.roi_bins)
        v = sv.values(sv.channels[0])
        g = sv.grid(v)
        assert sv.n > 0 and np.isfinite(np.asarray(v, float)).any()
        ctx["sv"] = sv
        return f"{sv.n} points, channels {sv.channels[:4]}, grid {getattr(g, 'shape', '')}"
    r.check("survey map", survey)

    # ------------------------------------------------------------------ zones (WP3)
    def zones_traces():
        from aurora.core.io.config import HolderZones, Zone, ZonesFile
        from aurora.core.zones import export_json, members, zone_traces
        proj, sd = ctx["proj"], ctx["sd"]
        zf = testdata.make_fake_zones(proj)
        ctx["zones"] = zf
        z = zf.holders["N5"].zones[0]
        sid = next(iter(z.settings))
        pix = members(z, sd.x, sd.y, sd.pitch)
        t = zone_traces(sd, pix, z.settings[sid])
        assert t is not None and np.isfinite(t.sum).any()
        # a polygon zone: pixel centres inside (John Bussey's rule), and the export for the zone tool as squares
        tri = Zone(id=1, label="tri", polys=[[[-0.6, 4.9], [0.35, 4.9], [-0.6, 5.6]]])
        got = members(tri, sd.x, sd.y, sd.pitch).tolist()
        assert got == [0, 1, 2, 6, 7, 8], got
        zp = ZonesFile(project="T")
        zp.holders["N5"] = HolderZones(holder="N5", day=testdata.DAY, survey=testdata.SURVEY, zones=[tri])
        back = export_json(zp, {sd.id: sd})["holders"]["N5"]["zones"][0]
        tool = Zone.from_json({k: v for k, v in back.items() if k != "polys"})
        assert members(tool, sd.x, sd.y, sd.pitch).tolist() == got
        return f"zone {z.label}: {len(pix)} px, sum {np.nanmin(t.sum):.3g}..{np.nanmax(t.sum):.3g}; triangle {got}"
    r.check("zones, polygons and zone traces", zones_traces)

    def xrf_windows():
        from aurora.core import xrf_windows as xw
        from aurora.core.constants import DETS
        from aurora.core.io.config import ScanSettings
        from aurora.core.io.raw import read_point_dir
        from aurora.core.model import Project
        from aurora.core.stack import StackData
        from aurora.core.zones import zone_sums, zone_traces
        root = testdata.make_fake_project(base / "windows beamtime")
        shutil.rmtree(root / "config")
        proj = Project.discover(root)
        xw.register_project(proj)
        scan = proj.find(testdata.STACK)
        sd = StackData.load(proj, scan)
        pix = np.arange(0, sd.n_pix, 2)
        st = ScanSettings(stack=scan.name, line="total XRF", roi={d: [60, 74] for d in DETS})
        sums = zone_sums(sd, pix, st)
        want = np.array([[read_point_dir(p).sdd[k][pix, 60:74].sum() for k in range(4)] for _e, p in scan.energy_dirs], float)
        assert sums.windows == {d: [60, 74] for d in DETS}
        assert np.array_equal(sums.det["total XRF"], want), "windows 60:74 differ from the raw bins"
        assert "windows 60:74 ch" in zone_traces(sd, pix, st).line
        spec = xw.stack_emission(scan, DETS, pix=[0, 3, 7], root=proj.root)
        assert spec.shape == (4, 256), spec.shape
        # the demo's fixed F Kα bins as a preset equal the cached line exactly
        demo, dsd = ctx["proj"], ctx["sd"]
        xw.register_project(demo)
        w = xw.bins_windows(demo.roi_bins, "F Kα")
        allpix = np.arange(dsd.n_pix)
        assert np.array_equal(xw.stack_window_sums(dsd, allpix, w), dsd.sum_over(allpix)["det"]["F Kα"])
        return f"windows 60:74 equal the raw bins at {len(want)} energies; F Kα preset {xw.describe(w)}"
    r.check("per-detector XRF windows and presets", xrf_windows)

    def rules():
        from aurora.core import rules as rl
        from aurora.core.io.config import ScanSettings
        proj = ctx["proj"]
        zf = testdata.make_fake_zones(proj)
        rule = rl.Rule.from_json({"name": "Minerals TEY", "scope": {"kind": "groups", "groups": ["Minerals"]},
                                  "patch": {"tn": "i0pre"}})
        rows = rule.dry_run(zf, proj)
        assert {(r_.holder, r_.zone) for r_ in rows} == {("N5", 1), ("N5", 3)}, rows
        rule.apply(zf, proj)
        z1, z2, z3 = zf.holders["N5"].zones
        st1 = next(iter(z1.settings.values()))
        assert st1.tey_norm == "i0pre" and next(iter(z2.settings.values())).tey_norm != "i0pre"
        assert rule.dry_run(zf, proj) == []
        st3 = next(iter(z3.settings.values()))
        st3.tey_norm, st3.pinned = "area", ["tn"]                       # a field set by hand survives the rule
        other = rl.Rule.from_json({"scope": {"kind": "groups", "groups": ["Minerals"]}, "patch": {"tn": "max"}})
        other.apply(zf, proj)
        assert st3.tey_norm == "area" and st1.tey_norm == "max"
        assert [c.field for c in other.apply(zf, proj, override=True)] == ["tn"] and st3.tey_norm == "max"
        assert isinstance(st1, ScanSettings)
        return f"{len(rows)} changes in the dry run, applied once, a pinned field kept until override"
    r.check("settings rules: dry run, apply, pinned fields", rules)

    # ------------------------------------------------------------------ checks (WP4)
    def qc():
        from aurora.core.qc import count_rate_probe, mcc_curves
        proj = ctx["proj"]
        scan = proj.find(testdata.STACK)
        cr = count_rate_probe(proj, scan)
        mc = mcc_curves(proj, scan)
        assert mc.E.size > 0
        return f"count rate {np.round(cr.top_kcps, 1).tolist()} kcps, mcc curves at {mc.E.size} energies"
    r.check("quality checks (count rate, I0 explorer)", qc)

    def qc_persisted():
        from aurora.core import flags as fl
        from aurora.core import qc as q
        from aurora.core.model import Project
        root = testdata.make_fake_project(base / "qc beamtime", n_holders=2)
        proj = Project.discover(root)
        holders = {h.name: list(h.stacks) for d in proj.days for h in d.holders}
        res = q.probe_holders(proj, holders)
        assert len(res) == sum(len(v) for v in holders.values()), (len(res), holders)
        p = q.save_qc(proj, res)
        back = q.load_qc(proj)
        assert p == proj.root / ".aurora" / "qc.json", (p, proj.root)
        assert set(back) == set(res), (sorted(back), sorted(res))
        assert all(back[k] == res[k] for k in res), [k for k in res if back[k] != res[k]]
        first = next(iter(back))
        back[first].saturated = ["sdd2"]
        q.save_qc(proj, {first: back[first]})
        scan = next(s for s in proj.scans if f"{s.day}_{s.name}" == first)
        merged = [str(f) for f in fl.merged_flags(proj, scan)]
        assert "SDD saturated: SDD2 (AURORA check)" in merged, merged
        ranges = {"a": (680, 730), "b": (870, 900), "far": (2030, 2080)}
        assert q.reference_pool("a", ranges) == ["b"], q.reference_pool("a", ranges)
        msgs = q.check_zone(None)
        assert q.worst(msgs) == "bad" and "contains 0 measurement points" in msgs[0][1], msgs
        return f"{len(res)} stacks checked, qc.json round trip, flag '{merged[-1]}', I0 pool ±150 eV"
    r.check("AURORA's checks persisted, flags and the energy-local I0 pool", qc_persisted)

    def despike_ipfy():
        from aurora.core.io.config import ScanSettings
        from aurora.core.spectra import build_traces
        from aurora.core.stack import StackData
        from aurora.core.yields import ipfy
        from aurora.core.zones import ZoneSums, zone_traces
        E = np.arange(680.0, 731.0, 1.0)
        rng = np.random.default_rng(4)
        mu = 300 + 900 / (1 + np.exp(-(E - 696.0) / 0.8))
        mu[E == 700] *= 2.5                                           # a one-point white line in every detector
        rpd = np.zeros((len(E), 4, 1, 4), np.float32)
        for k, g in enumerate((1.0, 0.8, 1.25, 0.6)):
            lam = np.repeat((g * mu)[:, None], 4, axis=1)
            if k == 1:
                lam[E == 715] *= 3.0                                   # a spike in SDD2 only
            rpd[:, :, 0, k] = rng.poisson(lam)
        i0 = 2.5 + 0.005 * rng.standard_normal((len(E), 4))
        tey = (mu / mu.max())[:, None] * (1 + 0.002 * rng.standard_normal((len(E), 4)))
        sd = StackData(id="2026-11-05_120000_coarse_stack", name="120000_coarse_stack", day="2026-11-05", E=E.copy(),
                       x=np.array([0.0, 0.1, 0.0, 0.1]), y=np.array([0.0, 0.0, 0.1, 0.1]), i0=i0, tey=tey,
                       roi_pix_det=rpd, valid=np.ones(len(E), bool), labels=["F Kα"], main="F Kα", edge=("F", "K"),
                       edge_ev=696.7, pitch=(0.1, 0.1))
        t = zone_traces(sd, np.arange(4), ScanSettings(line="F Kα", despike_mode="coherent"))
        iw, isp = int(np.where(E == 700)[0][0]), int(np.where(E == 715)[0][0])
        assert not any(iw in (t.spikes.get(k) or []) for k in t.spikes), t.spikes
        assert isp in t.spikes["sdd2"] and t.spikes_ev.get("sdd2") == [715.0], (t.spikes, t.spikes_ev)
        # an I0 dip to 5 % is left out of every / I0 trace
        n = 4
        i0z = np.full(len(E), 2.5 * n); i0z[30] *= 0.05
        det = np.column_stack([1000 * g * (1.0 + (E > 696)) for g in (1.0, 0.9, 1.1, 0.8)])
        sums = ZoneSums(E=E, n_pix=n, i0=i0z, tey=0.5 * n * (1.0 + (E > 696)), det={"F Kα": det}, main="F Kα", edge_ev=696.7)
        tm = build_traces(sums, ScanSettings(line="F Kα", despike=False))
        assert tm.masked["i0_low"].sum() == 1 and np.isnan(tm.sum[30])
        # IPFY: I0 / counts of a non-resonant line
        S, I0 = np.array([120.0, 150.0, 180.0, 400.0]), np.array([2.0, 2.1, 2.2, 2.3])
        assert np.allclose(ipfy(S, I0, "ratio").y, I0 / S)
        ti = build_traces(sums, ScanSettings(line="F Kα", signal="ipfy", fluo_norm="raw", despike=False))
        assert ti.signal == "ipfy" and ti.trace_names()["sum"] == "ipfy_sum_norm", ti.trace_names()
        assert any(s.startswith("signal: IPFY") for s in ti.notes), ti.notes
        return "white line kept, SDD2 spike at 715 eV removed, I0 dip masked, IPFY = I0 / S"
    r.check("coherent de-spike, I0 mask and IPFY", despike_ipfy)

    def john_format():
        from aurora.core.compare import load_spectra
        from aurora.core.export import write_john
        proj, sd, zf = ctx["proj"], ctx["sd"], ctx["zones"]
        z = zf.holders["N5"].zones[0]
        sid = next(iter(z.settings))
        scan = proj.find(testdata.STACK)
        paths = write_john(z, sid, sd, z.settings[sid], base / "john" / "1 alpha", label="1 alpha", project=proj,
                           scan=scan)
        names = sorted(p.name for p in paths)
        assert len(paths) == 4, names
        full = next(p for p in paths if p.name.endswith("_full_reduced.csv"))
        head = full.read_text(encoding="utf-8").split("\nenergy", 1)[0]
        assert "reduced_with: AURORA" in head and "roi_points:" in head, head[:300]
        signals = {s.signal for s in load_spectra(full)}
        assert {"sdd-scaled", "sdd_total", "TEY"} <= signals, signals
        try:
            write_john(z, sid, sd, z.settings[sid], scan.path / "aurora_selftest", label="x", project=proj, scan=scan)
        except PermissionError:
            pass
        else:
            raise AssertionError("John-format files were written into a raw scan folder")
        return f"{', '.join(names)}; signals {sorted(signals)}; the raw folder refused"
    r.check("John Bussey's file format written and read back", john_format)

    # ------------------------------------------------------------------ Compare and Fit (WP5)
    def spectra_set():
        from aurora.core.compare import load_spectra
        if "specs" in ctx:
            return ctx["specs"]
        E = np.linspace(660, 740, 321)
        a, b, c = _edge(E, 690, 1.0, 0.6), _edge(E, 700, 1.6, 0.2), _edge(E, 695, 1.2, 0.9)
        folder = base / "reduced"
        folder.mkdir(exist_ok=True)
        for name, y in (("A", a), ("B", b), ("C", c), ("mix", 0.5 * a + 0.3 * b + 0.2 * c)):
            _write_john_csv(folder / f"{name}_full_reduced.csv", name, E, y)
        specs = {s.label: s for p in sorted(folder.glob("*.csv")) for s in load_spectra(p) if s.signal != "TEY"}
        assert {"A", "B", "C", "mix"} <= {k.split()[0] for k in specs}, list(specs)
        ctx["ref_folder"] = folder
        ctx["specs"] = {k.split()[0]: s for k, s in specs.items() if s.signal == "sdd-scaled"} or \
            {k.split()[0]: s for k, s in specs.items()}
        return ctx["specs"]

    def compare_norm():
        from aurora.core.compare import (TraceRequest, local_peaks, normalise_larch, normalise_legacy,
                                         normalise_tey_line, process)
        sp = spectra_set()
        A = sp["A"]
        nr = normalise_larch(A.E, A.y)
        assert 688 < nr.e0 < 693, nr.e0
        assert np.isfinite(normalise_legacy(A.E, A.y).y).any() and np.isfinite(normalise_tey_line(A.E, A.y).y).any()
        out = {}
        for m, extra in (("deriv", {}), ("half", {}), ("table", {}), ("fixed", {"e0": 691.25})):
            res = process(A, TraceRequest(A.label, A.signal, e0_mode=m, **extra), edge_ev=696.7)
            assert np.isfinite(res.y).any(), m
            out[m] = res.e0
        assert out["table"] == 696.7 and out["fixed"] == 691.25, out
        peaks = local_peaks(A.E, nr.y)
        assert peaks and any(690 < e < 695 for e, _h in peaks), peaks
        return f"E0 larch {nr.e0:.2f} eV, modes {', '.join(f'{k} {v:.2f}' for k, v in out.items())}, {len(peaks)} peaks"
    r.check("compare normalisations, E0 choices and peaks (scipy.signal)", compare_norm)

    def lcf_check():
        from aurora.core.fit import lcf
        sp = spectra_set()
        res = lcf((sp["mix"].E, sp["mix"].y), [(sp[k].E, sp[k].y) for k in "ABC"], bootstrap=50)
        assert np.allclose(res.coefficients, [0.5, 0.3, 0.2], atol=0.02), res.coefficients
        assert res.converged and np.isfinite(res.redchi) and res.n_params >= 1, (res.converged, res.redchi)
        assert len(res.f_tests) == 3 and "bootstrap" in res.method, (len(res.f_tests), res.method)
        return (f"coefficients {np.round(res.coefficients, 4).tolist()} ± {np.round(res.uncertainties, 4).tolist()}, "
                f"χ²ν {res.redchi:.3g}, {res.method}")
    r.check("LCF recovers known fractions within 0.02 (scipy.optimize)", lcf_check)

    def pca_mcr_itfa():
        from aurora.core.fit import itfa, mcr_als, pca, prepare_matrix
        sp = spectra_set()
        _grid, D = prepare_matrix([(sp[k].E, sp[k].y) for k in ("A", "B", "C", "mix")])
        p = pca(D)
        assert p.explained[:3].sum() > 0.999, p.explained
        m = mcr_als(D, 2, max_iter=50)
        it = itfa(D, m, {0: np.array([1.0, 0.0])}, max_iter=20)
        assert m.ST.shape[0] == 2 and it.ST.shape == m.ST.shape
        return f"PCA {np.round(p.explained[:3], 3).tolist()}, MCR {m.ST.shape}, ITFA {it.ST.shape}"
    r.check("PCA, MCR-ALS and ITFA", pca_mcr_itfa)

    def peaks():
        from aurora.core.fit import peak_decomposition
        sp = spectra_set()
        pk = peak_decomposition(sp["A"].E, sp["A"].y, n_peaks=1, init_centres=[692])
        assert pk.converged and abs(pk.params["p1_centre"] - 692) < 1.5, pk.params
        assert "p1_fwhm" in pk.params and "p1_area" in pk.areas, (list(pk.params), list(pk.areas))
        return f"peak at {pk.params['p1_centre']:.2f} eV, FWHM {pk.params['p1_fwhm']:.2f} eV"
    r.check("peak decomposition (FWHM, areas)", peaks)

    def compare_config():
        from aurora.core.compare import Spectrum, TraceRequest, load_config, read_spectra_arrays, save_config, sidecar_path
        sp = spectra_set()
        A = sp["A"]
        zone_spec = Spectrum(label="zone 1 · N5", signal="fluo", E=A.E.copy(), y=A.y.copy(), source="zone:1:N5")
        reqs = [TraceRequest(A.label, A.signal), TraceRequest(zone_spec.label, zone_spec.signal, e0_mode="table")]
        path = base / "compare config" / "config.json"
        written = save_config(path, reqs, {A.label: str(ctx["ref_folder"] / "A_full_reduced.csv")}, spectra=[zone_spec])
        assert sidecar_path(path) in written and sidecar_path(path).exists(), written
        back, sources, _extra = load_config(path)
        assert [q.to_json() for q in back] == [q.to_json() for q in reqs]
        stored = json.loads(path.read_text(encoding="utf-8"))["stored"]
        arrays = read_spectra_arrays(sidecar_path(path), stored)
        assert len(arrays) == 1 and np.allclose(arrays[0].y, zone_spec.y)
        return f"{len(back)} rows and {len(arrays)} stored trace round trip ({', '.join(p.name for p in written)})"
    r.check("Compare config and its sidecar round trip", compare_config)

    def figures():
        from aurora.core.figures import FigureOptions, FigureTrace, compare_figure, save_figure
        from aurora.core.reportcheck import pdf_pages
        sp = spectra_set()
        from aurora.core.report import PAL
        opts = FigureOptions(dpi=100, formats=("png", "pdf"))         # matplotlib's own colours; SVG: the report check
        fig = compare_figure([FigureTrace(k, sp[k].E, sp[k].y + i, PAL[i]) for i, k in enumerate("ABC")], opts)
        (base / "figure").mkdir(exist_ok=True)
        paths = save_figure(fig, base / "figure" / "compare", opts)
        assert sorted(p.suffix for p in paths) == [".pdf", ".png"], paths
        png = next(p for p in paths if p.suffix == ".png")
        pdf = next(p for p in paths if p.suffix == ".pdf")
        import struct
        head = png.read_bytes()[:24]
        assert head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR", head
        size = struct.unpack(">II", head[16:24])                    # the PNG's own width and height
        assert size == (350, 450), size
        raw = pdf.read_bytes()
        assert b"/MediaBox [0 0 252 324]" in raw or b"/MediaBox [ 0 0 252 324 ]" in raw, raw[:400]
        assert pdf_pages(pdf) == 1
        return f"PNG {size[0]} x {size[1]} px at 100 dpi, PDF 252 x 324 pt (3.5 x 4.5 in)"
    r.check("fixed-size publication figures (PNG, PDF)", figures)

    def references():
        from aurora.core.references import list_references
        spectra_set()
        refs = list_references(ctx["ref_folder"])
        assert len(refs) == 4
        return f"{len(refs)} references"
    r.check("reference library", references)

    # ------------------------------------------------------------------ Maps (WP6)
    def hypercube():
        from aurora.core.cancel import Cancelled
        from aurora.core.hypercube import Hypercube, pca_kmeans
        proj = ctx["proj"]
        scan = proj.find(testdata.STACK)
        croot = base / "cube cache"
        hc = Hypercube.load(proj, scan, cache_root=croot)
        assert hc.dtype in ("uint16", "uint32") and (hc.cache_dir / "cube_meta.json").exists(), (hc.dtype, hc.cache_dir)
        assert isinstance(hc.cubes[0], np.memmap) and not list(croot.rglob(".building-*"))
        hc2 = Hypercube.load(proj, scan, cache_root=croot)               # the second open: from the cache
        assert hc2.cache_dir == hc.cache_dir
        stop = base / "cube cancelled"

        def cancel_at_2(i, _n, _m=""):
            if i == 2:
                raise Cancelled()
        try:
            Hypercube.load(proj, scan, progress=cancel_at_2, cache_root=stop)
        except Cancelled:
            pass
        else:
            raise AssertionError("the cancelled build went on")
        assert not list(stop.rglob("cube_meta.json")) and not list(stop.rglob(".building-*")), "a cancelled build left files"
        lo, hi = hc.window_channels(0, 600, 740)
        M = hc.pixel_matrix(None, lo, hi)
        cr = pca_kmeans(M, hc.E, n_components=2, n_clusters=2, seed=3)
        cr2 = pca_kmeans(M, hc.E, n_components=2, n_clusters=2, seed=3)
        assert np.array_equal(cr.labels, cr2.labels), "the same seed gave other clusters"
        ctx["hc"] = hc
        return f"{hc.n_energies} E x {hc.n_pix} px, {hc.dtype} on disk, cancel leaves nothing, {len(set(cr.labels.tolist()))} clusters"
    r.check("hypercube streamed to disk, cancelled cleanly, k-means", hypercube)

    def pymca():
        import h5py
        from aurora.core.hypercube import export_pymca_4d, export_pymca_xanes
        from aurora.core.io.config import Calibration
        hc = ctx["hc"]
        keep = hc.calibration
        hc.calibration = Calibration(data={"sdd1": {"gain": 10.058, "offset": 3.9}, "sdd2": {"gain": 9.398, "offset": -3.6},
                                           "sdd3": {"gain": 9.852, "offset": -1.3}, "sdd4": {"gain": 10.090, "offset": 7.9}})
        try:
            w = hc.ev_windows(640, 710)
            assert w[0] == (63, 71) and w[1] == (68, 76), w
            total = hc.window_map(w, energy_index=0)
            parts = sum(hc.window_map(d, *w[d], energy_index=0) for d in range(4))
            assert np.allclose(total, parts, equal_nan=True), "the sum map is not the sum of the detectors' own windows"
            calls = []
            p1 = export_pymca_xanes(hc, base / "x_PCA-CA.h5", w, progress=lambda *a: calls.append(a))
            p2 = export_pymca_4d(hc, base / "x_Elemental_PyMca.h5", progress=lambda *a: calls.append(a))
        finally:
            hc.calibration = keep
        with h5py.File(p1, "r") as h:
            rois = []
            h.visititems(lambda n, o: rois.append(n) if isinstance(o, h5py.Dataset) and "channel_roi" in o.attrs else None)
        with h5py.File(p2, "r") as h:
            d4 = h["entry/full_4d_measurement/sdd_sum_4d"]
            shape, chunks = d4.shape, d4.chunks
        assert len(rois) >= 4, rois
        assert shape[-1] == 256 and chunks[-2:] == (1, 256), (shape, chunks)
        return f"SDD1 {w[0]}, SDD2 {w[1]}; {len(rois)} datasets with channel_roi; 4D {shape} in chunks {chunks}"
    r.check("per-SDD windows and PyMca h5 files written and read back", pymca)

    def cluster_merger():
        from aurora.core.hypercube import merged_spectra, paint_squares, pca_kmeans
        from aurora.core.io.config import Zone
        from aurora.core.zones import members
        hc = ctx["hc"]
        lo, hi = hc.window_channels(0, 600, 740)
        M = hc.pixel_matrix(None, lo, hi)
        cr = pca_kmeans(M, hc.E, n_components=2, n_clusters=2, seed=3)
        mean, total, n = merged_spectra(M, cr.labels, {0})
        assert n == int((cr.labels == 0).sum()) and np.allclose(mean, M[cr.labels == 0].mean(axis=0))
        assert np.allclose(total, M[cr.labels == 0].sum(axis=0))
        pix = np.where(cr.labels == 0)[0]
        z = Zone(id=1, label="cluster 1", paint=paint_squares(hc.x, hc.y, hc.pitch, pix))
        assert members(z, hc.x, hc.y, hc.pitch).tolist() == pix.tolist()
        return f"cluster 1: {n} px, merged mean and sum, a painted zone with exactly its pixels"
    r.check("cluster merger and painted zones", cluster_merger)

    def deconv_stitch():
        import copy
        from scipy.ndimage import gaussian_filter
        from aurora.core.deconv import deconvolve_grid
        from aurora.core.propose import pads_from_survey
        from aurora.core.stitch import auto_trims, stitch
        img = np.zeros((15, 15)); img[7, 7] = 1.0
        blurred = gaussian_filter(img, 1.5)
        out = deconvolve_grid(blurred, fwhm_um=350, pitch_mm=0.1, method="lucy", iterations=30)
        assert np.isfinite(out).all() and out.max() > blurred.max()
        sd = ctx["sd"]
        b = copy.deepcopy(sd); b.x = b.x + 3 * sd.pitch[0]; b.name = "tile_b"; b.id = "tile_b"
        st = stitch([sd, b], auto_trims([sd, b]))
        pads = pads_from_survey(ctx["sv"])
        return f"deconvolved peak {blurred.max():.3f} -> {out.max():.3f}, stitched {st.n_pix} px, {len(pads)} pads"
    r.check("deconvolution, stitching, zone proposals", deconv_stitch)

    # ------------------------------------------------------------------ Report (WP7)
    def report():
        # the frozen build once failed here: matplotlib's svg / pdf backends are loaded by name at savefig time
        from aurora.core import provenance
        from aurora.core.report import ReportBuilder
        from aurora.core.reportcheck import check, pdf_pages
        out = base / "report é"
        res = ReportBuilder(ctx["proj"], ctx["zones"], out, title="Self-test", png_dpi=120, author="Self Test").build()
        assert res.pdf.exists() and res.pdf.stat().st_size > 10_000, res.pdf
        assert res.pages >= 5 and pdf_pages(res.pdf) >= 5, (res.pages, pdf_pages(res.pdf))
        chk = check(out, min_pages=5)                                     # the newest PDF and every figure below out
        assert not chk["problems"], chk["problems"]
        svgs = list((res.pdf.parent / "figures").glob("*.svg"))          # each build has its own folder (I39)
        assert svgs and res.csvs
        assert res.folder.parent == out and json.loads((out / "latest.json").read_text(encoding="utf-8"))
        assert not list(out.glob(".building-*")), "a build folder was left behind"
        # provenance: every CSV starts with '# AURORA' and its header gives back the settings
        for csv in res.csvs:
            assert csv.read_text(encoding="utf-8").startswith("# AURORA "), csv.name
            h = provenance.read_header(csv)
            assert {"settings", "members", "sdd_roi_channels", "data_source"} <= set(h), sorted(h)
            assert provenance.sidecar_path(csv).exists(), csv.name
        man = json.loads(res.manifest.read_text(encoding="utf-8"))
        assert all({"settings", "members", "flags", "sha256"} <= set(e) for e in man["entries"])
        v = ReportBuilder(ctx["proj"], ctx["zones"], out).verify(res.manifest)
        assert v.ok and v.checked == len(res.csvs), v.text()
        changed = copy_zones(ctx["zones"])
        next(iter(changed.holders["N5"].zones[0].settings.values())).fluo_norm = "max"
        v2 = ReportBuilder(ctx["proj"], changed, out).verify(res.manifest)
        assert not v2.ok and len(v2.differences) == 1 and "1 alpha" in v2.differences[0], v2.differences
        return (f"{res.pages} pages, {len(chk['pngs'])} PNG each with its SVG, {len(res.csvs)} CSV with provenance; "
                f"verify reproduces {v.checked}, names the changed zone")
    r.check("report (PDF, PNG, SVG, CSV), provenance and verify", report)

    def report_plan():
        from aurora.core.model import Project
        from aurora.core.report import AUTHOR, ReportBuilder, os_full_name, plan, preflight, resolve_author
        proj = Project.discover(testdata.make_fake_project(base / "two holders", n_holders=2))
        zf = testdata.make_fake_zones(proj)
        found = preflight(proj, zf)
        kinds = sorted(f.kind for f in found)
        assert kinds == ["no_included", "uncovered_stack", "ungrouped"], kinds
        p = plan(proj, zf)
        assert p.totals() == "2 zones · 2 spectra · 3 warnings", p.totals()
        out = base / "plan reports"
        draft = ReportBuilder(proj, zf, out, preset="draft")
        assert draft.png_dpi == 110 and not draft.appendix and not draft.maps
        assert ReportBuilder(proj, zf, out).png_dpi == 600
        assert ReportBuilder(proj, zf, out, holders=["N5"]).scope_keys() == ["N5"]
        assert AUTHOR == "" and resolve_author("X Y") == ("X Y", "Settings")
        assert isinstance(os_full_name(), str)
        return f"{len(found)} findings ({', '.join(kinds)}), '{p.totals()}', Draft 110 dpi, holders scope N5"
    r.check("report pre-flight, presets, scope and author", report_plan)

    # ------------------------------------------------------------------ command line (WP8a)
    def command_line():
        from aurora import cli
        from aurora.logsetup import log_file
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = cli.main(["report", str(base / "no such beamtime")])
        assert rc == 1, rc
        assert "Traceback" in err.getvalue(), err.getvalue()[:300]
        text = Path(log_file()).read_text(encoding="utf-8", errors="replace")
        assert "aurora report failed" in text, "the failure did not reach the log"
        with contextlib.redirect_stdout(io.StringIO()) as o:
            assert cli.main(["project", str(ctx["proj"].root)]) == 0
        assert "F Kα" in o.getvalue()
        return "a failing command returns 1 with its traceback in the log; 'aurora project' prints F Kα"
    r.check("command line: exit codes and the log", command_line)


def copy_zones(zf):
    from aurora.core.io.config import ZonesFile
    return ZonesFile.from_json(json.loads(json.dumps(zf.to_json())))


# ============================================================================ GUI
def _grab_image(widget):
    """The widget's grab as an (h, w) uint32 array of RGB32 pixels."""
    from PySide6.QtGui import QImage
    img = widget.grab().toImage().convertToFormat(QImage.Format_RGB32)
    return _qimage_array(img)


def _qimage_array(img):
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    if w == 0 or h == 0:
        return np.zeros((0, 0), np.uint32)
    buf = np.frombuffer(img.constBits(), np.uint8, count=bpl * h)
    return buf.reshape(h, bpl)[:, : 4 * w].copy().view(np.uint32).reshape(h, w)


def _painted(widget) -> int:
    """Number of distinct colours in a grab of the widget (a blank or unpainted widget has one or two)."""
    a = _grab_image(widget)
    if a.size == 0:
        return 0
    step_y, step_x = max(1, a.shape[0] // 60), max(1, a.shape[1] // 60)
    return int(np.unique(a[::step_y, ::step_x]).size)


def _plot_areas(page) -> list[int]:
    """For every visible pyqtgraph view on the page: the number of pixels inside its ViewBoxes that differ from the
    view's background (the data drawn: curves, images, markers)."""
    import pyqtgraph as pg
    from PySide6.QtGui import QImage
    out = []
    for gv in page.findChildren(pg.GraphicsView):
        if not gv.isVisible() or gv.width() < 30 or gv.height() < 30:
            continue
        img = gv.grab().toImage().convertToFormat(QImage.Format_RGB32)
        a = _qimage_array(img)
        if a.size == 0:
            continue
        dpr = img.devicePixelRatio() or 1.0
        best = 0
        for item in gv.scene().items():
            if not isinstance(item, pg.ViewBox) or not item.isVisible():
                continue
            rect = gv.mapFromScene(item.sceneBoundingRect()).boundingRect()
            x0, y0 = max(0, int(rect.left() * dpr) + 2), max(0, int(rect.top() * dpr) + 2)
            x1, y1 = min(a.shape[1], int(rect.right() * dpr) - 2), min(a.shape[0], int(rect.bottom() * dpr) - 2)
            if x1 - x0 < 8 or y1 - y0 < 8:
                continue
            sub = a[y0:y1, x0:x1]
            vals, counts = np.unique(sub, return_counts=True)
            best = max(best, int(sub.size - counts.max()))
        out.append(best)
    return out


class _Errors(list):
    """Exceptions raised in Qt slots (sys.excepthook) and failed background jobs (crash.show_error) during the GUI
    checks: recorded instead of opening a modal dialog, and failing the check during which they happened."""

    def hook(self, exc_type, exc, tb) -> None:
        self.append(f"{exc_type.__name__}: {exc}\n{''.join(traceback.format_exception(exc_type, exc, tb))[-1500:]}")

    def show_error(self, title, text, details="") -> None:
        self.append(f"{title}: {text}\n{str(details)[-1500:]}")


def _gui_checks(r: _Runner, ctx: dict, base: Path) -> None:
    state: dict = {}
    errors = _Errors()

    def guarded(fn):
        """Run a GUI check; an exception raised in a slot or an error dialog during it fails it."""
        def run_():
            n0 = len(errors)
            out = fn()
            if "app" in state:
                for _ in range(3):
                    state["app"].processEvents()
            if len(errors) > n0:
                raise AssertionError("during this check: " + errors[n0].splitlines()[0] +
                                     (f" (+{len(errors) - n0 - 1} more)" if len(errors) - n0 > 1 else ""))
            return out
        return run_

    def check(name, fn):
        return r.check(name, guarded(fn))

    def app():
        from aurora.app import crash
        from aurora.app.main import MainWindow, create_app
        state["hook"] = sys.excepthook
        sys.excepthook = errors.hook
        crash.show_error = errors.show_error           # a failed job is recorded, never a modal dialog
        state["app"] = create_app(["AURORA-selftest"])
        state["win"] = win = MainWindow()
        win.resize(1400, 900)
        win.show()
        state["app"].processEvents()
        from PySide6.QtGui import QGuiApplication
        ctx["qt_platform"] = QGuiApplication.platformName()
        return f"Qt platform {ctx['qt_platform']}, window {win.width()}x{win.height()}"
    if check("application window", app) is None and "win" not in state:
        sys.excepthook = state.get("hook", sys.__excepthook__)
        return
    app_, win = state["app"], state["win"]

    def pump(n=20):
        for _ in range(n):
            app_.processEvents()

    def wait_jobs(ms=60000):
        ok = win.jobs.wait_idle(ms)
        pump(5)
        assert ok, f"background jobs still running after {ms / 1000:.0f} s: {win.jobs.running()}"

    def wait_for(cond, seconds=10.0, what="the condition"):
        t0 = time.monotonic()
        while not cond():
            if time.monotonic() - t0 > seconds:
                raise AssertionError(f"{what} not met within {seconds:.0f} s")
            pump(2)
            time.sleep(0.01)

    grabs = base / "grabs"
    grabs.mkdir(exist_ok=True)

    # ------------------------------------------------------------------ the installation
    def images_and_icons():
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QIcon, QImageReader
        from aurora import resources_dir
        from aurora.app.icons import app_icon
        fmts = {bytes(f).decode() for f in QImageReader.supportedImageFormats()}
        assert {"svg", "png"} <= fmts, sorted(fmts)
        svgs = sorted((resources_dir() / "ui").glob("*.svg"))
        assert svgs, "no resources/ui/*.svg"
        null = [p.name for p in svgs if QIcon(str(p)).isNull() or QIcon(str(p)).pixmap(QSize(16, 16)).isNull()]
        assert not null, f"null icons: {null}"
        ic = app_icon()
        sizes = [s for s in (16, 24, 32, 48, 64, 128, 256) if not ic.pixmap(QSize(s, s)).isNull()]
        assert not ic.isNull() and sizes == [16, 24, 32, 48, 64, 128, 256], sizes
        return f"image formats incl. svg; {len(svgs)} SVG icons; application icon at {sizes[0]}-{sizes[-1]} px"
    check("Qt image formats, SVG icons and the application icon", images_and_icons)

    def pg_exporters():
        import pyqtgraph as pg
        import pyqtgraph.exporters as pex
        from aurora.app import pgcompat
        assert pgcompat.install()
        w = pg.PlotWidget()
        w.resize(400, 300)
        x = np.linspace(0, 10, 200)
        w.plot(x, np.sin(x), pen="b")
        w.plot(x, np.cos(x), pen=None, symbol="o", symbolSize=4)
        w.addItem(pg.FillBetweenItem(pg.PlotDataItem(x, np.sin(x) - 0.2), pg.PlotDataItem(x, np.sin(x) + 0.2)))
        w.show()
        pump(5)
        img = pex.ImageExporter(w.plotItem)
        img.parameters()["width"] = 400
        out_png = grabs / "exporter.png"
        img.export(str(out_png))
        out_svg = grabs / "exporter.svg"
        pex.SVGExporter(w.plotItem).export(str(out_svg))
        from PySide6.QtCore import QCoreApplication, QEvent
        w.hide()
        w.deleteLater()                               # PlotWidget.close() drops its plot item: delete it instead
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        text = out_svg.read_text(encoding="utf-8")
        assert out_png.stat().st_size > 1000 and "<svg" in text and text.count("<path") >= 2, (out_png.stat().st_size, text[:200])
        return f"PNG {out_png.stat().st_size} bytes, SVG {len(text)} characters with {text.count('<path')} paths"
    check("pyqtgraph Image and SVG exporters", pg_exporters)

    def tls():
        from PySide6.QtNetwork import QSslSocket
        backends = list(QSslSocket.availableBackends())
        assert backends, "no TLS backend plugin"
        assert QSslSocket.supportsSsl(), f"TLS not usable (backends {backends})"
        return f"active {QSslSocket.activeBackend()}, available {backends}, {QSslSocket.sslLibraryVersionString()}"
    check("TLS for Help > Check for updates", tls)

    def update_check():
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from aurora import __version__
        from aurora.app.updates import UpdateChecker

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):                           # noqa: N802
                tag = "v99.0.0" if "newer" in self.path else f"v{__version__}"
                body = json.dumps({"tag_name": tag, "body": "notes", "html_url": "http://127.0.0.1/release",
                                   "assets": []}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            got = {}
            for which in ("same", "newer"):
                c = UpdateChecker(win)
                c.start(f"http://127.0.0.1:{srv.server_address[1]}/{which}/releases/latest")
                wait_for(lambda c=c: c.done, 15.0, f"the update check ({which})")
                assert c.info is not None, c.error
                got[which] = c.info
                c.deleteLater()
        finally:
            srv.shutdown()
            srv.server_close()
        assert got["same"]["version"] == __version__ and not got["same"]["newer"], got["same"]
        assert got["newer"]["newer"] and got["newer"]["version"] == "99.0.0", got["newer"]
        return f"local server: {__version__} is up to date, 99.0.0 is newer"
    check("update check against a local http.server", update_check)

    def licences_and_build():
        from aurora import __version__
        from aurora.app.diagnostics import licence_texts
        texts = dict(licence_texts())
        assert "MIT" in texts.get("AURORA licence", ""), list(texts)
        assert "John Bussey" in texts.get("Credits", "") and "Third-party licences" in texts, list(texts)
        frozen = bool(getattr(sys, "frozen", False))
        if frozen:
            from platformdirs import user_cache_dir
            b = _build_info()
            assert b.get("version") == __version__, b
            assert "PySide6" in texts["Third-party licences"] or "LGPL" in texts["Third-party licences"]
            cache = Path(user_cache_dir("AURORA", appauthor=False))
            assert Path(os.environ.get("MPLCONFIGDIR", "")).resolve().is_relative_to(cache.resolve()), os.environ.get("MPLCONFIGDIR")
            return f"frozen build {b.get('describe', '')}, licences bundled, matplotlib cache in {cache}"
        return f"from source: LICENSE (MIT), credits, {len(texts)} licence texts"
    check("licences, version and build provenance", licences_and_build)

    # ------------------------------------------------------------------ the session (WP2), without a window
    def session_attach_undo():
        from aurora import __version__
        from aurora.app.session import Session
        from aurora.core import testdata
        from aurora.core.model import Project
        root = testdata.make_fake_project(base / "session beamtime")
        _add_stack_b(root)
        ses = Session()
        ses.set_project(Project.discover(root))
        try:
            sid_a, sid_b = f"{testdata.DAY}_{testdata.STACK}", f"{testdata.DAY}_{STACK_B}"
            ses.stack(sid_a)
            ses.touch()
            assert ses.undo_stack.count() == 0, "a stack load was recorded as an undo step"
            z = ses.new_zone("A")
            with ses.edit(f"add a rectangle to zone {z.id}"):
                z.rects.append([-0.6, 0.35, 4.9, 5.6])
            assert set(z.settings) == {sid_a, sid_b} and ses.stack_loaded(sid_b) is None, sorted(z.settings)
            assert ses.attach_text(z).endswith("(1 not loaded)"), ses.attach_text(z)
            done = z.to_json()
            steps = ses.undo_stack.count()
            assert ses.undo() and z.id not in [q.id for q in ses.holder_zones().zones]
            assert ses.redo() and [q.to_json() for q in ses.holder_zones().zones] == [done]
            ses.stack(sid_b)
            ses.touch()
            assert ses.undo_stack.count() == steps, "loading a stack added an undo step"
            ses.flush()
            d = json.loads(ses.session_file.read_text(encoding="utf-8"))
            assert (d["format"], d["schema"], d["app"]) == ("aurora-session", 2, f"AURORA {__version__}"), \
                (d.get("format"), d.get("schema"), d.get("app"))
            exp = base / "session export" / "zones_all.json"
            exp.parent.mkdir(exist_ok=True)
            ses.export_zones(exp)
            e = json.loads(exp.read_text(encoding="utf-8"))
            assert e["version"] == 1 and not {"ui", "rules", "format", "schema", "app"} & set(e), sorted(e)
            ctx["session_root"], ctx["session_export"] = root, exp
            return f"2 stacks attached (1 not loaded), undo/redo of '{ses.undo_stack.text(steps - 1)}', schema 2, export v1"
        finally:
            ses._save_timer.stop()
    check("session: zones attach without loading, undo and redo, versioned session.json", session_attach_undo)

    def session_recovery():
        from aurora.app.session import Session
        from aurora.core.io.config import ZonesFile
        from aurora.core.model import Project
        root = ctx["session_root"]
        ses = Session()
        ses.set_project(Project.discover(root))
        try:
            z = ses.new_zone("B")
            with ses.edit(f"add a rectangle to zone {z.id}"):
                z.rects.append([-0.3, 0.1, 4.9, 5.2])
            ses.flush()
            bak = ses.session_file.with_name("session.json.bak")
            assert bak.exists(), "no session.json.bak after the second save"
            labels_bak = [q["label"] for q in json.loads(bak.read_text(encoding="utf-8"))["holders"]["N5"]["zones"]]
            data = ses.session_file.read_bytes()
            ses.session_file.write_bytes(data[: len(data) // 2])            # a damaged session.json
        finally:
            ses._save_timer.stop()
        ses2 = Session()
        told = []
        ses2.recovered.connect(told.append)
        ses2.set_project(Project.discover(root))
        try:
            got = [q.label for q in ses2.holder_zones().zones]
            assert got == labels_bak, (got, labels_bak)
            corrupt = list(ses2.session_file.parent.glob("session.corrupt-*.json"))
            assert len(corrupt) == 1 and told and "was damaged" in told[0], (corrupt, told)
            assert ZonesFile.load(ses2.session_file).holders["N5"].zones, "session.json was not rewritten"
            n = len(ses2.holder_zones().zones)
            added = ses2.import_zones(ctx["session_export"], policy="merge")
            li = ses2.last_import
            assert added == 0 and li.duplicates == n and li.backup and Path(li.backup).exists(), (added, li)
            return f"{len(got)} zone(s) restored from the .bak, damaged file kept; import merged {n} duplicate(s)"
        finally:
            ses2._save_timer.stop()
    check("session: a damaged session.json is recovered, imports merge", session_recovery)

    # ------------------------------------------------------------------ the demo in the window
    def open_demo():
        win.home.open_project(ctx["proj"].root)
        # discovery runs in a worker: wait on the clock, not on a number of event-loop turns (200 turns took 0.04 s
        # on macOS 26 started through `open`, before the worker had finished)
        t0 = time.monotonic()
        while win.session.project is None and time.monotonic() - t0 < 60.0:
            pump(2)
            time.sleep(0.01)
        jobs = getattr(win, "jobs", None)
        if jobs is not None:
            jobs.wait_idle(60000)
        assert win.session.project is not None, f"the demo did not open within {time.monotonic() - t0:.0f} s"
        return f"{win.crumb.text()} ({time.monotonic() - t0:.2f} s)"
    check("open the demo in the window", open_demo)

    def home():
        from aurora.app import mode
        from aurora.core import testdata
        mode.set_advanced(False)
        hm = win.home
        win.show_perspective("home")
        pump(5)
        text = hm.details.toPlainText()
        assert "No project open" not in text and "holder" in text.lower(), text[:200]
        st = win.session.project.find(testdata.STACK)
        tree = hm.tree
        holder = tree.topLevelItem(0).child(0)
        item = next(holder.child(i) for i in range(holder.childCount()) if holder.child(i).text(0) == testdata.STACK)
        tree.setCurrentItem(item)
        pump(5)
        wait_jobs()
        wait_for(lambda: "every map complete" in hm.details.toPlainText(), 15.0, "the map check of the stack")
        assert hm.map_check(st) == []
        hm.recent_menu.aboutToShow.emit()
        recent = [a.text() for a in hm.recent_actions()]
        assert any(ctx["proj"].root.name in t for t in recent), recent
        hm.open_path(base / "no such beamtime")
        wait_jobs()
        wait_for(lambda: hm.failure_state.isVisible(), 15.0, "the failure panel")
        names = [b.text() for b in hm.failure_state.buttons]
        assert names[-1] == "Keep the open project", names
        hm.failure_state.buttons[-1].click()
        pump(5)
        assert not hm.failure_state.isVisible() and win.session.project is not None
        return f"project and scan summaries, {len(recent)} recent project(s), a failed open offers {names}"
    check("Home: summaries, recent projects, a failed open", home)

    def survey_grid():
        from aurora.app import mode
        mode.set_advanced(True)
        win.show_perspective("survey")
        pump(5)
        sv, ses = win.survey, win.session
        n0 = len(ses.holder_zones().zones)
        steps = ses.undo_stack.count()
        dlg = sv.open_grid_dialog((-0.9, 0.3, -0.5, 0.3))
        pump(5)
        assert dlg.isVisible()
        dlg.layout_combo.setCurrentIndex(0)
        dlg.labels.setCurrentIndex(dlg.labels.findData("numbers"))
        dlg.rows.setValue(2)
        dlg.cols.setValue(2)
        dlg.accept()
        pump(5)
        n1 = len(ses.holder_zones().zones)
        assert n1 == n0 + 4 and sv.grid_dialog is None, (n0, n1)
        assert ses.undo_stack.count() == steps + 1
        ses.undo()                                    # one step: the demo goes on without the grid
        pump(5)
        assert len(ses.holder_zones().zones) == n0
        mode.set_advanced(False)
        return "a 2 x 2 grid of numbered zones added in one edit, undone in one step"
    check("Survey: grid of zones (Advanced)", survey_grid)

    def reduce_():
        from aurora.app import mode
        from aurora.app import shortcuts
        from aurora.core import testdata
        from aurora.core.constants import DETS
        mode.set_advanced(False)
        ses, rd = win.session, win.reduce
        sid = ses.stack_id(ses.project.find(testdata.STACK))
        win.show_perspective("reduce")
        pump(5)
        rd.load_stack(sid)
        wait_jobs()
        sd = ses.stack_loaded(sid)
        assert sd is not None and ses.current_stack == sid, ses.current_stack
        titles = rd.step_titles()
        assert [t[:3] for t in titles] == ["1 ·", "2 ·", "3 ·", "4 ·"], titles
        assert not rd.add_check_btn.isEnabled()
        x0, x1, y0, y1 = sd.bbox
        rd.map.rect_drawn.emit(x0, x1, y0, y1)
        pump(5)
        z = ses.zone()
        assert z is not None and sid in z.settings and rd.add_check_btn.isEnabled()
        z.label = "1 alpha"
        assert rd.region_caption.text().startswith("Region: 1 rectangle"), rd.region_caption.text()
        wc = rd.window_card
        wc.set_window(None, 50, 70)
        wc.commit_now()
        pump(5)
        wait_jobs()
        st = z.settings[sid]
        assert st.roi == {d: [50, 70] for d in DETS}, st.roi
        rd.update_preview()
        pump(5)
        assert "windows 50:70 ch" in rd.preview._traces[0][1].line, rd.preview._traces[0][1].line
        wc.preset.setCurrentIndex(0)                      # back to the fixed F Kα bins
        pump(5)
        wait_jobs()
        assert z.settings[sid].roi is None and z.settings[sid].line == "F Kα", (z.settings[sid].roi, z.settings[sid].line)
        lasso = next(a for a in shortcuts.registered(win) if a.shortcut().toString() == "L"
                     and shortcuts.section_of(a) == "Reduce")
        lasso.trigger()
        pump(5)
        assert rd.map.vb.tool == "lasso+", rd.map.vb.tool
        rd.set_tool("navigate")
        assert rd.add_to_check() == 1 and len(ses.check_queue) == 1
        ctx["zone_id"], ctx["sid"] = z.id, sid
        return f"{' / '.join(titles)}; windows 50:70 ch then F Kα bins; L = lasso; one spectrum sent to Check"
    check("Reduce: four Easy steps, XRF windows, tools, Add to Check", reduce_)

    def check_():
        from PySide6.QtCore import Qt
        ck, ses = win.check, win.session
        win.show_perspective("check")
        pump(5)
        wait_jobs()
        wait_for(lambda: ck.queue_rows() and ck.queue_rows()[0]["state"] == "ready", 30.0, "the queued spectrum")
        rows = ck.queue_rows()
        assert len(rows) == 1 and rows[0]["looks_ok"] and "bad" not in rows[0]["levels"], rows
        assert ck.view_combo.count() == 5
        for i in range(5):
            ck.view_combo.setCurrentIndex(i)
            pump(3)
            assert ck.qplot.getPlotItem().listDataItems(), ck.view_combo.currentText()
        ck.view_combo.setCurrentIndex(0)
        lib = win.library
        before = len(lib.requests)
        ck.ask_overwrite = lambda paths: "yes"
        assert ck.queue.item(0, 5).checkState() == Qt.Checked
        ck.save_btn.click()
        wait_jobs()
        z = ses.zone(ctx["zone_id"])
        folder = ses.project.root / "processed" / "reduced" / z.label
        names = sorted(p.name for p in folder.iterdir())
        assert len(names) == 4 and len(lib.requests) > before, (names, len(lib.requests), before)
        ck.run_every_holder()
        assert win.jobs.job("check:every") is not None
        wait_jobs()
        qcj = json.loads((ses.project.root / ".aurora" / "qc.json").read_text(encoding="utf-8"))
        assert ctx["sid"] in qcj, sorted(qcj)
        return f"queue row ready and looks OK, 5 views drawn, {len(names)} John-format files, {len(qcj)} stack(s) in qc.json"
    check("Check: queue, views, John-format save, every holder", check_)

    def compare_():
        from aurora.app.figure_export import FigureDialog
        from aurora.core.figures import FigureOptions
        lib, cp = win.library, win.compare
        lib.add_folder(win.session.project.root / "processed" / "reduced")
        for q in lib.requests:
            q.show = True
            q.fit = q.signal == "sdd-scaled"
        lib.changed.emit()
        win.show_perspective("compare")
        pump(10)
        traces = cp.plot_traces()
        assert len(traces) >= 5, len(traces)
        dlg = FigureDialog(win)
        dlg.show(); pump(3); dlg.close(); dlg.deleteLater()
        out = base / "compare export" / "compare.csv"
        out.parent.mkdir(exist_ok=True)
        paths = cp.export(out, FigureOptions(dpi=100, formats=("png", "pdf")))
        kinds = sorted({p.suffix for p in paths})
        assert {".csv", ".png", ".pdf", ".json"} <= set(kinds) and all(p.stat().st_size > 0 for p in paths), kinds
        return f"{len(traces)} traces drawn; export wrote {', '.join(p.name for p in paths)}"
    check("Compare: traces, Figure size and export", compare_)

    def fit_():
        from PySide6.QtCore import Qt
        fit = win.fit
        win.show_perspective("fit")
        fit.refresh()
        pump(5)
        names = [fit.target.itemText(i) for i in range(fit.target.count())]
        target = next(t for t in names if t.startswith("mixture 1") and "sdd-scaled" in t)
        fit.target.setCurrentText(target)
        comps = {"NaF [sdd-scaled]", "CaF2 [sdd-scaled]", "BaY2F8 [sdd-scaled]"}
        for i in range(fit.components.count()):
            it = fit.components.item(i)
            it.setCheckState(Qt.Checked if it.text() in comps else Qt.Unchecked)
        fit.bootstrap.setValue(20)
        assert fit.run_lcf() is not None, fit.lcf_info.text()
        wait_jobs()
        res = fit.lcf_result
        assert res is not None and fit.lcf_chip.text().startswith("converged"), (fit.lcf_chip.text(), fit.lcf_info.text())
        coef = dict(zip(fit._lcf_names[1], res.coefficients))
        want = {"NaF [sdd-scaled]": 0.5, "CaF2 [sdd-scaled]": 0.3, "BaY2F8 [sdd-scaled]": 0.2}
        assert all(abs(coef[k] - v) < 0.03 for k, v in want.items()), coef
        fit.run_pca()
        fit.n_comp.setValue(2)
        fit.run_mcr()
        wait_jobs()
        assert fit.mcr_chip.text().startswith("converged") or fit.mcr_chip.text(), fit.mcr_chip.text()
        fit.pk_trace.setCurrentText("NaF [sdd-scaled]")
        fit.pk_n.setValue(1)
        fit.pk_centres.setText("692")
        fit.run_peaks()
        wait_jobs()
        assert fit.pk_chip.text(), "the peaks chip is empty"
        fit.bootstrap.setValue(200)
        return (f"LCF {', '.join(f'{k.split()[0]} {v:.3f}' for k, v in coef.items())} ({fit.lcf_chip.text()}); "
                f"MCR '{fit.mcr_chip.text()}'; peaks '{fit.pk_chip.text()}'")
    check("Fit: LCF, MCR-ALS and peaks as background jobs", fit_)

    def maps_():
        from PySide6.QtCore import Qt
        from aurora.app import mode
        from aurora.core.zones import members
        mode.set_advanced(True)
        mp, ses = win.maps, win.session
        win.show_perspective("maps")
        pump(5)
        idx = mp.scan_combo.findData(ctx["sid"])
        assert idx >= 0, [mp.scan_combo.itemData(i) for i in range(mp.scan_combo.count())]
        mp.scan_combo.setCurrentIndex(idx)
        mp.load()
        wait_jobs()
        assert mp.hc is not None, mp.status.text()
        assert [b.text() for b in mp.sdd_boxes] == ["SDD1", "SDD2", "SDD3", "SDD4"]
        mp.draw_elements()
        wait_jobs()
        assert len(mp.element_grids) >= 1, "no elemental map"
        mp.n_k.setValue(2)
        mp.run_clusters()
        wait_jobs()
        assert mp._cluster is not None and mp.cluster_list.count() == 2, mp.cluster_list.count()
        for i in range(mp.cluster_list.count()):
            it = mp.cluster_list.item(i)
            it.setCheckState(Qt.Checked if i == 0 else Qt.Unchecked)
        pump(3)
        z = mp.make_zone_from_clusters()
        hc = mp.hc
        want = np.where(np.isin(mp._cluster.labels, mp.ticked_clusters()))[0]
        assert z is not None and members(z, hc.x, hc.y, hc.pitch).tolist() == want.tolist()
        n = len(win.library.requests)
        mp.send_clusters_to_compare()
        assert len(win.library.requests) == n + 1
        paths = mp.export_clusters(base / "clusters.csv")
        assert all(p.stat().st_size > 0 for p in paths), paths
        before = dict(mp.stats)
        for i in range(hc.n_energies):
            mp.energy.setValue(i)
            pump(1)
        assert mp.stats.get("xanes") == before.get("xanes"), (before, mp.stats)
        mode.set_advanced(False)
        return (f"hypercube {hc.n_energies} E x {hc.n_pix} px, {len(mp.element_grids)} elemental maps, 2 clusters, "
                f"zone of {want.size} px, sent to Compare, {', '.join(p.name for p in paths)}")
    check("Maps: hypercube, elemental maps, clusters and the merger (Advanced)", maps_)

    def report_():
        import re
        rp = win.report
        win.show_perspective("report")
        pump(5)
        rp.refresh_check()
        wait_jobs()
        pump(5)
        lst = rp.preflight_list
        total = rp.totals.text()
        assert re.fullmatch(r"\d+ zones? · \d+ spectr(um|a) · \d+ warnings?", total), total
        n = lst.topLevelItemCount()
        if n:
            rp.show_delegate.click(lst.topLevelItem(0))
            pump(5)
            assert win.stack.currentWidget() is win.reduce
        return f"'{total}', {n} finding(s)" + (", Show opens Reduce" if n else "")
    check("Report: check before building and its jump to Reduce", report_)

    def save_indicator():
        ses = win.session
        ui = ses._ui
        z = ses.zone(ctx["zone_id"])
        with ses.edit(f"rename zone {z.id}"):
            z.comment = "self-test"
        pump(3)
        assert ui.indicator.text() == "unsaved", ui.indicator.text()
        ses.flush()
        pump(3)
        assert ui.indicator.text().startswith("saved "), ui.indicator.text()
        assert ui.undo_action.text().startswith("Undo "), ui.undo_action.text()
        edit = [a.text().replace("&", "") for a in win.menus["edit"].actions()]
        assert any(t.startswith("Undo") for t in edit) and any(t.startswith("Redo") for t in edit), edit
        assert any(t.startswith("History") for t in edit), edit
        return f"'{ui.indicator.text()}', '{ui.undo_action.text()}', Edit menu {[t for t in edit if t][:4]}"
    check("save indicator and the Edit menu", save_indicator)

    def re_reduce():
        from aurora.app import mode, stack_cache
        from aurora.app.main import MainWindow
        from aurora.cli import cmd_demo
        from aurora.core import testdata
        from aurora.core.io.cache import CACHE_NAME
        from aurora.core.io.raw import stack_last_mtime
        from aurora.core.model import Project
        from aurora.core.stack import StackData, project_windows
        root = base / "re-reduce beamtime"
        with contextlib.redirect_stdout(io.StringIO()):
            cmd_demo(root)
        proj = Project.discover(root)
        scan = proj.find(testdata.STACK)
        sd = StackData.load(proj, scan)
        _labels, wins = project_windows(proj.roi_bins)
        other = {"F Kα": {d: [b[0] + 2, b[1] + 2] for d, b in wins["F Kα"].items()},
                 "total XRF": {d: list(b) for d, b in wins["total XRF"].items()}}
        d = proj.processed / scan.day / scan.name                    # a cache in sgm_report v2.5's format
        d.mkdir(parents=True, exist_ok=True)
        k = [sd.labels.index(lab) for lab in other]
        rpd = sd.roi_pix_det[:, :, k, :]
        sig = f"v2.5|{len(scan.energy_dirs)}|{stack_last_mtime(scan.energy_dirs):.0f}|{json.dumps(other, sort_keys=True)}"
        np.savez(d / CACHE_NAME, signature=sig, i0=sd.i0, tey=sd.tey, roi_pix_det=rpd, roi_det=np.nansum(rpd, axis=1),
                 valid=sd.valid, skipped=np.array([], dtype=object), notes=np.array([], dtype=object),
                 roi_labels=np.array(list(other), dtype=object))
        (root / ".aurora" / "cache" / scan.day / scan.name / CACHE_NAME).unlink()
        mode.set_advanced(True)
        w2 = MainWindow()
        try:
            offer = stack_cache.install(w2)
            act = offer.action
            assert act in w2.menus["file"].actions() and not act.isEnabled()
            w2.home.set_project(Project.discover(root))
            assert w2.jobs.wait_idle(30000)
            pump(5)
            sid = w2.session.stack_id(w2.session.project.find(testdata.STACK))
            w2.reduce.load_stack(sid)
            assert w2.jobs.wait_idle(30000)
            pump(5)
            loaded = w2.session.stack_loaded(sid)
            assert loaded.source.startswith("sgm_report cache with other windows"), loaded.source
            assert act.isEnabled(), act.toolTip()
            act.trigger()
            assert w2.jobs.wait_idle(30000)
            pump(5)
            new = w2.session.stack_loaded(sid)
            assert new is not loaded and not new.from_report_cache, new.source
            return f"offered for '{loaded.source[:40]}…', re-reduced: {new.source}"
        finally:
            w2.jobs.wait_idle(30000)
            w2.session._save_timer.stop()
            w2.close()
            w2.deleteLater()
            pump(5)
            mode.set_advanced(False)
    check("File > Re-reduce the stack with the project ROI bins", re_reduce)

    # ------------------------------------------------------------------ every page, both themes, both modes
    def perspectives():
        from aurora.app import mode
        from aurora.app.main import PERSPECTIVES
        mode.set_advanced(True)
        plotted = {"survey", "reduce", "check", "compare", "fit", "maps"}
        out = []
        for key, _title in PERSPECTIVES:
            win.show_perspective(key)
            pump(10)
            page = win.pages[key]
            n = _painted(page)
            assert n > 3, f"{key} looks blank ({n} colours)"
            assert page.grab().save(str(grabs / f"{key}.png")), f"{key}.png not written"
            areas = _plot_areas(page)
            if key in plotted:
                assert areas and max(areas) > 30, f"{key}: no data drawn in its plot areas {areas}"
            out.append(f"{key}:{n}" + (f"/{max(areas)}px" if areas else ""))
        return " ".join(out)
    check("every perspective paints, data in its plot areas (PNG grabs)", perspectives)

    def plots_paint():
        win.session.select_stack(ctx["sid"])
        win.show_perspective("reduce")
        pump(20)
        n = _painted(win.reduce)
        assert n > 20, f"Reduce shows almost nothing ({n} colours): plots not painted"
        return f"Reduce {n} colours"
    check("plots and maps paint (PySide6 >= 6.10)", plots_paint)

    def themes():
        from aurora.app.theme import apply_theme, scheme
        out = []
        for sch in ("dark", "light"):
            apply_theme(app_, appearance=sch)
            pump(5)
            assert scheme() == sch
            n = _painted(win)
            assert win.grab().save(str(grabs / f"window_{sch}.png"))
            out.append(f"{sch}:{n}")
        return " ".join(out)
    check("light and dark themes", themes)

    def modes():
        from aurora.app import mode
        from aurora.app.main import ADVANCED_PERSPECTIVES
        mode.set_advanced(False)
        pump(5)
        hidden = [k for k in ADVANCED_PERSPECTIVES if win.rail_buttons[k].isHidden()]
        assert set(hidden) == set(ADVANCED_PERSPECTIVES), hidden
        mode.set_advanced(True)
        pump(5)
        assert not any(win.rail_buttons[k].isHidden() for k in ADVANCED_PERSPECTIVES)
        return f"Easy hides {sorted(ADVANCED_PERSPECTIVES)}"
    check("Easy and Advanced modes", modes)

    def manuals():
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QTextDocument
        from aurora.app.help import DOCS, docs_dir
        names = list(dict.fromkeys([n for n, _t, _tip in DOCS] + sorted(p.name for p in docs_dir().glob("*.md"))))
        out, missing, n_img = [], [], 0
        for name in names:
            win.show_help(name)
            pump(5)
            br = win._help.browser
            text = br.toPlainText()
            assert len(text) > 500, name
            doc = br.document()
            block = doc.begin()
            while block.isValid():
                it = block.begin()
                while not it.atEnd():
                    frag = it.fragment()
                    if frag.isValid() and frag.charFormat().isImageFormat():
                        src = frag.charFormat().toImageFormat().name()
                        res = doc.resource(QTextDocument.ImageResource, QUrl(src))
                        n_img += 1
                        if res is None or getattr(res, "isNull", lambda: True)():
                            missing.append(f"{name}: {src}")
                    it += 1
                block = block.next()
            out.append(f"{name}:{len(text)}")
        win._help.close()
        assert not missing, f"images that do not resolve: {missing[:5]}"
        return f"manuals ok: {' '.join(out)}; {n_img} images resolve"
    check("manuals open and their images resolve", manuals)

    def dialogs():
        from aurora.app.about import AboutDialog
        from aurora.app.diagnostics import LicencesDialog, SelftestDialog
        from aurora.app.dialogs import ReferencesDialog
        from aurora.app.settings_dialog import SettingsDialog
        made = []
        summary = {"version": "x", "ok": True, "checks": [{"name": "a", "ok": True, "seconds": 0.0, "detail": ""}]}
        for cls, args in ((AboutDialog, (win,)), (SettingsDialog, (win,)), (ReferencesDialog, (str(ctx.get("ref_folder", base)),)),
                          (LicencesDialog, (win,)), (SelftestDialog, (summary, win))):
            d = cls(*args)
            d.show(); pump(3)
            if cls is SelftestDialog:
                assert "1 of 1 checks ok" in d.view.toPlainText(), d.view.toPlainText()
            d.close(); d.deleteLater()
            made.append(cls.__name__)
        return ", ".join(made)
    check("dialogs", dialogs)

    def close():
        from aurora.app.main import shutdown_app
        win.session._save_timer.stop() if hasattr(win.session, "_save_timer") else None
        shutdown_app(app_, win)
        return "closed"
    r.check("close and shut down", close)
    sys.excepthook = state.get("hook", sys.__excepthook__)
