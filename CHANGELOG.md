# Changelog

## 0.4.0 (2026-11-08)
Easy mode follows John Bussey's GUI step by step and speaks plain words, Advanced mode keeps every option, zones are
drawn and erased on the Survey and in Reduce with a live preview, a beamtime of any lab opens with only its scan
folders, zones are never lost, and AURORA installs on Windows and macOS.

### For newcomers, and for your own data
- Help > Glossary explains every word AURORA shows in plain language; tooltips end with "What is …?", underlined
  words in the captions open the glossary, and small "?" buttons sit next to the step-2 preset, the Check view, the
  Compare signals and the Fit target.
- Getting started on Home (Easy mode): what a beamtime folder looks like and the steps before a project is open, then
  a row with the steps done and the next one, its button and Hide, and a Next link in the top bar. Settings "show the
  getting-started guide (Easy mode)". F1 in Easy mode opens the user guide's quick start at the page, with a
  screenshot of that page in Easy mode.
- Easy mode speaks plain words everywhere, the expert term in parentheses; Advanced mode keeps the precise terms.
  Reduce step 4 says in one sentence how the spectrum is made (the exact settings in its tooltip); maps say what their
  colour shows and their colour scale reads low to high; Check says what to look for and its messages are plain;
  Compare names signals in words and draws a spectrum it cannot scale dashed, with the reason; Fit says what a
  coefficient means; the report has one plain row per kind of finding and "How to read the figures". Errors read in
  plain words (a protected or read-only folder, a file open in another program, a missing share, a full disk).
- A beamtime AURORA may not write into (a locked drive, a read-only share, a protected folder) gets a banner with
  **Choose a working folder…**: the outlines, the checks, the Compare library, the caches, the saved spectra and the
  reports then go to a folder of your own, remembered for that beamtime, and nothing is written into the beamtime.
  Until then Check offers Show in Compare without saving and Build the report is disabled with the reason.
- A new sample takes its name and group from the sample sheet when the stack's name holds a sample ID of the holder;
  without a sheet row the sample box offers the labels of the holder's scans; Add to Check on a sample still called
  "zone N" opens a field "Name this sample:" (Enter names it, Esc keeps "zone N").
- A session saved on another computer says so when it opens (session.json keeps a short hash of the computer, never
  its name).
- After two newcomers tried it (second round): a grain drawn beside the selected sample starts a new sample (touching
  is not adding; Ctrl adds, a brush paints on), the Survey has **New sample**, Esc and a click on the bare map
  deselect, and a stroke that grew a sample says how to start the next one; each sample is drawn as the pixels it
  holds, so an erase shows; Easy says sample and pixels everywhere; footprint labels stay inside their footprints;
  a click on a sample-sheet row (now with its notes) names the next sample, and Rename offers the sheet's names;
  Reduce's stack map colours from 0, its scan list leads with the sample's name; a sample on a stack that stopped
  early gets one sentence everywhere; the top bar's Next link reads on the navy bar and links follow a theme switch;
  a read-only beamtime opens without a box; a working folder asks before it adopts outlines saved earlier and keeps
  the holder; Check marks rows already saved, names the electron yield when it falls, and the guide counts saved
  spectra from the files; Compare names every curve and says they are offset; the report can start with the
  Compare plot, draws Appendix B as the pixels each sample holds, wraps its captions and reads in plain words; the
  first report asks once for the name to print; Help > Quick start.
- A beamtime with only `raw/<day>/<scan>` opens and works: every configuration file is optional, and a file of the
  wrong shape never stops a project; Home and `aurora project` say what was left out and why. The project's own
  `config/sdd_calibration.json` is used before the CLS toolkit's file next to the beamtime (named when used); File >
  Import SDD calibration… adds one.
- Sample sheets of any lab: other encodings, separators, title lines and column names; File > Choose sample sheet…
  and New sample sheet…; the summary names the columns read and every CSV not used.
- Holders keep their numbers; day folders under other names, scan folders without _survey / _stack and scans one
  level too deep are read or explained; scan labels and edges follow any lab's naming (`config/naming.json` for a
  beamtime's own words), and the edge is checked against the energies. A sample named dark_glass is no longer a test
  scan; a stack can be treated as a sample or a test by hand (Home, right-click).
- Without a fixed line for the edge's own line, a new sample starts on the window of that line: the calibrated line
  of the edge, else John Bussey's 60:83 for F K (his GUI's default, so the saved files equal his), else the line of
  the edge on the nominal 10 eV per channel. Save as a fixed line… writes `config/roi_bins.json`; a zone on a line the
  beamtime does not define is flagged in Reduce and before building.
