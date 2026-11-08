"""Publication figures of Compare and Fit at a fixed size (John Bussey's 'Plot size'), drawn with matplotlib Agg.

    opts = FigureOptions(width_in=3.5, height_in=4.5, font_pt=8, dpi=600, formats=("png", "svg", "pdf"))
    fig = compare_figure(traces, opts)                 # the shown traces with their on-screen offsets and x range
    paths = save_figure(fig, Path("comparison"), opts) # comparison.png (2100 x 2700 px), .svg, .pdf (252 x 324 pt)

Colours come from the caller, who reads them from aurora.app.theme.export_tokens() (LIGHT + CURVES on white,
whatever the screen scheme): this module holds no colour of its own. The size is exact: no tight bounding box, the
layout is fitted inside the page instead. Text stays text: TrueType (Type 42) fonts in the PDF and real text elements
in the SVG, so the labels can be edited and searched. No pyplot, so nothing global is touched.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

WIDTH_PRESETS_IN = (3.5, 7.0)              # single and double column
FORMATS = ("png", "svg", "pdf")
LINESTYLES = {"solid": "-", "dash": "--", "dot": ":", "dashdot": "-."}


@dataclass
class FigureOptions:
    width_in: float = 3.5
    height_in: float = 4.5
    font_pt: float = 8.0
    dpi: int = 600
    formats: tuple[str, ...] = FORMATS
    # plot content (filled by the caller from the on-screen state)
    x_range: tuple[float, float] | None = None
    xlabel: str = "incident energy (eV)"
    ylabel: str = ""
    title: str = ""
    labels: bool = True
    label_inset_ev: float = 0.0            # labels sit this many eV in from the right edge (John's 'Label position')
    legend: bool = True
    ink: str = ""                          # text colour (export token 'ink')
    axis: str = ""                         # axis colour (export token 'plot_axis')
    fg: str = ""                           # tick labels (export token 'plot_fg')
    bg: str = ""                           # background (export token 'plot_bg')
    metadata: dict = field(default_factory=dict)

    def pixel_size(self) -> tuple[int, int]:
        return int(round(self.width_in * self.dpi)), int(round(self.height_in * self.dpi))


@dataclass
class FigureTrace:
    """One stacked trace of Compare: the energies and the offset values as drawn on screen."""
    label: str
    E: np.ndarray
    y: np.ndarray
    colour: str
    label_colour: str = ""
    dash: str = "solid"
    width_pt: float = 1.0


@dataclass
class FigureSeries:
    """One element of a Fit figure: 'line' (E, y), 'vline' (E holds the positions) or 'text' (name is the text)."""
    name: str
    E: np.ndarray
    y: np.ndarray | None = None
    colour: str = ""
    dash: str = "solid"
    width_pt: float = 1.0
    kind: str = "line"
    in_legend: bool = True


def _figure(opts: FigureOptions):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    fig = Figure(figsize=(float(opts.width_in), float(opts.height_in)), dpi=int(opts.dpi), layout="constrained")
    FigureCanvasAgg(fig)
    if opts.bg:
        fig.set_facecolor(opts.bg)
    ax = fig.add_subplot()
    if opts.bg:
        ax.set_facecolor(opts.bg)
    fs = float(opts.font_pt)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        if opts.axis:
            ax.spines[side].set_color(opts.axis)
        ax.spines[side].set_linewidth(0.6)
    # colours only when given: matplotlib >= 3.11 refuses colors=None (FigureOptions() keeps matplotlib's defaults)
    ticks = {k: v for k, v in (("colors", opts.axis), ("labelcolor", opts.fg)) if v}
    ax.tick_params(labelsize=fs, width=0.6, length=3, **ticks)
    ink = {"color": opts.ink} if opts.ink else {}
    ax.set_xlabel(opts.xlabel, fontsize=fs, **ink)
    if opts.ylabel:
        ax.set_ylabel(opts.ylabel, fontsize=fs, **ink)
    if opts.title:
        ax.set_title(opts.title, fontsize=fs, **ink)
    return fig, ax


def _limits(ax, xs: list[np.ndarray], ys: list[np.ndarray], x_range, top_room: float = 0.04) -> None:
    lo_x = min(float(np.nanmin(e)) for e in xs) if xs else 0.0
    hi_x = max(float(np.nanmax(e)) for e in xs) if xs else 1.0
    xr = tuple(x_range) if x_range else (lo_x, hi_x)
    vals = []
    for e, v in zip(xs, ys):
        sel = np.isfinite(v) & (e >= xr[0]) & (e <= xr[1])
        if sel.any():
            vals.append(v[sel])
    ax.set_xlim(*xr)
    if vals:
        v = np.concatenate(vals)
        lo, hi = float(v.min()), float(v.max())
        span = hi - lo if hi > lo else 1.0
        ax.set_ylim(lo - 0.04 * span, hi + top_room * span)


def compare_figure(traces: list[FigureTrace], opts: FigureOptions):
    """The stacked Compare plot: every shown trace at its on-screen offset, the on-screen x range, each label at the
    right end ('eV in from the right edge'), axis labels with units."""
    fig, ax = _figure(opts)
    xs, ys = [], []
    for t in traces:
        E, y = np.asarray(t.E, float), np.asarray(t.y, float)
        ax.plot(E, y, color=t.colour, linestyle=LINESTYLES.get(t.dash, "-"), linewidth=t.width_pt)
        xs.append(E); ys.append(y)
    _limits(ax, xs, ys, opts.x_range, top_room=0.10 if opts.labels else 0.04)
    if opts.labels:
        x0, x1 = ax.get_xlim()
        xl = x1 - max(0.0, float(opts.label_inset_ev))
        for t, E, y in zip(traces, xs, ys):
            fin = np.isfinite(y) & (E >= x0) & (E <= x1)
            if not fin.any():
                continue
            e, v = E[fin], y[fin]
            xa = min(max(xl, float(e.min())), float(e.max()))
            ya = float(np.interp(xa, e, v))
            ax.text(xa, ya, t.label, ha="right", va="bottom", fontsize=float(opts.font_pt),
                    color=t.label_colour or t.colour)
    ax.set_yticks([])
    ax.spines["left"].set_visible(False)
    return fig


def fit_figure(series: list[FigureSeries], opts: FigureOptions):
    """A Fit result (LCF target / fit / components / residual, MCR or ITFA component spectra, peaks): lines with a
    legend, vertical markers, and centred notes such as 'fit not shown: not converged'."""
    fig, ax = _figure(opts)
    xs, ys = [], []
    for s in series:
        if s.kind == "line" and s.y is not None:
            E, y = np.asarray(s.E, float), np.asarray(s.y, float)
            ax.plot(E, y, color=s.colour or None, linestyle=LINESTYLES.get(s.dash, "-"), linewidth=s.width_pt,
                    label=s.name if s.in_legend else "_nolegend_")
            xs.append(E); ys.append(y)
    _limits(ax, xs, ys, opts.x_range, top_room=0.06)
    for s in series:
        if s.kind == "vline":
            for x in np.atleast_1d(np.asarray(s.E, float)):
                ax.axvline(float(x), color=s.colour or None, linestyle=LINESTYLES.get(s.dash, ":"), linewidth=s.width_pt,
                           label="_nolegend_")
        elif s.kind == "text":
            ax.text(0.5, 0.5, s.name, transform=ax.transAxes, ha="center", va="center", fontsize=float(opts.font_pt),
                    color=s.colour or opts.ink or None)
    if opts.legend and any(s.kind == "line" and s.in_legend for s in series):
        lg = ax.legend(fontsize=float(opts.font_pt) * 0.9, frameon=False, loc="best")
        if opts.ink:
            for txt in lg.get_texts():
                txt.set_color(opts.ink)
    return fig


def save_figure(fig, base: Path, opts: FigureOptions) -> list[Path]:
    """Write the figure as base.png (at opts.dpi), base.svg and base.pdf (the chosen formats), at exactly the
    figure size. Returns the files written."""
    import matplotlib
    from aurora.core.io.safety import assert_writable
    base = Path(base)
    out = []
    meta = dict(opts.metadata or {})
    with matplotlib.rc_context({"pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "none"}):
        for fmt in opts.formats:
            fmt = fmt.lower()
            if fmt not in FORMATS:
                continue
            p = base.with_suffix("." + fmt)
            assert_writable(p)
            md = {"png": {"Software": meta.get("Creator", "")}, "pdf": {k: v for k, v in meta.items() if k in
                  ("Creator", "Title", "Author", "Subject")}, "svg": {k: v for k, v in meta.items() if k in
                  ("Creator", "Title", "Description")}}[fmt]
            md = {k: v for k, v in md.items() if v}
            fig.savefig(p, format=fmt, dpi=int(opts.dpi), metadata=md or None)
            out.append(p)
    return out
