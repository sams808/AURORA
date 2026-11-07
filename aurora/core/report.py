"""The final report: a multi-page PDF organised by sample group, built from the manual zones and their per-scan
settings (docs/HANDOFF.md section 6), plus one CSV per zone and scan and the group figures as PNG + SVG.

Headless (matplotlib Agg); the Report perspective and `aurora report` both call ReportBuilder.
Defaults still to be confirmed are marked DEFAULT in the code.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402

from aurora import APP_NAME, __version__  # noqa: E402
from aurora.core.constants import DETS  # noqa: E402
from aurora.core.io.config import ScanSettings, Zone, ZonesFile  # noqa: E402
from aurora.core.model import Project, ScanInfo  # noqa: E402
from aurora.core.names import pretty_edge  # noqa: E402
from aurora.core.stack import StackData  # noqa: E402
from aurora.core.survey import SurveyData  # noqa: E402
from aurora.core.zones import ZoneTraces, good_i0_curve, members, zone_traces  # noqa: E402

log = logging.getLogger("aurora.report")

AUTHOR = "Sami Soudani"
CREDIT = ("Built with AURORA, which builds on the SGM XAS GUI by John Bussey (WSU), the SGM_Beamline_Analysis toolkit "
          "by James J. Dynes and the SGM beamline staff (CLS) and the sgm_report pipeline by Sami Soudani (WSU). "
          "Line and edge energies from xraydb.")
# DEFAULT: the sample-sheet groups in the order of the handoff; unknown groups follow alphabetically, blank last
GROUP_ORDER = ["Minerals", "Group 7", "RE-(O)-F", "Se", "Simple Glass", "Lab beakers",
               "Standards", "Misc",
               "Oxyfluoride Glass", "Microfocus"]
UNGROUPED = "Ungrouped"
# Okabe-Ito, colour never alone: the line style cycles too
PAL = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#F0E442", "#000000"]
STYLES = ["-", "--", "-.", ":"]
TRACES_PER_PAGE = 8          # DEFAULT
PAGE = (8.27, 11.69)         # A4 portrait, inches
NORM_TEXT = {"raw": "raw counts", "i0": "divided by I0", "i0pre": "divided by I0, pre-edge line removed",
             "i0jump": "divided by I0, pre-edge line removed, edge jump = 1", "area": "divided by I0, area = 1",
             "max": "divided by I0, maximum = 1"}


def slug(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s.replace("α", "a").replace("β", "b")).strip("_")
    return s or "x"


@dataclass
class Entry:
    """One zone on one included scan: everything the report needs."""
    holder_key: str
    holder: str
    day: str
    zone: Zone
    sid: str
    scan: ScanInfo
    stack: StackData
    settings: ScanSettings
    traces: ZoneTraces
    group: str
    flags: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.zone.label

    @property
    def edge_key(self) -> str:
        return pretty_edge(self.stack.edge) or "unknown edge"

    @property
    def title(self) -> str:
        return f"{self.zone.label} · {self.holder} · {self.scan.time} {self.edge_key}"

    @property
    def spikes_removed(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.traces.spikes.items() if isinstance(v, list) and v}


@dataclass
class ReportResult:
    pdf: Path
    pages: int
    figures: list[Path]
    csvs: list[Path]
    entries: int
    skipped: list[str]
    manifest: Path
    page_pngs: list[Path] = field(default_factory=list)


class ReportBuilder:
    def __init__(self, project: Project, zones: ZonesFile, out_dir: Path, title: str | None = None,
                 groups: list[str] | None = None, author: str = AUTHOR, stacks: dict[str, StackData] | None = None,
                 appendix: bool = True, maps: bool = True, png_dpi: int = 600, page_png_dpi: int = 110):
        self.project, self.zones, self.out = project, zones, Path(out_dir)
        self.title = title or f"{APP_NAME} report · {zones.project or project.root.name}"
        self.groups = groups
        self.author = author
        self._stacks: dict[str, StackData] = dict(stacks or {})
        self.appendix, self.maps, self.png_dpi, self.page_png_dpi = appendix, maps, png_dpi, page_png_dpi
        self.skipped: list[str] = []
        self.not_included: list[tuple[str, str, str]] = []
        self.page_pngs: list[Path] = []
        self._page_no = 0

    # ------------------------------------------------------------------ data
    def stack(self, sid: str) -> StackData | None:
        if sid not in self._stacks:
            scan = self.project.find(sid[11:])
            if scan is None:
                self._skip(f"{sid}: scan not in the project")
                return None
            try:
                self._stacks[sid] = StackData.load(self.project, scan)
            except Exception as err:
                self._skip(f"{sid}: {err}")
                return None
        return self._stacks[sid]

    def _skip(self, msg: str) -> None:
        if msg not in self.skipped:
            self.skipped.append(msg)

    def good_curve(self, E: np.ndarray) -> np.ndarray | None:
        curves = []
        for sid, good in self.zones.good_i0.items():
            if good:
                sd = self.stack(sid)
                if sd is not None:
                    curves.append((sd.E, sd.i0_mean()))
        return good_i0_curve(E, curves) if curves else None

    def collect(self, progress=None) -> list[Entry]:
        entries: list[Entry] = []
        items = [(hk, hz, z, sid, st) for hk, hz in self.zones.holders.items() for z in hz.zones for sid, st in z.settings.items()]
        for i, (hk, hz, z, sid, st) in enumerate(items):
            if progress:
                progress(i, len(items), f"{hz.holder} {z.label} {sid[11:16]}")
            group = z.group.strip() or UNGROUPED
            if self.groups and group not in self.groups:
                continue
            scan = self.project.find(sid[11:])
            if not st.include:
                self.not_included.append((hz.holder, z.label, sid[11:]))
                continue
            sd = self.stack(sid)
            if sd is None or scan is None:
                continue
            pix = members(z, sd.x, sd.y, sd.pitch)
            if pix.size == 0:
                self.skipped.append(f"{hz.holder} {z.label} {sid[11:]}: no pixel inside the zone")
                continue
            good = self.good_curve(sd.E) if st.i0_source == "good" else None
            t = zone_traces(sd, pix, st, good)
            if t is None:
                continue
            flags = []
            if scan.summary.get("sdd_saturated"):
                flags.append("SDD saturated: " + ", ".join(scan.summary["sdd_saturated"]).upper())
            if scan.summary.get("i0_bad"):
                flags.append("I0 invalid on this scan")
            if st.i0_source == "good" and good is None:
                flags.append("good-I0 requested but no good scan ticked: own I0 used")
            entries.append(Entry(holder_key=hk, holder=hz.holder, day=hz.day, zone=z, sid=sid, scan=scan, stack=sd,
                                 settings=st, traces=t, group=group, flags=flags))
        log.info("report: %d zone-scan entries, %d skipped, %d not included", len(entries), len(self.skipped), len(self.not_included))
        return entries

    @staticmethod
    def group_sort_key(g: str):
        return (GROUP_ORDER.index(g) if g in GROUP_ORDER else len(GROUP_ORDER) + (1 if g == UNGROUPED else 0), g.lower())

    # ------------------------------------------------------------------ build
    def build(self, progress=None) -> ReportResult:
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "figures").mkdir(exist_ok=True)
        (self.out / "csv").mkdir(exist_ok=True)
        entries = self.collect(progress)
        by_group: dict[str, list[Entry]] = {}
        for e in entries:
            by_group.setdefault(e.group, []).append(e)
        groups = sorted(by_group, key=self.group_sort_key)
        date = dt.date.today().isoformat()
        pdf_path = self.out / f"{slug(self.title)}_{date}.pdf"
        figures: list[Path] = []
        csvs: list[Path] = []
        n_pages = 0
        with PdfPages(pdf_path) as pdf:
            n_pages += self._cover(pdf, entries, groups, by_group)
            for gi, g in enumerate(groups):
                if progress:
                    progress(gi, len(groups), f"group {g}")
                n_pages += self._group_pages(pdf, g, by_group[g], figures)
            if self.appendix and entries:
                n_pages += self._sdd_appendix(pdf, entries)
            if self.maps:
                n_pages += self._plate_maps(pdf, entries)
            n_pages += self._qc_pages(pdf, entries)
            for e in entries:
                csvs.append(self._write_csv(e))
            self._stamp_all(pdf_path, n_pages)
        manifest = self.out / "report_manifest.json"
        manifest.write_text(json.dumps({
            "title": self.title, "built": dt.datetime.now().isoformat(timespec="seconds"), "aurora": __version__,
            "project": str(self.project.root), "zones_exported": self.zones.exported, "pdf": pdf_path.name,
            "pages": n_pages, "groups": {g: [e.title for e in by_group[g]] for g in groups},
            "skipped": self.skipped, "not_included": self.not_included, "figures": [f.name for f in figures],
            "csv": [str(c.relative_to(self.out)) for c in csvs]}, indent=1, ensure_ascii=False), encoding="utf-8")
        log.info("report written: %s (%d pages), %d figures, %d csv", pdf_path, n_pages, len(figures), len(csvs))
        return ReportResult(pdf=pdf_path, pages=n_pages, figures=figures, csvs=csvs, entries=len(entries),
                            skipped=self.skipped, manifest=manifest, page_pngs=list(self.page_pngs))

    # ------------------------------------------------------------------ pages
    def _footer(self, fig, page_text: str = "") -> None:
        fig.text(0.05, 0.015, f"{APP_NAME} {__version__} · {self.zones.project or self.project.root.name} · {self.author} · "
                 f"{dt.date.today().isoformat()}", fontsize=7, color="#5B6B8C")
        if page_text:
            fig.text(0.95, 0.015, page_text, fontsize=7, color="#5B6B8C", ha="right")

    def _save_page(self, pdf: PdfPages, fig) -> int:
        pdf.savefig(fig)
        self._page_no += 1
        if self.page_png_dpi:
            p = self.out / "pages" / f"page_{self._page_no:03d}.png"
            p.parent.mkdir(exist_ok=True)
            fig.savefig(p, dpi=self.page_png_dpi)
            self.page_pngs.append(p)
        plt.close(fig)
        return 1

    @staticmethod
    def _last_finite(y: np.ndarray) -> float:
        fin = y[np.isfinite(y)]
        return float(np.mean(fin[-5:])) if fin.size else 0.0

    def _cover(self, pdf, entries, groups, by_group) -> int:
        fig = plt.figure(figsize=PAGE)
        fig.text(0.08, 0.90, self.title, fontsize=20, weight="bold", color="#15213B")
        proj = self.project
        days = sorted({e.day for e in entries}) or sorted(d.date for d in proj.days)
        holders = sorted({e.holder for e in entries})
        lines = [
            f"Beamtime project {self.zones.project or proj.root.name}, SGM beamline (ambxas endstation), Canadian Light Source",
            f"Days: {', '.join(days)}",
            f"Holders: {', '.join(holders)}",
            f"{len(entries)} zone spectra in {len(groups)} sample groups; {len(self.not_included)} scans marked not included; "
            f"{len(self.skipped)} skipped (see QC pages)",
            "",
            "Provenance",
            f"  zones file exported {self.zones.exported or '?'} ({self.zones.built and 'tool build ' + self.zones.built})",
            f"  {proj.calibration.describe() if proj.calibration else 'no SDD calibration'}",
            "  fixed ROI windows: " + (", ".join(proj.roi_bins.describe(l) for l in proj.roi_bins.labels[:6]) if proj.roi_bins and proj.roi_bins.labels else "none")
            + (" …" if proj.roi_bins and len(proj.roi_bins.labels) > 6 else ""),
            f"  {APP_NAME} {__version__}, report built {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "How to read the figures",
            "  Fluorescence = partial fluorescence yield of the line named in each panel, summed over the zone pixels",
            "  and the ticked silicon drift detectors, normalised as stated in the caption (default: divided by I0,",
            "  pre-edge line removed, edge jump = 1). TEY = total electron yield, same normalisation. Traces are offset.",
            "  Spikes (single-point outliers from loose electrons) were interpolated; the counts are in the QC pages.",
            "  Colour never carries the meaning alone: the line style and the label at the right end identify each trace.",
            "",
            "Contents",
        ] + [f"  {g}: {len(by_group[g])} spectra" for g in groups] + [
            "  Appendix A: the four detectors per zone" if self.appendix else "",
            "  Appendix B: plate maps with the zones" if self.maps else "",
            "  Appendix C: quality notes, scans not included, skipped",
        ]
        y, x = 0.85, 0.08
        for ln in lines:
            if not ln:
                y -= 0.012
                continue
            if y < 0.11 and x == 0.08:                    # second column for a long contents list
                x, y = 0.52, 0.85 - 0.022 * (lines.index("Contents") + 1)
            fig.text(x, y, ln[:110], fontsize=9 if not ln.startswith("  ") else 8.5,
                     weight="bold" if ln in ("Provenance", "How to read the figures", "Contents") else "normal",
                     family="DejaVu Sans", color="#15213B")
            y -= 0.022
        fig.text(0.08, 0.075, CREDIT[:150] + "\n" + CREDIT[150:], fontsize=7, color="#5B6B8C")
        self._footer(fig, "page 1")
        return self._save_page(pdf, fig)

    def _group_pages(self, pdf, group: str, entries: list[Entry], figures: list[Path]) -> int:
        n = 0
        # DEFAULT: within a group, one figure per edge type, at most TRACES_PER_PAGE traces per page
        by_edge: dict[str, list[Entry]] = {}
        for e in sorted(entries, key=lambda e: (e.day, e.holder, e.zone.id, e.scan.name)):
            by_edge.setdefault(e.edge_key, []).append(e)
        for edge, items in by_edge.items():
            for p0 in range(0, len(items), TRACES_PER_PAGE):
                chunk = items[p0:p0 + TRACES_PER_PAGE]
                fig = self._waterfall_figure(group, edge, chunk, p0 // TRACES_PER_PAGE + 1, (len(items) - 1) // TRACES_PER_PAGE + 1)
                name = slug(f"{group}_{edge}_p{p0 // TRACES_PER_PAGE + 1}")
                for ext in ("png", "svg"):
                    fp = self.out / "figures" / f"{name}.{ext}"
                    fig.savefig(fp, dpi=self.png_dpi if ext == "png" else None)
                    if ext == "png":
                        figures.append(fp)
                n += self._save_page(pdf, fig)
        return n

    def _waterfall_figure(self, group: str, edge: str, items: list[Entry], page: int, pages: int):
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=PAGE, gridspec_kw={"height_ratios": [3, 2], "hspace": 0.28},
                                       constrained_layout=False)
        fig.subplots_adjust(left=0.1, right=0.78, top=0.9, bottom=0.07 + 0.0125 * len(items))     # room for the captions
        fig.suptitle(f"{group} · {edge} · {len(items)} spectra" + (f" (page {page} of {pages})" if pages > 1 else ""),
                     fontsize=12, weight="bold", x=0.1, ha="left", color="#15213B")
        off1 = off2 = 0.0
        gap1 = gap2 = 0.0
        captions = []
        e_min = min(float(e.traces.E[0]) for e in items); e_max = max(float(e.traces.E[-1]) for e in items)
        for i, e in enumerate(items):
            t = e.traces
            col, ls = PAL[i % len(PAL)], STYLES[(i // len(PAL)) % len(STYLES)] if i >= len(PAL) else STYLES[i % len(STYLES)]
            if t.sum is not None:
                y = t.sum + off1
                ax1.plot(t.E, y, color=col, ls=ls, lw=1.1)
                ax1.text(e_max + 0.01 * (e_max - e_min), self._last_finite(y), e.title, fontsize=6.5, color=col, va="center")
                gap1 = max(np.nanmax(t.sum) - np.nanmin(t.sum), 1e-9) * 0.35 if e.settings.fluo_norm != "raw" else np.nanmax(t.sum) * 0.35
                off1 += (np.nanmax(t.sum) - np.nanmin(t.sum)) * 0.6 + gap1
            if e.settings.tey:
                y2 = t.tey + off2
                ax2.plot(t.E, y2, color=col, ls=ls, lw=1.0)
                ax2.text(e_max + 0.01 * (e_max - e_min), self._last_finite(y2), e.zone.label, fontsize=6.5, color=col, va="center")
                rng = np.nanmax(t.tey) - np.nanmin(t.tey)
                off2 += (rng if np.isfinite(rng) else 1.0) * 0.6 + 0.35 * (rng if np.isfinite(rng) and rng > 0 else 1.0)
            sdd_txt = "".join(str(k + 1) for k in range(4) if e.settings.sdd[k]) or "none (TEY only)"
            captions.append(f"{e.zone.label}: {t.n_pix} px, SDD {sdd_txt}, "
                            f"{NORM_TEXT.get(e.settings.fluo_norm, e.settings.fluo_norm)}"
                            + (", I0 = good-I0 mean" if e.settings.i0_source == "good" else "")
                            + (f", spikes {sum(e.spikes_removed.values())}" if e.spikes_removed else "")
                            + (f"; {'; '.join(e.flags)}" if e.flags else ""))
            if e.stack.edge_ev and e_min < e.stack.edge_ev < e_max:
                for ax in (ax1, ax2):
                    ax.axvline(e.stack.edge_ev, color="#999999", lw=0.6, ls=":")
        ax1.set_ylabel("fluorescence (offset)"); ax2.set_ylabel("TEY (offset)")
        ax2.set_xlabel("incident energy (eV)")
        ax1.set_xlim(e_min, e_max); ax2.set_xlim(e_min, e_max)
        for ax in (ax1, ax2):
            ax.tick_params(labelsize=8); ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
        fig.text(0.1, 0.03, "\n".join(c[:160] for c in captions), fontsize=5.8, color="#3A4866", va="bottom")
        self._footer(fig)
        return fig

    def _sdd_appendix(self, pdf, entries: list[Entry]) -> int:
        n = 0
        per_page = 6
        for p0 in range(0, len(entries), per_page):
            chunk = entries[p0:p0 + per_page]
            fig, axes = plt.subplots(3, 2, figsize=PAGE)
            fig.subplots_adjust(left=0.1, right=0.97, top=0.92, bottom=0.07, hspace=0.45, wspace=0.3)
            fig.suptitle(f"Appendix A · the four detectors per zone ({p0 + 1}-{p0 + len(chunk)} of {len(entries)})",
                         fontsize=11, weight="bold", x=0.1, ha="left", color="#15213B")
            for ax, e in zip(axes.flat, chunk):
                t = e.traces
                for k in range(4):
                    y = t.det[k]
                    if y is None:
                        continue
                    on = e.settings.sdd[k]
                    ax.plot(t.E, y, color=PAL[k], ls=STYLES[k], lw=0.8, alpha=1.0 if on else 0.35, label=f"SDD{k + 1}" + ("" if on else " (off)"))
                if t.sum is not None:
                    ax.plot(t.E, t.sum, color="black", lw=1.4, label="sum of ticked")
                ax.set_title(e.title, fontsize=7)
                ax.tick_params(labelsize=6)
                ax.set_xlabel("E (eV)", fontsize=7); ax.set_ylabel("normalised", fontsize=7)
                ax.legend(fontsize=5.5, frameon=False)
            for ax in axes.flat[len(chunk):]:
                ax.axis("off")
            self._footer(fig)
            n += self._save_page(pdf, fig)
        return n

    def _plate_maps(self, pdf, entries: list[Entry]) -> int:
        n = 0
        holders = {}
        for e in entries:
            holders.setdefault(e.holder_key, []).append(e)
        keys = sorted(holders, key=lambda k: (holders[k][0].day, k))
        for p0 in range(0, len(keys), 2):
            fig, axes = plt.subplots(2, 1, figsize=PAGE)
            fig.subplots_adjust(left=0.08, right=0.97, top=0.93, bottom=0.06, hspace=0.3)
            fig.suptitle("Appendix B · plate maps with the zones", fontsize=11, weight="bold", x=0.08, ha="left", color="#15213B")
            for ax, key in zip(axes, keys[p0:p0 + 2]):
                es = holders[key]
                hz = self.zones.holders.get(key)
                surv = self.project.find(hz.survey) if hz and hz.survey else None
                ax.set_title(f"holder {es[0].holder} · {es[0].day}" + (f" · survey {surv.name}" if surv else ""), fontsize=8)
                if surv is not None:
                    try:
                        sv = SurveyData.load(surv, self.project.roi_bins)
                        ch = next((l for l in sv.lines if l == es[0].stack.main), "total")
                        g = sv.grid(sv.values(ch, per_i0=True, log10=True))
                        dx, dy = g.dx, g.dy
                        ax.pcolormesh(np.r_[g.xs - dx / 2, g.xs[-1] + dx / 2], np.r_[g.ys - dy / 2, g.ys[-1] + dy / 2], g.img,
                                      cmap="viridis", shading="flat", rasterized=True)
                        ax.set_aspect("equal")
                    except Exception as err:
                        ax.text(0.02, 0.95, f"survey unreadable: {err}", transform=ax.transAxes, fontsize=7, va="top")
                seen = set()
                for i, e in enumerate(es):
                    z = e.zone
                    if z.id in seen:
                        continue
                    seen.add(z.id)
                    col = PAL[i % len(PAL)]
                    for r in z.rects:
                        ax.add_patch(plt.Rectangle((r[0], r[2]), r[1] - r[0], r[3] - r[2], fill=False, ec=col, lw=0.9))
                    for s in z.paint:
                        ax.add_patch(plt.Rectangle((s[0] - s[2] / 2, s[1] - s[3] / 2), s[2], s[3], fill=False, ec=col, lw=0.6))
                    pts = [(r[0], r[3]) for r in z.rects] + [(s[0], s[1]) for s in z.paint]
                    if pts:
                        x, y = pts[0]
                        ax.text(x, y, z.label, fontsize=5, color=col, va="bottom")
                ax.set_xlabel("x (mm)", fontsize=7); ax.set_ylabel("y (mm)", fontsize=7); ax.tick_params(labelsize=6)
            for ax in axes[len(keys[p0:p0 + 2]):]:
                ax.axis("off")
            self._footer(fig)
            n += self._save_page(pdf, fig)
        return n

    def _qc_pages(self, pdf, entries: list[Entry]) -> int:
        rows = []
        for e in entries:
            sp = ", ".join(f"{k} {v}" for k, v in e.spikes_removed.items())
            flags = "; ".join(e.flags).replace("I0 invalid on this scan", "I0 invalid").replace(
                "good-I0 requested but no good scan ticked: own I0 used", "no good I0 ticked")
            rows.append(f"{e.holder:>5} {e.zone.label[:24]:24} {e.scan.time} {e.scan.label[:20]:20} {e.edge_key:7} "
                        f"{e.traces.n_pix:4d}px SDD{''.join(str(k + 1) for k in range(4) if e.settings.sdd[k]) or '-':4} "
                        f"{e.settings.fluo_norm:6} {('spk ' + sp) if sp else '':24.24} {flags[:40]}")
        lines = ["Appendix C · quality notes", "", "Zone spectra included (holder, zone, scan, edge, pixels, detectors, "
                 "normalisation, spikes interpolated, flags):"] + rows
        if self.not_included:
            lines += ["", "Scans marked NOT included in the zone tool (kept out of the figures and CSVs):"]
            lines += [f"  {h}  {z}  {s}" for h, z, s in self.not_included]
        if self.skipped:
            lines += ["", "Skipped:"] + [f"  {s}" for s in self.skipped]
        n = 0
        per_page = 62
        for p0 in range(0, len(lines), per_page):
            fig = plt.figure(figsize=PAGE)
            y = 0.95
            for ln in lines[p0:p0 + per_page]:
                mono = not ln.startswith(("Appendix", "Zone spectra", "Scans marked", "Skipped"))
                fig.text(0.04, y, ln[:150], fontsize=5.6 if mono else 7, family="DejaVu Sans Mono" if mono else "DejaVu Sans",
                         weight="bold" if ln.startswith("Appendix") else "normal", color="#15213B")
                y -= 0.0145
            self._footer(fig)
            n += self._save_page(pdf, fig)
        return n

    def _stamp_all(self, pdf_path: Path, n_pages: int) -> None:
        pass            # page numbers are written per page as they are produced; total known only at the end (DEFAULT: none)

    # ------------------------------------------------------------------ csv
    def _write_csv(self, e: Entry) -> Path:
        t, s = e.traces, e.settings
        d = self.out / "csv" / slug(e.group)
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{slug(e.holder)}_{slug(e.zone.label)}_{slug(e.scan.name)}.csv"
        hdr = [f"# {APP_NAME} {__version__} zone spectrum", f"# zone: {e.zone.label} (holder {e.holder}, zone id {e.zone.id}, group {e.group})",
               f"# scan: {e.scan.name} ({e.day}), edge {e.edge_key}, line {t.line}, {t.n_pix} pixels",
               f"# detectors in the sum: {', '.join(d for d, on in zip(DETS, s.sdd) if on)}",
               f"# fluorescence normalisation: {NORM_TEXT.get(s.fluo_norm, s.fluo_norm)}; TEY: {NORM_TEXT.get(s.tey_norm, s.tey_norm)}",
               f"# pre-edge window: {t.pre}; post-edge window: {t.post}; I0 source: {s.i0_source}",
               f"# de-spike: {'k = ' + str(s.k) if s.despike else 'off'}; spikes interpolated: "
               + (", ".join(f"{k} at indices {v}" for k, v in t.spikes.items() if isinstance(v, list) and v) or "none"),
               f"# flags: {'; '.join(e.flags) or 'none'}",
               f"# {self.project.calibration.describe() if self.project.calibration else ''}",
               "# columns: energy_eV, I0_zone_sum, I0_used, TEY_raw, TEY_norm, fluo_sum_raw, fluo_sum_norm, "
               + ", ".join(f"{d}_raw, {d}_norm" for d in DETS)]
        cols = [t.E, t.i0_raw, t.i0_used, t.tey_raw, t.tey, t.sum_raw if t.sum_raw is not None else np.full(len(t.E), np.nan),
                t.sum if t.sum is not None else np.full(len(t.E), np.nan)]
        for k in range(4):
            cols.append(t.det_raw[k] if t.det_raw[k] is not None else np.full(len(t.E), np.nan))
            cols.append(t.det[k] if t.det[k] is not None else np.full(len(t.E), np.nan))
        arr = np.column_stack(cols)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(hdr) + "\n")
            f.write("energy_eV,I0_zone_sum,I0_used,TEY_raw,TEY_norm,fluo_sum_raw,fluo_sum_norm," + ",".join(f"{d}_raw,{d}_norm" for d in DETS) + "\n")
            np.savetxt(f, arr, delimiter=",", fmt="%.6g")
        return p
