# AURORA user guide

AURORA reads the raw folders of an SGM beamtime (ambxas endstation, Canadian Light Source) and turns them into
quality-checked spectra, comparisons, fits, maps and a report. Nothing is ever written into `raw/`.

This is the short guide. `MANUAL.md` describes every control, `TUTORIALS.md` walks through the demo beamtime
(`aurora demo <folder>`), `INSTALL.md` covers installation.

## 1. Open a beamtime

File > Open project, or drop the folder that contains `raw/` (for example `my_beamtime`). A single day folder or a
scan folder works too. AURORA lists the days, the holders (a survey starts a holder) and the scans with their status:

| Status | Meaning |
|---|---|
| complete | every planned energy folder is there |
| in progress | folders still arriving (touched in the last 25 min) |
| aborted | fewer energies than planned, untouched for 25 min |
| test | beamline diagnostic (photodiode, dark, beam test) or a span under 5 eV |

Flags come from the sgm_report summaries when present (saturated SDDs, I0 invalid). Double-click a scan to open it in
Reduce.

Session state (zones, settings, good-I0 choices) is saved automatically to `<project>/.aurora/session.json`. Caches of
reduced stacks and hypercubes live under `<project>/.aurora/cache/` and can be deleted at any time.

## 2. Survey

The plate map of the holder: any fixed emission line, total XRF, TEY or I0; per detector or summed; divided by I0 when
the survey's I0 is usable; log scale; inverted for high-energy surveys where samples are darker than the plate.
Orange rectangles are the stack footprints (dashed when aborted or test); click one to open it in Reduce. The zones
of the holder are drawn with their labels. The sketch (`zones_html/sketches/<holder>.png`) and the sample-sheet rows
of the holder sit on the right. Import zones… reads a `zones_all.json` exported by the HTML zone tool; Export zones…
writes the same format.

## 3. Reduce (the zone tool)

