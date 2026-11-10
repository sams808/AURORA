# AURORA tutorials

Nine tutorials, each ten minutes or less. The first eight use the synthetic demo beamtime that AURORA writes for you,
so nothing you do touches real data; the ninth is the recipe for a real beamtime. Controls are described in full in
`MANUAL.md`. AURORA starts in Easy mode; the steps marked *Advanced* need Advanced mode (**View > Advanced mode**, the
mode button at the right of the top bar, or **Ctrl+Shift+A**).

The demo beamtime is one click away: **Help > Open the demo beamtime** (or the **Open the demo beamtime** button on
Home before a project is open) writes it into the user data folder and opens it. To put it somewhere else (a new or
empty folder: AURORA refuses one that already holds files, so a real beamtime is never written into):

```
aurora demo C:\Users\<you>\Desktop\aurora_demo
```

It contains one day (2026-11-05) with one holder, N5: the survey `010013_holderA_survey`, one complete F K stack
`092758_alpha_beta_f_k_stack` (41 energies × 12 points), one aborted stack, one photodiode test, fixed ROI bins for
F Kα and O Kα and a two-row sample sheet (`1 alpha` in Minerals, `2 beta` in Simple Glass). It also holds five reduced
spectra in John Bussey's layout in `processed/reduced/` (NaF, CaF2, BaY2F8 and two mixtures of them) and three
reference spectra in `references/`. Every number in it is synthetic and says so in its headers. From an installed copy
use `aurora-cli.exe demo …` on Windows or `AURORA.app/Contents/MacOS/AURORA demo …` on macOS.

---

## Tutorial 1: open a beamtime and read the tree

1. Start AURORA. Home shows **Open project…**, **Open the demo beamtime** and **Read the tutorials**; click **Open the
   demo beamtime** (or **File > Open project…** and the `aurora_demo` folder).
2. Home shows one day, one holder (`N5`, named after the survey) and four scans. Read the Status column: `complete`
   for the stack, `aborted` for the short one, `test` for the photodiode test; the survey is the first row.
3. Select the stack `092758_alpha_beta_f_k_stack` and read its scan analysis summary on the right: *6 s per map*,
   *41 maps × 6 s = 4.1 min*, *4 × 3 points, pitch 0.250 × 0.250 mm*, energy regions *660–740 eV in 2 eV steps*, *41 found
   of 41 planned; every map complete*. Select the aborted stack: *3 found of 5 planned; missing 689.00, 692.00 eV*.
4. Run the same listing headless to see what the GUI saw:

   ```
   aurora project C:\Users\<you>\Desktop\aurora_demo
   ```

5. Double-click the complete stack. AURORA switches to Reduce with that scan loaded.

What you learned: how a holder is formed (a survey followed by its stacks), what the statuses mean, and that the
headless listing and the tree agree.

## Tutorial 2: draw zones and reduce a stack

1. In Reduce, step **1 · Scans of holder N5** lists the complete stack; it is selected.
2. Step **2 · XRF window** shows the emission spectrum with the preset *F Kα (fixed bins)*: **lo** 60 and **hi** 74
   channels on SDD1 (hi excluded). Keep it.
3. Step **3 · Region**: with **Draw** and **Rectangle** chosen (or **Lasso**, and draw around the area), drag over the
   left half of the map. The caption reads *Region: 1 rectangle · 6 of 12 points of 092758_alpha_beta_f_k_stack*. Pick the sample
   label `1 alpha` (offered from the sample sheet) in the **sample** box above the map.
4. Press **Ctrl+Z**: the label goes back to *zone 1* (**Edit > Undo** names each step); press it again and the zone
   disappears; **Ctrl+Y** twice brings both back. The top bar shows *unsaved*, then *saved* with the time.
5. **New sample**, then drag over the right half; label `2 beta`. A click inside the first zone selects it again.
   (Without **New sample**, a stroke that does not touch the selected zone starts a new zone by itself.)
