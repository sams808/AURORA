"""Deep self-test: `aurora --selftest [--out FILE.json] [--platform offscreen|native] [--keep]`.

Runs every core path and every perspective on a synthetic beamtime in a temporary folder (spaces and accents in its
path on purpose), in the installed or frozen application itself, so a missing module, plugin or data file in a
build shows up here and not on a colleague's computer. Writes a JSON report and returns 0 only when every check
passed. Settings and logs go to the temporary folder: the user's real settings are never touched.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

import numpy as np


class _Runner:
    def __init__(self):
        self.results: list[dict] = []

    def check(self, name: str, fn) -> object:
        t0 = time.perf_counter()
        try:
            detail = fn()
            self.results.append({"name": name, "ok": True, "seconds": round(time.perf_counter() - t0, 3),
                                 "detail": "" if detail is None else str(detail)[:400]})
            print(f"  ok    {name}  {'' if detail is None else str(detail)[:120]}", flush=True)
            return detail
        except Exception as err:                       # noqa: BLE001  every failure is reported, none stops the run
            self.results.append({"name": name, "ok": False, "seconds": round(time.perf_counter() - t0, 3),
                                 "detail": f"{type(err).__name__}: {err}", "traceback": traceback.format_exc()})
            print(f"  FAIL  {name}  {type(err).__name__}: {err}", flush=True)
            return None

    @property
    def ok(self) -> bool:
        return all(r["ok"] for r in self.results)


def _edge(E, e0, width, white):
    return 0.2 + 0.002 * (E - E[0]) + 1.0 / (1 + np.exp(-(E - e0) / width)) + white * np.exp(-((E - e0 - 2) / 1.5) ** 2)


def _write_john_csv(path: Path, label: str, E, y) -> None:
    rows = "\n".join(f"{e},{10 * v},60,83,{20 * v},60,83,{5 * v},60,83,{5 * v},60,83,2.0,{0.5 * v},{40 * v},{10 * v},False"
                     for e, v in zip(E, y))
    path.write_text(f"# sample_label: {label}\nenergy,sdd1,sdd1_roi_lo,sdd1_roi_hi,sdd2,sdd2_roi_lo,sdd2_roi_hi,sdd3,sdd3_roi_lo,"
                    "sdd3_roi_hi,sdd4,sdd4_roi_lo,sdd4_roi_hi,I0,TEY,sdd_total,sdd_mean,bad_point\n" + rows, encoding="utf-8")


def run(out: Path | None = None, qpa: str | None = None, keep: bool = False, gui: bool = True,
        native: bool = False) -> int:
    """native=True runs the GUI checks on the platform's own windowing system (Cocoa, Windows, xcb/wayland) and
    never falls back to offscreen; the JSON records the platform Qt actually used."""
    from aurora import __version__
    cwd = os.getcwd()                                   # '/' when macOS LaunchServices (Finder, `open`) started us
    base = Path(tempfile.mkdtemp(prefix="aurora selftest é "))
    os.environ["AURORA_SETTINGS_DIR"] = str(base / "settings")
    os.environ["AURORA_LOG_DIR"] = str(base / "logs")
    if qpa:
        os.environ["QT_QPA_PLATFORM"] = qpa
    elif not native and "QT_QPA_PLATFORM" not in os.environ and gui:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    r = _Runner()
    build = _build_info().get("describe", "")
    print(f"AURORA {__version__}{f' (build {build})' if build else ''} self-test in {base}", flush=True)
    ctx: dict = {}

    # ------------------------------------------------------------------ imports a frozen build must carry
    def imports():
        import h5py, matplotlib, scipy, xraydb  # noqa: E401,F401
        import scipy.optimize, scipy.signal, scipy.cluster.vq, scipy.ndimage, scipy.linalg, scipy.interpolate  # noqa: E401,F401
        import matplotlib.backends.backend_agg, matplotlib.backends.backend_pdf, matplotlib.backends.backend_svg  # noqa: E401,F401
        e = xraydb.xray_edge("F", "K").energy
        assert 690 < e < 700, e
        return f"numpy {np.__version__}, scipy {scipy.__version__}, h5py {h5py.__version__}, matplotlib {matplotlib.__version__}, F K edge {e:.1f} eV"
    r.check("imports and the X-ray database", imports)

    def logging_ok():
        from aurora.logsetup import setup_logging
        p = setup_logging()
        import logging
        logging.getLogger("aurora.selftest").info("self-test log line")
        return str(p)
    r.check("log file", logging_ok)

    # ------------------------------------------------------------------ core
    def demo():
        from aurora.cli import cmd_demo
        from aurora.core.model import Project
        root = base / "demo beamtime"
        cmd_demo(root)
        proj = Project.discover(root)
        statuses = sorted(s.status for s in proj.scans)
        assert statuses == ["aborted", "complete", "survey", "test"], statuses
        ctx["proj"] = proj
        return f"{len(proj.scans)} scans, statuses {statuses}"
    r.check("demo beamtime and discovery", demo)

    def stack():
        from aurora.core import testdata
        from aurora.core.stack import StackData
        proj = ctx["proj"]
        sd = StackData.load(proj, proj.find(testdata.STACK))
        sd2 = StackData.load(proj, proj.find(testdata.STACK))          # second read: from the cache
        assert sd.n_pix == sd2.n_pix and np.allclose(sd.i0, sd2.i0, equal_nan=True)
        ctx["sd"] = sd
        return f"{sd.n_energies} energies x {sd.n_pix} px from {sd.source}, then {sd2.source}"
    r.check("stack reduction and cache", stack)

    def survey():
        from aurora.core.survey import SurveyData
        proj = ctx["proj"]
        sv = SurveyData.load(next(s for s in proj.scans if s.kind == "survey"), proj.roi_bins)
        v = sv.values(sv.channels[0])
        g = sv.grid(v)
        ctx["sv"] = sv
        return f"{sv.n} points, channels {sv.channels[:4]}, grid {getattr(g, 'shape', '')}"
    r.check("survey map", survey)

    def zones_traces():
        from aurora.core import testdata
        from aurora.core.zones import members, zone_traces
        proj, sd = ctx["proj"], ctx["sd"]
        zf = testdata.make_fake_zones(proj)
        ctx["zones"] = zf
        z = zf.holders["N5"].zones[0]
        sid = next(iter(z.settings))
        pix = members(z, sd.x, sd.y, sd.pitch)
        t = zone_traces(sd, pix, z.settings[sid])
        assert t is not None and np.isfinite(t.sum).any()
        return f"zone {z.label}: {len(pix)} px, sum {np.nanmin(t.sum):.3g}..{np.nanmax(t.sum):.3g}"
    r.check("zones and zone traces", zones_traces)

    def qc():
        from aurora.core import testdata
        from aurora.core.qc import count_rate_probe, mcc_curves
        proj = ctx["proj"]
        scan = proj.find(testdata.STACK)
        cr = count_rate_probe(proj, scan)
        mc = mcc_curves(proj, scan)
        assert mc.E.size > 0
        return f"count rate {np.round(cr.top_kcps, 1).tolist()} kcps, mcc curves at {mc.E.size} energies"
    r.check("quality checks (count rate, I0 explorer)", qc)

    def compare_fit():
        from aurora.core.compare import TraceRequest, load_spectra, normalise_larch
        from aurora.core.fit import itfa, lcf, mcr_als, pca, peak_decomposition, prepare_matrix
        E = np.linspace(660, 740, 321)
        a, b, c = _edge(E, 690, 1.0, 0.6), _edge(E, 700, 1.6, 0.2), _edge(E, 695, 1.2, 0.9)
        folder = base / "reduced"
        folder.mkdir()
        for name, y in (("A", a), ("B", b), ("C", c), ("mix", 0.5 * a + 0.3 * b + 0.2 * c)):
            _write_john_csv(folder / f"{name}_full_reduced.csv", name, E, y)
        specs = {s.label: s for p in sorted(folder.glob("*.csv")) for s in load_spectra(p) if s.signal != "TEY"}
        assert {"A", "B", "C", "mix"} <= {k.split()[0] for k in specs}, list(specs)
        get = lambda k: next(s for lab, s in specs.items() if lab.split()[0] == k)          # noqa: E731
        nr = normalise_larch(get("A").E, get("A").y)
        assert 688 < nr.e0 < 693, nr.e0
        res = lcf((get("mix").E, get("mix").y), [(get(k).E, get(k).y) for k in "ABC"], bootstrap=20)
        assert np.allclose(res.coefficients, [0.5, 0.3, 0.2], atol=0.03), res.coefficients
        _grid, D = prepare_matrix([(get(k).E, get(k).y) for k in ("A", "B", "C", "mix")])
        p = pca(D)
        m = mcr_als(D, 2, max_iter=50)
        it = itfa(D, m, {0: np.array([1.0, 0.0])}, max_iter=20)
        pk = peak_decomposition(E, a, n_peaks=1, init_centres=[692])
        assert pk.converged and abs(pk.params["p1_centre"] - 692) < 1.5
        ctx["ref_folder"] = folder
        return (f"E0 {nr.e0:.2f} eV, LCF {np.round(res.coefficients, 3).tolist()}, PCA {np.round(p.explained[:3], 3).tolist()}, "
                f"MCR {m.ST.shape}, ITFA {it.ST.shape}, peak at {pk.params['p1_centre']:.2f} eV")
    r.check("compare and fits (LCF, PCA, MCR-ALS, ITFA, peaks)", compare_fit)

    def references():
        from aurora.core.references import list_references
        refs = list_references(ctx["ref_folder"])
        assert len(refs) == 4
        return f"{len(refs)} references"
    r.check("reference library", references)

    def hypercube():
        import h5py
        from aurora.core import testdata
        from aurora.core.hypercube import Hypercube, export_pymca_4d, export_pymca_xanes, pca_kmeans
        proj = ctx["proj"]
        hc = Hypercube.load(proj, proj.find(testdata.STACK))
        lo, hi = hc.window_channels(0, 600, 740)
        mp = hc.window_map(None, lo, hi, energy_index=0, per_i0=True)
        M = hc.pixel_matrix(None, lo, hi)
        cr = pca_kmeans(M, hc.E, n_components=2, n_clusters=2)
        p1 = export_pymca_xanes(hc, base / "x_PCA-CA.h5", None, lo, hi)
        p2 = export_pymca_4d(hc, base / "x_Elemental_PyMca.h5")
        with h5py.File(p1, "r") as h:
            n1 = len(list(h.keys()))
        with h5py.File(p2, "r") as h:
            n2 = len(list(h.keys()))
        ctx["hc"] = hc
        return f"{hc.n_energies} E x {hc.n_pix} px, map {mp.shape}, {len(set(cr.labels.tolist()))} clusters, h5 groups {n1}/{n2}"
    r.check("hypercube, clusters, PyMca exports", hypercube)

    def deconv_stitch():
        import copy
        from aurora.core.deconv import deconvolve_grid
        from aurora.core.propose import pads_from_survey
        from aurora.core.stitch import auto_trims, stitch
        img = np.zeros((15, 15)); img[7, 7] = 1.0
        from scipy.ndimage import gaussian_filter
        blurred = gaussian_filter(img, 1.5)
        out = deconvolve_grid(blurred, fwhm_um=350, pitch_mm=0.1, method="lucy", iterations=30)
        assert np.isfinite(out).all() and out.max() > blurred.max()
        sd = ctx["sd"]
        b = copy.deepcopy(sd); b.x = b.x + 3 * sd.pitch[0]; b.name = "tile_b"; b.id = "tile_b"
        st = stitch([sd, b], auto_trims([sd, b]))
        pads = pads_from_survey(ctx["sv"])
        return f"deconvolved peak {blurred.max():.3f} -> {out.max():.3f}, stitched {st.n_pix} px, {len(pads)} pads"
    r.check("deconvolution, stitching, zone proposals", deconv_stitch)

    def report():
        # the frozen build once failed here: matplotlib's svg / pdf backends are loaded by name at savefig time
        from aurora.core.report import ReportBuilder
        from aurora.core.reportcheck import check, pdf_pages
        out = base / "report é"
        res = ReportBuilder(ctx["proj"], ctx["zones"], out, title="Self-test", png_dpi=120).build()
        assert res.pdf.exists() and res.pdf.stat().st_size > 10_000, res.pdf
        assert res.pages >= 5 and pdf_pages(res.pdf) >= 5, (res.pages, pdf_pages(res.pdf))
        chk = check(out, min_pages=5)                                     # the newest PDF and every figure below out
        assert not chk["problems"], chk["problems"]
        svgs = list((res.pdf.parent / "figures").glob("*.svg"))          # each build has its own folder (I39)
        assert svgs and res.csvs
        return f"{res.pages} pages, {len(chk['pngs'])} PNG each with its SVG, {len(res.csvs)} CSV"
    r.check("report (PDF, PNG, SVG, CSV)", report)

    # ------------------------------------------------------------------ GUI
    if gui:
        _gui_checks(r, ctx, base)

    summary = {
        "app": "AURORA", "version": __version__, "ok": r.ok, "frozen": bool(getattr(sys, "frozen", False)),
        "python": sys.version.split()[0], "platform": platform.platform(), "machine": platform.machine(),
        "qt_platform": ctx.get("qt_platform") or os.environ.get("QT_QPA_PLATFORM", ""), "native": native,
        "folder": str(base), "cwd": cwd, "build": _build_info(), "checks": r.results,
    }
    try:
        import PySide6
        summary["pyside6"] = PySide6.__version__
    except ImportError:
        pass
    n_ok = sum(c["ok"] for c in r.results)
    print(f"self-test {'PASSED' if r.ok else 'FAILED'}: {n_ok}/{len(r.results)} checks", flush=True)
    if out is not None:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"report written to {out}", flush=True)
    if not keep:
        import logging
        for h in list(logging.getLogger().handlers):
            if getattr(h, "_aurora", False):
                logging.getLogger().removeHandler(h)
                h.close()
        shutil.rmtree(base, ignore_errors=True)
    return 0 if r.ok else 1


def _build_info() -> dict:
    """aurora/_build.json of a frozen build (version, git describe, commit, date, library versions); {} from source."""
    import aurora
    try:
        return json.loads((Path(aurora.__file__).resolve().parent / "_build.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _painted(widget) -> int:
    """Number of distinct colours in a grab of the widget (a blank or unpainted widget has one or two)."""
    img = widget.grab().toImage().convertToFormat(widget.grab().toImage().Format.Format_RGB32)
    w, h = img.width(), img.height()
    seen = set()
    for yy in range(0, h, max(1, h // 60)):
        for xx in range(0, w, max(1, w // 60)):
            seen.add(img.pixel(xx, yy))
    return len(seen)


def _gui_checks(r: _Runner, ctx: dict, base: Path) -> None:
    state: dict = {}

    def app():
        from aurora.app.main import MainWindow, create_app
        state["app"] = create_app(["AURORA-selftest"])
        state["win"] = win = MainWindow()
        win.resize(1400, 900)
        win.show()
        state["app"].processEvents()
        from PySide6.QtGui import QGuiApplication
        ctx["qt_platform"] = QGuiApplication.platformName()
        return f"Qt platform {ctx['qt_platform']}, window {win.width()}x{win.height()}"
    if r.check("application window", app) is None and "win" not in state:
        return
    app_, win = state["app"], state["win"]

    def pump(n=20):
        for _ in range(n):
            app_.processEvents()

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
    r.check("open the demo in the window", open_demo)

    def perspectives():
        from aurora.app import mode
        from aurora.app.main import PERSPECTIVES
        mode.set_advanced(True)
        out = []
        for key, _title in PERSPECTIVES:
            win.show_perspective(key)
            pump(10)
            n = _painted(win.pages[key])
            assert n > 3, f"{key} looks blank ({n} colours)"
            out.append(f"{key}:{n}")
        return " ".join(out)
    r.check("every perspective paints", perspectives)

    def plots_paint():
        from aurora.core import testdata
        sid = win.session.stack_id(ctx["proj"].find(testdata.STACK))
        win.session.stack(sid)
        win.session.touch()
        win.session.select_stack(sid)
        zf = ctx["zones"]
        win.session.zones.holders.update(zf.holders) if hasattr(win.session.zones, "holders") else None
        win.show_perspective("reduce")
        pump(20)
        n = _painted(win.reduce)
        assert n > 20, f"Reduce shows almost nothing ({n} colours): plots not painted"
        return f"Reduce {n} colours"
    r.check("plots and maps paint (PySide6 >= 6.10)", plots_paint)

    def themes():
        from aurora.app.theme import apply_theme, scheme
        out = []
        for sch in ("dark", "light"):
            apply_theme(app_, appearance=sch)
            pump(5)
            assert scheme() == sch
            out.append(f"{sch}:{_painted(win)}")
        return " ".join(out)
    r.check("light and dark themes", themes)

    def modes():
        from aurora.app import mode
        from aurora.app.main import ADVANCED_PERSPECTIVES
        mode.set_advanced(False)
        pump(5)
        hidden = [k for k in ADVANCED_PERSPECTIVES if win.rail_buttons[k].isHidden()]
        assert hidden == sorted(ADVANCED_PERSPECTIVES) or set(hidden) == set(ADVANCED_PERSPECTIVES)
        mode.set_advanced(True)
        pump(5)
        assert not any(win.rail_buttons[k].isHidden() for k in ADVANCED_PERSPECTIVES)
        return f"Easy hides {sorted(ADVANCED_PERSPECTIVES)}"
    r.check("Easy and Advanced modes", modes)

    def manuals():
        from aurora.app.help import DOCS
        out = []
        for name, _t, _tip in DOCS:
            win.show_help(name)
            pump(5)
            text = win._help.browser.toPlainText()
            assert len(text) > 500, name
            out.append(f"{name}:{len(text)}")
        win._help.close()
        return " ".join(out)
    r.check("manuals in the Help menu", manuals)

    def dialogs():
        from aurora.app.about import AboutDialog
        from aurora.app.dialogs import ReferencesDialog
        from aurora.app.settings_dialog import SettingsDialog
        made = []
        for cls, args in ((AboutDialog, (win,)), (SettingsDialog, (win,)), (ReferencesDialog, (str(ctx.get("ref_folder", base)),))):
            d = cls(*args)
            d.show(); pump(3); d.close(); d.deleteLater()
            made.append(cls.__name__)
        return ", ".join(made)
    r.check("dialogs", dialogs)

    def close():
        from aurora.app.main import shutdown_app
        win.session._save_timer.stop() if hasattr(win.session, "_save_timer") else None
        shutdown_app(app_, win)
        return "closed"
    r.check("close and shut down", close)
