"""Command line: `aurora` (GUI), `aurora gui <project>` (also `aurora <project folder or .h5>`), `aurora project <path>`,
`aurora scan <path>`, `aurora report <project>`, `aurora demo <folder>`, `aurora --distcheck`, `aurora --selftest`.

The headless commands never import Qt's GUI modules, so `aurora.core` keeps working without a display (`aurora report`
reads the Settings author through QtCore when PySide6 is installed, which needs no display). Every command logs to the
AURORA log file (aurora.logsetup); a headless command that fails returns 1, so neither the console twin
aurora-cli.exe nor the windowed AURORA.exe ever stops on an error window. A user error (a path that is no beamtime or
scan, a missing zones file, a malformed file) is one line on stderr, its traceback in the log only; any other failure
writes its traceback to stderr too. Output is UTF-8 whatever the console or pipe.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
import traceback
from pathlib import Path

from aurora import APP_NAME, __version__


def _utf8_streams() -> None:
    """Print UTF-8 whatever the console or pipe: a cp1252 pipe (Windows CI, a redirected aurora-cli.exe) raised
    UnicodeEncodeError on 'Kα'. The windowed frozen app has no streams at all (None)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


log = logging.getLogger("aurora.cli")
FILE_ONLY = "aurora_file_only"            # LogRecord attribute: written to the log file, not echoed on the console
# what a wrong path or a malformed file raises: one line for the user, the traceback in the log only
USER_ERRORS = (FileNotFoundError, NotADirectoryError, IsADirectoryError, PermissionError, ValueError)


def user_message(err: BaseException) -> str:
    """The line printed for a user error: an OSError as 'reason: path' (not its repr with doubled backslashes)."""
    if isinstance(err, OSError) and err.filename is not None and err.strerror:
        return f"{err.strerror}: {err.filename}"
    return str(err) or type(err).__name__


def _console_logging(verbose: bool) -> logging.Handler | None:
    """Log messages on stdout (warnings, or everything with -v), only when there is a stdout: the windowed frozen app
    has none. Replaces the handler of an earlier main() call in the same process (its stream may be gone)."""
    root = logging.getLogger()
    for h in [h for h in root.handlers if getattr(h, "_aurora_console", False)]:
        root.removeHandler(h)
    if sys.stdout is None:
        return None
    h = logging.StreamHandler(sys.stdout)
    h._aurora_console = True
    h.setFormatter(logging.Formatter("%(message)s"))
    h.setLevel(logging.INFO if verbose else logging.WARNING)
    h.addFilter(lambda rec: not getattr(rec, FILE_ONLY, False))
    root.addHandler(h)
    if verbose and (root.level > logging.INFO or root.level == logging.NOTSET):
        root.setLevel(logging.INFO)
    return h


def _drop_console(h: logging.Handler | None) -> None:
    if h is not None:
        logging.getLogger().removeHandler(h)


def _logging(verbose: bool) -> logging.Handler | None:
    """The log file for every command, plus the console when there is one."""
    from aurora.logsetup import setup_logging
    try:
        setup_logging()
    except Exception:                         # noqa: BLE001  a log folder that cannot be made never stops a command
        pass
    return _console_logging(verbose)


def _guarded(name: str, fn, *args) -> int:
    """Run a headless command: its return code, or 1 with the traceback in the log and on stderr when it raises."""
    try:
        rc = fn(*args)
        return int(rc or 0)
    except KeyboardInterrupt:
        log.warning("aurora %s interrupted", name)
        return 130
    except Exception as err:                  # noqa: BLE001  every failure ends as exit code 1, never a hang
        tb = traceback.format_exc()
        log.error("aurora %s failed: %s: %s\n%s", name, type(err).__name__, err, tb, extra={FILE_ONLY: True})
        if isinstance(err, USER_ERRORS):
            from aurora.logsetup import log_file
            text = f"aurora {name}: {user_message(err)}\n  (details in {log_file()})\n"
        else:
            text = f"aurora {name} failed: {type(err).__name__}: {err}\n{tb}"
        if sys.stderr is not None:
            try:
                sys.stderr.write(text)
                sys.stderr.flush()
            except (OSError, ValueError):
                pass
        return 1


