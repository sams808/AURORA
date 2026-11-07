# AURORA user manual

AURORA turns the raw folders of an SGM beamtime (ambxas endstation, Canadian Light Source) into quality-checked
spectra, comparisons, fits, maps and a report. This manual describes every perspective and every control. The
short version is `USER_GUIDE.md`; step-by-step walkthroughs are in `TUTORIALS.md`; installation is in `INSTALL.md`.

Nothing is ever written into `raw/`. Everything AURORA produces lives in `<project>/.aurora/` (session and caches),
in the output folder you choose for a report, or in files you export explicitly.

Contents

1. [The window](#1-the-window)
2. [Home](#2-home)
3. [Survey](#3-survey)
4. [Reduce](#4-reduce)
5. [Check](#5-check)
6. [Compare](#6-compare)
7. [Fit](#7-fit)
8. [Maps](#8-maps)
9. [Report](#9-report)
10. [Settings](#10-settings)
11. [Command line](#11-command-line)
12. [Files AURORA reads and writes](#12-files-aurora-reads-and-writes)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. The window

![Home](img/home.png)

- **Rail** (left): the eight perspectives, Home → Report, in the order of a normal workflow. Settings sits at the
  bottom. Every perspective keeps its state when you switch.
- **Header**: the project name, number of days and holders.
- **Menu**: File (Open project… Ctrl+O, Reload F5, Settings…, Quit), View (Full screen F11), Help (About: versions of
  AURORA, Python, numpy, h5py, PySide6, xraydb).
- **Status bar**: the last thing AURORA did (zones saved, report built, …).

One *session* is attached to the open project: the current holder, the zones of every holder, the per-scan settings
of every zone, the good-I0 choices, the stitched stacks. It is saved automatically to `<project>/.aurora/session.json`
on every change and restored when the project is opened again.

Vocabulary used throughout:

| Term | Meaning |
|---|---|
| project | a beamtime folder containing `raw/` (and usually `processed/`, `config/`, `reports/`) |
| day | one `raw/<YYYY-MM-DD>/` folder |
| holder | a plate on the manipulator; a survey scan starts a holder, the stacks that follow belong to it |
| survey | one energy over the whole plate (`<time>_<name>_survey`) |
| stack | the XAS map: n energies × m points (`<time>_<name>_<edge>_stack`) |
| zone | an area of the plate in mm (rectangles, painted pixels, erased pixels) that becomes one spectrum per scan |
| group | the sample-sheet group of a zone; the report is organised by group |
| line | an emission window in the SDD spectrum: F Kα, O Kα, … from `config/roi_bins.json`, or any custom window |
| I0 | the Au-mesh current (mcc channel 1); the photodiode is channel 2, TEY channel 4 |

## 2. Home

Open a beamtime with **File > Open project…** or **Open folder…**. The folder that contains `raw/` is the normal
choice; a single day folder or a single scan folder also works. The tree lists days, holders and scans:

| Column | Meaning |
|---|---|
| Scan | the scan name without its time prefix |
| Time | HH:MM of the scan folder |
| Kind | survey, stack, other |
| Edge | the target edge read from the plan and the name (F K, O K, Se L3/L2, …) |
| Status | complete, in progress, aborted, test |
| Energies | first–last eV and the number of energy folders |
| Points | pixels per energy |
| Flags | from the sgm_report summary when present: saturated SDDs, I0 invalid |

Status rules: *complete* when every planned energy folder is there; *in progress* when folders are still arriving
(touched within the last 25 min); *aborted* when fewer energies than planned and untouched for 25 min; *test* when
the span is under 5 eV or the name says photodiode, dark or beam test. Test stacks are hidden from zones but stay
available in the I0 explorer of Check.

Double-click a scan to open it in Reduce. **Reload** rescans the folder (new scans arriving during a beamtime appear
without reopening). The sgm_report `summary.json` of a day, when present, feeds the flags and the zone proposals.

Calibration (`sdd_calibration.json`) is taken from a sibling `SGM_Beamline_Analysis-main/` folder when there is one
(where sgm_report keeps the live file), otherwise from `config/`; the one used is printed by `aurora project`.

The sample sheet is the first CSV at the root of the project that parses (cp1252, columns `Final plate#`,
`Sample group`, `Sample ID`, …). Its sample IDs are offered as zone labels when the plate column equals the holder
name; its groups are offered for every zone.

## 3. Survey

![Survey](img/survey.png)

The plate map of the holder chosen in the **Holder** combo.

- **channel**: a fixed emission line, total XRF, TEY or I0. **SDD**: sum or one detector.
- **/ I0** divides by the mesh current when the survey's I0 is usable. **log** shows log10. **invert** flips the
  scale for high-energy surveys where samples are darker than the plate. **clip %** trims the colour range at both
  ends (0.5 % by default).
- **footprints**: orange rectangles are the stacks of the holder, dashed when aborted or test. Click one to open it
  in Reduce. **zones**: the zones of the holder with their labels.
- Hovering prints x, y and the value under the cursor.
- The card on the right shows the sketch of the holder (`zones_html/sketches/<holder>.png` when it exists) and the
  rows of the sample sheet that belong to the holder.
- **Import zones…** reads a `zones_all.json` written by the browser zone tool or by another AURORA; **Export zones…**
  writes the same format for the whole project (every holder).

## 4. Reduce

![Reduce](img/reduce.png)

Reduce is the zone tool: zones are drawn on the stack map of one scan, in mm, so that every scan of the holder that
covers the area attaches to the zone automatically.

### 4.1 Scans of the holder

The list holds the complete stacks of the current holder. Pick one to load it (a few seconds the first time; the
reduced stack is cached). **Load all** reads every complete stack in one go so that zones attach to all of them.
Stitched stacks (see 4.6) appear at the end of the list.

### 4.2 Stack map

- **show**: line, detector (sum or one SDD), what to integrate (integrated above the edge, at one energy chosen with
  the E# spinner, the mean over all energies), **/ I0**, **log**.
- Tools: **navigate** (pan with the left button, wheel to zoom), **rect+** adds a rectangle to the current zone,
  **rect−** removes one, **brush+** / **brush−** paint or erase single pixels, the brush size being n×n pixels.
  **rect+ starts a new zone** makes every rectangle a new zone, useful when drawing many samples in a row.
- A pixel belongs to a rectangle when its square touches it (tolerance 0.45 of the pitch, the paint squares are
  0.51 of the pitch), the same rule as the browser zone tool, so the two agree pixel for pixel.
- The caption gives the scan, its energies, pixel count, pitch, main line and where the data came from (sgm_report
  cache, AURORA cache or raw).

### 4.3 Zones

**New zone**, **Delete**, **Propose…**, **Stitch…** and the list of zones of the holder (number, label, group,
number of attached scans). Selecting a zone shows its rectangles on the map and its card on the right.

**Propose…** adds a first guess of zones that you then correct or delete: when the day has an sgm_report summary, the
zones sgm_report found (one square of the right area per distinct name); otherwise the bright pads of the survey map
(Otsu threshold on log XRF / I0, numbered in reading order). Proposed zones are ordinary zones.

### 4.4 The zone card

- **label**: the sample-sheet IDs of the holder are offered; type anything.
- **group**: the sample-sheet group; the report is organised by it.
- **comment**: goes into the report caption and the CSV header.
- The info line counts rectangles, painted and erased pixels, and the loaded scans that cover the zone.
- **Send the zone's traces to Compare** puts the normalised fluorescence sum and TEY of every included scan into the
  Compare library.

### 4.5 One card per attached scan

Every loaded scan that covers the zone gets a settings card. The settings travel with the zone in the session file
and decide what the report contains.

| Control | Effect |
|---|---|
| include | the scan contributes to the report figures and CSVs |
| SDD1–SDD4 | detectors in the fluorescence sum (saturated detectors start unticked) |
| line | the emission window (one of the fixed lines) |
| fluo | fluorescence normalisation: raw; / I0; / I0 minus the pre-edge line; / I0 with edge jump = 1; area = 1; max = 1 |
| TEY | include TEY and its normalisation (same choices) |
| de-spike k | single-point outliers beyond k MADs of a 5-point running median are interpolated (k = 6 by default) |
| I0 | own, or the mean of the good scans ticked in Check (scans flagged I0 invalid start with the good-I0 mean) |
| pre / post | windows in eV, "680-684" style; blank = first 15 % / last 20 % of the energies |

The header of the card prints the flags of the scan (saturated SDDs, I0 invalid) or the pixel count of the zone.

### 4.6 Stitching tiles

A plate measured as several overlapping stacks (tiles) becomes one stack with **Stitch…** (after **Load all**):

1. Tick two or more tiles.
2. **trim**: *halve the overlaps* splits every pairwise overlap of the pixel boxes in half (the CLS
   "Auto-Calculate Overlaps"); *no trim* keeps every pixel.
3. **Check the seams** lists every adjacent pair with the gap between the kept edges and a verdict.
4. **Bake** concatenates the kept pixels on the energies common to all tiles (within 0.01 eV) and the ROI labels
   common to all; the result is a stack named `Stitched_<first>_to_<last>`, listed with the scans, usable by zones,
   traces, Check and Maps, and saved in the session.

### 4.7 Preview

Three panels for the current zone and selected scan: fluorescence (the four detectors and their sum, spikes marked),
TEY, I0 (zone sum). **all scans of the zone** overlays every included scan. The per-detector scale is the sum's scale
divided by the number of ticked detectors, so a missing detector is visible as a gap rather than hidden by rescaling.

## 5. Check

![Check](img/check.png)

**Run the checks on this holder** probes seven energies of every stack (fast: the brightest pixels only) and fills
the table:

| Column | Meaning |
|---|---|
| I0 level, I0 | median mesh current and its verdict (ok / invalid) |
| SDD1–4 kcps | count rate of the brightest pixels per detector |
| Saturated | the brightest pixels give the same total at every energy (CV < 2 %) while I0 varied (> 5 %), the rate is ≥ 2× the holder median and the output no longer follows the input (slope < 0.5): fluorescence unusable, TEY fine |
| High rate | ≥ 2× the holder median without the flat signature: dead time / pile-up likely |
| Notes | the reasons in words |

I0 is *invalid* when the mesh current is ≤ 0 or below 10 % of the other scans of the day.

**I0 of the holder's scans** scores every scan's I0 curve: level, biggest step, roughness, number of non-positive
points. Tick the good ones, or **Auto-select good I0**; their mean is what scans set to "good-I0 mean" in Reduce are
divided by. **normalise to the level** overlays the curves on one scale.

**The four detectors on the selected zone** compares SDD1–4 on the zone chosen in Reduce and the scan selected in the
table, each with its pre-edge subtracted and scaled to its own range. A detector that differs by more than 25 % of the
edge step, or that carries isolated spikes, is named. The spike line counts the interpolated points per channel.

**I0 explorer: mesh vs photodiode** reads only the mcc CSVs of any stack, including the photodiode tests that are
hidden elsewhere, and plots the mesh (mcc1, I0), the photodiode (mcc2), their ratio diode / mesh and TEY (mcc4), each
normalised to its median. The note gives the mesh and diode levels and the span of the ratio over the scan. A ratio
that drifts with energy means the mesh and the diode disagree on the beam, which is what the photodiode test is
there to decide. The curves are cached as JSON under `.aurora/cache`.

## 6. Compare

![Compare](img/compare.png)

A library of spectra, each row one trace, and a stacked plot.

### 6.1 Loading

- **Add reduced folder…**: a folder of reduced CSVs in any of the known layouts: AURORA zone CSVs, sgm_report
  `_xanes.csv`, John Bussey's `_full_reduced.csv` / `_absorption.csv`. Each file gives its signals (sdd-scaled sum,
  TEY, …) as separate rows.
- **Add CSV…**: any column file; the energy column is found by name or taken as the first column.
- **References…**: the reference-spectra library (section 6.5).
- From Reduce: **Send the zone's traces to Compare**.
- **Load config…** / **Save config…**: the whole library (files, shifts, normalisation, ticks) as a JSON file.

### 6.2 The table

| Column | Meaning |
|---|---|
| Show | plotted |
| Label | editable; **Rename** and **Duplicate** act on the selected rows |
| Signal | sdd-scaled, TEY, sdd1…, as read from the file |
| Shift eV | added to the energy axis (calibration offsets) |
| Norm | larch (two lines, E0 at the maximum of the derivative), legacy (constant pre / post), tey (pre-edge line only), none |
| Pre lo / hi, Post lo / hi | the fitting windows in eV relative to E0; **set pre/post** + **Apply** writes them into every selected row |
| Flatten | post-edge line flattened to 1 |
| Spacing, Scale | per-row offset and multiplier in the stacked plot |
| Fit | the row is available in Fit |
| Diag | the row's raw signal and fitted lines are shown in the diagnostics panel |

**Remove row**, **Remove sample** (all rows of the file), ▲ ▼ reorder, **Show all** / **Hide all**.

### 6.3 Stacked plot

**x range** (auto or fixed), **spacing** (offset between traces), **labels** at the right end of every trace,
**signal in label**, **first derivative** with a smoothing width, **Difference of the two selected rows**.
**mark peaks** finds local maxima with a minimum prominence and minimum distance and lists them (shifted energies)
under the plot.

### 6.4 Diagnostics and export

The diagnostics panel shows the raw signal of the selected row with its pre-edge and post-edge lines and the E0 and
edge step found. **Export…** writes a wide CSV (energy, every shown trace as processed), a PNG, an SVG and the config
JSON.

### 6.5 Reference spectra

Settings holds a **reference spectra folder**. **References…** lists every csv / txt / dat / nor / xmu file in that
folder and one level of subfolders, with the edge guessed from the file or folder name (`NaF_F_K.csv` → F K,
`CaF2-fluorine-k.txt` → F K, `ref_Se_L3.csv` → Se L3) and a label cleaned of "ref" / "edge" and underscores. Type an
element to filter, tick the references, OK: they join the library as ordinary rows, ready for Fit. The folder can be
changed in the dialog.

## 7. Fit

![Fit, LCF](img/fit_lcf.png)

Fit works on the rows ticked **Fit** in Compare, with their shifts and normalisation applied. Four tabs.

### 7.1 LCF (linear combination fit)

Target, ticked components, fit range in eV, coefficients ≥ 0 and sum to 1 (each optional), **bootstrap draws**
(residual resampling; 0 disables). Results: coefficients with uncertainties, RMSE, R-factor, the fit and the residual
plotted. **Export figure + coefficients…** writes PNG, SVG and CSV.

### 7.2 PCA / MCR-ALS

![Fit, MCR](img/fit_mcr.png)

**Update PCA scree** shows the explained variance of the ticked rows over the range. Choose the number of
**components**, **max iterations** and the **tolerance** on the error change, **Run MCR-ALS**: alternating least
squares with non-negativity on both spectra and concentrations. The component spectra and the per-sample proportions
are plotted and exported with **Export…**.

### 7.3 ITFA

Refines the MCR-ALS concentration profiles with some samples' component percentages held fixed (**Add target**:
sample and percentages, comma separated, one per component). Use only with independent chemical evidence for the
anchored samples.

### 7.4 Peaks

![Fit, Peaks](img/fit_peaks.png)

Decomposition of one trace into peaks on top of an edge: **trace**, number of **peaks** (1–8), **shape** (gaussian,
lorentzian, pseudo-voigt), **background** (linear or constant), **range** in eV, **initial centres** (comma
separated eV, or blank for automatic guesses from the local maxima). **Run the decomposition** fits peaks + arctan step
+ background with least squares and fills the table with every parameter (`p1_amp`, `p1_centre`, `p1_width`, …,
`step_height`, `step_centre`, `step_width`, the background). The fit, the components and the residual are plotted;
**Export…** writes the parameters as CSV with a provenance header.

## 8. Maps

![Maps, explore](img/maps_explore.png)

The full hypercube of one stack: every channel of every detector at every energy. **Load hypercube** reads the raw
`.bin` files once and caches them as memory-mapped arrays under `.aurora/cache` (a few hundred MB per stack).

Top controls: **detector** (sum or one SDD), **window** (a fixed line or *custom* with the **eV** bounds), **/ I0**,
**log**, **deconvolve** with its **method** (Richardson-Lucy or Wiener), **PSF** (full width at half maximum of the
beam in µm) and **iterations** (Richardson-Lucy only), **ROI** (whole map, current zone, rectangle drawn on the map),
the **energy** slider or **average over energies**.

Deconvolution sharpens a map blurred by a beam larger than the pixel pitch; it works on the gridded image, fills
pixels outside the measured points before and restores them after. Richardson-Lucy keeps counts non-negative and
needs 10–50 iterations; Wiener is a one-shot frequency filter with a constant noise-to-signal ratio. Treat the result
as a view, not as data: the exports are of the measured cube.

- **Explore**: the map of the window at the chosen energy; the emission spectrum of the ROI with the window as a
  draggable band; the XANES of the ROI in the window, per detector and summed.
- **Elemental maps**: one map per fixed line (`config/roi_bins.json`). **Export PyMca 4D** writes
  `_Elemental_PyMca.h5`.
- **RGB**: three windows as a colour composite.
- **Clusters**: PCA on the per-pixel XANES then k-means (**PCA components**, **clusters**, **Run**): the cluster map
  with a categorical colour scale and the mean spectrum of every cluster. **Export PyMca XANES stack** writes
  `_PCA-CA.h5` (y, x, E).

![Maps, elemental](img/maps_elements.png)

![Maps, clusters](img/maps_clusters.png)

## 9. Report

![Report](img/report.png)

The final report, organised by sample group, built from the zones and their settings.

- **title** (default `AURORA report · <project>`), **output** folder (default `<project>/reports/aurora`).
- **Groups to include**: the sample-sheet groups found in the zones; **All** / **None**.
- **Appendix A: the four detectors per zone**, **Appendix B: plate maps with the zones**.
- **Build the report** runs in the background with progress; the pages are previewed with ◀ ▶; **Open folder**,
  **Open PDF**.

What the folder contains:

| Item | Content |
|---|---|
| `<title>.pdf` | cover with provenance (project, days, calibration, AURORA version, author from Settings), one figure per group and edge (fluorescence above, TEY below, traces offset, labels at the right), Appendix A (four detectors per zone and scan), Appendix B (plate maps and zones), Appendix C (quality notes, scans not included, anything skipped) |
| `figures/` | every figure as PNG (600 dpi by default) and SVG |
| `csv/<group>/` | one CSV per zone and scan: header with zone, scan, detectors, normalisation, windows, I0 source, de-spike, flags, calibration; columns energy_eV, I0_zone_sum, I0_used, TEY_raw, TEY_norm, fluo_sum_raw, fluo_sum_norm, then raw and normalised per detector |
| `pages/` | PNG preview of every page |
| `report_manifest.json` | everything above with the settings used |

The same report runs headless, see section 11.

## 10. Settings

| Setting | Effect |
|---|---|
| de-spike threshold k | default for new zone settings (6) |
| default fluorescence normalisation, default TEY normalisation | defaults for new zone settings (edge jump = 1) |
| map colour map | viridis, magma, inferno, plasma, cividis |
| report author | printed on the cover |
| reference spectra folder | the folder behind Compare > References… |
| open the last project at start | on by default |

Per-scan choices (detectors, lines, normalisation, I0 source) live with each zone in the session file, not here.
Settings are stored in the user's registry (Windows) or preferences (macOS); the environment variable
`AURORA_SETTINGS_DIR` redirects them to an ini file in that folder (used by the tests and the smoke test).

## 11. Command line

```
aurora                          the application (opens the last project when enabled in Settings)
aurora gui <project>            the application on that project
aurora project <project>        list days, holders, scans, status and flags (no Qt)
aurora scan <scan folder>       one scan's metadata and first energy (no Qt)
aurora report <project> [--zones zones_all.json] [--out folder] [--title T] [--groups A B]
                                [--no-appendix] [--no-maps] [--dpi 600]
aurora demo <folder>            a small synthetic beamtime for the tutorials
aurora --distcheck              offscreen smoke test of an installation
aurora --version
```

`aurora report` without `--zones` uses `<project>/.aurora/session.json`, so a report built from the session the GUI
saved is identical to the one the Report perspective builds. From a frozen build, replace `aurora` by the executable
(`AURORA.exe` on Windows, `AURORA.app/Contents/MacOS/AURORA` on macOS).

## 12. Files AURORA reads and writes

Reads (never modified):

| Path | Content |
|---|---|
| `raw/<day>/<HHMMSS>_<name>_<plan>/` | `.h5` (hexapod waves, stack and scan metadata); per-energy folders `…_<EEE>_<ff>eV/` with `mcc_flyer_0.csv` (ch1 mesh I0, ch2 photodiode, ch4 TEY) and `sdd1..4_0.bin` (uint32, n_pix × 256) |
| `processed/<day>/<scan>/_reduced_cache.npz`, `_count_rate.json`, `summary.json` | sgm_report v2.5 caches; used when present so that the numbers equal the beamtime report |
| `config/roi_bins.json` | fixed emission windows per line and detector |
| `config/sdd_calibration.json` (or the sibling `SGM_Beamline_Analysis-main/`) | channel → eV per detector |
| `config/plate_layout.json`, the sample sheet (first parsable cp1252 CSV at the project root) | zone labels, groups, sketches |
| `zones_html/sketches/<holder>.png` | holder sketches |

Writes:

| Path | Content |
|---|---|
| `<project>/.aurora/session.json` | the session: holders, zones (id, label, group, comment, rects, erects, paint, erase, per-scan settings), good-I0 choices, stitched stacks; the same format as `zones_all.json` |
| `<project>/.aurora/cache/<day>/<scan>/` | reduced stack (`_reduced_cache.npz` style), hypercube `cube_sddN.npy`, `_mcc_curves.json`; delete at will |
| report output folder | section 9 |
| exports | Compare (CSV, PNG, SVG, config JSON), Fit (CSV, PNG, SVG), Maps (PyMca h5), zones (`zones_all.json`) |

## 13. Troubleshooting

- **Plots are empty, maps are blank**: PySide6 older than 6.10 paints no pyqtgraph items. The frozen builds ship
  6.12; from source, install in a fresh environment (`INSTALL.md`).
- **A scan is listed as test**: its span is under 5 eV or its name contains photodiode / dark / test. It is still
  readable in the I0 explorer and with `aurora scan`.
- **A scan is aborted but the beamline is still writing it**: the 25 min rule; Reload later.
- **The zone has no settings card**: no loaded scan covers it; pick the scan in the list or **Load all**.
- **The report skipped a zone**: Appendix C and the status line say why (no included scan, no pixel, missing line).
- **Numbers differ from the beamtime report**: AURORA uses the sgm_report caches when they exist; after deleting
  `processed/`, the raw reduce gives the same spectra within floating point but the I0 flags come from AURORA's own
  Check instead of the summary.
- **Settings look wrong in a test**: set `AURORA_SETTINGS_DIR` so that tests never touch the real settings.
