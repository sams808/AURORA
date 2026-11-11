# AURORA tutorials

Nine tutorials, each ten minutes or less. The first eight use the synthetic demo beamtime that AURORA writes for you,
so nothing you do touches real data; the ninth takes the same steps on a beamtime of your own. Controls are described
in full in `MANUAL.md`. AURORA starts in Easy mode; the steps marked *Advanced* need Advanced mode (**View > Advanced
mode**, the mode button at the right of the top bar, or **Ctrl+Shift+A**). In Easy mode the *Getting started* row at
the top of Home names the next step and its button, and **Help > Glossary** explains every word the window shows (an
underlined word in a caption opens it at that word).

The demo beamtime is one click away: **Help > Open the demo beamtime** (or the **Open the demo beamtime** button on
Home before a project is open) writes it into the user data folder and opens it. To put it somewhere else (a new or
empty folder: AURORA refuses one that already holds files, so a real beamtime is never written into):

```
aurora demo C:\Users\<you>\Desktop\aurora_demo
```

It is a small made-up beamtime: one day (2025-03-14) with one holder, holderA. Its survey `010013_holderA_survey`
shows two bright pads, the samples `1 alpha` (left) and `2 beta` (right), under the footprint of one complete F K stack
`092758_alpha_beta_f_k_stack` (66 energies from 670 to 735 eV, across the F K edge at 696.7 eV, × 12 points). It also
has one aborted stack, one photodiode test, fixed ROI bins for F Kα and O Kα and a two-row sample sheet `samples.csv`
(`1 alpha` in Group 1, `2 beta` in Group 2), five reduced spectra in John Bussey's layout in `processed/reduced/` (NaF,
CaF2, LiF and two mixtures of them) and three reference spectra in `references/`. It has no sketch: sketches are
optional. Every name and number in it is synthetic and says so in its headers and in `README_demo.txt`. From an installed copy
use `aurora-cli.exe demo …` on Windows or `AURORA.app/Contents/MacOS/AURORA demo …` on macOS.

---

## Tutorial 1: open a beamtime and read the tree

1. Start AURORA. Home shows **Open project…**, **Open the demo beamtime** and **Read the tutorials**; click **Open the
   demo beamtime** (or **File > Open project…** and the `aurora_demo` folder).
2. Home shows one day, one holder (`holderA`, named after the survey) and four scans. Read the Status column: `complete`
   for the stack, `aborted` for the short one, `test` for the photodiode test; the survey is the first row.
3. Select the stack `092758_alpha_beta_f_k_stack` and read its scan analysis summary on the right: *6 s per map*,
   *66 maps × 6 s = 6.6 min*, *4 × 3 points, pitch 0.250 × 0.250 mm*, energy regions *670–735 eV in 1 eV steps*, *66
   found of 66 planned; every map complete*. Select the aborted stack: *20 found of 66 planned; missing 690.00,
   691.00, …*.
4. Run the same listing headless to see what the GUI saw:

   ```
   aurora project C:\Users\<you>\Desktop\aurora_demo
   ```

5. Double-click the complete stack. AURORA switches to Reduce with that scan loaded.
6. Choose **Survey** (**Ctrl+2**): the map of holderA, its colour scale from *low* to *high*, the rectangle of the
   complete stack over the two bright pads. Choose **Draw** and drag a rectangle around the left pad, holding the
   button down for a moment: the **Zone preview** on the right shows the X-rays of the points inside the outline while
   you drag and, once you let go, the fluorescence spectrum of the stack it covers, rising at the F K edge. **Open in
   Reduce** opens that stack with the new sample selected. Press **Ctrl+Z** to take the outline away again: Tutorial 2
   draws the two samples in Reduce.

What you learned: how a holder is formed (a survey followed by its stacks), what the statuses mean, that the
headless listing and the tree agree, and that a sample can be outlined on the Survey with its spectrum shown while
you draw.

## Tutorial 2: draw zones and reduce a stack

1. In Reduce, step **1 · Scans of holder holderA** lists the complete stack; it is selected.
2. Step **2 · Which X-rays to count** shows the emission spectrum with the preset *F Kα (fixed window)*: **from** 60
   and **to** 74 channels on SDD1 (to excluded). Keep it.
3. Step **3 · Outline the sample**: with **Draw** and **Rectangle** chosen (or **Lasso**, and draw around the area),
   drag over the left half of the map. The caption reads *Region: 1 rectangle · 6 of 12 pixels of
   092758_alpha_beta_f_k_stack*. Pick the sample name `1 alpha` (offered from the sample sheet) in the **sample name**
   box above the map.
4. Press **Ctrl+Z**: the label goes back to *zone 1* (**Edit > Undo** names each step); press it again and the zone
   disappears; **Ctrl+Y** twice brings both back. The top bar shows *unsaved*, then *saved* with the time.
5. **New sample**, then drag over the right half; label `2 beta`. A click inside the first zone selects it again.
   (Without **New sample**, a stroke that does not touch the selected zone starts a new zone by itself.)