6. Step **4 · Reduce** states the settings in force (*I0 mcc ch1 (Au mesh) · TEY mcc ch4 · zone mean (John Bussey's reduction) · fluorescence /
   I0, edge jump = 1 · de-spike k = 6 (John Bussey's default: off) · SDD1-4*). Select zone 1 and press **Add to Check**; do the same for zone 2.
7. *Advanced*: switch with **Ctrl+Shift+A**. The cards lose their numbers; the **Zone** card shows **group**, filled
   from the sample sheet when the label was picked (`Minerals` for zone 1, `Simple Glass` for zone 2). The scan card below shows **include** ticked, SDD1–4 ticked,
   line F Kα, fluorescence */ I0, edge jump = 1*, **de-spike** with k = 6, I0 own. Untick **SDD3** and watch the
   preview's sum change; switch back to Easy mode: the chip *advanced settings in use* appears, its tooltip names *SDD3
   off*, and **Reset to Easy defaults** ticks it again.
8. Choose **Erase** and **Brush** (size 1) and click a pixel inside zone 1: it is erased from the zone (a right-drag
   erases too, in Draw mode); a click in another zone selects it instead. *Advanced*: On the scan card, **Copy to…** > *Copy to this scan in every
   zone of the holder*: zone 2 takes the same detectors. Then **Rules…**: **Add** a rule with **edge** `F K` that
   writes the TEY normalisation */ I0, pre-edge = 0*, look at the table of what would change, **Apply now**, and
   **Edit > Undo**.
9. Close and reopen AURORA: the zones are back, from `aurora_demo\.aurora\session.json`, and so is the Check queue.

What you learned: zones are in mm and belong to the holder, settings belong to the zone and scan, every edit can be
undone, and everything is saved automatically.

## Tutorial 3: proposals and the zones file (Advanced)

1. In Reduce, **Propose zones** (in the zone card). The demo has no sgm_report summary, so the proposal comes from the
   bright pads of the survey map: one zone. **Ctrl+Z** removes the whole proposal again.
2. Go to Survey. The zones are drawn on the plate map with the footprints of the stacks. Click the footprint of the
   complete stack: Reduce opens on that stack.
3. **Export zones…** to `zones_demo.json`. Open it in a text editor: one entry per holder, zones with rects, paint and
   erase lists in mm and the per-scan settings. This is the `zones_all.json` format the browser zone tool uses, so
   zones can travel both ways.
4. **Import zones…** reads it back. The holder already has these zones, so nothing is added: the status line counts
   them as duplicates. A file with other zones for N5 would open the **Import zones** dialog (**Merge**, **Replace** or
   **Skip** per holder).
5. Survey > **Grid of zones…**, drag over the pads of the plate map, choose *zone numbers* in **labels from**,
   **Accept**: the grid is added in one step, and **Edit > Undo** removes the whole grid at once.

## Tutorial 4: check the spectra and explore I0

1. Go to Check. **Spectra to check** lists the two regions sent with **Add to Check** in Tutorial 2. Each row reads
   *✓ ok* and *no problem found*; select one, and the messages under the preview read *region contains 6 of 12
   measurement points* and *no bad points*.
2. Select a row and switch **view** to *fluorescence sum and its bad points*, then to *sum / I0 and TEY / I0*.
3. Press **Save ticked → Compare**: the two spectra are written in John Bussey's format to
   `processed/reduced/1 alpha/` and `processed/reduced/2 beta/`, and Compare lists them.
4. *Advanced*: **Run the checks on holder N5** (in **Scans of holder N5**). The table fills with the I0 level, the
   count rate per detector, and empty Saturated / High rate columns (the demo is clean).
5. *Advanced*: in **I0 of the holder's scans (per-pixel mean)**, tick the stack, or **Auto-select good I0**. Back in
   Reduce, a scan card's I0 list now offers *good-I0 mean* with a meaning.
6. *Advanced*: select zone 1 in Reduce, return to Check: **The four detectors on the selected zone** overlays SDD1–4
   on the zone. The note names a detector only when it strays by more than 25 % of the edge step.
7. *Advanced*: in the I0 explorer, choose the photodiode test scan (it is hidden everywhere else) and **Read**. Mesh,
   diode, their ratio and TEY are plotted normalised to their medians; the note gives the levels and the span of
   diode / mesh. On a real beamtime a span of a few per mille means the mesh is trustworthy.

## Tutorial 5: compare and fit reduced spectra

This tutorial uses the demo's `processed/reduced/` folder: NaF, CaF2 and BaY2F8, and the two mixtures
`mixture 1` (0.5 NaF + 0.3 CaF2 + 0.2 BaY2F8) and mixture 2 (0.2 + 0.5 + 0.3), plus the two spectra saved in
Tutorial 4. Any folder of reduced CSVs works the same way.

1. Compare, **Add saved spectra…** → `processed/reduced`. Each file gives a sdd-scaled row (the four SDDs each
   scaled to 0–1, summed, / I0: John Bussey's fluorescence signal) and a TEY row; the TEY rows start with **Show**
   unticked, as in John Bussey's GUI, so the plot holds the seven sdd-scaled traces, the two zones of Tutorial 4
   included. In Easy mode only John-format files are read;
   Advanced mode also reads AURORA zone CSVs and sgm_report `_xanes.csv`.
2. *Advanced*: select every sdd-scaled row, set **pre** and **post** to −20, −5 and 15, 35 in the windows row under the
   table and **Apply**. Norm stays `larch`. In the **E0** column choose `row:NaF [sdd-scaled]` on the other rows: they
   now share NaF's E0, and their windows with it.
3. *Advanced*: tick **mark peaks**: the maxima are dotted and listed under the plot. Select one row and read its E0 and
   edge step in the diagnostics panel.
4. Tick **Fit** on the sdd-scaled rows. Go to Fit > **LCF**, target `mixture 1 [sdd-scaled]`, tick the three end
   members, **Run LCF**: the coefficients with ± from 200 bootstrap draws (about 0.51 NaF, 0.29 CaF2, 0.20 BaY2F8),
   χ²ν and the R-factor, and the chip *converged*. In Advanced mode the detail under the table gives the F-test of
   each component.
5. *Advanced*: Fit > **PCA / MCR-ALS**: **Update PCA scree** (three components explain 99.9 %), three components,
   **Run MCR-ALS**. Compare the proportions with the LCF.
6. *Advanced*: Fit > **Peaks**: **trace** = NaF, two peaks, **shape** pseudo-voigt, **Run the decomposition**. Read the
   centres, FWHM (eV) and eta in the table and the areas under it, **Export…**.
7. **Figure size…**: 3.5 × 4.5 in, 600 dpi, PNG + SVG + PDF. Compare > **Export…** writes `comparison.csv`,
   `comparison.png` (2100 × 2700 px), `.svg`, `.pdf` and `comparison_config.json`.

## Tutorial 6: a reference library (Advanced)

1. The demo's `references/` folder holds `NaF_F_K_ref.csv`, `CaF2_F_K_ref.csv` and `BaY2F8_F_K_ref.csv` (energy and
   normalised absorption). Your own library can have subfolders per element, for example `references/F/NaF_F_K.csv`.
2. **File > Settings…**, set **reference spectra folder** to that folder.
3. Compare, **References…**: the list shows `NaF F K   (F K)   NaF_F_K_ref.csv` and the two others. Type `F` in
   **element** to filter, tick the ones you want, **OK**. They are ordinary library rows now, so they can be ticked
   **Fit** and used as LCF components.

The edge is guessed from the file or folder name (`<element>_<K|L3|…>` or the element's English name); files without
a recognisable edge are listed without one and never filtered out.

## Tutorial 7: maps, deconvolution and clusters (Advanced)

1. Maps, **stack** = the complete stack, **Load hypercube** (the demo cube is tiny; a real one takes a minute the
   first time and is cached; the panel gives its size before loading).
2. Explore: **window** = F Kα, move the **energy** slider, watch the map; the line under the map names the channels of
   each SDD. Untick **SDD3**: the map, the spectrum and the XANES sum now cover SDD1, SDD2 and SDD4. Tick it again.
   Change **ROI** to *rectangle (draw)* and drag on the map: the emission spectrum and the XANES on the right follow
   the rectangle. Drag the band on the emission spectrum to set a custom window.
3. Tick **deconvolve**, **PSF** = 300 µm, 20 iterations: the map sharpens. Switch to Wiener and compare. Untick when
   done; exports always use the measured cube.
4. Elemental maps, **Draw the elemental maps (every fixed line)**: one map per fixed line (F Kα and O Kα in the demo).
5. Clusters: **PCA** 3, **k-means** 3, **seed (k-means)** 0, **Run**: the cluster map, one row per cluster under
   **Clusters: tick to merge**, and the mean XANES of each cluster. Untick one cluster: it is dimmed on the map, and the
   merged mean and the ticked sum follow. **Make zone from ticked clusters** adds a zone (see it in Reduce); **Send to
   Compare** adds the merged mean to Compare.
6. **Export PyMca XANES stack (_PCA-CA.h5)…** to open the same cube in PyMca.
7. **Export…** in Clusters writes the cluster spectra as CSV and the cluster map as PNG.

## Tutorial 8: build the report, in the GUI and headless

1. Report: leave the title and the output folder. Look at **Check before building**: with the two zones of Tutorial 2
   it reads *2 zones · 2 spectra · 0 warnings*. Delete zone 2 in Reduce and come back: it reads *1 zone · 1 spectrum ·
   0 warnings · 1 note*, with the finding *Sample '2 beta' of N5 (sample sheet) has no zone.* Click **Show** to open N5 in
   Reduce, or tick **Ignore**; **Ctrl+Z** in Reduce brings zone 2 back.
2. **Build the report**. Browse the pages with **◀** **▶**, then **Open folder**: `reports/aurora/<date>_<time>_all/`
   holds the PDF, `figures/`, `csv/` (each CSV with its `.json` sidecar), `pages/` and `report_manifest.json`;
   `reports/aurora/latest.json` points at it.
3. Look at `csv/Minerals/`: one CSV per zone and scan with the full provenance header (zone, scan, detectors,
   normalisation, windows, I0 source, de-spike, flags, calibration, settings).
4. Rename a zone in Reduce and build again: a second folder appears, and the cover's *Changes since the previous
   build* names the rename.
5. *Advanced*: choose **preset** *Draft* for a quick check of the current holder (110 dpi, no Appendix A or B).
6. The same report from the command line, from the session the GUI saved (the output path printed ends in the build
   folder):

   ```
   aurora report C:\Users\<you>\Desktop\aurora_demo --out C:\Users\<you>\Desktop\aurora_demo\reports\cli
   ```

   Or from an exported zones file, only one group, as a Draft:

   ```
   aurora report <project> --zones zones_demo.json --groups Minerals --preset draft --out <folder>
   ```

## Tutorial 9: a real beamtime, the recipe

The order that worked during the November 2026 F K campaign:

1. Open the beamtime folder (the one with `raw/`). If sgm_report ran, its summaries feed the flags and proposals.
2. *Advanced*: Check, **Run the checks on every holder**, and read Saturated / High rate / I0 invalid. AURORA's verdicts
   set the defaults of new zones (saturated SDDs off, the good-I0 mean for an invalid I0); tick the good I0 scans.
3. Per holder: Survey to see the plate, Reduce (*Advanced*: **Propose zones** or Survey > **Grid of zones…**), correct
   the labels and groups from the sample sheet, delete what is not a sample. Zones attach to every stack of the holder
   that covers them, loaded or not.
4. Select a few zones and check the four detectors; untick a detector that misbehaves on that zone. **Copy to…** and
   **Rules…** apply a choice to many cards at once.
5. If the plate was measured as tiles, **Stitch…** after **Load all**, check the seams, bake, and draw the zones on the
   stitched stack.
6. Send each sample with **Add to Check**, look at the messages, and **Save ticked → Compare**.
7. Report: read **Check before building** first, fix or ignore the findings, build. Appendix C says what was skipped
   and why.
8. Compare the saved spectra, add references, fit.
9. Keep `.aurora/` with the beamtime: `session.json` is the record of every choice (with its backups), and `aurora
   report` rebuilds the report from it at any time.