- The hand-drawn holder sketch is optional: the Survey shows a Sketch card only when the holder has one; **Load a
  sketch…** (next to the holder, and in the map's right-click menu) and **Remove the sketch** manage AURORA's copy in
  `.aurora/sketches/`, and `sketches/` and `zones_html/sketches/` are read too. The sample card is one line when there
  is no sheet or no row for the holder, and Grid of zones hides the rows it has nothing for.
- Survey: opens on the beamtime's own line; the first view holds the survey map and every scan and zone of the holder
  and says when some lie outside the map (the map's right-click menu offers Fit view); a holder without a survey map
  and a missing or unmatched sample sheet are explained. Grid of zones can save its grid as a plate layout.
- The sample sheet reader also recognises the columns Holder, Group, Edges, Formula and Notes (names compared without
  case, spaces or punctuation).
- The demo beamtime is neutral and carries the whole Easy job: made-up names (holder holderA, samples 1 alpha and
  2 beta, groups Group 1 and Group 2, spectra NaF, CaF2, LiF, mixture 1 and 2), a survey over the stack with a bright
  pad under each sample, a sample sheet in plain columns and a README.
- Nothing of one lab or beamtime ships: neutral examples in the code and the manuals, and tests that keep account
  names, beamtime folder names and an optional local list of words out of the published files.
- Home's tree has a Pixels column, edges in words, "not used" holders and flags in words; the scan summary starts with
  what the scan is; opening shows "Reading <name> … i of n scans"; the save indicator dates an earlier save.

### Window, help and navigation
- Light and dark themes (View > Appearance: follow the system, light or dark) with contrast checked for text and
  curves, and three text sizes (View > Text size). Plots, maps and legends follow a theme change at once; series keep
  their colours and dash styles in both themes, and status cells show an icon with a word. A switch takes a few
  seconds on a large project, with a wait cursor. Check boxes in lists, tables and menus keep the standard 14 px size
  at every text size; stand-alone check boxes and radio buttons grow with the text.
- Easy and Advanced modes. AURORA starts in Easy mode, the basic path of John Bussey's GUI; Advanced mode (the
  top-bar switch, View > Advanced mode, Ctrl+Shift+A) shows every option. Switching never changes a value, and Maps
  is part of Advanced mode.
- The manuals (user guide, manual, tutorials, installation) open inside AURORA from the Help menu, with search and
  links. F1 opens the manual at the current perspective or tab, the **?** of a card opens its section, Shift+F1 is
  What's this?, and Help > Keyboard shortcuts (Ctrl+/) lists every shortcut that works, with the Advanced tools
  marked "(Advanced mode)". Code blocks follow the text size and wrap, paragraphs are spaced, Esc closes the help
  window, and the release notes in the update dialog look like the manuals.
- A holder switcher in the top bar (project ▸ day ▸ holder), View > Previous / Next holder (Ctrl+PgUp /
  Ctrl+PgDown), View > Go to (Ctrl+1 to Ctrl+8), Ctrl+Return for the main action of the page, and an Edit menu.
  Enter closes About, and the first Tab on the welcome page reaches Open project….
- macOS: Full screen is Control-Command-F, Option-Command-Up / Down also step the holders, and the interface uses the
  system font (no missing font is looked up at start).
- Long tasks run in the background on one progress strip with the elapsed time and Cancel (Esc); Cancel of "Read every
  scan of the holder" and of "Run the checks" stops within one energy. A failure opens one error dialog with Copy
  details and Open log folder, an error in a page's update after a task is reported instead of being swallowed, and
  finished tasks no longer stay in memory over a long session. The window closes within seconds even while a task
  runs or the project sits on a share that cannot be reached.
- Every control has a tooltip and an accessible name in one vocabulary (normalisations read "/ I0, edge jump = 1"
  and so on; spin boxes carry their units). An empty perspective says what to do next, its buttons say what they do,
  and a button that cannot act says why.
- No control is clipped at 1200 × 760: a perspective that needs more height than the window has (at the minimum size,
  or with Large or Larger text) scrolls instead of squeezing its cards, so plots keep a readable height and nothing is
  printed over anything else. The help window and the Rules and Import zones dialogs fit the screen with their buttons
  reachable. Disabled check boxes show their box, and spin boxes inset their value like the other fields.
- File dialogs start in the folder last used for that kind of file, exports say what they wrote, and a failed export
  gives a calm message. SVG exports of plots work again on Qt 6.12, and the LCF export writes CSV, SVG and PNG.
- Footprint, zone and grid-preview outlines have a dark edge and a light rim of their own hue, so they read on every
  tone of every colour map, also over bright samples. Survey, Reduce, Maps Explore and each elemental map show a
  colour scale naming the quantity ("counts / I0", "log10(counts / I0 + 1)").
- Renamed controls: Reduce **New sample** and **Propose zones**, the map energy in eV, **de-spike** with its k box;
  Compare **Add saved spectra…** and **Remove**; Report **Browse…**. Spectra sent from Reduce are labelled with the
  scan time, for example "1 alpha · holderA 09:27".
- A folder or an .h5 file dropped on the window opens it. "Open the last project at start" runs after the window
  appears, and a last project that cannot be reached gives a note instead of blocking the start.
- Help menu: Open the demo beamtime (written in the background, with reduced spectra in John Bussey's layout and a
  reference folder), Run self-test…, Report a problem… (Copy diagnostics with the home folder written as ~, a link to
  the issues page and a reminder that GitHub issues are public), Check for updates… (on request, or once a day at
  start when Settings asks; nothing is ever downloaded, and it says so calmly while no release is published),
  Licences… and About with the build.
- The credits are the same everywhere (About, the report cover and footers, the self-test report): AURORA is version
  2 of John Bussey's SGM XAS GUI, based on James J. Dynes' SGM_Beamline_Analysis notebooks. Help > Licences shows them
  as a table of what AURORA reimplements and what stays in the original tools.
- A log file in the user log folder records every session and every unexpected error.

### Projects and Home
- Opens the folder John Bussey's GUI used: a loose folder of scan folders (`CLS GUI/`) is a project of its own, with
  real days read from the scan files and its zones saved inside it; one scan's `.h5` file opens its project. A folder
  that holds the day folders directly, without the `raw/` level, opens as a beamtime.
- Opening a day or scan folder (or one `.h5`) of a beamtime with more days says so in the path line ("2026-11-03 only
  (1 of 4 days)") and in the project summary; **Show every day** opens the whole beamtime, and Reload keeps the day
  alone.
- Opening a file that is not a scan's `.h5` says what it is: one file of a scan (open its scan folder), an archive
  (extract it first) or another file, and the panel then offers Choose another… only. Open project… is a folder
  dialog; a scan's `.h5` opens by a drop or from the command line. On macOS a folder sent with Finder's Open With >
  AURORA, or dropped on the Dock icon, opens.
- Holders continue across midnight: a stack recorded after midnight before the next survey stays with its holder,
  marked *(next day)* in Home.
- Home has a welcome page (Open project…, Open the demo beamtime, Read the tutorials) and a recent-projects list that
  greys projects that are not reachable; File > Open recent lists the last ten, with Clear list.
- Home shows a project summary (days, holders, scans by status, calibration, ROI bins, sample sheet) and, per scan,
  John Bussey's Scan analysis summary: grating, mirror stripe, time per map and estimated total, grid and pitch,
  energy regions, energy folders found against planned, and the incomplete maps. Every column of the scan tree is
  visible at 1200 px.
- A project that cannot be opened says why, once, with Retry and Choose another (the progress strip does not repeat
  the raw error), and a last project that fails at start is named in one sentence. The window never waits for a slow
  share, opening a project while another is still being read opens the new one, and checks or a stack still being
  read for the previous project never end up in the new one.
- Survey (Advanced): **Grid of zones…** cuts a block of pads into zones from `config/plate_layout.json` or the
  sample sheet, with rotation for plates mounted turned, optional snap to the survey pads, and a dashed preview until
  Accept.

### Zones and the session
- One AURORA at a time edits a project. A second AURORA on the same beamtime (on this computer, or on another one
  over a shared folder) opens it read-only and names the one that holds it: close that one, then Reload. The lock of
  an AURORA that crashed is taken over.
- Zones attach to every complete stack of their holder from the stage coordinates, without Load all: a second stack
  over the same samples gets its settings at once and no longer drops out of the report.
- Undo and redo for every zone, settings and good-I0 edit (Ctrl+Z; Ctrl+Y or Ctrl+Shift+Z), naming the edit. One
  stroke on a map, one Propose zones, one import, one "Untick all" over spectra of several holders and one save of the
  settings rules each undo in one step; Edit > History… in Advanced mode. Delete no longer asks for a confirmation.
- Zones are never lost: atomic saves, `session.json.bak`, timestamped backups in `.aurora/backups` (at most one per
  10 min, also after a move to a time zone further west, and one before every import, proposal, rule and restore),
  automatic recovery of a damaged `session.json` from the newest saved state, and File > Restore zones from a
  backup… (Advanced mode), which brings the rules back too. A `session.json` changed on disk by another program is
  kept in `.aurora/backups/` before AURORA writes over it.
- Closing the window or opening another project with zone edits that could not be saved (a read-only share, a
  protected folder, a newer session, a full disk) asks first: Export zones… (in Easy mode too), Discard or Cancel.
- A zones file or session with a malformed rectangle, painted square or polygon is refused when it is read, with a
  message naming the zone, instead of every later edit of that holder silently not being saved.
- Import zones… merges by default and asks per holder (Merge, Replace, Skip), skips duplicate zones, backs up first,
  and reports what it did. Zones exported from a one-day project and imported where the holder repeats over days land
  on that holder.
- A save indicator in the top bar (unsaved / saved 12:03 / read-only), warn-coloured when edits cannot be saved. A
  folder that cannot be written is read-only from the moment it opens; a failing save names the file plainly, leaves
  no temporary file and does not slow the window. Nothing is written when nothing changed, and a pending change
  always goes to the project it belongs to.
- File > Reload keeps the holder, zone and stack on screen, the stacks already read and the undo history.
- `session.json` carries a versioned header and keeps every key it does not understand; a session written by a newer
  AURORA opens read-only instead of being overwritten. Export zones… still writes the zone tool's version 1. A
  `session.json` or `zones_all.json` saved with a UTF-8 byte-order mark (Notepad, PowerShell 5) opens normally.

### Drawing and erasing zones, with a live preview
- One tool bar on the Survey's plate map and on Reduce's stack map, in Easy and Advanced: **Draw** / **Erase**,
  **Move**, **Rectangle**, **Brush** (with its size, in Easy too), **Lasso**, **Polygon** (Advanced), **Undo**,
  **Clear**, and in Advanced **every stroke starts a new zone**. The buttons no longer carry John Bussey's tool names
  (rect+, rect-, brush+, brush-, lasso+, polygon+); the manual names them once.
- Every drag or click on a zone map is one stroke, written in one edit and undone in one step: Ctrl+Z undoes a whole
  brush stroke, and two quick strokes are two steps. The right button, or Shift held when the drag starts, uses the
  other mode with the same shape; a right click still opens the zone menu. Esc or Ctrl+Z during a drag cancels it, and
  so does another scan or holder picked meanwhile. A drag may start in the margin beside the map, on its axis: it
  starts where the button went down (it started a whole axis width to the right, so a rectangle round the scan missed
  its first column). The tool bar's Undo names the step it undoes in its tooltip.
- While a stroke moves, the map highlights the points it adds (in the zone's colour) or removes (crossed), the brush
  shows its square, and a line on the map says what the release will do (*adds 12 points to zone 3 'NaF'*, *new zone
  4: 12 points*, *erases 5 points from zone 3*); between strokes it says what a drag does, while the pointer is over
  the map.
- A stroke over no measured point, or an erase that removes nothing, is not written, and the map says why; no stroke
  creates an empty zone, and a click outside the scan no longer adds an empty point rectangle. A brush away from
  every point paints nothing (it painted the nearest edge pixel from any distance), and a fast brush stroke leaves no
  gap.
- The zone a stroke goes to: an erase goes to the selected zone, or to the zone under it; a draw stroke that neither
  overlaps nor touches the selected zone starts a new zone ("draw one sample, then the next"); Ctrl held at the
  release adds it to the selected zone.
- Erasing with the lasso or the polygon keeps the outline exactly (zone key `epolys`, drawn dashed; the HTML zone tool
  gets erase squares; an AURORA older than 0.4 keeps the key but does not subtract it). Drawing over an erased area
  takes it back on the scan it is drawn on: the newest stroke wins.
- Survey: zones are drawn and erased on the plate map. The page opens in Move: a click inside a zone selects it, a
  click on a stack outside the zones opens it in Reduce, and a double-click inside a zone opens the stack under it
  with the zone selected. The zone menu offers *Open … in Reduce* for every stack the zone covers, the hover line
  names the zone, and N, Delete, Alt+Up / Alt+Down and F work there too. The zones table shows two zones at the
  smallest window and selects the zone a stroke creates.
- Survey: a **Zone preview** shows the zone's XRF emission from its survey points (counts per point; the window of the
  map's channel shaded and named, the other lines ticked; the curves named under the plot) and its XAS on a stack it
  covers, live while drawing; a stack not loaded is read once the stroke is written, with Cancel. Its empty states
  say what to do.
- Reduce: the preview follows the stroke while it is drawn (at most every 0.15 s; on a scan where that is slow,
  without the de-spike, then at the release), and the region caption counts the change (*(+12 while drawing)*). An
  empty preview says why: no scan, no zone, an empty zone, a zone outside this scan (naming the scans it covers). The
  display toggles are one **Show ▾** menu and the readout is the line under the plots; at the smallest window the
  page scrolls rather than squeezing the three panels.
- Step 2: **spectrum of** offers *survey, inside the zone* (John Bussey's "XRF from survey, only inside region").
- Keys of both maps: **D** / **E** / **X** set or switch Draw and Erase; **R**, **B**, **L** and **P** keep the mode,
  so **E** then **R** erases with a rectangle (**E** alone was the former rect-); **B**, **Shift+B** and **+** / **−**
  work in Easy mode too.
- Faster: the coherent de-spike is vectorised (the same values, bit for bit); zone traces are cached and shared by
  Reduce, Check and the Zone preview; a hidden Check or Survey waits until it is shown; a zone edit on a large holder
  takes about 0.1 s instead of 2 to 6 s, and the selected zone's pixel outlines are one item.

### Reduce
- In Easy mode Reduce follows John Bussey's four steps: 1 · Scan, 2 · XRF window, 3 · Region, 4 · Reduce, with a
  read-only line of the settings used and **Add to Check**. Step 4 states the reduction ("zone mean (John Bussey's
  reduction)", "de-spike k = 6 (John Bussey's default: off)") and lists any setting in force that differs from the
  Easy defaults: a flagged SDD still in the sum, an I0 flagged invalid still used, the earlier de-spike rule of an
  older session.
- XRF windows (John Bussey's step 2): the emission spectrum of the stack, the survey or the region, four draggable
  per-detector windows, lo / hi in channels with eV, and presets (the fixed lines, the line of the edge with a
  calibration, custom). Custom windows are summed from the raw spectra and used by previews, Check, Compare and the
  report. The fixed F Kα bins read this way equal the sgm_report caches, the line-of-the-edge preset gives exactly
  sgm_report's fixed bins (per-detector calibration), and John Bussey's own reduction of BO3_LiSO3F (windows 60-83
  and his polygon) is reproduced at all 311 energies.
- Changing lo / hi or dragging a window in Easy mode (or in Advanced mode with "same range" ticked) moves all four
  windows by as many channels, so each detector keeps its own offsets. Without `roi_bins.json`, step 2 offers John
  Bussey's default window 60:83 for an F K stack and says that the total XRF holds every emission line.
- Map clicks: a click selects a zone (the smallest one wins), Shift+click applies the tool to one pixel, stray drags
  are ignored; the **Lasso** and the **Polygon** take the pixels whose centre is inside (John Bussey's rule);
  right-click menus on zones and on the map; **Load polygon…** (in Easy mode too) / **Save polygon…** read and write
  John Bussey's `_polygon_roi.json`.
- A sample label picked from the list renames the sample at once, and a sample still called "zone N" is named as such
  when sent to Check and when saved. Ctrl+Z / Ctrl+Y in the sample, group and comment boxes reach Edit > Undo / Redo
  when the box has nothing of its own to undo.
- The preview labels its energies only under the lowest of its three panels, and its y tick labels never touch.
  Zones previewed with the good-I0 mean read the good-I0 scans in the background, with Cancel (in Check and Send to
  Compare too).
- The automatic pre-edge window (pre left blank) ends at least 5 eV below E0 when the first 15 % of the scan reaches
  the edge, in Reduce, Check and the report: on a scan that starts close to its edge the Easy trace's white line was
  the minimum of the trace (-0.27) and is now its maximum (+1.2). A note in Check and the report says when the
  window was shortened.
- The preview's legend sits in a line of its own under the fluorescence panel (SDD1 to SDD4 and the sum, a detector
  left out of the sum marked so), never over the rise at the edge nor cut at the bottom of a low panel, and the
  plots always fit their panel after a resize. One line under the plots says what the grey bands are and gives the
  hover readout while the pointer is over them. The step-2 plot labels the decades of its log axis only, and its
  x axis reads *channel (about 10 eV each)*. At 1500 × 940 in Advanced mode the zone's comment shares the group's row
  and the preview's toggles (**Show**, **all scans**) its title row, so the per-scan settings card shows a row more
  and the page does not scroll. Compare's y axis label fits the plot, and the Survey's sample-sheet table shows its
  column titles whole, without row numbers.
- Batch settings: **Copy to…** a card (all scans of the zone, this scan in every zone, every scan of the edge),
  Ctrl+Shift+C / Ctrl+Shift+V, and **Rules…** with a live table of what would change, Apply now and Save as default
  (scans that arrive later inherit them). A field set by hand on a card is pinned.
- Easy mode never hides a setting silently: the chip **advanced settings in use** names what differs from the Easy
  defaults, including a flagged SDD that is still on, with **Reset to Easy defaults**; the de-spike rule of an older
  session is named as such, not as an Advanced-mode setting.
- Keyboard shortcuts: the zone keys of both maps (see Drawing and erasing zones), N, Delete, Alt+Up / Alt+Down
  (samples), [ / ] (scans), F (fit the view) and Ctrl+Shift+C / Ctrl+Shift+V (the scan settings), listed in Help >
  Keyboard shortcuts.

### Check
- In Easy mode Check is John Bussey's "Check scans" step: the spectra sent from Reduce, his sanity messages as ok /
  check / bad chips, a **Looks OK** tick, five preview views, and **Save ticked → Compare**. The most serious warning
  (a saturated SDD in the sum) heads its row, and the I0 list shows its numbers on a second line. The Check queue is
  saved with the session: Reload (F5) and a restart keep it.
- **Save ticked → Compare** writes John Bussey's file formats (`_full_reduced.csv`, `_absorption.csv`,
  `_polygon_roi.json`, `_gui_settings.json`) to `processed/reduced/<sample>/`; on his BO3 stack the numbers equal his
  file. The four files of a sample are replaced together or not at all: a file open in another program leaves them as
  they were, the other samples are still saved, and one calm message names the file; a folder that cannot be written
  gives one calm message, never an error dialog. An overwrite removes the other ROI file of an earlier save
  (`_polygon_roi.json` / `_zone.json`). Two samples whose labels differ only in letter case (`1 alpha`, `1 ALPHA`) no
  longer overwrite each other on Windows and macOS, and two zones of one scan with one name get `_2`, `_3`. The note
  ("1 spectrum saved") names the folder that holds every saved sample.
- In John Bussey's file formats on the stack's fixed bins (the Easy default), the energies the reduced stack left out
  (an incomplete detector map) are read from the raw folder by his rule, as his GUI does; `sdd_total` is always the
  sum of the written `sdd1`-`sdd4` columns; `spike_z` keeps his meaning (his "Detect spikes" threshold, None: AURORA
  does not run it), and AURORA's de-spike k is written under `despike` / `despike_k`.
- The checks are kept in `.aurora/qc.json`, shown again when a project opens, checked again when a scan changes, and
  **Run the checks on every holder** checks the whole beamtime in one job.
- AURORA's own verdicts (saturated SDDs, invalid I0) set the defaults of new zones and appear in the flags with their
  source, also on a beamtime without sgm_report; the I0 reference only uses scans within ±150 eV. In Easy mode a
  sample drawn before the verdict (Check's own probe, or an sgm_report summary that Reload finds) follows it: a
  saturated SDD leaves the sum and an invalid I0 takes the good-I0 mean, unless the field was set on a card (one undo
  step, named in the status line). A ticked detector that counts nothing in the window is a warning, as in John
  Bussey's GUI (the spectrum of the other detectors stays valid), and its message says how to leave it out.
- New scans use a coherent de-spike that keeps real white lines (each detector is compared with the median of the
  others, so a white line common to all four stays); spike energies are reported in eV. Its threshold is never below
  k times the trace's point-to-point noise, so ordinary noise on the smooth traces of large zones is no longer taken
  for spikes. I0 is de-spiked only where the fluorescence or TEY divided by it shows the outlier too: a beam dip that
  the signals follow is kept, so it divides out. "n points near the edge were treated as spikes" refers to the
  measured edge of the spectrum, not the tabulated one.
- IPFY is computed when chosen (I0 / counts of a non-resonant line) and becomes the default when the sgm_report mode
  says IPFY; energies with I0 below 20 % of its median are left out of every / I0 trace. IPFY with the edge's own line
  (F Kα on an F K scan) is named 1 / PFY in the traces' notes, and Check warns.

### Compare and Fit
- In Easy mode Compare is John Bussey's view: Show, Fit, Label, Signal, Shift eV and Colour, rows from his saved files
  only (raw / I0 plus larch). Every cell has a typed editor, and a value that cannot be used is put back with the
  reason in a message line.
- Easy mode states the normalisation and John Bussey's windows under the plot and John Bussey's LCF options on the LCF
  line, starts a John-format file's TEY row hidden as his GUI does, and shows **advanced settings in use** with
  **Reset to Easy defaults** when a row's hidden columns were set in Advanced mode; its empty state and the folder
  dialog name only what Easy mode reads. "sdd-scaled" is explained (the four SDDs each min-max scaled, summed, / I0,
  as in John Bussey's GUI).
- The Compare library is saved with the project and comes back on the next start, zone traces included. Configs keep
  absolute and relative paths, carry the traces without a file in a sidecar, read John Bussey's keys, and
  **Locate…** finds a moved folder. A damaged library is kept as `library.corrupt-<time>.json`, with its arrays,
  instead of being overwritten with an empty one.
- E0 choices per row: smoothed derivative near the tabulated edge (scatter below 0.5 eV at 2 % noise, 4.4 eV
  before; never the first or last energy or the end of the search window), half step, tabulated edge, fixed value,
  or locked to another row. The E0 in force is a column and in the diagnostics, with a note when it lies far from the
  tabulated edge. The legacy normalisation honours its windows.
- A signal that falls across the edge (a negative edge step, e.g. IPFY of the U N4,5 scans) keeps its orientation, as
  in larch, with a note in Compare's diagnostics, Check and the report, instead of being flipped silently into a
  rising edge. TEY rows with a settling transient now normalise and plot, and a row that cannot be normalised is
  named with its signal, energy range and reason, with what to do in Easy or Advanced mode.
- Scale is a plot multiplier only, as in John Bussey's GUI: LCF, MCR-ALS, Difference and the CSV export use the
  unscaled traces. The labels of the stacked plot never print over each other (a label that cannot be placed is left
  out until the plot is taller or zoomed in).
- Export… with first derivative ticked writes the derivative the figure shows (`<label>-d/dE <signal>` columns). Fit
  (LCF, peaks, MCR, ITFA) and Compare exports are refused in a beamline `raw/` folder or a protected folder before any
  file or folder is made, and their CSVs are written in one step.
- Publication figures at a fixed size for Compare and Fit (**Figure size…**: 3.5 × 4.5 in, 600 dpi, PNG, SVG and PDF
  with editable text), drawn again on white instead of a screen grab.
- LCF is weighted and reports χ²ν with N and p, the R-factor, uncertainties that hold on correlated residuals
  (moving-block bootstrap; without a bootstrap, the covariance scaled by √χ²ν), an F-test per component and bound
  flags. LCF, MCR-ALS and ITFA run in the background with Cancel, and say "cancelled" or "failed: …" after a Cancel
  or a failure. The MCR-ALS and ITFA tolerance is relative, as labelled, so the scale of the data does not change the
  result.
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
  existing 0.3 cache is converted without reading the raw data again. A damaged cache (a cut or corrupted
  `cube_aux.npz`, any unreadable member) is rebuilt from the raw data.
- A hypercube that finishes after a switch of holder, stack or project is not shown in the wrong place; it stays
  cached, so Load hypercube opens it at once.
- With / I0, an energy whose I0 is below 20 % of its median (beam loss) is left out of the XANES of the ROI, the
  clusters and both PyMca exports, as in Reduce; the exports list it in an `i0_low_ev` attribute, and the line under
  the map names it. The Reduce stack map / I0 leaves it out of its averages too.
- Maps responds faster: the map, spectrum and XANES are each cached for their inputs, dragging the energy slider
  moves only the map, and clustering, elemental maps, large deconvolutions and both PyMca exports run in the
  background with progress and Cancel.
- Cluster merger: tick clusters to see their merged mean and sum spectra, then **Make zone from ticked clusters** (in
  the holder the cube belongs to), **Send to Compare** or **Export…** (a CSV and the map as PNG). Twenty clearly
  different cluster colours, each readable on the map and the plots and matching the list; the line under the
  cluster map names the cluster under the mouse, and the k-means seed can be set so that a result can be reproduced.
- `_PCA-CA.h5` exports record the channel window of each detector.

### Report
- Reports never overwrite: each build gets its own folder `reports/aurora/<date>_<time>_<scope>/`, `latest.json`
  points at the latest five, file names carry the zone id (labels that look alike no longer share a CSV), and a file
  held open by another program is written as `<name>_2` instead of failing. A folder that cannot be written gives
  one calm message that points to **Browse…**.
- A **Check before building** card lists the findings (zones without a group or an included scan, labels not in the
  sample sheet with the closest match, stacks covered by no zone, scans covering a zone without settings, saturated
  detectors still ticked, and more), each with **Show** (opens the zone in Reduce; Enter does the same) and
  **Ignore** (remembered with the session), plus a Contents tree and a totals line that counts notes apart from
  warnings ("0 warnings · 1 note"). The check and Ignore do not freeze the window, even with hundreds of findings.
- Draft and Final presets and a holder / day scope (Advanced): a quick Draft of the current holder at 110 dpi without
  Appendix A or B, or the Final report of everything at 600 dpi. The page preview fits the card and, in Advanced
  mode, zooms to 100 / 150 / 200 % (Ctrl+wheel).
- Groups follow the order of the sample sheet, then the other groups alphabetically, Ungrouped last.
- Every report CSV carries its full provenance (settings, zone geometry, pixel count, ROI channels per SDD, data
  source, good-I0 scans, spikes and skipped energies in eV, flags with their source) and a JSON sidecar. The manifest
  holds every entry's settings, pixels, flags and checksum, and `ReportBuilder.verify()` rebuilds a past report's
  CSVs to say whether the current project still reproduces them.
- The cover, every report CSV and `aurora project` name the SDD calibration file used and its date, or say that none
  was found and the data are not calibrated. Long provenance lines on the cover wrap instead of being cut.
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
  is UTF-8 in any console or pipe. A wrong path, a folder that is not a scan, a missing zones file or a malformed
  file prints one line (and where the log is) instead of a traceback; `aurora scan` on something that is not a scan
  exits 1, and on a scan's `.h5` describes its scan folder; `aurora --help` says what AURORA is, with examples.
- `aurora report` takes `--preset draft|final`, `--holders`, `--days` and `--author` (default: Settings > report
  author, else the system account's full name; `--author ""` prints no author line). `--dpi` now defaults to the
  preset's resolution, a holder that matches nothing is named instead of building an empty report, right after
  `aurora demo` it says that there are no zones yet, and its summary counts what it wrote ("1 zone spectrum, 1 CSV,
  1 figure").
- `aurora demo <folder>` writes only into a new or empty folder and never into a protected one (written into an
  existing beamtime, it could replace raw files and `config/roi_bins.json`). Its complete stack covers 670–735 eV in
  1 eV steps (66 maps) across the F K edge, so the tutorials' Easy path ends in normalised Compare plots; a demo
  written by an earlier version is left as it is, and the current one is written next to it (`demo_beamtime_2`).
- Deep self-test: `aurora --selftest` and Help > Run self-test… run 57 named and timed checks of every core path and
  every perspective (the update check against a local server, TLS, icons, manuals and their images, the session's
  recovery, each perspective's main action) on a synthetic beamtime in a temporary folder, removed afterwards on
  every system. The JSON report names the version, build, platform, Qt and credits; the exit code is the number of
  failed checks; `--platform native` runs it on the system's own windowing. In the window it shows its progress and
  Cancel in the status bar and runs once at a time.
- Reduced-stack caches are checked against `config/roi_bins.json` and the energy folders: an edited ROI file
  re-reduces the stack (it used to open the old windows silently), a damaged cache is set aside as `*.bad` and rebuilt
  from raw, and an sgm_report cache with other windows says so and can be reduced again with the project bins (File >
  Re-reduce the stack with the project ROI bins, Advanced). The emission cache (`_emission_cache.npz`) is read
  without pickle, and a file that needs it is rebuilt from raw.
- AURORA never writes into a beamline `raw/` folder or a folder listed in `AURORA_PROTECT_DIRS`.
- Licences: LICENSE (MIT) in the repository and every installer, THIRD_PARTY_LICENSES.txt for exactly the bundled
  packages (Qt for Python under the LGPL-3.0, xraydb under the MIT licence, and the licence texts that packages name
  by a suffix, such as packaging's BSD and Apache texts and fontTools' LICENSE.external), all shown by
  **Help > Licences**.
- One version source (`aurora/__init__.py`) for the Windows version resource, the macOS Info.plist and the build
  provenance shown in About and the self-test; the minimum macOS is measured from the bundled binaries.
- Releases: a `v*` tag builds and tests everything and opens a draft GitHub release with versioned installers,
  `SHA256SUMS.txt` and notes from this changelog (`packaging/release_notes.py` also prints on a Windows console). CI
  unpacks and self-tests the portable zip, checks every file of each disk image, and gives the test suite 45 minutes.
- The repository no longer tracks the local copies of the beamtime scripts, the notes for the coding assistant or the
  internal planning document, and a test keeps private files and home-folder paths out of what git tracks. Tests
  that need real data (a beamtime, John Bussey's GUI) read their folders from `AURORA_REAL_PROJECT`,
  `AURORA_LEGACY` and `AURORA_JOHN_GUI`, or from an untracked `tests/local_paths.json` (template
  `tests/local_paths.example.json`).

### Manuals
- The user guide starts with a quick start for non-specialists (the first spectrum in five steps, what each page
  shows in Easy mode with its screenshot, and "When something looks wrong"), and "Your own beamtime" says what each
  optional file adds; the manual describes the folder layout and the sample sheet (2.3, 2.4), the working folder, the
  glossary and Getting started (2.6); Tutorial 9 opens a beamtime of one's own.
- The user guide, manual, tutorials and installation guide describe this version: Easy mode as the start mode and
  what Advanced mode adds in each perspective, every menu entry and shortcut, the session's undo and backups, the new
  subsections (opening a project, project and scan summaries, grid of zones, XRF windows, copying settings and rules,
  spectra to check, the holder checks, John Bussey's file formats, the four Maps tabs and the hypercube cache, the
  report's options, check, build folders and CSV provenance), every file read and written, and the troubleshooting
  cases. The Check thresholds are stated as John Bussey's (verified on his program), and the E0 defaults with their
  origin.
- F1, the **?** of every card and What's this? open these subsections, including each Maps tab.
- "AURORA installation and sharing" follows the real release files (per-user setup, portable zip, one disk image per
  Mac architecture, `SHA256SUMS.txt`), the measured minimum macOS (15.0), SmartScreen and Gatekeeper, where settings,
  logs and caches live on each system, how to run the self-test and report a problem, and that a portable copy is
  updated by unzipping into a new folder, never over the old one. The macOS builds are tested on GitHub's runners,
  not on a physical Mac, and the guide says so.
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