6. Step **4 · Send to Check** says in one sentence how the spectrum is made (*The spectrum is the F Kα X-rays counted
   in the step-2 window, averaged over the outlined pixels and added over the four detectors, divided by the beam
   intensity (I0), …*); its tooltip has the exact settings (*I0 mcc ch1 (Au mesh) · TEY mcc ch4 · zone mean (as the
   earlier SGM XAS GUI) · fluorescence / I0, edge jump = 1 · de-spike k = 6 (off by default in the earlier SGM XAS GUI)
   · SDD1-4 · PFY F Kα*). Select zone 1 and press **Add to Check**; do the same for zone 2. (A sample still called
   *zone 1* when it is added gets a field *Name this sample:* under the map: type the name and press **Enter**.)
7. *Advanced*: switch with **Ctrl+Shift+A**. The cards lose their numbers; the **Zone** card shows **group**, filled
   from the sample sheet when the label was picked (`Group 1` for zone 1, `Group 2` for zone 2). The scan card below shows **include** ticked, SDD1–4 ticked,
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
   bright pads of the survey map: two zones, one over the two samples under the complete stack and one over the pad of
   the aborted stack. **Ctrl+Z** removes the whole proposal again.
2. Go to Survey. The zones are drawn on the plate map with the footprints of the stacks. Click the footprint of the
   complete stack: Reduce opens on that stack.
3. **Export zones…** to `zones_demo.json`. Open it in a text editor: one entry per holder, zones with rects, paint and
   erase lists in mm and the per-scan settings. This is the `zones_all.json` format the browser zone tool uses, so
   zones can travel both ways.
4. **Import zones…** reads it back. The holder already has these zones, so nothing is added: the status line counts
   them as duplicates. A file with other zones for holderA would open the **Import zones** dialog (**Merge**, **Replace** or
   **Skip** per holder).
5. Survey > **Grid of zones…**, drag over the pads of the plate map, choose *zone numbers* in **labels from**,
   **Accept**: the grid is added in one step, and **Edit > Undo** removes the whole grid at once.

## Tutorial 4: check the spectra and explore I0

1. Go to Check. **Spectra to check** lists the two regions sent with **Add to Check** in Tutorial 2. Each row reads
   *✓ ok* and *no problem found*; select one, and the messages under the preview read *region contains 6 of 12
   measurement points* and *no bad points*.
2. Select a row and switch **view** to *sum of the detectors, bad energies marked*, then to *fluorescence and TEY
   divided by the beam (I0)*.
3. Press **Save ticked → Compare**: the two spectra are written in John Bussey's format to
   `processed/reduced/1 alpha/` and `processed/reduced/2 beta/`, and Compare lists them.
4. *Advanced*: **Run the checks on holder holderA** (in **Scans of holder holderA**). The table fills with the I0 level, the
   count rate per detector, and empty Saturated / High rate columns (the demo is clean).
5. *Advanced*: in **I0 of the holder's scans (per-pixel mean)**, tick the stack, or **Auto-select good I0**. Back in
   Reduce, a scan card's I0 list now offers *good-I0 mean* with a meaning.
6. *Advanced*: select zone 1 in Reduce, return to Check: **The four detectors on the selected zone** overlays SDD1–4
   on the zone. The note names a detector only when it strays by more than 25 % of the edge step.
7. *Advanced*: in the I0 explorer, choose the photodiode test scan (it is hidden everywhere else) and **Read**. Mesh,
   diode, their ratio and TEY are plotted normalised to their medians; the note gives the levels and the span of
   diode / mesh. On a real beamtime a span of a few per mille means the mesh is trustworthy.

## Tutorial 5: compare and fit reduced spectra

This tutorial uses the demo's `processed/reduced/` folder: NaF, CaF2 and LiF, and the two mixtures `mixture 1`
(0.5 NaF + 0.3 CaF2 + 0.2 LiF) and `mixture 2` (0.2 + 0.5 + 0.3), plus the two spectra saved in Tutorial 4 (`1 alpha`
has the NaF edge, `2 beta` 0.6 CaF2 + 0.4 LiF). Any folder of reduced CSVs works the same way.