def cmd_project(path: Path) -> int:
    from aurora.core.model import Project
    proj = Project.discover(path)
    print(f"project {proj.root}\nraw     {proj.raw}\n{proj.calibration.describe() if proj.calibration else ''}")
    if proj.roi_bins and proj.roi_bins.labels:
        print("fixed ROI bins: " + ", ".join(proj.roi_bins.labels))
    for day in proj.days:
        print(f"\n{day.date or day.path.name}: {len(day.holders)} holders, {len(day.scans)} scans"
              + ("  (sgm_report summary present)" if day.summary else ""))
        for h in day.holders:
            print(f"  holder {h.name}: {len(h.stacks)} complete, {len(h.dropped)} dropped")
            for s in h.scans:
                flags = ", ".join(s.flags)
                print(f"    {s.time:>5} {s.kind:6} {s.status:11} {s.edge_text:7} {s.energy_text:>18} {s.n_points:>5} px  "
                      f"{s.name}" + (f"  [{flags}]" if flags else "") + (f"  ! {s.error}" if s.error else ""))
        for s in day.others:
            print(f"    {s.time:>5} {s.kind:6} {s.status:11} {s.name}")
    return 0


def cmd_scan(path: Path) -> int:
    from aurora.core.io.raw import read_point_dir
    from aurora.core.model import NotAProjectError, ScanInfo
    path = Path(path)
    if path.suffix.lower() == ".h5" and path.is_file():
        path = path.parent                    # one scan's .h5: its scan folder, as `aurora <file>.h5` opens it
    if path.is_file():
        raise NotAProjectError(f"{path.name} is a file, not a scan folder (or a scan's .h5 file)")
    if not path.is_dir():
        raise FileNotFoundError(f"{path}: no such folder")
    s = ScanInfo.from_dir(path)
    if s.kind == "other":
        raise NotAProjectError(f"{path.name} is not a scan folder (a <HHMMSS>_<name>_survey or ..._stack folder); "
                               "`aurora project` lists the scans of a beamtime")
    print(f"{s.name}: kind {s.kind}, status {s.status} ({s.status_reason}), edge {s.edge_text or '-'}, "
          f"{s.n_energies} energies {s.energy_text}, {s.n_points} points, h5 {s.h5}")
    for k, v in sorted(s.md.items()):
        print(f"  {k} = {v[:120]}")
    if s.kind == "survey" and s.h5 is not None:
        pd = read_point_dir(path)
        print(f"  survey point data: mcc {pd.mcc.shape}, sdd {[a.shape for a in pd.sdd]}, complete {pd.complete}")
    elif s.energy_dirs:
        e, p = s.energy_dirs[0]
        pd = read_point_dir(p)
        print(f"  first energy {e} eV: mcc {pd.mcc.shape}, sdd {[a.shape for a in pd.sdd]}, complete {pd.complete}")
    return 0


REPORT_PRESETS = ("final", "draft")          # aurora.core.report.PRESETS (tests/test_cli_robust.py keeps them equal)


def settings_author() -> str:
    """Settings > report author, read through QtCore (no display needed); '' when unset or PySide6 is missing."""
    try:
        from aurora.app.settings import settings
    except ImportError:
        return ""
    try:
        return str(settings().value("report_author", "") or "").strip()
    except Exception:                         # noqa: BLE001  an unreadable settings store means no Settings value
        return ""


def report_author(given: str | None) -> tuple[str, str]:
    """(author, source) of a command-line report: --author when given ('' prints no author line), else Settings >
    report author, else the system account's full name (aurora.core.report.resolve_author), else no author line."""
    from aurora.core.report import resolve_author
    if given is not None:
        return given.strip(), "--author"
    return resolve_author(settings_author())


def cmd_report(args) -> int:
    from aurora.core.io.config import ZonesFile
    from aurora.core.model import Project
    from aurora.core.report import ReportBuilder, holder_keys
    proj = Project.discover(args.project)
    zpath = args.zones or (proj.root / ".aurora" / "session.json")
    if not Path(zpath).is_file():
        if args.zones:
            raise FileNotFoundError(f"the zones file {zpath} does not exist")
        raise FileNotFoundError(f"no zones yet in {proj.root.name} (.aurora/session.json is missing): draw zones in "
                                "AURORA's Reduce, or pass --zones <file>")
    zones = ZonesFile.load(zpath)
    out = args.out or (proj.root / "reports" / "aurora")
    author, source = report_author(args.author)
    builder = ReportBuilder(proj, zones, out, title=args.title, groups=args.groups or None, holders=args.holders,
                            days=args.days, preset=args.preset, author=author,
                            appendix=False if args.no_appendix else None, maps=False if args.no_maps else None,
                            png_dpi=args.dpi)
    if (args.holders or args.days) and not builder.scope_keys():
        known = ", ".join(sorted(set(holder_keys(proj)) | set(zones.holders))) or "none"
        asked = " ".join([*(args.holders or []), *(args.days or [])])
        sys.stderr.write(f"aurora report: no holder of {proj.root.name} matches {asked} (holders: {known})\n")
        return 1
    print(f"{args.preset} report, figures at {builder.png_dpi} dpi, "
          + (f"author {author} (from {source})" if author else "no author line"))
    res = builder.build(progress=lambda i, n, m: print(f"  {i + 1}/{n} {m}", end="\r"))
    print(f"\n{res.pdf}: {res.pages} pages, {res.entries} zone spectra, {len(res.csvs)} CSV, {len(res.figures)} figures")
    for s in res.skipped:
        print("  skipped:", s)
    return 0


