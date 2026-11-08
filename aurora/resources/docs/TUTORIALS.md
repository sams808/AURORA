# AURORA tutorials

Eight walkthroughs, each ten minutes or less. The first seven use the synthetic demo beamtime that AURORA writes
for you, so nothing you do touches real data; the last one is the recipe for a real beamtime. Controls are described
in full in `MANUAL.md`.

Create the demo beamtime once:

```
aurora demo C:\Users\<you>\Desktop\aurora_demo
```

It contains one day with a survey, one complete F K stack (5 energies × 12 pixels), one aborted stack, one photodiode
test, fixed ROI bins for F Kα and O Kα and a two-row sample sheet. From a frozen build use `AURORA.exe demo …` or
`AURORA.app/Contents/MacOS/AURORA demo …`.

---

## Tutorial 1: open a beamtime and read the tree

1. Start AURORA and choose **File > Open project…**, then the `aurora_demo` folder.
2. Home shows one day, one holder (named after the survey, `alpha_beta`) and four scans. Read the status column:
   `complete` for the stack, `aborted` for the short one, `test` for the photodiode test, `survey` for the survey.
3. Run the same listing headless to see what the GUI saw:

   ```
   aurora project C:\Users\<you>\Desktop\aurora_demo
   ```

4. Double-click the complete stack. AURORA switches to Reduce with that scan loaded.

What you learned: how a holder is formed (a survey followed by its stacks), what the four statuses mean, and that
the headless listing and the tree agree.

## Tutorial 2: draw zones and reduce a stack

1. In Reduce, **Load all** (one stack here; on a real holder this loads every complete stack).
2. The stack map shows F Kα integrated above the edge, divided by I0. Select **rect+**, drag a rectangle over the
   left half of the map. A zone appears in the list.
3. In the zone card, pick the label `1 alpha` (offered from the sample sheet) and the group `Minerals`.
4. **New zone**, **rect+**, drag over the right half; label `2 beta`, group `Simple Glass`.
5. Select zone 1 again. The scan card below shows include ticked, SDD1–4 ticked, line F Kα, fluorescence
   "/ I0, edge jump = 1", de-spike k = 6, I0 own. Untick SDD3 and watch the preview's sum change.
6. Try **brush−** with brush 1 and click a pixel inside zone 1: it is erased from the zone (the info line counts it).
7. The status bar says `zones saved to …\.aurora\session.json`. Close and reopen AURORA: the zones are back.

What you learned: zones are in mm and belong to the holder, settings belong to the zone and scan, everything is
saved automatically.

## Tutorial 3: proposals and the zones file

1. In Reduce, **Delete** both zones.
2. **Propose…**. The demo has no sgm_report summary, so the proposals come from the bright pads of the survey map.
   Rename and regroup them as in tutorial 2, delete the ones you do not want.
3. Go to Survey. The zones are drawn on the plate map with the footprint of the stack. Click the footprint: Reduce
   opens on that stack.
4. **Export zones…** to `zones_demo.json`. Open it in a text editor: one entry per holder, zones with rects, paint
   and erase lists in mm and the per-scan settings. This is the `zones_all.json` format the browser zone tool uses,
   so zones can travel both ways.
5. **Import zones…** reads it back (zones of the same holder are replaced).

## Tutorial 4: run the checks and explore I0

1. Go to Check, **Run the checks on this holder**. The table fills with the I0 level, the count rate per detector,
   and empty Saturated / High rate columns (the demo is clean).
2. In **I0 of the holder's scans**, tick the stack, or **Auto-select good I0**. Back in Reduce, a scan card's I0
   combo now offers "good-I0 mean" with a meaning.
3. Select zone 1 in Reduce, return to Check: **The four detectors on the selected zone** overlays SDD1–4 on the zone.
   The note names a detector only when it strays by more than 25 % of the edge step.
4. In **I0 explorer**, choose the photodiode test scan (it is hidden everywhere else) and **Read**. Mesh, diode,
   their ratio and TEY are plotted normalised to their medians; the note gives the levels and the span of
   diode / mesh. On a real beamtime a span of a few per mille means the mesh is trustworthy.

## Tutorial 5: compare and fit reduced spectra