1. Compare, **Add saved spectra…** → `processed/reduced`. Each file gives a fluorescence row (sdd-scaled: the four SDDs
   each scaled to 0–1, summed, / I0, John Bussey's fluorescence signal) and an electron yield (TEY) row; Easy mode
   shows the words, Advanced mode adds the column name. The TEY rows start with **Show** unticked, as in John Bussey's
   GUI, so the plot holds the seven fluorescence traces, the two zones of Tutorial 4 included. In Easy mode only
   John-format files are read;
   Advanced mode also reads AURORA zone CSVs and sgm_report `_xanes.csv`.
2. *Advanced*: select every sdd-scaled row, set **pre** and **post** to −20, −5 and 15, 35 in the windows row under the
   table and **Apply**. Norm stays `larch`. In the **E0** column choose `row:NaF [sdd-scaled]` on the other rows: they
   now share NaF's E0, and their windows with it.
3. *Advanced*: tick **mark peaks**: the maxima are dotted and listed under the plot. Select one row and read its E0 and
   edge step in the diagnostics panel.
4. Tick **Fit** on the sdd-scaled rows. Go to Fit > **LCF**, target `mixture 1 [sdd-scaled]`, tick the three end
   members, **Run LCF**: the coefficients with ± from 200 bootstrap draws (about 0.51 NaF, 0.29 CaF2, 0.20 LiF),
   χ²ν and the R-factor, and the chip *converged*. In Advanced mode the detail under the table gives the F-test of
   each component.
5. *Advanced*: Fit > **PCA / MCR-ALS**: **Update PCA scree** (three components explain 99.9 %), three components,
   **Run MCR-ALS**. Compare the proportions with the LCF.
6. *Advanced*: Fit > **Peaks**: **trace** = NaF, two peaks, **shape** pseudo-voigt, **Run the decomposition**. Read the
   centres, FWHM (eV) and eta in the table and the areas under it, **Export…**.
7. **Figure size…**: 3.5 × 4.5 in, 600 dpi, PNG + SVG + PDF. Compare > **Export…** writes `comparison.csv`,
   `comparison.png` (2100 × 2700 px), `.svg`, `.pdf` and `comparison_config.json`.

## Tutorial 6: a reference library (Advanced)

1. The demo's `references/` folder holds `NaF_F_K_ref.csv`, `CaF2_F_K_ref.csv` and `LiF_F_K_ref.csv` (energy and
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
   0 warnings · 1 note*, with the finding *Sample '2 beta' of holderA (sample sheet) has no zone.* Click **Show** to open holderA in
   Reduce, or tick **Ignore**; **Ctrl+Z** in Reduce brings zone 2 back.
2. **Build the report**. Browse the pages with **◀** **▶**, then **Open folder**: `reports/aurora/<date>_<time>_all/`
   holds the PDF, `figures/`, `csv/` (each CSV with its `.json` sidecar), `pages/` and `report_manifest.json`;
   `reports/aurora/latest.json` points at it.
3. Look at `csv/Group 1/`: one CSV per zone and scan with the full provenance header (zone, scan, detectors,
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
   aurora report <project> --zones zones_demo.json --groups "Group 1" --preset draft --out <folder>
   ```

## Tutorial 9: your own beamtime

The same steps on a beamtime of your own. Nothing in it needs to be renamed or prepared; every file besides `raw/` is
optional (MANUAL 12 lists what each one adds).

1. **File > Open project…** and choose the folder you downloaded from the beamline, the one that contains `raw/`.
   Home lists its days, holders and scans. If it shows no day, holder or scan, the **Project** summary's *Layout note*
   says what it found; compare the folder with the layout of MANUAL 2.3: one folder per day under `raw/`, the scan
   folders directly inside it.
2. Read the **Project** summary on the right: the calibration (*none (no config/sdd_calibration.json), nominal 10 eV
   per channel* until **File > Import SDD calibration…** adds one), the fixed emission lines of `config/roi_bins.json`
   (*none found* means total fluorescence until you set a window), the sample sheet and the plate layout. None of them
   is required.
3. Pick a holder in the top bar. Each holder is named after its survey; Survey shows its plate map with the measured
   areas of its stacks (the footprints).
4. In Reduce, step **2 · XRF window**: without fixed lines, drag the window over the emission peak of your element
   in the spectrum (the line under it gives the channels and the eV of each detector). **Save as a fixed line…** keeps
   it in `config/roi_bins.json` for every scan of the beamtime, and Maps then draws that element.
5. Optional: a sample sheet. **File > New sample sheet…** writes `samples.csv` at the root of the beamtime folder with
   the columns of MANUAL 2.4 (`Sample ID,Holder,Group,Composition,Edges,Notes`) and one row per holder; complete it in
   a spreadsheet, one row per sample, the holder written as AURORA names it, and save it as CSV. A sheet of your own
   works too: **File > Choose sample sheet…** picks it. Reopen the project (**Reload**): Reduce offers the names of
   each holder's samples, and the report follows the groups.
6. Optional: a sketch or a photo of a holder, with **Load a sketch…** in Survey.
7. Draw each sample in Reduce, **Add to Check**, look at the messages in Check and **Save ticked → Compare**.
8. Report: read **Check before building**, fix or ignore the findings, **Build the report**. Appendix C says what was
   skipped and why.
9. Keep `.aurora/` with the beamtime: `session.json` is the record of every choice (with its backups), and `aurora
   report` rebuilds the report from it at any time.

If your beamtime already has sgm_report outputs (`processed/<day>/summary.json` and its caches), AURORA uses them:
the flags in Home, **Propose zones** (*Advanced*) from the summary, and the same numbers as that report. *Advanced*:
Check > **Run the checks on every holder** reads Saturated / High rate / I0 invalid for every scan first; **Copy to…**
and **Rules…** apply one choice to many zones; **Stitch…** joins a plate measured as tiles.