def cmd_demo(folder: Path) -> int:
    """A synthetic beamtime with fixed ROI bins, a sample sheet, reduced spectra and references, for the tutorials
    (no zones: the tutorials draw them)."""
    import json
    from aurora.core import testdata
    root = testdata.make_fake_project(Path(folder))
    (root / "config" / "roi_bins.json").write_text(json.dumps({
        "_meta": {"gains": {"sdd1": 10.0, "sdd2": 10.0, "sdd3": 10.0, "sdd4": 10.0}, "n_sigma": 1.5,
                  "help": "line -> {sdd: [first channel, last channel + 1]}; the demo uses 10 eV per channel"},
        "F Kα": {"sdd1": [60, 74], "sdd2": [61, 75], "sdd3": [62, 76], "sdd4": [63, 77], "line_ev": 676.8},
        "O Kα": {"sdd1": [46, 58], "sdd2": [46, 58], "sdd3": [46, 58], "sdd4": [46, 58], "line_ev": 525.0}}, indent=1), encoding="utf-8")
    from aurora.core.names import holder_from_survey
    holder = holder_from_survey(testdata.SURVEY)       # the sheet's plate column must equal the holder name
    (root / "demo samples.csv").write_bytes(("measured demo,,,\n"
                                            "Paper,Final plate#,Sample group,Sample ID,Composition,F K edge,Other edges?,Notes\n"
                                            f",{holder},Minerals,1 alpha,CaF2,y,,demo mineral\n,{holder},Simple Glass,2 beta,NaF-glass,y,,demo glass\n").encode("cp1252"))
    extras = testdata.write_demo_extras(root)
    print(f"demo beamtime written to {root}\n  reduced spectra in {extras['reduced']}, references in {extras['references']}"
          f"\n  open it with:  aurora gui \"{root}\"")
    return 0


