# Changelog

## 0.4.0 (2026-11-08)
Easy mode follows John Bussey's GUI step by step, Advanced mode keeps every option, zones are never lost, and AURORA
installs on Windows and macOS.

### Window, help and navigation
- Light and dark themes (View > Appearance: follow the system, light or dark) with contrast checked for text and
  curves, and three text sizes (View > Text size). Plots, maps and legends follow a theme change at once; series keep
  their colours and dash styles in both themes, and status cells show an icon with a word.
- Easy and Advanced modes. AURORA starts in Easy mode, the basic path of John Bussey's GUI; Advanced mode (the
  top-bar switch, View > Advanced mode, Ctrl+Shift+A) shows every option. Switching never changes a value, and Maps
  is part of Advanced mode.
- The manuals (user guide, manual, tutorials, installation) open inside AURORA from the Help menu, with search and
  links. F1 opens the manual at the current perspective or tab, the **?** of a card opens its section, Shift+F1 is
  What's this?, and Help > Keyboard shortcuts (Ctrl+/) lists every shortcut.
- A holder switcher in the top bar (project ▸ day ▸ holder), View > Previous / Next holder (Ctrl+PgUp /
  Ctrl+PgDown), View > Go to (Ctrl+1 to Ctrl+8), Ctrl+Return for the main action of the page, and an Edit menu.
- Long tasks run in the background on one progress strip with the elapsed time and Cancel (Esc). A failure opens one
  error dialog with Copy details and Open log folder, and the window closes within seconds even while a task runs.
- Every control has a tooltip and an accessible name in one vocabulary (normalisations read "/ I0, edge jump = 1"
  and so on; spin boxes carry their units). An empty perspective says what to do next, and a button that cannot act
  says why.
- No control is clipped at 1200 × 760. File dialogs start in the folder last used for that kind of file, exports say
  what they wrote, and a failed export gives a calm message. SVG exports of plots work again on Qt 6.12, and the LCF
  export writes CSV, SVG and PNG.
- Renamed controls: Reduce **New sample** and **Propose zones**, the map energy in eV, **de-spike** with its k box;
  Compare **Add saved spectra…** and **Remove**; Report **Browse…**. Spectra sent from Reduce are labelled with the
  scan time, for example "1 alpha · N5 09:27".
- A folder or an .h5 file dropped on the window opens it. "Open the last project at start" runs after the window
  appears, and a last project that cannot be reached gives a note instead of blocking the start.
