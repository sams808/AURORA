# AURORA user guide

AURORA reads the raw folders of an SGM beamtime (ambxas endstation, Canadian Light Source) and turns them into
quality-checked spectra, comparisons, fits, maps and a report. AURORA is version 2 of John Bussey's SGM XAS GUI, based
on James J. Dynes' SGM_Beamline_Analysis notebooks. Nothing is ever written into `raw/`.

This is the short guide. `MANUAL.md` describes every control, `TUTORIALS.md` walks through the demo beamtime
(**Help > Open the demo beamtime**, or `aurora demo <folder>`), `INSTALL.md` covers the installers (Windows 10 or 11;
macOS 15 or later, Apple silicon or Intel) and where things are stored. All four open from the **Help** menu; **F1**
opens the manual at the section of the page you are on, and the small **?** of every panel at that panel's section.
**Help > Keyboard shortcuts** (**Ctrl+/**) lists every shortcut.

**Easy and Advanced mode.** AURORA starts in **Easy mode**, the basic path of John Bussey's SGM XAS GUI: open the
beamtime, draw the samples in Reduce, check the spectra, compare them, fit them with LCF, build the report. Easy mode
shows the controls of that path and states the values it uses. **Advanced mode** shows every option, and the Maps
perspective; switch with **View > Advanced mode**, the mode button at the right of the top bar (it reads **Easy mode**
or **Advanced mode**), or **Ctrl+Shift+A**. Switching never changes a value, and the mode chosen is kept for the next
start.

The top bar shows *project ▸ day ▸ holder ▾*: click the holder to switch, or use **Ctrl+PgUp** / **Ctrl+PgDown**;
**Ctrl+1** … **Ctrl+8** open the perspectives. Next to it, the save indicator reads *unsaved*, then *saved* with the
time: the session saves itself within a second of every change. **Ctrl+Z** undoes the last zone or settings change
(**Edit > Undo** names it), **Ctrl+Y** redoes it. **View > Appearance** and **View > Text size** choose light or dark
and a larger text, at once. Long tasks run in the background with a progress strip and **Cancel** (**Esc**).

## 1. Open a beamtime

**File > Open project…**, or drop the folder that contains `raw/` (the folder you downloaded from the beamline) on the window. A single
day folder or a scan folder works too (that day alone: **Show every day** opens the rest), and so does one scan's
`.h5` file dropped on the window. AURORA also opens the folder John Bussey's GUI used,
where the scan folders sit side by side (`CLS GUI/`): choose that folder with **Open project…**. Its zones are saved
inside it, in `.aurora/`.

Before a project is open, Home offers **Open project…**, **Open the demo beamtime**, **Read the tutorials** and the
**Recent projects**; a greyed one is not reachable now (a network share that is not mounted). AURORA lists the days,
the holders (a survey starts a holder, and a stack recorded after midnight stays with its holder) and the scans with
their status:

| Status | Meaning |
|---|---|
| complete | every planned energy folder is there |
| in progress | folders still arriving (touched in the last 25 min) |
| aborted | fewer energies than planned, untouched for 25 min |
| test | beamline diagnostic (photodiode, dark, beam test) or a span under 5 eV |

Flags come from the sgm_report summaries and from AURORA's own checks (saturated SDDs, I0 invalid). Selecting a scan
shows John Bussey's scan analysis summary (grating, time per map, grid and pitch, energy regions, energy folders found
vs planned); double-click a scan to open it in Reduce.

The session (zones, settings, good-I0 choices) is saved automatically to `<project>/.aurora/session.json`, with
backups in `.aurora/backups/`. Caches of reduced stacks and hypercubes live under `<project>/.aurora/cache/` and can
be deleted at any time. One AURORA at a time edits a project: a second one opens it read-only. Edits that could not
be saved are never dropped without asking: closing the window offers **Export zones…**, *Discard* or *Cancel*.

### Your own beamtime

1. Download the beamtime from the beamline and open the folder that contains `raw/` (nothing to prepare, nothing to
   rename).
2. `raw/` holds one folder per day (`2025-03-14/`), each with the scan folders as the beamline named them:
   `<HHMMSS>_<name>_survey` and `<HHMMSS>_<name>_<edge>_stack`. A survey starts a holder and gives it its name
   (`010013_holderA_survey` is holder `holderA`); the stacks recorded after it belong to that holder.
3. Everything else is optional; each file adds something when it is there:

| File | What it adds | Without it |
|---|---|---|
| `config/roi_bins.json` | fixed emission lines (F Kα, O Kα, …) | the total fluorescence, or a window set by hand in Reduce (**Save as a fixed line…** keeps it in this file); no elemental maps |
| `config/sdd_calibration.json` | the energy of each detector channel | the nominal 10 eV per channel (**File > Import SDD calibration…** adds one) |
| a sample sheet (a CSV with a sample column, MANUAL 2.4) | sample names offered in Reduce, groups for the report | names are typed by hand (**File > New sample sheet…** writes one to fill in, **File > Choose sample sheet…** picks a CSV of your own) |
| `config/plate_layout.json` | rows, columns and names for **Grid of zones…** (Advanced) | the grid is typed |
| `references/` | reference spectra for Compare and Fit | none offered |
| a sketch of a holder | a picture of the holder in Survey | no sketch card |

4. If Home shows no day, holder or scan, read the *Layout note* of the **Project** summary and compare the folder with
   MANUAL 2.3 (one folder per day under `raw/`, the scan folders directly inside it). Tutorial 9 walks through a beamtime of your own from the first open to the report.

## 2. Survey

The plate map of the holder with the stack footprints (one colour and dash per status; click one to open it in
Reduce) and the zones with their labels; the first view holds every footprint and zone of the holder. The
sample-sheet rows of the holder sit on the right (one line when there are none, with **New sample sheet…** and
**Choose sample sheet…** when the beamtime has no sheet). A sketch of the holder is optional:
**Load a sketch…**, next to the holder, adds a drawing or photo (AURORA keeps a copy in `.aurora/sketches/`), and its
card appears only for a holder that has one; **Remove the sketch** removes AURORA's copy. Advanced mode adds the display options (any fixed emission line, total XRF, TEY or I0;
per detector or summed; / I0; log; inverted for high-energy surveys), **Import zones…** (a `zones_all.json` of the
browser zone tool or another AURORA; merged per holder, duplicates skipped, one undo step), **Export zones…** and
**Grid of zones…** (a block of pads cut into zones from `config/plate_layout.json` or the sample sheet).

## 3. Reduce (the zone tool)

Easy mode shows John Bussey's four steps as numbered cards:

1. **1 · Scans of holder holderA**: pick a scan of the holder (the holder is chosen in the top bar).
2. **2 · XRF window**: keep the fixed line (*F Kα (fixed bins)*) or drag the window over the emission line; **lo** /
   **hi** are in channels (hi excluded).
3. **3 · Region**: draw the sample with **rect+** or **lasso+**; a click inside another sample selects it; **New
   sample** starts the next one; the caption counts the points (*Region: 6 of 12 points of …*). Unlike John Bussey's
   GUI, a rectangle also takes the edge pixels whose square it touches; lasso+ takes only the pixels whose centre is
   inside (his rule). Zones are in mm, so
   every scan of the holder that covers the area attaches to the zone automatically, loaded or not.
4. **4 · Reduce**: check the settings line and press **Add to Check**. **Load polygon…** reads a polygon saved by
   John Bussey's GUI (`_polygon_roi.json`) into a new sample. Name the sample before saving it in Check: a sample
   still called *zone 1* is saved under that name.

Made a mistake? **Ctrl+Z** undoes the last zone or settings change (**Edit > Undo** names it), **Ctrl+Y** redoes it;
the zones are saved by themselves within a second.

Advanced mode adds **rect-** (remove a rectangle), **brush+** / **brush-** (paint or erase single pixels),
**polygon+**, **Load all**, **Propose zones** (a first guess from the sgm_report summary or the bright pads of the
survey), **Stitch…** (overlapping tiles into one stack), **Save polygon…** (John Bussey's
`_polygon_roi.json`), the group and comment of the zone, and one settings card per attached scan: include, which SDDs,
which line, PFY or IPFY, fluorescence normalisation (raw counts, / I0, / I0, pre-edge = 0, / I0, edge jump = 1, / I0,
area = 1, / I0, maximum = 1), TEY and its normalisation, de-spike threshold k, I0 source (own, or the good-I0 mean of
the scans chosen in Check), pre/post windows in eV. **Copy to…** on each card and **Rules…** set many cards at once
(MANUAL 4.5, 4.9). Defaults for new settings come from Settings; saturated SDDs start unticked and scans with an
invalid I0 start with the good-I0 mean. A setting made in Advanced stays in force in Easy, where the chip *advanced
settings in use* says so (**Reset to Easy defaults** goes back).

## 4. Check

In Check, every region sent with **Add to Check** is a row of **Spectra to check**, with John Bussey's sanity messages
(*✓ ok*, *! check*, *✗ bad*). Look at the preview (**view** chooses one of his five views), untick **Looks OK** for
anything that does not look right, then press **Save ticked → Compare**: the spectra are written in John Bussey's
format to `processed/reduced/` and appear in Compare.

Advanced mode adds the checks of every scan of the holder (**Run the checks on holder holderA**, **Run the checks on every
holder**): the count rate per detector, *saturated* (the output no longer follows the input; fluorescence unusable,
TEY fine), *high rate* (dead time / pile-up likely) and *I0 invalid* (≤ 0 or below 10 % of the other scans within
±150 eV), kept in `.aurora/qc.json`. The I0 panel scores every scan's I0 curve (tick the good ones, or **Auto-select
good I0**, to define the good-I0 mean); the four-detector panel compares SDD1–4 on the selected zone; the I0 explorer
plots mesh, photodiode, their ratio and TEY of any stack, the photodiode tests included.

## 5. Compare

A library of spectra, saved with the project. Easy mode is John Bussey's Compare: **Add saved spectra…** reads the
John-format files saved by Check, and the table shows Show, Fit, Label, Signal, Shift eV and Colour; every row is the
raw signal / I0 normalised the larch way (two lines around E0, pre −20..−5 eV and post +15..+35 eV, flattened; AURORA's windows, John Bussey's GUI used −10..−5 and +10..+34 eV, and the Easy line under the plot says so). As in his GUI, a file's TEY row starts hidden. The
diagnostics panel shows how each row was normalised. Advanced mode adds every column (normalisation, windows, E0
choice and value, flatten, spacing, scale), other file layouts, references (**References…**, from the folder set in
Settings) and configs. **Figure size…** sets the exported figure (3.5 × 4.5 in, 600 dpi, PNG + SVG + PDF by default);
**Export…** writes the wide CSV, the figure and a config that reloads the library.

## 6. Fit

Works on the rows ticked Fit in Compare, with their shifts and normalisation. Easy mode shows the LCF tab with its
options stated (*coefficients sum to 1 · no negative coefficients · ± from 200 bootstrap draws*), AURORA's defaults: John Bussey's GUI left the sum free, allowed negative coefficients and fitted 685–705 eV.

- **LCF**: target and ticked components, fit range (blank: the Compare x range), coefficients ≥ 0 and summing to 1
  (both optional); a weighted fit with χ²ν, the R-factor and uncertainties that hold on correlated residuals
  (moving-block bootstrap, or the covariance scaled by √χ²ν); an F-test says whether each component improves the fit
  beyond noise; a chip says whether the fit converged.
- **PCA / MCR-ALS** (Advanced): scree of the explained variance, then alternating least squares with non-negativity
  for the chosen number of components; component spectra and per-sample proportions; export.
- **ITFA** (Advanced): refine the MCR solution with some samples anchored to known component percentages (use only
  with independent chemical evidence).
- **Peaks** (Advanced): one trace decomposed into 1–8 gaussian / lorentzian / pseudo-voigt peaks (each by its FWHM in
  eV) on an arctan step and a background; parameters with units and errors, peak areas, undetermined parameters
  tinted; a fit that did not converge is not drawn.

## 7. Maps

Advanced mode only. The full hypercube of one stack (every channel of every detector at every energy; read once into
a cache on disk, with the free space checked first). Every sum uses each SDD's own channels for the window; the
**sum of** **SDD1** … **SDD4** ticks choose the detectors, and an SDD flagged saturated starts unticked. Explore: the
map of any emission window (a fixed line or a custom window in eV, dragged on the ROI spectrum) at any energy or
averaged; the ROI is the whole map, the current zone, or a drawn rectangle; the XANES of the ROI per detector.
Elemental maps: one map per fixed line. RGB: three windows as a composite. Clusters: PCA on the per-pixel XANES and
k-means with a seed, the cluster map and mean spectra; tick clusters to merge them, then **Make zone from ticked
clusters**, **Send to Compare** or **Export…**. PyMca exports: the XANES stack (`_PCA-CA.h5`) and the 4D file
(`_Elemental_PyMca.h5`), written in the background. **deconvolve** sharpens the map (Richardson-Lucy or Wiener, PSF in
µm); it is a view, the exports stay the measured cube.

## 8. Report

Before building, read the **Check before building** card: its first line counts the zones, spectra and warnings of
the next build, **Findings** lists what to look at (each with **Show** to open the zone in Reduce, or **Ignore**) and
**Contents** shows what the report will hold. **Build the report** writes a new dated folder every time
(`reports/aurora/<date>_<time>_<scope>/`); the previous builds stay as they were, and the cover lists what changed
since the last one. The PDF holds a cover with provenance, one figure per group and edge (fluorescence above, TEY
below), Appendix A with the four detectors per zone, Appendix B with the plate maps and zones, and Appendix C with the
quality notes; beside it `figures/` (PNG and SVG), `csv/<group>/` (one CSV per zone and scan with a full provenance
header and a JSON sidecar), `pages/` and `report_manifest.json`. The author is taken from Settings, else from your
system account. Advanced mode adds the *Final* / *Draft* preset (a Draft covers the current holder at 110 dpi without
Appendix A or B), the resolution, the appendices and the **Scope** tab (groups, holders by day). The same report runs
headless:

```
aurora report <project> [--zones zones_all.json] [--out folder] [--groups A B] [--holders holderA] [--preset draft]
```

## 9. Command line

```
aurora                        the application (opens the last project when enabled in Settings)
aurora <project folder>       the application on that project
aurora project <project>      list days, holders, scans, status and flags
aurora scan <scan folder>     one scan's metadata and first energy
aurora report …               the report, headless
aurora demo <folder>          a synthetic beamtime for the tutorials (a new or empty folder only)
aurora --selftest             every core path and perspective on a synthetic beamtime (also Help > Run self-test…)
```

From an installed copy, use `aurora-cli.exe` on Windows and `AURORA.app/Contents/MacOS/AURORA` on macOS (MANUAL §11).