def cmd_distcheck() -> int:
    """Smoke test of a frozen build: build a synthetic project, open the window offscreen, read it back."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    tmp = Path(tempfile.mkdtemp(prefix="aurora_distcheck_"))
    os.environ["AURORA_SETTINGS_DIR"] = str(tmp / "settings")
    from aurora.core.model import Project
    from aurora.core.testdata import make_fake_project
    root = make_fake_project(tmp / "project")
    proj = Project.discover(root)
    assert len(proj.scans) == 4, proj.scans
    statuses = sorted(s.status for s in proj.scans)
    assert statuses == ["aborted", "complete", "survey", "test"], statuses
    from aurora.app.main import MainWindow, create_app, shutdown_app
    app = create_app([APP_NAME])
    win = MainWindow()
    win.home.set_project(proj)
    app.processEvents()
    assert win.home.tree.topLevelItemCount() == 1
    from aurora.app.about import versions_text
    size = f"{win.width()}x{win.height()}"
    shutdown_app(app, win)
    print(versions_text())
    print(f"distcheck ok: {len(proj.scans)} scans, statuses {statuses}, window {size}")
    return 0


COMMANDS = ("gui", "project", "scan", "report", "demo")


def _as_gui_path(argv: list[str]) -> list[str]:
    """`aurora <folder>` or `aurora <file>.h5` (a Finder / Explorer 'open with', a desktop shortcut) means
    `aurora gui <path>`: a first argument that is no command or option and names an existing folder or an .h5 file."""
    if argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        p = Path(argv[0])
        if p.suffix.lower() == ".h5" or p.is_dir():
            return ["gui", *argv]
    return argv


def build_parser() -> argparse.ArgumentParser:
    """The command line; every option and argument carries its help text (tests/test_cli_robust.py checks it)."""
    p = argparse.ArgumentParser(
        prog="aurora", formatter_class=argparse.RawDescriptionHelpFormatter,
        description=f"{APP_NAME} {__version__}: CLS SGM XAS and XRF map analysis\n(version 2 of John Bussey's SGM "
                    "XAS GUI, based on James J. Dynes' notebooks)",
        epilog="examples:\n"
               "  aurora                          the application\n"
               "  aurora <beamtime folder>        the application on that beamtime (also a scan's .h5 file)\n"
               "  aurora demo <folder>            write the synthetic demo beamtime of the tutorials\n"
               "  aurora project <beamtime>       list its days, holders and scans\n"
               "  aurora report <beamtime>        build the report from the zones saved in AURORA\n"
               "  aurora <command> --help         the options of one command")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}",
                   help="print the version and exit")
    p.add_argument("--distcheck", action="store_true", help="smoke-test this installation offscreen and exit")
    p.add_argument("--selftest", action="store_true",
                   help="deep self-test of this installation (every core path and perspective); the exit code is the "
                        "number of failed checks")
    p.add_argument("--out", type=Path, help="with --selftest: write the JSON report here")
    p.add_argument("--platform", choices=["offscreen", "native"], help="with --selftest: Qt platform (default offscreen)")
    p.add_argument("--keep", action="store_true", help="with --selftest: keep the temporary folder")
    p.add_argument("--no-gui", action="store_true", help="with --selftest: core checks only")
    p.add_argument("-v", "--verbose", action="store_true", help="print the log messages too, not only the warnings")
    sub = p.add_subparsers(dest="cmd", metavar="{gui,project,scan,report,demo}",
                           help="a command (default: gui, which also opens a folder or .h5 given alone)")
    g = sub.add_parser("gui", help="open the application (default)")
    g.add_argument("project", nargs="?", type=Path, help="a beamtime folder, a loose folder of scans or a scan's .h5 file")
    s = sub.add_parser("project", help="list the days, holders and scans of a beamtime folder")
    s.add_argument("path", type=Path, help="the beamtime folder (or a loose folder of scan folders)")
    s = sub.add_parser("scan", help="describe one scan folder and read its first energy")
    s.add_argument("path", type=Path, help="the scan folder")
    r = sub.add_parser("report", help="build the group-organised report from a zones file (headless)")
    r.add_argument("project", type=Path, help="the beamtime folder")
    r.add_argument("--zones", type=Path, help="a zones file (zones_all.json of the browser zone tool, or Survey > "
                                              "Export zones…; default: the session AURORA saved, "
                                              "<project>/.aurora/session.json)")
    r.add_argument("--out", type=Path, help="output folder (default: <project>/reports/aurora)")
    r.add_argument("--title", help="the title on the cover (default: 'AURORA report · <project>')")
    r.add_argument("--groups", nargs="*", help="only these sample groups")
    r.add_argument("--holders", nargs="+", metavar="HOLDER",
                   help="only these holders, by name or key (for example N5 N6; default: every holder)")
    r.add_argument("--days", nargs="+", metavar="YYYY-MM-DD", help="only the holders of these days")
    r.add_argument("--preset", choices=REPORT_PRESETS, default="final",
                   help="final: Appendix A and B, figures at 600 dpi (default); draft: no Appendix A or B, 110 dpi")
    r.add_argument("--author", help="the author named on the cover, footers and PDF metadata; '' for none (default: "
                                    "Settings > report author, else the system account's full name)")
    r.add_argument("--no-appendix", action="store_true",
                   help="leave out Appendix A, the four detectors per zone (Appendix C is always printed)")
    r.add_argument("--no-maps", action="store_true", help="leave out Appendix B, the plate maps with the zones")
    r.add_argument("--dpi", type=int, help="resolution of the figure PNGs (default: the preset's, 600 final, 110 draft)")
    dm = sub.add_parser("demo", help="create a small synthetic beamtime folder (the tutorials use it)")
    dm.add_argument("folder", type=Path, help="where to write the demo beamtime")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = _as_gui_path(list(sys.argv[1:] if argv is None else argv))
    p = build_parser()
    _utf8_streams()
    args = p.parse_args(argv)
    console = _logging(args.verbose)
    name = "--distcheck" if args.distcheck else "--selftest" if args.selftest else (args.cmd or "gui")
    log.info("command %s %s", name, " ".join(argv), extra={FILE_ONLY: True})
    try:
        if args.distcheck:
            return _guarded(name, cmd_distcheck)
        if args.selftest:
            return _guarded(name, _selftest, args)
        if args.cmd == "project":
            return _guarded(name, cmd_project, args.path)
        if args.cmd == "scan":
            return _guarded(name, cmd_scan, args.path)
        if args.cmd == "report":
            return _guarded(name, cmd_report, args)
        if args.cmd == "demo":
            return _guarded(name, cmd_demo, args.folder)
        from aurora.app.main import run
        try:
            return run(getattr(args, "project", None))
        except Exception:
            # the GUI keeps its own error window (the frozen bootloader shows the traceback); the log gets it too
            log.exception("aurora gui failed", extra={FILE_ONLY: True})
            raise
    finally:
        _drop_console(console)


def _selftest(args) -> int:
    from aurora.selftest import run as selftest
    native = args.platform == "native"
    if native:
        os.environ.pop("QT_QPA_PLATFORM", None)
    return selftest(out=args.out, qpa=None if native else "offscreen", keep=args.keep, gui=not args.no_gui,
                    native=native)


if __name__ == "__main__":
    raise SystemExit(main())