This tutorial uses CSVs. Write five with the script below or use any folder of reduced CSVs you have
(AURORA, sgm_report `_xanes.csv`, John Bussey's `_full_reduced.csv`).

1. Compare, **Add reduced folder…** → the folder. Each file gives rows for its signals. Untick **Show** on the TEY
   rows (or **Hide all** then tick the sdd-scaled ones).
2. Select every sdd-scaled row, set pre/post to −20, −5, 15, 35 and **Apply**. Norm stays `larch`.
3. Tick **mark peaks**: the maxima are dotted and listed under the plot. Select one row and read its E0 and edge
   step in the diagnostics panel.
4. Tick **Fit** on the sdd-scaled rows. Go to Fit > LCF, target the mixture, tick the three end members,
   bootstrap 200, **Run LCF**: coefficients with uncertainties, RMSE and R-factor.
5. Fit > MCR-ALS: **Update PCA scree** (three components explain nearly everything), components = 3,
   **Run MCR-ALS**. Compare the proportions with the LCF.
6. Fit > Peaks: trace = one end member, peaks = 2, shape pseudo-voigt, **Run the decomposition**. Read the centres and
   widths in the table, **Export…**.

A script that writes five synthetic spectra in John Bussey's layout (the mixture is 0.5 a + 0.3 b + 0.2 c):

```python
import numpy as np
from pathlib import Path
out = Path("reduced_demo"); E = np.linspace(660, 740, 321)
def edge(e0, w, white): return 0.2 + 1/(1 + np.exp(-(E - e0)/w)) + white*np.exp(-((E - e0 - 2)/1.5)**2)
a, b, c = edge(690, 1.0, 0.6), edge(700, 1.6, 0.2), edge(695, 1.2, 0.9)
for name, y in {"NaF": a, "CaF2": b, "BaY2F8": c, "mix": 0.5*a + 0.3*b + 0.2*c}.items():
    d = out / name; d.mkdir(parents=True, exist_ok=True)
    y = y + np.random.default_rng(1).normal(0, 0.01, E.size)
    rows = "\n".join(f"{e},{10*v},60,83,{20*v},60,83,{5*v},60,83,{5*v},60,83,2.0,{0.5*v},{40*v},{10*v},False" for e, v in zip(E, y))
    (d / f"{name}_full_reduced.csv").write_text(f"# sample_label: {name}\nenergy,sdd1,sdd1_roi_lo,sdd1_roi_hi,sdd2,sdd2_roi_lo,sdd2_roi_hi,"
        "sdd3,sdd3_roi_lo,sdd3_roi_hi,sdd4,sdd4_roi_lo,sdd4_roi_hi,I0,TEY,sdd_total,sdd_mean,bad_point\n" + rows)
```

## Tutorial 6: a reference library

1. Make a folder `references` with subfolders per element, for example `references/F/NaF_F_K.csv` and
   `references/F/CaF2_F_K.csv` (any column file with an energy column; copy two files from tutorial 5 and rename
   them).
2. **File > Settings…**, set **reference spectra folder** to that folder.
3. Compare, **References…**: the list shows `NaF F K   (F K)   NaF_F_K.csv`. Type `F` in **element** to filter,
   tick the ones you want, OK. They are ordinary library rows now, so they can be ticked **Fit** and used as LCF
   components.

The edge is guessed from the file or folder name (`<element>_<K|L3|…>` or the element's English name); files without
a recognisable edge are listed without one and never filtered out.

## Tutorial 7: maps, deconvolution and clusters

1. Maps, stack = the complete stack, **Load hypercube** (the demo cube is tiny; a real one takes a minute the
   first time and is cached).
2. Explore: window = F Kα, move the **energy** slider, watch the map. Change **ROI** to *rectangle (draw)* and drag
   on the map: the emission spectrum and the XANES on the right follow the rectangle. Drag the band on the emission
   spectrum to set a custom window.
3. Tick **deconvolve**, PSF = 300 µm, 20 iterations: the map sharpens. Switch to Wiener and compare. Untick when
   done; exports always use the measured cube.
4. Elemental maps, **Draw the elemental maps**: one map per fixed line (F Kα and O Kα in the demo).
5. Clusters: PCA components 3, clusters 2, **Run**: the cluster map and the mean XANES of each cluster.
6. **Export PyMca XANES stack** to open the same cube in PyMca.

## Tutorial 8: build the report, in the GUI and headless

1. Report: leave the title, set the output folder, keep both appendices, **Build the report**. Browse the pages
   with ◀ ▶, then **Open folder**.
2. Look at `csv/Minerals/`: one CSV per zone and scan with the full provenance header (zone, scan, detectors,
   normalisation, windows, I0 source, de-spike, flags, calibration).
3. The same report from the command line, from the session the GUI saved:

   ```
   aurora report C:\Users\<you>\Desktop\aurora_demo --out C:\Users\<you>\Desktop\aurora_demo\reports\cli
   ```

   Or from an exported zones file, only one group, without the appendix:

   ```
   aurora report <project> --zones zones_demo.json --groups Minerals --no-appendix --out <folder>
   ```

## Tutorial 9: a real beamtime, the recipe

The order that worked during the November 2026 F K campaign:

1. Open the beamtime folder (the one with `raw/`). If sgm_report ran, its summaries feed the flags and proposals.
2. Per holder: Survey to see the plate, Reduce **Load all**, **Propose…**, correct the labels and groups from the
   sample sheet, delete what is not a sample.
3. Check: **Run the checks**, read Saturated / High rate / I0 invalid. For saturated scans untick the SDDs on the zone
   cards (TEY only); for scans with an invalid I0, tick the good scans and set their cards to "good-I0 mean".
4. Select a few zones and check the four detectors; untick a detector that misbehaves on that zone.
5. If the plate was measured as tiles, **Stitch…** after **Load all**, check the seams, bake, and draw the zones on
   the stitched stack.
6. Report, all groups, build. Read Appendix C first: it says what was skipped and why.
7. Send the zone traces of interest to Compare, add references, fit.
8. Keep `.aurora/session.json` with the beamtime: it is the record of every choice, and `aurora report` rebuilds the
   report from it at any time.