1. Pick a scan in the list (or Load all). The stack map shows the main line integrated above the edge, divided by I0.
2. Draw zones on the map: **rect+** adds a rectangle (a new zone when none is selected, or when "rect+ starts a new
   zone" is ticked), **rect−** removes a rectangle from the current zone, **brush+** / **brush−** paint or erase single
   pixels (brush size n×n). A pixel belongs to the zone when its square touches the rectangle. Zones are in mm, so
   every scan of the holder that covers the area attaches to the zone automatically.
3. Name the zone (the sample-sheet IDs of the holder are offered), set its group (the sample-sheet group, used to
   organise the report) and a comment.
4. For each attached scan, the settings card decides what goes into the report: include, which SDDs, which line,
   fluorescence normalisation (raw, / I0, / I0 minus pre-edge line, edge jump = 1, area = 1, max = 1), TEY and its
   normalisation, de-spike threshold k (single-point outliers from loose electrons are interpolated), I0 source
   (own, or the mean of the good scans chosen in Check), pre/post windows in eV (blank = first 15 % / last 20 %).
5. The preview shows the four detectors, their sum, TEY and I0 (spikes marked). "Send the zone's traces to Compare"
   puts the normalised sum and TEY of every included scan into the Compare library.

Defaults for new settings come from Settings (de-spike k, normalisations). Saturated scans start with their SDDs
unticked (TEY only); scans with an invalid I0 start with the good-I0 source.

**Propose…** adds zones as a first guess (the sgm_report summary when the day has one, else the bright pads of the
survey). **Stitch…** (after Load all) merges overlapping tiles into one stack: halve the overlaps, check the seams,
bake; the stitched stack is listed with the scans and works with zones, Check and Maps.

## 4. Check

**Run the checks on this holder** probes seven energies of every stack:

- count rate of the brightest pixels per detector, against the median of the holder's other scans;
- **saturated**: the brightest pixels give the same total at every energy while I0 varied (output no longer follows
  input) and the rate is ≥ 2× the others; fluorescence unusable, TEY fine;
- **high rate**: ≥ 2× the others with a weak I0 following but without the flat signature: dead time / pile-up likely;
- **I0 invalid**: the mesh current is ≤ 0 or below 10 % of the other scans.

The I0 panel scores every scan's I0 curve (level, biggest step, roughness, non-positive points); tick the good ones
(or Auto-select) to define the good-I0 mean that scans with an invalid I0 are divided by. The four-detector panel
compares the detectors on the selected zone and scan (each pre-edge subtracted and scaled to its own range): a
detector differing by more than 25 % of the edge step, or with isolated spikes, is named.

The **I0 explorer** reads the mcc columns of any stack, including the photodiode tests, and plots mesh, photodiode,
their ratio and TEY normalised to their medians: a ratio that drifts with energy means the two disagree on the beam.

## 5. Compare

A library of spectra from CSV files: AURORA zone CSVs, sgm_report `_xanes.csv`, John Bussey's `_full_reduced.csv` /
`_absorption.csv`, or any column file. Each row has a shift, a normalisation (larch-style two-line with E0 from the
derivative, legacy constant pre/post, TEY pre-edge line, none), pre/post offsets from E0, flatten, spacing, scale, and
ticks for Show, Fit and Diagnostics. The stacked plot labels every trace at its right end; peaks (prominence, distance)
are listed; the diagnostics panel shows the raw signal with the two fitted lines and E0. First derivative (with
smoothing) and the difference of two selected rows are one click away. Export writes a wide CSV, PNG and SVG and a
config JSON that reloads the library. **References…** lists the spectra of the reference folder set in Settings
(edge guessed from the file name, filter by element) and adds the ticked ones as rows.

## 6. Fit

Works on the rows ticked Fit in Compare, with their shifts and normalisation.

- **LCF**: target and ticked components, fit range, coefficients ≥ 0 and summing to 1 (both optional), bootstrap
  uncertainties, RMSE and R-factor; export coefficients and curves.
- **PCA / MCR-ALS**: scree of the explained variance, then alternating least squares with non-negativity for the
  chosen number of components; component spectra and per-sample proportions; export.
- **ITFA**: refine the MCR solution with some samples anchored to known component percentages (use only with
  independent chemical evidence).
- **Peaks**: one trace decomposed into 1–8 gaussian / lorentzian / pseudo-voigt peaks on an arctan step and a
  linear or constant background; initial centres automatic or typed; parameters table and export.

## 7. Maps

The full hypercube of one stack (every channel of every detector at every energy; read once, cached).
Explore: the map of any emission window (a fixed line or a custom window in eV, dragged on the ROI spectrum) at any
energy or averaged; the ROI is the whole map, the current zone, or a drawn rectangle; the XANES of the ROI per detector.
Elemental maps: one map per fixed line. RGB: three windows as a composite. Clusters: PCA on the per-pixel XANES and
k-means, with the cluster map and mean spectra. PyMca exports: the XANES stack (`_PCA-CA.h5`) and the 4D file
(`_Elemental_PyMca.h5`). **deconvolve** sharpens the map (Richardson-Lucy or Wiener, PSF in µm); it is a view, the
exports stay the measured cube.

## 8. Report

Builds the group-organised PDF from the zones and their settings: cover with provenance, one figure per group and
edge (fluorescence above, TEY below, traces offset, labels at the right), Appendix A with the four detectors per zone,
Appendix B with the plate maps and zones, Appendix C with the quality notes, scans marked not included and anything
skipped. Beside the PDF: `figures/` (PNG 600 dpi and SVG), `csv/<group>/` (one CSV per zone and scan with a full
provenance header), `pages/` (previews), `report_manifest.json`. The same report runs headless:

```
aurora report <project> --zones <zones_all.json> --out <folder> [--groups A B] [--no-appendix] [--no-maps] [--dpi 600]
```

## 9. Command line

```
aurora                        the application (opens the last project when enabled in Settings)
aurora gui <project>
aurora project <project>      list days, holders, scans, status and flags
aurora scan <scan folder>     one scan's metadata and first energy
aurora report …               the report, headless
aurora demo <folder>          a synthetic beamtime for the tutorials
aurora --distcheck            smoke test of an installation
```