- Help menu: Open the demo beamtime (now with reduced spectra in John Bussey's layout and a reference folder), Run
  self-test…, Report a problem… (Copy diagnostics and the issues address), Check for updates… (on request, or once a
  day at start when Settings asks; nothing is ever downloaded), Licences… and About with the build.
- The credits are the same everywhere (About, the report cover and footers, the self-test report): AURORA is version
  2 of John Bussey's SGM XAS GUI, based on James J. Dynes' SGM_Beamline_Analysis notebooks.
- A log file in the user log folder records every session and every unexpected error.

### Projects and Home
- Opens the folder John Bussey's GUI used: a loose folder of scan folders (`CLS GUI/`) is a project of its own, with
  real days read from the scan files and its zones saved inside it; one scan's `.h5` file opens its project.
- Holders continue across midnight: a stack recorded after midnight before the next survey stays with its holder,
  marked *(next day)* in Home.
- Home has a welcome page (Open project…, Open the demo beamtime, Read the tutorials) and a recent-projects list that
  greys projects that are not reachable; File > Open recent lists the last ten, with Clear list.
- Home shows a project summary (days, holders, scans by status, calibration, ROI bins, sample sheet) and, per scan,
  John Bussey's Scan analysis summary: grating, mirror stripe, time per map and estimated total, grid and pitch,
  energy regions, energy folders found against planned, and the incomplete maps.
- A project that cannot be opened says why, with Retry and Choose another; the window never waits for a slow share.
- Survey (Advanced): **Grid of zones…** cuts a block of pads into zones from `config/plate_layout.json` or the
  sample sheet, with rotation for plates mounted turned, optional snap to the survey pads, and a dashed preview until
  Accept.

### Zones and the session
- Zones attach to every complete stack of their holder from the stage coordinates, without Load all: a second stack
  over the same samples gets its settings at once and no longer drops out of the report.
- Undo and redo for every zone, settings and good-I0 edit (Ctrl+Z; Ctrl+Y or Ctrl+Shift+Z), naming the edit. A
  brush stroke, one Propose zones or one import undoes in one step; Edit > History… in Advanced mode. Delete no
  longer asks for a confirmation.
- Zones are never lost: atomic saves, `session.json.bak`, timestamped backups in `.aurora/backups` (at most one per
  10 min, and one before every import, proposal, rule and restore), automatic recovery of a damaged `session.json`,
  and File > Restore zones from a backup… (Advanced mode).
- Import zones… merges by default and asks per holder (Merge, Replace, Skip), skips duplicate zones, backs up first,
  and reports what it did.
- A save indicator in the top bar (unsaved / saved 12:03 / read-only). Nothing is written when nothing changed, and a
  pending change always goes to the project it belongs to.
- File > Reload keeps the holder, zone and stack on screen, the stacks already read and the undo history.
- `session.json` carries a versioned header and keeps every key it does not understand; a session written by a newer
  AURORA opens read-only instead of being overwritten. Export zones… still writes the zone tool's version 1.

### Reduce
- In Easy mode Reduce follows John Bussey's four steps: 1 · Scan, 2 · XRF window, 3 · Region, 4 · Reduce, with a
  read-only line of the settings used and **Add to Check**.
- XRF windows (John Bussey's step 2): the emission spectrum of the stack, the survey or the region, four draggable
  per-detector windows, lo / hi in channels with eV, and presets (the fixed lines, the line of the edge with a
  calibration, custom). Custom windows are summed from the raw spectra and used by previews, Check, Compare and the
  report. The fixed F Kα bins read this way equal the sgm_report caches, and John Bussey's own reduction of
  BO3_LiSO3F (windows 60-83 and his polygon) is reproduced at all 311 energies.
- Map tools: a click selects a zone (the smallest one wins), Shift+click adds a pixel, stray drags are ignored; new
  **lasso+** and **polygon+** tools (pixel centres inside, John Bussey's rule); right-click menus on zones and on the
  map; **Load polygon…** / **Save polygon…** read and write John Bussey's `_polygon_roi.json`.
- Batch settings: **Copy to…** a card (all scans of the zone, this scan in every zone, every scan of the edge),
  Ctrl+Shift+C / Ctrl+Shift+V, and **Rules…** with a live table of what would change, Apply now and Save as default
  (scans that arrive later inherit them). A field set by hand on a card is pinned.
- Easy mode never hides a setting silently: the chip **advanced settings in use** names what differs from the Easy
  defaults, with **Reset to Easy defaults**.
- Keyboard shortcuts: V, R, E, B, Shift+B, L, P (tools), + / − (brush size), N, Delete, Alt+Up / Alt+Down (samples),
  [ / ] (scans) and F (fit the view), listed in Help > Keyboard shortcuts.

### Check
- In Easy mode Check is John Bussey's "Check scans" step: the spectra sent from Reduce, his sanity messages as ok /
  check / bad chips, a **Looks OK** tick, five preview views, and **Save ticked → Compare**.
- **Save ticked → Compare** writes John Bussey's file formats (`_full_reduced.csv`, `_absorption.csv`,
  `_polygon_roi.json`, `_gui_settings.json`) to `processed/reduced/<sample>/`; on his BO3 stack the numbers equal his
  file.
- The checks are kept in `.aurora/qc.json`, shown again when a project opens, checked again when a scan changes, and
  **Run the checks on every holder** checks the whole beamtime in one job.
- AURORA's own verdicts (saturated SDDs, invalid I0) set the defaults of new zones and appear in the flags with their
  source, also on a beamtime without sgm_report; the I0 reference only uses scans within ±150 eV.
- New scans use a coherent de-spike that keeps real white lines (each detector is compared with the median of the
  others, so a white line common to all four stays); spike energies are reported in eV.
- IPFY is computed when chosen (I0 / counts of a non-resonant line) and becomes the default when the sgm_report mode
  says IPFY; energies with I0 below 20 % of its median are left out of every / I0 trace.

### Compare and Fit
- In Easy mode Compare is John Bussey's view: Show, Fit, Label, Signal, Shift eV and Colour, rows from his saved files
  only (raw / I0 plus larch). Every cell has a typed editor, and a value that cannot be used is put back with the
  reason in a message line.
- The Compare library is saved with the project and comes back on the next start, zone traces included. Configs keep
  absolute and relative paths, carry the traces without a file in a sidecar, read John Bussey's keys, and
  **Locate…** finds a moved folder.
- E0 choices per row: smoothed derivative near the tabulated edge (scatter below 0.5 eV at 2 % noise, 4.4 eV
  before), half step, tabulated edge, fixed value, or locked to another row. The E0 in force is a column and in the
  diagnostics, with a note when it lies far from the tabulated edge. The legacy normalisation honours its windows.
- Publication figures at a fixed size for Compare and Fit (**Figure size…**: 3.5 × 4.5 in, 600 dpi, PNG, SVG and PDF
  with editable text), drawn again on white instead of a screen grab.
- LCF is weighted and reports χ²ν with N and p, the R-factor, uncertainties that hold on correlated residuals
  (moving-block bootstrap; without a bootstrap, the covariance scaled by √χ²ν), an F-test per component and bound
  flags. LCF, MCR-ALS and ITFA run in the background with Cancel.
- Every fit says what it did: a converged / not converged / undetermined chip, tinted rows with the reason, peak
  widths as FWHM in eV for every shape, a fitted pseudo-Voigt fraction, peak areas with errors, a peak fit that did
  not converge not drawn, and ITFA refusing a matrix that changed since its MCR-ALS run.

### Maps
- Maps sums each SDD over its own channels: every map, spectrum, elemental map, RGB composite, cluster matrix and
  PyMca export uses each detector's own calibration (0.3 applied SDD1's channels to all four, so SDD2 kept only 3 of
  its 8 channels in 640-710 eV). Four **SDD1-SDD4** ticks replace the detector menu, and a detector flagged saturated
  starts unticked.
- Hypercubes are written to disk one energy at a time instead of being built in memory. The cache is 16-bit when the
  counts allow, about half the size of 0.3's; the free space is checked first; Cancel leaves nothing behind, and an
  existing 0.3 cache is converted without reading the raw data again.
- Maps responds faster: the map, spectrum and XANES are each cached for their inputs, dragging the energy slider
  moves only the map, and clustering, elemental maps, large deconvolutions and both PyMca exports run in the
  background with progress and Cancel.
- Cluster merger: tick clusters to see their merged mean and sum spectra, then **Make zone from ticked clusters**,
  **Send to Compare** or **Export…** (a CSV and the map as PNG). Clusters keep their own colours up to 20, matching
  the list, and the k-means seed can be set so that a result can be reproduced.
- `_PCA-CA.h5` exports record the channel window of each detector.

### Report
- Reports never overwrite: each build gets its own folder `reports/aurora/<date>_<time>_<scope>/`, `latest.json`
  points at the latest five, file names carry the zone id (labels that look alike no longer share a CSV), and a file
  held open by another program is written as `<name>_2` instead of failing.
- A **Check before building** card lists the findings (zones without a group or an included scan, labels not in the
  sample sheet with the closest match, stacks covered by no zone, scans covering a zone without settings, saturated
  detectors still ticked, and more), each with **Show** (opens the zone in Reduce) and **Ignore** (remembered with the
  session), plus a Contents tree and a totals line.
- Draft and Final presets and a holder / day scope (Advanced): a quick Draft of the current holder at 110 dpi without
  Appendix A or B, or the Final report of everything at 600 dpi.
- Every report CSV carries its full provenance (settings, zone geometry, pixel count, ROI channels per SDD, data
  source, good-I0 scans, spikes and skipped energies in eV, flags with their source) and a JSON sidecar. The manifest
  holds every entry's settings, pixels, flags and checksum, and `ReportBuilder.verify()` rebuilds a past report's
  CSVs to say whether the current project still reproduces them.
- The cover lists what changed since the previous build (zones added, removed or renamed, settings changed, new
  scans).
- The report author is the person who builds it: the Settings value, else the system account's full name, else a
  one-time prompt. No report names Sami Soudani by default any more, and Settings > report author starts blank.
- A build works on a snapshot of the zones, so editing zones during a build cannot change the report.

### Installation, command line and self-test
- Installers: a per-user Windows setup (`AURORA-<version>-windows-x64-setup.exe`: no administrator rights, a
  Start-menu entry, an optional desktop shortcut and an **Open in AURORA** folder verb, clean upgrades, and an
  uninstall that keeps settings and logs) and one macOS disk image per architecture (Apple silicon and Intel, macOS
  15 or later), each with its first-launch note and the licences.
- The frozen app builds reports again (matplotlib's SVG and PDF backends are bundled) and keeps matplotlib's font
  cache between launches.
- A command line that always answers: `aurora-cli.exe` prints and returns an exit code; a failing command logs its
  traceback and returns 1 instead of stopping on an error window; `AURORA.exe <folder>` opens a beamtime; the output
  is UTF-8 in any console or pipe.
- `aurora report` takes `--preset draft|final`, `--holders`, `--days` and `--author` (default: Settings > report
  author, else the system account's full name; `--author ""` prints no author line). `--dpi` now defaults to the
  preset's resolution, and a holder that matches nothing is named instead of building an empty report.
- Deep self-test: `aurora --selftest` and Help > Run self-test… run 57 named and timed checks of every core path and
  every perspective (the update check against a local server, TLS, icons, manuals and their images, the session's
  recovery, each perspective's main action) on a synthetic beamtime in a temporary folder. The JSON report names the
  version, build, platform, Qt and credits; the exit code is the number of failed checks; `--platform native` runs it
  on the system's own windowing.
- Reduced-stack caches are checked against `config/roi_bins.json` and the energy folders: an edited ROI file
  re-reduces the stack (it used to open the old windows silently), a damaged cache is set aside as `*.bad` and rebuilt
  from raw, and an sgm_report cache with other windows says so and can be reduced again with the project bins (File >
  Re-reduce the stack with the project ROI bins, Advanced).
- AURORA never writes into a beamline `raw/` folder or a folder listed in `AURORA_PROTECT_DIRS`.
- Licences: LICENSE (MIT) in the repository and every installer, THIRD_PARTY_LICENSES.txt for exactly the bundled
  packages (Qt for Python under the LGPL-3.0), all shown by **Help > Licences**.
- One version source (`aurora/__init__.py`) for the Windows version resource, the macOS Info.plist and the build
  provenance shown in About and the self-test; the minimum macOS is measured from the bundled binaries.
- Releases: a `v*` tag builds and tests everything and opens a draft GitHub release with versioned installers,
  `SHA256SUMS.txt` and notes from this changelog.

### Manuals
- The user guide, manual, tutorials and installation guide describe this version: Easy mode as the start mode and
  what Advanced mode adds in each perspective, every menu entry and shortcut, the session's undo and backups, the new
  subsections (opening a project, project and scan summaries, grid of zones, XRF windows, copying settings and rules,
  spectra to check, the holder checks, John Bussey's file formats, the five Maps tabs, the report's options, check,
  build folders and CSV provenance), every file read and written, and the troubleshooting cases.
- F1, the **?** of every card and What's this? open these subsections, including each Maps tab.
- The installation guide follows the real release files (per-user setup, portable zip, one disk image per Mac
  architecture, `SHA256SUMS.txt`), the measured minimum macOS (15.0), SmartScreen and Gatekeeper, where settings, logs
  and caches live on each system, and how to run the self-test and report a problem. The macOS builds are tested on
  GitHub's runners, not on a physical Mac, and the guide says so.
- The tutorials use the demo beamtime's own spectra and references, and every number they quote is measured on it.
- Tests keep the manuals honest: every bold control name must exist in the window, every control of the window must be
  named in the manual, every Settings row must be a setting the application reads, every link, image and quoted
  command-line option must resolve, and the stated minimum macOS must equal the measured one.

## 0.3.0 (2026-11-06)
Phase 5 and the documentation.
- Check: I0 explorer, mesh (mcc1) vs photodiode (mcc2), their ratio and TEY for any stack including the photodiode tests.
- Compare: References… picks spectra from a reference folder (Settings), edge guessed from the file name.
- Reduce: Propose… zones from the sgm_report summary or from the survey pads (Otsu); Stitch… tiles into one stack with automatic overlap halving, seam check and a baked stack usable everywhere.
- Maps: Richardson-Lucy / Wiener deconvolution of the map with a Gaussian PSF in µm (view only, exports stay measured).
- Fit: Peaks tab, decomposition into gaussian / lorentzian / pseudo-voigt peaks + arctan step + background, export.
- `aurora demo <folder>` writes a synthetic beamtime with ROI bins and a sample sheet for the tutorials.
- The Report perspective now passes the Settings author to the cover (it used the built-in default before).
- Documentation: manual, tutorials, installation, developer manual, screenshots (`docs/`).

## 0.2.0 (2026-11-06)
First complete pass over the planned perspectives.
- Home: beamtime folder discovery (days, holders, scans, status, flags), double-click opens a scan in Reduce.
- Survey: plate map with scan footprints and zones, sketch, sample-sheet rows, zones import/export (zones_all.json).
- Reduce: the zone tool in Qt (rect / brush zones in mm, per-scan settings, preview of the four SDDs, sum, TEY, I0, spikes), send a zone to Compare.
- Check: count-rate probe, saturation / high-rate, I0 validity, good-I0 selection, four-detector agreement, spike summary.
- Compare: spectra library (AURORA, sgm_report and John Bussey's CSV layouts), larch-style / legacy / TEY-line normalisation, stacked plot, peaks, diagnostics, config save/load, exports.
- Fit: LCF with bootstrap uncertainties, PCA scree, MCR-ALS, ITFA.
- Maps: hypercube explorer (window maps, ROI spectrum with draggable window, ROI XANES), elemental maps, RGB composite, pixel PCA + k-means, PyMca exports.
- Report: group-organised PDF with figures (PNG + SVG), one CSV per zone and scan, QC appendix; `aurora report` on the command line.
- Settings dialog; open the last project at start.

## 0.1.0 (2026-11-06)
Phase 0: package skeleton, readers, project discovery, Qt shell, CLI, tests, CI on Windows and macOS.
