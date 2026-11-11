"""One table of help texts: tooltip, accessible name and manual section for every control.

    from aurora.app.help_text import explain, labelled, header_tips
    explain(self.load_btn, "maps.load")                 # toolTip, accessibleName, statusTip, What's This
    row.addWidget(labelled("clip (%)", self.clip, "survey.clip"))   # a QLabel with the field as its buddy
    header_tips(self.table, {0: "check.col.time", 3: "check.col.status"})

The texts follow the 'Effect' / 'Meaning' cells of MANUAL.md, so the tooltip, the What's This text and the manual say
the same thing. Plain strings only (no Qt objects at module level).
"""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QWidget

# key -> (tooltip, accessible name, MANUAL.md anchor)
HELP: dict[str, tuple[str, str, str]] = {
    # ------------------------------------------------------------------ main window
    "main.mode": ("Easy mode: the main steps with standard settings, and the values it uses stated.\n"
                  "Advanced mode: every option, and the Maps page. Switching never changes a value. Ctrl+Shift+A "
                  "switches.", "Easy or Advanced mode", "11-easy-and-advanced-mode"),
    "main.holder": ("The holder on screen. Choose another one here, or with Ctrl+PgUp / Ctrl+PgDown.", "holder",
                    "1-the-window"),
    "main.save_state": ("Whether your outlines and settings are saved: 'unsaved' while a change waits for the "
                        "autosave (under a second), 'saved 15:16' once it is written (with the date when that was "
                        "another day), 'read-only' when they cannot be saved here.",
                        "save state", "14-the-session-saving-undo-and-backups"),
    "main.undo": ("Undo the last zone, settings or good-I0 edit (Ctrl+Z). A brush stroke, or a run of edits of the "
                  "same kind, undoes at once.", "Undo", "14-the-session-saving-undo-and-backups"),
    "main.redo": ("Redo the edit just undone (Ctrl+Y or Ctrl+Shift+Z).", "Redo",
                  "14-the-session-saving-undo-and-backups"),
    "main.history": ("Every edit since the project was opened (the last 100); click a line to go back to that point.",
                     "Edit history", "14-the-session-saving-undo-and-backups"),
    "main.history_list": ("The edits, oldest first; the highlighted line is the current state.", "edit history",
                          "14-the-session-saving-undo-and-backups"),
    "main.restore": ("Put back the zones of a backup: one every 10 minutes while you edit, one before every import, "
                     "proposal, rule and restore, and the previous save (session.json.bak).",
                     "Restore zones from a backup", "14-the-session-saving-undo-and-backups"),
    "restore.table": ("The backups of this project, newest first, with the holders and zones each holds.", "backups",
                      "14-the-session-saving-undo-and-backups"),
    "import.table": ("The holders of the file that already have other zones in this session.", "holders to import",
                     "3-survey"),
    "import.policy": ("Merge: add the file's zones with new numbers, skipping those with the same rectangles, painted "
                      "pixels and polygons as a zone here. Replace: the file's zones replace this holder's. Skip: keep "
                      "this holder as it is.", "what to do with this holder", "3-survey"),
    # ------------------------------------------------------------------ home
    "home.open": ("Open the folder of a beamtime: the one that contains a folder named raw. A day folder or one "
                  "scan folder also works (Ctrl+O); a scan's .h5 file opens when dropped on the window.",
                  "Open project", "2-home"),
    "home.all_days": ("A day or scan folder opened that day only: open the whole beamtime, every day of its raw "
                      "folder.", "Show every day", "2-home"),
    "home.reload": ("Read the project folder again: scans that arrived since the last read appear (F5).", "Reload",
                    "2-home"),
    "home.col.scan": ("The scan folder's name, without its time (HHMMSS) at the front.", "Scan", "2-home"),
    "home.col.time": ("HH:MM of the scan folder.", "Time", "2-home"),
    "home.col.kind": ("survey (one energy, the whole holder), stack (many energies, one area) or other.",
                      "Kind", "2-home"),
    "home.col.edge": ("The element and the absorption edge the scan was measured across (F K-edge: the energy "
                      "where fluorine starts to absorb), read from the scan's plan and name.", "Edge", "2-home"),
    "home.col.status": ("Complete: every planned energy was measured. In progress: files are still arriving. "
                        "Aborted: stopped early. Test: a beamline check, not a sample.", "Status", "2-home"),
    "home.col.energies": ("The X-ray energies the scan was recorded at: one energy for a survey; the first and "
                          "last energy and their number for a stack.", "Energies", "2-home"),
    "home.col.points": ("Pixels in each map of the scan (its measured spots).", "Pixels", "2-home"),
    "home.col.flags": ("Warnings about the measurement: a detector that saturated (its fluorescence is not usable, "
                       "the electron yield is), or a beam-intensity reading (I0) that cannot be trusted. From "
                       "AURORA's own checks, and from an earlier analysis's summary files when the beamtime has "
                       "them.", "Flags", "2-home"),
    "home.demo": ("Open a small made-up beamtime, to try every step without your own data (written once into "
                  "AURORA's data folder).", "Open the demo beamtime", "21-opening-a-project"),
    "home.tutorials": ("Open the tutorials: step-by-step walks through the demo beamtime.", "Read the tutorials",
                       "21-opening-a-project"),
    "home.recent": ("Projects opened before (also in File > Open recent). Double-click opens one; a greyed 'not "
                    "found' entry is missing or on a share that is not reachable now; right-click to forget it.",
                    "recent projects", "21-opening-a-project"),
    "home.retry": ("Try to open the same folder again (for example once the share is reachable).", "Retry",
                   "21-opening-a-project"),
    "home.choose_another": ("Choose another beamtime folder, loose folder of scans or scan folder.",
                            "Choose another", "21-opening-a-project"),
    "home.keep": ("Close this note and keep the project that is open.", "Keep the open project",
                  "21-opening-a-project"),
    # ------------------------------------------------------------------ survey
    "survey.holder": ("The holder whose plate map is shown (also chosen in the top bar).", "holder", "3-survey"),
    "survey.channel": ("What the map shows: the X-rays of one emission line or all X-rays (total XRF) counted by "
                       "the detectors, the electron yield (TEY), or the beam intensity (I0).", "channel", "3-survey"),
    "survey.sdd": ("The sum of the four detectors, or one detector.", "detector", "3-survey"),
    "survey.per_i0": ("Divide by the beam intensity (I0, the mesh current) when the survey's reading of it is "
                      "usable.", "divide by I0", "3-survey"),
    "survey.log": ("Show log10 of the values.", "log scale", "3-survey"),
    "survey.invert": ("Flip the colour scale (high-energy surveys, where samples are darker than the plate).",
                      "invert the colour scale", "3-survey"),
    "survey.clip": ("Trim the colour range at both ends by this percentage (0.5 % by default).", "clip", "3-survey"),
    "survey.footprints": ("Show the stacks of the holder as rectangles. With Move, a click on a complete one outside "
                          "every zone opens it in Reduce; a double-click inside a zone opens the stack under it with "
                          "the zone selected.", "show the scan footprints", "3-survey"),
    "survey.zones": ("Show the zones of the holder with their labels.", "show the zones", "3-survey"),
    "survey.sketch_load": ("Optional: a drawing or photo of the holder (PNG or JPEG), shown beside the map. AURORA "
                           "keeps a copy with the beamtime (in its .aurora folder); loading another one replaces it. "
                           "Nothing else depends on a sketch.", "Load a sketch", "3-survey"),
    "survey.sketch_remove": ("Delete AURORA's copy of the holder's sketch (the original image is kept).",
                             "Remove the sketch", "3-survey"),
    "survey.import": ("Read a zones_all.json written by the browser zone tool or by another AURORA.", "Import zones",
                      "3-survey"),
    "survey.export": ("Write the zones of every holder as zones_all.json (the browser zone tool's format).",
                      "Export zones", "3-survey"),
    "survey.grid": ("Drag a rectangle over a block of pads, then cut it into a grid of zones (rows and columns from "
                    "config/plate_layout.json when one of its groups matches the holder's scans). Click again to "
                    "cancel.", "Grid of zones", "31-grid-of-zones-advanced"),
    "survey.new_sheet": ("Write samples.csv in the beamtime folder, one row per holder with the holder filled in, to "
                         "complete in a spreadsheet (the same as File > New sample sheet…).", "New sample sheet",
                         "3-survey"),
    "survey.choose_sheet": ("Pick the CSV that lists the samples (the same as File > Choose sample sheet…).",
                            "Choose sample sheet", "3-survey"),
    "survey.grid.save_layout": ("Save this grid (rows, columns and labels) in config/plate_layout.json as a layout "
                                "group of the holder: the next Grid of zones on it offers the same grid. The file is "
                                "created when missing; the previous one is kept as a backup.",
                                "Save as plate layout", "31-grid-of-zones-advanced"),
    "survey.grid.layout": ("The plate_layout.json group whose rows, columns and sample names fill the grid.",
                           "layout group", "31-grid-of-zones-advanced"),
    "survey.grid.rows": ("Rows of pads in the rectangle, as seen on the map.", "rows", "31-grid-of-zones-advanced"),
    "survey.grid.cols": ("Columns of pads in the rectangle, as seen on the map.", "columns",
                         "31-grid-of-zones-advanced"),
    "survey.grid.fill": ("Each zone covers this share of its cell's width and height, centred in the cell.",
                         "cell fill", "31-grid-of-zones-advanced"),
    "survey.grid.rotation": ("How the plate sat in the holder compared with the layout, counter-clockwise: 180° for "
                             "a plate mounted upside down reverses the order of the labels.", "rotation",
                             "31-grid-of-zones-advanced"),
    "survey.grid.labels": ("Where the zone labels come from: the layout's cells, the holder's rows of the sample sheet "
                           "in reading order (top row first), or numbers.", "labels from", "31-grid-of-zones-advanced"),
    "survey.grid.first_row": ("The holder's sample-sheet row that labels the top-left cell.", "first sheet row",
                              "31-grid-of-zones-advanced"),
    "survey.grid.snap": ("Shrink each cell to the bright survey pad inside it (Otsu threshold of the survey map); "
                         "cells without a pad are skipped and listed.", "snap to pads", "31-grid-of-zones-advanced"),
    # ------------------------------------------------------------------ reduce
    "reduce.load_all": ("Read every complete stack of the holder, so the zones attach to all of them.", "Load all",
                        "41-scans-of-the-holder"),
    "reduce.scans": ("The complete stacks of the holder: pick one to load it (cached after the first read).",
                     "scans of the holder", "41-scans-of-the-holder"),
    "reduce.new_zone": ("Start a new, empty sample, then draw its outline on the map (Draw, then drag).", "New sample",
                        "43-zones"),
    "reduce.delete_zone": ("Delete the selected sample: its outline, its name and its settings for every scan.",
                           "Delete sample", "43-zones"),
    "reduce.propose": ("Zones from sgm_report's summary of the day, else from the bright pads of the survey map, as "
                       "a proposal to edit or delete.", "Propose zones", "43-zones"),
    "reduce.stitch": ("Join overlapping stacks (tiles) of the holder into one stack (after Load all).", "Stitch tiles",
                      "46-stitching-tiles"),
    "reduce.map_line": ("The emission line shown on the stack map.", "map line", "42-stack-map"),
    "reduce.map_sdd": ("The sum of the four detectors, or one detector.", "map detector", "42-stack-map"),
    "reduce.map_mode": ("What the map integrates: above the edge, all energies, the edge jump, or one energy.",
                        "map mode", "42-stack-map"),
    "reduce.map_energy": ("The energy shown when the map mode is 'at energy'.", "map energy", "42-stack-map"),
    "reduce.map_i0": ("Divide the map by I0.", "map divided by I0", "42-stack-map"),
    "reduce.map_log": ("Show log10 of the map.", "map log scale", "42-stack-map"),
    "reduce.label": ("The name of the sample: the names of the holder's sample sheet are offered; any name can be "
                     "typed.", "sample name", "44-the-zone-card"),
    "reduce.group": ("The sample-sheet group; the report is organised by it.", "zone group", "44-the-zone-card"),
    "reduce.comment": ("Goes into the report caption and the CSV header.", "zone comment", "44-the-zone-card"),
    "reduce.send": ("Put the normalised fluorescence sum and TEY of every included scan into the Compare library.",
                    "Send the zone's traces to Compare", "44-the-zone-card"),
    "reduce.include": ("The scan contributes to the report figures and CSVs.", "include the scan",
                       "45-one-card-per-attached-scan"),
    "reduce.sdd": ("Detector in the fluorescence sum (saturated detectors start unticked).", "detector in the sum",
                   "45-one-card-per-attached-scan"),
    "reduce.line": ("The emission window (one of the fixed lines).", "line", "45-one-card-per-attached-scan"),
    "reduce.signal": ("PFY: counts in the emission line window / I0.\nIPFY: I0 / counts of a non-resonant line (inverse "
                      "partial fluorescence yield, Achkar et al. 2011), for the sum and each SDD. New scans default to "
                      "IPFY when the sgm_report mode says IPFY or the line belongs to another element than the edge.",
                      "signal", "45-one-card-per-attached-scan"),
    "reduce.fluo": ("Fluorescence normalisation: raw; / I0; / I0 minus the pre-edge line; / I0 with edge jump = 1; "
                    "area = 1; maximum = 1.", "fluorescence normalisation", "45-one-card-per-attached-scan"),
    "reduce.tey": ("Include TEY (total electron yield) with its own normalisation.", "include TEY",
                   "45-one-card-per-attached-scan"),
    "reduce.tey_norm": ("TEY normalisation (same choices as the fluorescence).", "TEY normalisation",
                        "45-one-card-per-attached-scan"),
    "reduce.despike": ("Interpolate single-point outliers beyond k MADs of a 5-point running median.", "de-spike",
                       "45-one-card-per-attached-scan"),
    "reduce.k": ("The de-spike threshold k in MADs (6 by default).", "de-spike threshold k",
                 "45-one-card-per-attached-scan"),
    "reduce.i0": ("I0: the scan's own, or the mean of the good scans ticked in Check.", "I0 source",
                  "45-one-card-per-attached-scan"),
    "reduce.pre": ("Pre-edge window in eV, '680-684' style; blank = the first 15 % of the energies, ending at least 5 eV "
                   "below E0.", "pre-edge window",
                   "45-one-card-per-attached-scan"),
    "reduce.post": ("Post-edge window in eV, '700-720' style; blank = the last 20 % of the energies.",
                    "post-edge window", "45-one-card-per-attached-scan"),
    "preview.show": ("What the preview draws: the four detectors, their sum, TEY, I0 and the repaired spikes. On a "
                     "short preview the TEY and I0 panels hide (TEY and I0 panels brings them back).", "Show",
                     "47-preview"),
    "preview.panels": ("Show the TEY and I0 panels under the fluorescence, even on a short preview.",
                       "TEY and I0 panels", "47-preview"),
    "preview.sdd": ("Show the four detectors.", "show the detectors", "47-preview"),
    "preview.sum": ("Show the sum of the ticked detectors.", "show the sum", "47-preview"),
    "preview.tey": ("Show the TEY panel's curve.", "show TEY", "47-preview"),
    "preview.i0": ("Show the I0 panel's curve.", "show I0", "47-preview"),
    "preview.spikes": ("Mark the interpolated spikes.", "show spikes", "47-preview"),
    "preview.all_scans": ("Overlay every included scan of the sample.", "all scans of the sample", "47-preview"),
    # Reduce, WP3: map tools, XRF windows (step 2), Easy steps, batch settings
    "reduce.clear": ("Remove the outline of the selected sample to draw it again (the sample and its name stay). To "
                     "take every pixel of the map instead (the earlier SGM XAS GUI's 'Clear (full map)'), draw a "
                     "Rectangle over the whole map.",
                     "Clear the region", "42-stack-map"),
    "reduce.zones": ("The samples (zones) of the holder: pick one to edit it. A click inside a zone on the map also "
                     "selects it.", "samples of the holder", "43-zones"),
    "reduce.load_polygon": ("Read a polygon outline saved by the earlier SGM XAS GUI (<sample>_polygon_roi.json, "
                            "at least three [x, y] corners in mm) into a new sample.", "Load polygon", "43-zones"),
    "reduce.save_polygon": ("Write the selected sample as a <sample>_polygon_roi.json of the earlier SGM XAS GUI "
                            "(a sample outlined by exactly one polygon).", "Save polygon", "43-zones"),
    "reduce.window.preset": ("Which band of the emission spectrum is counted: a fixed window of the beamtime "
                             "(config/roi_bins.json), the line of the edge element (needs a detector calibration), "
                             "all X-rays, or a band you drag on the plot.", "which X-rays to count", "48-xrf-windows"),
    "reduce.window.lo": ("First detector channel of the window (included).", "window first channel", "48-xrf-windows"),
    "reduce.window.hi": ("End of the window (excluded): from 60 to 83 counts channels 60 to 82.",
                         "window end channel", "48-xrf-windows"),
    "reduce.window.source": ("The emission spectrum shown: the whole stack summed over its energies, the survey of "
                             "the holder, the pixels of the selected sample, or the survey points inside the selected "
                             "sample (the earlier SGM XAS GUI's 'only inside region').", "emission spectrum source",
                             "48-xrf-windows"),
    # zones: the tool bar of the Survey map and the stack map, the Survey's zone preview
    "zones.draw": ("Draw zones: drag on the map to add to the zone; right-drag erases (D).", "draw zones",
                   "42-stack-map"),
    "zones.erase": ("Erase: drag on the map to remove from the selected zone (E). Right-drag draws.",
                    "erase from the zone", "42-stack-map"),
    "zones.move": ("Move the view: drag to pan, the wheel zooms (V). A click on a zone selects it.", "move the view",
                   "42-stack-map"),
    "zones.rect": ("Drag a rectangle (R).", "rectangle", "42-stack-map"),
    "zones.brush": ("Paint or erase points under the brush as you drag (B); its size is beside it.", "brush",
                    "42-stack-map"),
    "zones.lasso": ("Drag freehand around the sample; the outline closes when you let go (L). Points whose centre "
                    "lies inside count.", "lasso", "42-stack-map"),
    "zones.polygon": ("Click the corners, double-click to close (P). Points whose centre lies inside count.",
                      "polygon", "42-stack-map"),
    "zones.brush_size": ("The brush covers n × n points of the map (+ / - change it).", "brush size in map points",
                         "42-stack-map"),
    "zones.undo": ("Undo the last edit (Ctrl+Z), the one Edit > Undo names. One stroke is one step.",
                   "undo the last edit",
                   "42-stack-map"),
    "zones.clear": ("Remove the drawing of the selected sample (every drawn and erased shape), to draw it again; the "
                    "sample and its name stay. One step of Undo.", "Clear the region", "42-stack-map"),
    "zones.new_each": ("Every stroke starts a new zone, even one that touches the selected zone (drawing many samples "
                       "in a row).", "every stroke starts a new zone", "42-stack-map"),
    "zones.preview.scan": ("The scan whose spectrum of the zone is shown: the scans the zone covers.",
                           "scan of the zone preview", "3-survey"),
    "zones.preview.open": ("Open this scan in Reduce with the zone selected.", "Open in Reduce", "3-survey"),
    "reduce.window.same": ("One window for all four detectors: dragging one region moves the four. Untick to set each "
                           "detector's window apart.", "same range for all detectors", "48-xrf-windows"),
    "reduce.window.det": ("The detector whose window the from / to boxes edit when the windows are set apart.",
                          "detector edited", "48-xrf-windows"),
    "reduce.window.remember": ("Save these windows as a default rule for the edge of this stack: scans of that edge "
                               "attached later use them (Rules…).", "Remember these windows", "48-xrf-windows"),
    "reduce.window.save_line": ("Save the windows of the four detectors shown now as a fixed line of this beamtime "
                                "(config/roi_bins.json, created when missing; the previous file is kept as a backup): "
                                "every scan offers it as a preset, and Maps draws its elemental map.",
                                "Save as a fixed line", "48-xrf-windows"),
    "reduce.add_to_check": ("Send the spectrum of the selected sample on this scan to Check, made as the sentence "
                            "above says (Ctrl+Return).", "Add to Check", "4-reduce"),
    "reduce.reset_easy": ("Rewrite the settings that differ from the Easy defaults (named in the chip) in one step; Undo "
                          "brings them back.", "Reset to Easy defaults", "44-the-zone-card"),
    "reduce.copy": ("Copy this card's settings to all scans of the zone, to this scan in every zone of the holder, or "
                    "to every scan with the same edge. The ticked fields travel; include and the pixel count never do. "
                    "Ctrl+Shift+C / Ctrl+Shift+V copy and paste between cards.", "Copy to", "45-one-card-per-attached-scan"),
    "reduce.rules": ("Rules that write the same settings into many cards (scope, scan filter, fields), with a table of "
                     "what would change; saved rules also apply to scans attached later.", "Rules",
                     "49-copying-settings-and-rules"),
    "reduce.rules.list": ("The rules, in the order they run (a later rule wins). Ticked rules are used.", "rules",
                          "49-copying-settings-and-rules"),
    "reduce.rules.add": ("Add an empty rule at the end of the list.", "Add a rule", "49-copying-settings-and-rules"),
    "reduce.rules.remove": ("Remove the selected rule.", "Remove the rule", "49-copying-settings-and-rules"),
    "reduce.rules.up": ("Run the selected rule earlier.", "Move the rule up", "49-copying-settings-and-rules"),
    "reduce.rules.down": ("Run the selected rule later.", "Move the rule down", "49-copying-settings-and-rules"),
    "reduce.rules.name": ("A name for the rule (shown in the list).", "rule name", "49-copying-settings-and-rules"),
    "reduce.rules.scope": ("The zones the rule reaches: the whole project, some holders, some sample-sheet groups, or "
                           "the selected zone.", "rule scope", "49-copying-settings-and-rules"),
    "reduce.rules.scope_text": ("Holders or groups, separated by commas.", "holders or groups",
                                "49-copying-settings-and-rules"),
    "reduce.rules.edge": ("Only scans of this edge ('F K', 'Se L3,2'); blank: every edge.", "edge filter",
                          "49-copying-settings-and-rules"),
    "reduce.rules.plan": ("Only scans whose folder name matches this regular expression ('_TEY'); blank: every scan.",
                          "scan name filter", "49-copying-settings-and-rules"),
    "reduce.rules.span": ("Only scans whose energies lie inside this range; 'any' leaves that end open.",
                          "energy range filter", "49-copying-settings-and-rules"),
    "reduce.rules.flags": ("Only scans with this flag (sgm_report summary or AURORA's check).", "flag filter",
                           "49-copying-settings-and-rules"),
    "reduce.rules.field": ("Tick the fields the rule writes, then set their value. Pre / post windows may be written "
                           "from the edge energy: 'E0-15..E0-5'.", "field written by the rule",
                           "49-copying-settings-and-rules"),
    "reduce.rules.override": ("Also replace fields set by hand on a card (pinned); otherwise they are kept.",
                              "override pinned fields", "49-copying-settings-and-rules"),
    "reduce.rules.apply": ("Write the ticked rules into the existing cards now (one undo step).", "Apply now",
                           "49-copying-settings-and-rules"),
    "reduce.rules.save": ("Keep the rules with the session: zone x scan settings created later inherit them.",
                          "Save as default", "49-copying-settings-and-rules"),
    "reduce.rules.col.0": ("The holder of the card.", "Holder", "49-copying-settings-and-rules"),
    "reduce.rules.col.1": ("The zone (number and label).", "Zone", "49-copying-settings-and-rules"),
    "reduce.rules.col.2": ("The scan (time and folder name).", "Scan", "49-copying-settings-and-rules"),
    "reduce.rules.col.3": ("The field that would change.", "Field", "49-copying-settings-and-rules"),
    "reduce.rules.col.4": ("Its value now.", "Now", "49-copying-settings-and-rules"),
    "reduce.rules.col.5": ("Its value once the rules are applied.", "After", "49-copying-settings-and-rules"),
    # ------------------------------------------------------------------ check
    "check.run": ("Probe seven energies of every stack of the holder (the brightest pixels only) and fill the table: "
                  "count rate, saturation, I0 validity.", "Run the checks", "52-the-holder-checks-advanced"),
    "check.i0_norm": ("Overlay the I0 curves on one scale (each divided by its level).", "normalise to the level",
                      "52-the-holder-checks-advanced"),
    "check.auto": ("Tick the scans whose I0 looks good (level, steps, roughness, non-positive points).",
                   "Auto-select good I0", "52-the-holder-checks-advanced"),
    "check.x_scan": ("Any stack of the holder, including the photodiode tests hidden elsewhere.", "stack to explore",
                     "52-the-holder-checks-advanced"),
    "check.x_read": ("Read the mcc CSVs of the stack: mesh, photodiode, their ratio and TEY.", "Read the I0 curves",
                     "52-the-holder-checks-advanced"),
    "check.col.time": ("HH:MM of the scan folder.", "Time", "52-the-holder-checks-advanced"),
    "check.col.scan": ("The scan's sample label.", "Scan", "52-the-holder-checks-advanced"),
    "check.col.edge": ("The target edge.", "Edge", "52-the-holder-checks-advanced"),
    "check.col.status": ("complete, in progress, aborted or test.", "Status", "52-the-holder-checks-advanced"),
    "check.col.i0_level": ("Median mesh current.", "I0 level", "52-the-holder-checks-advanced"),
    "check.col.i0": ("I0 verdict: ok, or invalid when the mesh current is ≤ 0 or below 10 % of the other scans.",
                     "I0", "52-the-holder-checks-advanced"),
    "check.col.kcps": ("Count rate of the brightest pixels of this detector (kcps).", "count rate",
                       "52-the-holder-checks-advanced"),
    "check.col.saturated": ("Same total at every energy while I0 varied, rate ≥ 2× the holder median, output no longer "
                            "following the input: fluorescence unusable, TEY fine.", "Saturated",
                            "52-the-holder-checks-advanced"),
    "check.col.high": ("≥ 2× the holder median without the flat signature: dead time or pile-up likely.", "High rate",
                       "52-the-holder-checks-advanced"),
    "check.col.notes": ("The reasons in words.", "Notes", "52-the-holder-checks-advanced"),
    "check.every": ("Probe every stack of every holder in one job (count rate, saturation, I0 validity), with progress "
                    "and Cancel. The results are kept in .aurora/qc.json.", "Run the checks on every holder",
                    "52-the-holder-checks-advanced"),
    "check.queue": ("The spectra sent from Reduce with Add to Check: one row per sample and scan, with AURORA's "
                    "checks. Tick Looks OK for the ones to keep; right-click for more.",
                    "spectra to check", "51-spectra-to-check"),
    "check.tick_all": ("Tick 'Looks OK' on every row of the queue.", "Tick all", "51-spectra-to-check"),
    "check.untick_all": ("Untick 'Looks OK' on every row of the queue.", "Untick all", "51-spectra-to-check"),
    "check.save": ("Save every ticked spectrum without a ✗ bad message as CSV files in processed/reduced/<sample>/ "
                   "(_full_reduced.csv, _absorption.csv, _polygon_roi.json, _gui_settings.json: the format of the "
                   "earlier SGM XAS GUI), then add them to Compare.",
                   "Save ticked spectra to Compare", "51-spectra-to-check"),
    "check.view": ("What the preview shows for the selected spectrum: the four detectors; their sum with the bad "
                   "energies marked; the beam intensity and the electron yield; the spectra divided by the beam; "
                   "the same scaled 0 to 1.", "preview view", "51-spectra-to-check"),
    "check.qcol.sample": ("The sample region (zone) drawn in Reduce.", "Sample", "51-spectra-to-check"),
    "check.qcol.scan": ("HH:MM and sample label of the scan.", "Scan", "51-spectra-to-check"),
    "check.qcol.pts": ("Pixels of the scan inside the outline, of all its pixels. More pixels give a less noisy "
                       "spectrum.", "Pixels", "51-spectra-to-check"),
    "check.qcol.bad": ("Energies repaired or left out: a spike (replaced by the value of its neighbours), a beam "
                       "intensity below 20 % of its median, or a map that is missing.",
                       "Bad energies", "51-spectra-to-check"),
    "check.qcol.messages": ("AURORA's checks of this spectrum: ✓ ok, ! check (worth a look), ✗ bad (not saved). "
                            "Hover a chip to read them.", "Messages", "51-spectra-to-check"),
    "check.qcol.ok": ("Your verdict: ticked spectra are saved by Save ticked → Compare and go into the report. "
                      "AURORA ticks the rows its checks found no problem with; a row with a ✗ bad message starts "
                      "unticked.", "Looks OK", "51-spectra-to-check"),
    # ------------------------------------------------------------------ compare
    "compare.add_folder": ("Add the spectra saved in a folder: the files Save ticked → Compare writes "
                           "(processed/reduced/<sample>/), and the reduced files of the earlier SGM XAS GUI "
                           "(_full_reduced.csv, _absorption.csv). Advanced mode also reads the summary spectra of "
                           "an earlier analysis (_xanes.csv), AURORA zone CSVs and other column files.",
                           "Add saved spectra", "61-loading"),
    "compare.reset_easy": ("Rewrite the columns Easy mode hides, on every row that differs, with the Easy defaults: Norm "
                           "larch, pre -20..-5 eV and post +15..+35 eV from E0, flattened, E0 from the derivative, "
                           "scale 1 (a trace normalised already keeps Norm none).", "Reset to Easy defaults",
                           "6-compare"),
    "compare.add_csv": ("Add any column file; the energy column is found by name or taken as the first column.",
                        "Add CSV", "61-loading"),
    "compare.references": ("Pick spectra from the reference folder (Settings), filtered by element.", "References",
                           "65-reference-spectra"),
    "compare.save_config": ("Save the whole library (files, shifts, normalisation, E0, colours, ticks) as a JSON file, "
                            "with the paths absolute and relative to it; traces without a file go to <name>_spectra.npz "
                            "next to it.", "Save config", "61-loading"),
    "compare.load_config": ("Load a library saved with Save config… (or a compare_config.json of the earlier SGM "
                            "XAS GUI); it replaces the rows.", "Load config", "61-loading"),
    "compare.export": ("Write a wide CSV of the shown traces as processed, the figure drawn again at the Figure size "
                       "(PNG, SVG, PDF) and the config JSON.", "Export", "64-diagnostics-and-export"),
    "compare.remove": ("Remove the selected rows.", "Remove", "62-the-table"),
    "compare.remove_sample": ("Remove every row of the selected rows' samples.", "Remove sample", "62-the-table"),
    "compare.rename": ("Rename the selected sample.", "Rename", "62-the-table"),
    "compare.duplicate": ("Duplicate the selected row (another normalisation or shift of the same spectrum).",
                          "Duplicate", "62-the-table"),
    "compare.up": ("Move the selected row up.", "Move up", "62-the-table"),
    "compare.down": ("Move the selected row down.", "Move down", "62-the-table"),
    "compare.show_all": ("Tick Show on every row.", "Show all", "62-the-table"),
    "compare.hide_all": ("Untick Show on every row.", "Hide all", "62-the-table"),
    "compare.pre_lo": ("Start of the pre-edge window, eV from E0.", "pre-edge window start", "62-the-table"),
    "compare.pre_hi": ("End of the pre-edge window, eV from E0.", "pre-edge window end", "62-the-table"),
    "compare.post_lo": ("Start of the post-edge window, eV from E0.", "post-edge window start", "62-the-table"),
    "compare.post_hi": ("End of the post-edge window, eV from E0.", "post-edge window end", "62-the-table"),
    "compare.apply": ("Write these pre / post windows into every selected row.", "Apply the windows", "62-the-table"),
    "compare.x_lo": ("Lower end of the plotted energy range (eV); blank = automatic.", "x range from", "63-stacked-plot"),
    "compare.x_hi": ("Upper end of the plotted energy range (eV); blank = automatic.", "x range to", "63-stacked-plot"),
    "compare.spacing": ("Offset between the stacked traces, in units of the normalised signal.", "spacing",
                        "63-stacked-plot"),
    "compare.labels": ("Label every trace at its right end.", "labels", "63-stacked-plot"),
    "compare.signal_in_label": ("Add the signal (fluorescence, TEY, …) to the labels.",
                                "signal in label", "63-stacked-plot"),
    "compare.deriv": ("Plot the first derivative of the normalised signal.", "first derivative", "63-stacked-plot"),
    "compare.smooth": ("Smoothing width of the derivative, in points.", "derivative smoothing", "63-stacked-plot"),
    "compare.difference": ("Add the difference of the two selected rows (normalised A minus normalised B) as a new row.",
                           "Difference of the two selected rows", "63-stacked-plot"),
    "compare.peaks": ("Mark the local maxima and list them under the plot (shifted energies).", "mark peaks",
                      "63-stacked-plot"),
    "compare.prominence": ("Minimum prominence of a marked peak, in units of the normalised signal.",
                           "minimum prominence", "63-stacked-plot"),
    "compare.distance": ("Minimum distance between two marked peaks, in points.", "minimum distance",
                         "63-stacked-plot"),
    "compare.col.show": ("Draw this spectrum on the graph.", "Show", "62-the-table"),
    "compare.col.label": ("Editable; Rename and Duplicate act on the selected rows.", "Label", "62-the-table"),
    "compare.col.signal": ("Which signal of the saved spectrum the row plots: fluorescence (the usual choice: the four "
                           "detector signals each scaled to 0–1 (min-max), summed and divided by I0), its plain sum "
                           "÷ I0, electron yield (TEY, the surface) ÷ I0, inverse yield (IPFY) or one detector. Advanced "
                           "mode adds the file's column name in parentheses.", "Signal", "62-the-table"),
    "compare.col.shift": ("Moves the curve along the energy axis by this many eV, to correct an energy offset (for "
                          "example between two beamtimes). Leave 0 unless the offset is known.",
                          "Shift eV", "62-the-table"),
    "compare.col.norm": ("larch (two lines around E0), legacy (constant pre / post means over absolute windows), "
                         "tey_line (pre-edge line only), none.", "Norm", "62-the-table"),
    "compare.col.pre_lo": ("Start of the pre-edge window: eV from E0 (larch, tey_line); absolute eV for legacy "
                           "(blank = auto, the first 15 % of the scan).", "Pre lo", "62-the-table"),
    "compare.col.pre_hi": ("End of the pre-edge window: eV from E0 (larch, tey_line); absolute eV for legacy "
                           "(blank = auto, the first 15 % of the scan).", "Pre hi", "62-the-table"),
    "compare.col.post_lo": ("Start of the post-edge window: eV from E0 (larch, tey_line); absolute eV for legacy "
                            "(blank = auto, the last 20 % of the scan).", "Post lo", "62-the-table"),
    "compare.col.post_hi": ("End of the post-edge window: eV from E0 (larch, tey_line); absolute eV for legacy "
                            "(blank = auto, the last 20 % of the scan).", "Post hi", "62-the-table"),
    "compare.col.flatten": ("Post-edge line flattened to 1.", "Flatten", "62-the-table"),
    "compare.col.spacing": ("Offset after this row in the stacked plot (blank = the common spacing).", "Spacing",
                            "62-the-table"),
    "compare.col.scale": ("Multiplier of this row in the stacked plot and its figure only; Fit, Difference and the CSV "
                          "use the trace unscaled.", "Scale", "62-the-table"),
    "compare.col.fit": ("Use this spectrum in Fit, as the target or as a reference.", "Fit", "62-the-table"),
    "compare.col.diag": ("The row's raw signal and fitted lines are shown in the diagnostics panel.", "Diag",
                         "62-the-table"),
    "compare.col.e0_mode": ("How E0 is found: deriv (maximum of the smoothed derivative, near the tabulated edge), half "
                            "(half of the edge step), table (the tabulated edge), fixed (the E0 eV value), row:<row> "
                            "(the E0 of another row, so the edge shifts become the measured quantity).", "E0",
                            "62-the-table"),
    "compare.col.e0": ("The E0 in force, eV. Type a value to fix it; empty the cell for the derivative again.", "E0 eV",
                       "62-the-table"),
    "compare.col.colour": ("The colour of the trace; click to choose another. Saved with the config.", "Colour",
                           "62-the-table"),
    "compare.colour": ("The colour of this trace in the plot and the exported figure: click to choose another.",
                       "trace colour", "62-the-table"),
    "compare.default_shift": ("Energy shift given to every row added from now on.",
                              "default shift for new rows", "62-the-table"),
    "compare.e0_smooth": ("Width of the Savitzky-Golay smoothing of the derivative that finds E0, eV.",
                          "E0 derivative smoothing", "62-the-table"),
    "compare.locate": ("Choose the folder the missing files moved to: they are found by name and every row gets its "
                       "spectrum back.", "Locate the missing files", "61-loading"),
    "compare.dismiss": ("Hide this message.", "Dismiss the message", "62-the-table"),
    "compare.label_inset": ("Labels sit this many eV in from the right edge of the plot; 0 = at the right edge.",
                            "labels in from the right edge", "63-stacked-plot"),
    "compare.figure_size": ("Size, font, PNG resolution and formats of every exported Compare and Fit figure.",
                            "Figure size", "64-diagnostics-and-export"),
    "compare.fig_width": ("Figure width in inches: 3.5 for one column, 7.0 for two.", "figure width",
                          "64-diagnostics-and-export"),
    "compare.fig_height": ("Figure height in inches.", "figure height", "64-diagnostics-and-export"),
    "compare.fig_font": ("Size of every text of the figure, in points.", "font size", "64-diagnostics-and-export"),
    "compare.fig_dpi": ("Resolution of the PNG; 600 dpi gives 2100 x 2700 px at 3.5 x 4.5 in.", "PNG resolution",
                        "64-diagnostics-and-export"),
    "compare.fig_preset": ("Set a journal column width.", "width preset", "64-diagnostics-and-export"),
    "compare.fig_png": ("Write a PNG at the chosen resolution.", "PNG", "64-diagnostics-and-export"),
    "compare.fig_svg": ("Write an SVG with editable text.", "SVG", "64-diagnostics-and-export"),
    "compare.fig_pdf": ("Write a one-page PDF of the figure size with embedded TrueType text.", "PDF",
                        "64-diagnostics-and-export"),
    # ------------------------------------------------------------------ fit
    "fit.target": ("The spectrum to describe (a row ticked Fit in Compare).",
                   "target", "71-lcf-linear-combination-fit"),
    "fit.components": ("The reference spectra it may be made of (components): tick one or more.",
                       "references", "71-lcf-linear-combination-fit"),
    "fit.lcf_lo": ("Lower end of the fit range (eV); blank = the Compare x range, else the whole common range.",
                   "fit range from", "71-lcf-linear-combination-fit"),
    "fit.lcf_hi": ("Upper end of the fit range (eV); blank = the Compare x range, else the whole common range.",
                   "fit range to", "71-lcf-linear-combination-fit"),
    "fit.sum_one": ("Force the coefficients to sum to 1.", "force sum to 1", "71-lcf-linear-combination-fit"),
    "fit.allow_neg": ("Allow negative coefficients.", "allow negative coefficients", "71-lcf-linear-combination-fit"),
    "fit.bootstrap": ("Refits on resampled residuals for the uncertainties; 0 = the covariance scaled by "
                      "√(reduced χ²), the Larch / Athena convention.", "bootstrap draws", "71-lcf-linear-combination-fit"),
    "fit.run_lcf": ("Describe the target as a mix of the ticked references (linear combination fit, LCF).",
                    "Run LCF", "71-lcf-linear-combination-fit"),
    "fit.export_lcf": ("Write the coefficients (with the fit statistics: chi-square, reduced chi-square, N, p, the "
                       "R-factor, the uncertainty method, the flags and the F-tests in the header) and the curves "
                       "as CSV, and the figure at the Figure size.",
                       "Export figure and coefficients", "71-lcf-linear-combination-fit"),
    "fit.col.component": ("The component spectrum.", "component", "71-lcf-linear-combination-fit"),
    "fit.col.coefficient": ("Its weight in the linear combination.", "coefficient", "71-lcf-linear-combination-fit"),
    "fit.col.uncertainty": ("Standard deviation over the bootstrap draws, or the covariance error scaled by the "
                            "square root of the reduced chi-square without a bootstrap.",
                            "uncertainty", "71-lcf-linear-combination-fit"),
    "fit.mcr_lo": ("Lower end of the energy range (eV); blank = the whole common range.", "range from",
                   "72-pca--mcr-als"),
    "fit.mcr_hi": ("Upper end of the energy range (eV); blank = the whole common range.", "range to",
                   "72-pca--mcr-als"),
    "fit.n_comp": ("Number of MCR-ALS components.", "components", "72-pca--mcr-als"),
    "fit.max_iter": ("Maximum number of alternating least-squares iterations.", "maximum iterations",
                     "72-pca--mcr-als"),
    "fit.tol": ("Stop when the change of the error between two iterations, divided by the error, falls below this "
                "value (John Bussey's GUI compared the absolute change with 1e-8).", "tolerance on the error change",
                "72-pca--mcr-als"),
    "fit.clip_neg": ("Set negative values to 0 before the fit.", "clip negative values", "72-pca--mcr-als"),
    "fit.pca": ("Explained variance of the ticked rows over the range.", "Update PCA scree", "72-pca--mcr-als"),
    "fit.run_mcr": ("Alternating least squares with non-negative spectra and concentrations.", "Run MCR-ALS",
                    "72-pca--mcr-als"),
    "fit.export_mcr": ("Write MCR_component_spectra.csv, MCR_component_makeup.csv and the component figure to a "
                       "folder.", "Export MCR-ALS", "72-pca--mcr-als"),
    "fit.col.percent": ("Share of this component in the sample (%).", "component percent", "72-pca--mcr-als"),
    "fit.itfa_sample": ("The sample whose component percentages are held fixed.", "anchored sample", "73-itfa"),
    "fit.itfa_pct": ("The percentages, comma separated, one per component.", "percentages", "73-itfa"),
    "fit.add_target": ("Add the sample and its percentages to the anchored targets.", "Add target", "73-itfa"),
    "fit.remove_target": ("Remove the selected anchored targets.", "Remove target", "73-itfa"),
    "fit.run_itfa": ("Refine the MCR-ALS concentration profiles with the anchored targets held fixed.", "Run ITFA",
                     "73-itfa"),
    "fit.export_itfa": ("Write ITFA_component_spectra.csv, ITFA_component_makeup.csv and the component figure to a "
                        "folder.", "Export ITFA", "73-itfa"),
    "fit.pk_trace": ("The trace to decompose (a row ticked Fit in Compare).", "trace", "74-peaks"),
    "fit.pk_n": ("Number of peaks (1 to 8).", "peaks", "74-peaks"),
    "fit.pk_shape": ("Peak shape: gaussian, lorentzian or pseudo-voigt, each by height, centre and FWHM (eV); the "
                     "pseudo-voigt also fits its Lorentzian fraction eta (0 to 1).", "shape", "74-peaks"),
    "fit.pk_step": ("Add an arctan edge step under the peaks.", "edge step", "74-peaks"),
    "fit.pk_bg": ("Background: linear or constant.", "background", "74-peaks"),
    "fit.pk_lo": ("Lower end of the range (eV); blank = the whole trace.", "range from", "74-peaks"),
    "fit.pk_hi": ("Upper end of the range (eV); blank = the whole trace.", "range to", "74-peaks"),
    "fit.pk_centres": ("Initial peak centres in eV, comma separated; blank = guesses from the local maxima.",
                       "initial centres", "74-peaks"),
    "fit.run_peaks": ("Fit peaks + arctan step + background with least squares.", "Run the decomposition", "74-peaks"),
    "fit.export_peaks": ("Write every parameter with its unit, the peak areas and the curves as CSV with a provenance "
                         "header, and the figure at the Figure size.", "Export the decomposition", "74-peaks"),
    "fit.col.parameter": ("p1_amp, p1_centre, p1_fwhm (p1_eta), …, step_height, step_centre, step_width, the "
                          "background, then the peak areas p1_area, …", "parameter", "74-peaks"),
    "fit.col.value": ("The fitted value.", "value", "74-peaks"),
    "fit.col.error": ("Standard error from the covariance of the fit (areas: propagated from it).", "uncertainty",
                      "74-peaks"),
    "fit.col.unit": ("eV for centres, FWHM, the step centre and the step width; a.u. for amplitudes.", "unit",
                     "74-peaks"),
    "fit.col.note": ("'at the 0 bound' or 'undetermined' (σ > |value|); the tooltip says why.", "note",
                     "71-lcf-linear-combination-fit"),
    "fit.resample": ("How the bootstrap resamples the residual: moving blocks as long as its autocorrelation (correlated "
                     "residuals) or single points (iid).", "resampling", "71-lcf-linear-combination-fit"),
    "fit.figure_size": ("Size, font, PNG resolution and formats of the exported Fit figures (shared with Compare).",
                        "Figure size", "71-lcf-linear-combination-fit"),
    # ------------------------------------------------------------------ maps
    "maps.stack": ("The stack whose hypercube is loaded.", "stack", "85-the-hypercube-cache"),
    "maps.load": ("Read the raw .bin files of the stack once, one energy at a time, into a cache on disk (a few "
                  "hundred MB per stack; the free space is checked first). Cancel keeps nothing.",
                  "Load hypercube", "85-the-hypercube-cache"),
    "maps.line": ("A fixed line (each SDD's own channels from config/roi_bins.json), or custom with the eV bounds "
                  "(each SDD's own calibration).", "window", "81-explore"),
    "maps.w_lo": ("Lower end of the emission window (eV).", "window from", "81-explore"),
    "maps.w_hi": ("Upper end of the emission window (eV).", "window to", "81-explore"),
    "maps.per_i0": ("Divide by I0.", "divide by I0", "81-explore"),
    "maps.log": ("Show log10 of the map.", "log scale", "81-explore"),
    "maps.deconv": ("Sharpen a map blurred by a beam larger than the pixel pitch (a view, not data: exports keep the "
                    "measured cube).", "deconvolve", "81-explore"),
    "maps.deconv_method": ("Richardson-Lucy (non-negative, 10-50 iterations) or Wiener (one-shot filter).",
                           "deconvolution method", "81-explore"),
    "maps.psf": ("Full width at half maximum of the beam (µm).", "beam width", "81-explore"),
    "maps.iter": ("Richardson-Lucy iterations.", "deconvolution iterations", "81-explore"),
    "maps.roi": ("The region of the spectra: the whole map, the current zone, or a rectangle drawn on the map.", "ROI",
                 "81-explore"),
    "maps.energy": ("The incident energy of the map.", "energy", "81-explore"),
    "maps.avg": ("Average the map over every valid energy.", "average over energies", "81-explore"),
    "maps.draw_elements": ("One map per fixed line (config/roi_bins.json).", "Draw the elemental maps",
                           "82-elemental-maps"),
    "maps.export_4d": ("Write the PyMca 4D stack _Elemental_PyMca.h5 (every SDD; the sums over the ticked SDDs), in "
                       "the background.", "Export PyMca 4D", "82-elemental-maps"),
    "maps.rgb": ("The window shown in this colour.", "colour window", "83-rgb"),
    "maps.draw_rgb": ("Three windows as a colour composite.", "Draw the RGB composite", "83-rgb"),
    "maps.n_pc": ("Number of PCA components kept before k-means.", "PCA components", "84-clusters"),
    "maps.n_k": ("Number of k-means clusters.", "clusters", "84-clusters"),
    "maps.run_clusters": ("PCA on the per-pixel XANES, then k-means: the cluster map and the mean spectra.",
                          "Run the clustering", "84-clusters"),
    "maps.export_xanes": ("Write the PyMca XANES stack _PCA-CA.h5 (y, x, E): one dataset per ticked SDD, each with "
                          "its own channel window, in the background.", "Export PyMca XANES stack", "84-clusters"),
    "maps.sdd": ("Include this SDD in every sum (map, spectrum, XANES sum, elemental maps, RGB, clusters, exports). "
                 "Each SDD uses its own channels for the window. An SDD flagged saturated starts unticked.",
                 "include the SDD in the sum", "81-explore"),
    "maps.seed": ("The k-means seed: the same seed gives the same clusters again.", "k-means seed", "84-clusters"),
    "maps.clusters": ("One row per cluster with its pixel count and map colour. Tick the clusters to merge: their mean "
                      "and sum spectra follow; unticked clusters are dimmed on the map.", "clusters", "84-clusters"),
    "maps.make_zone": ("A new zone of the holder made of the ticked clusters' pixels (one painted square per pixel), "
                       "ready for Reduce and Check.", "Make zone from ticked clusters", "84-clusters"),
    "maps.send": ("Add the merged mean spectrum of the ticked clusters to Compare, with its provenance (stack, windows, "
                  "SDDs, PCA components, k, seed, clusters).", "Send to Compare", "84-clusters"),
    "maps.export_clusters": ("Write a CSV (energy, the mean of each cluster, the merged mean of the ticked ones) and "
                             "the cluster map as PNG next to it.", "Export the clusters", "84-clusters"),
    # ------------------------------------------------------------------ report
    "report.title": ("The report title (default 'AURORA report · <project>').", "report title", "91-options"),
    "report.out": ("The reports folder (default <project>/reports/aurora). Each build gets its own folder in it, "
                   "named by date, time and scope; nothing earlier is overwritten.", "output folder", "91-options"),
    "report.browse": ("Choose the output folder.", "Browse for the output folder", "91-options"),
    "report.appendix": ("Appendix A: the four detectors per zone and scan.", "Appendix A", "91-options"),
    "report.maps": ("Appendix B: plate maps with the zones.", "Appendix B", "91-options"),
    "report.groups": ("The sample-sheet groups found in the zones: tick the ones to include.", "groups",
                      "92-check-before-building"),
    "report.all": ("Tick every group.", "All groups", "92-check-before-building"),
    "report.none": ("Untick every group.", "No group", "92-check-before-building"),
    "report.build": ("Build the PDF, the figures (PNG and SVG) and one CSV per zone and scan, in the background.",
                     "Build the report", "91-options"),
    "report.prev": ("Previous page of the preview.", "Previous page", "93-what-a-build-writes"),
    "report.next": ("Next page of the preview.", "Next page", "93-what-a-build-writes"),
    "report.open_folder": ("Open the folder of the last build.", "Open folder", "93-what-a-build-writes"),
    "report.open_pdf": ("Open the PDF.", "Open PDF", "93-what-a-build-writes"),
    "report.zoom": ("Size of the page preview: the width of the card, or 100 / 150 / 200 % of the page image "
                    "(Ctrl+wheel over the page steps through them). The PDF itself is vector: Open PDF for full detail.",
                    "page preview zoom", "93-what-a-build-writes"),
    "report.preset": ("Final: every holder, Appendix A and B, figures at 600 dpi. Draft: the current holder, no "
                      "Appendix A or B, figures at 110 dpi. The options below can still be changed.", "preset",
                      "91-options"),
    "report.dpi": ("Resolution of the figure PNGs (the SVG copies are vector).", "PNG figure resolution", "91-options"),
    "report.holders": ("The holders the report covers, by day: tick a day for all its holders.", "holders",
                       "92-check-before-building"),
    "report.current_holder": ("Cover only the holder shown in the top bar.", "Current holder only",
                              "92-check-before-building"),
    "report.all_holders": ("Cover every holder of the project.", "All holders", "92-check-before-building"),
    "report.check": ("What to check before building: zones without a group or an included scan, labels not in the "
                     "sample sheet, stacks covered by no zone, scans covering a zone without settings, saturated "
                     "detectors still ticked. Show opens the zone in Reduce; Ignore keeps a finding out of the count.",
                     "check before building", "92-check-before-building"),
    "report.contents": ("What the next build contains: group, holder, zone, with the edge and the number of included "
                        "scans. A zone that will be left out carries a warning icon; its tooltip says why.",
                        "report contents", "92-check-before-building"),
    "report.jump": ("Show this zone (or scan) in Reduce.", "Show in Reduce", "92-check-before-building"),
    "report.col.finding": ("The finding, in the words of the check.", "Finding", "92-check-before-building"),
    "report.col.ignore": ("Ticked: ignored, not counted in the warnings, remembered with the session.", "Ignore",
                          "92-check-before-building"),
    "report.col.show": ("Show the zone in Reduce.", "Show", "92-check-before-building"),
    "report.col.zone": ("Sample group, then holder (with its day), then zone.", "Group / holder / zone",
                        "92-check-before-building"),
    "report.col.edge": ("The absorption edge of the zone's included scans.", "Edge", "92-check-before-building"),
    "report.col.scans": ("Included scans that give a spectrum of the zone.", "Scans", "92-check-before-building"),
    # ------------------------------------------------------------------ settings and dialogs
    "settings.appearance": ("Follow the system, light or dark. Applied at once.", "appearance", "10-settings"),
    "settings.text_size": ("Normal, large or larger text (13, 15 or 17 px). Applied at once.", "text size",
                           "10-settings"),
    "settings.author": ("Printed in the report footer and the PDF metadata. Blank: resolved when a report is built.",
                        "report author", "10-settings"),
    "settings.open_last": ("Open the last project when AURORA starts (on by default).",
                           "open the last project at start", "10-settings"),
    "settings.advanced": ("Every option (Advanced mode). Easy mode shows the main steps with standard settings "
                          "(Ctrl+Shift+A).", "advanced mode", "10-settings"),
    "settings.show_guide": ("Easy mode: the Getting started row on Home and the Next link of the top bar, which say what "
                            "to do next at every step. Hide on the row turns it off; this box turns it on again.",
                            "show the getting-started guide", "10-settings"),
    "settings.update_check": ("Ask GitHub once a day whether a newer AURORA exists. Nothing is downloaded. Off by "
                              "default.", "check for a newer AURORA at start", "10-settings"),
    "settings.k": ("De-spike threshold for new zone settings, in MADs (6).", "de-spike threshold k", "10-settings"),
    "settings.fluo": ("Fluorescence normalisation of new zone settings (edge jump = 1).",
                      "default fluorescence normalisation", "10-settings"),
    "settings.tey": ("TEY normalisation of new zone settings (edge jump = 1).", "default TEY normalisation",
                     "10-settings"),
    "settings.cmap": ("Colour map of every map: viridis, magma, inferno, plasma, cividis.", "map colour map",
                      "10-settings"),
    "settings.ref_dir": ("The folder behind Compare > References….", "reference spectra folder", "10-settings"),
    "refs.folder": ("The reference spectra folder (csv, txt, dat, nor, xmu; one level of subfolders).",
                    "reference folder", "65-reference-spectra"),
    "refs.browse": ("Choose the reference folder.", "Browse for the reference folder", "65-reference-spectra"),
    "refs.element": ("Show only the references of this element (blank: all).", "element filter",
                     "65-reference-spectra"),
    "stitch.mode": ("halve the overlaps splits every pairwise overlap in half; no trim keeps every pixel.", "trim",
                    "46-stitching-tiles"),
    "stitch.check": ("List every adjacent pair of tiles with the gap between the kept edges and a verdict.",
                     "Check the seams", "46-stitching-tiles"),
}

# Texts that read differently by mode: key -> (Easy, Advanced). Easy says it in plain words (the expert term in
# parentheses where useful); Advanced keeps the precise term. say(key, **fields) picks the one of the current mode.
EASY_TEXT: dict[str, tuple[str, str]] = {
    # Reduce
    "reduce.subtitle": ("Outline each sample on its map: AURORA adds up the pixels inside into one spectrum.",
                        "draw the zones on the stack map, choose per scan what goes into the report"),
    "reduce.title.window": ("Which X-rays to count", "XRF window"),
    "reduce.title.map": ("Outline the sample", "Stack map"),
    "reduce.title.zone": ("Send to Check", "Zone"),
    "reduce.no_zone": ("select a sample first", "select a zone first"),
    "reduce.zone_empty": ("the sample has no outline yet", "the zone is empty"),
    "reduce.nothing": ("nothing outlined: drag over the map around your sample",
                       "nothing selected: draw over the stack footprint"),
    "reduce.cleared": ("{label}: outline removed, draw it again", "zone {id} cleared: draw its region again"),
    "reduce.caption.no_scan": ("Pick a scan in step 1 first.", "Region: pick a scan in step 1"),
    "reduce.caption.empty": ("Nothing outlined yet: drag over the map around your sample.",
                             "Region: nothing selected: draw over the stack footprint"),
    "reduce.caption.region": ("Region: {shape} · {k} of {m} pixels of {scan}", "Region: {shape} · {k} of {m} points of {scan}"),
    "reduce.added": ("{label} on {scan} added to Check ({waiting} waiting)", "{label} on {scan} added to Check ({waiting} waiting)"),
    "reduce.window.whole": ("No sample outlined yet: the X-rays of the whole map.",
                            "nothing selected yet: the spectrum of the whole stack"),
    "reduce.window.inside": ("The X-rays inside {label}: {k} pixels, all energies.",
                             "inside the region ({k} points), all energies"),
    "reduce.window.stack": ("The X-rays of the whole map: {n} pixels, all {e} energies.",
                            "{name}: all {e} energies, {n} points"),
    "reduce.window.no_raw": ("This stack has no raw detector spectra (stitched, or the files are missing): only the fixed "
                             "windows can be used.", "no raw spectra for this stack (stitched or not found): the fixed bins only"),
    # Check
    "check.subtitle": ("Look at each spectrum before keeping it: untick the ones that look wrong, then save the rest.",
                       "spectra to check · Looks OK · save to Compare"),
    # Compare
    "compare.subtitle": ("Saved spectra on one graph, each scaled so that its edge step is 1.",
                         "normalise, shift, stack and compare reduced spectra"),
    "compare.diag_title": ("How this spectrum was scaled", "Diagnostics of the selected row"),
    # Fit
    "fit.subtitle": ("Describe one spectrum as a mix of reference spectra: the rows ticked Fit in Compare, scaled as there.",
                     "rows ticked 'Fit' in Compare, with their shifts and normalisation"),
    "fit.lcf_title": ("Mix of references (LCF)", "Linear combination"),
    "fit.components_label": ("references (tick)", "references / components (tick)"),
    # Survey
    # (the texts of the plain spec 5.3 for a Survey that draws zones)
    "survey.subtitle": ("Where each sample and each measured area sits on the holder. Draw around a sample, or click a "
                        "measured area to open it in Reduce.", "plate map, scan footprints, zones"),
    "survey.zones_title": ("Samples outlined on this holder", "Zones of the holder"),
    "survey.zones_empty": ("No samples outlined on this holder yet. Draw around a sample on the map (Draw, then drag), "
                           "or click a measured area to open it in Reduce.", "No zones on this holder yet."),
    # Report
    "report.zone_state": ("No sample outlined yet: the report is built from the samples outlined in Reduce and kept in "
                          "Check.", "No zone yet: the report is built from the zones drawn in Reduce."),
}


def say(key: str, **fields) -> str:
    """The text of EASY_TEXT[key] for the current mode, with its fields filled in."""
    from aurora.app.mode import is_advanced
    easy, adv = EASY_TEXT[key]
    text = adv if is_advanced() else easy
    return text.format(**fields) if fields else text


# standard dialog buttons -> tooltip (by their text, without the mnemonic)
BUTTON_TIPS = {
    "OK": "Apply and close", "Cancel": "Close without changing anything", "Close": "Close this window",
    "Help": "Open the manual at this dialog's section", "Bake": "Build the stitched stack from the ticked tiles",
    "Apply": "Apply without closing", "Save": "Save and close", "Yes": "Yes", "No": "No",
}


# HELP key -> the glossary terms its tooltip ends with ('What is …?' lines, at most two; aurora.glossary)
HELP_TERMS: dict[str, tuple[str, ...]] = {
    "main.mode": ("easy-mode",), "main.holder": ("holder",), "main.save_state": ("session",),
    "home.open": ("beamtime",), "home.demo": ("beamtime",),
    "home.col.kind": ("survey", "stack"), "home.col.edge": ("edge", "edge-name"), "home.col.status": ("status",),
    "home.col.energies": ("stack",), "home.col.points": ("pixel",), "home.col.flags": ("saturated", "i0"),
    "survey.footprints": ("footprint",), "survey.zones": ("zone",), "survey.channel": ("emission-line", "i0"),
    "reduce.scans": ("stack", "status"),
    "reduce.window.preset": ("xrf-window", "emission-line"),
    "reduce.window.lo": ("channel",), "reduce.window.hi": ("channel",),
    "reduce.window.source": ("emission-spectrum",),
    "reduce.new_zone": ("zone",), "reduce.zones": ("zone",), "reduce.delete_zone": ("zone",), "reduce.clear": ("zone",),
    "reduce.label": ("sample-sheet",),
    "reduce.add_to_check": ("normalisation", "de-spike"),
    "reduce.signal": ("fluorescence", "ipfy"),
    "reduce.fluo": ("normalisation", "edge-step"), "reduce.tey_norm": ("normalisation", "edge-step"),
    "reduce.tey": ("tey",),
    "reduce.sdd": ("sdd", "saturated"), "maps.sdd": ("sdd", "saturated"),
    "reduce.despike": ("de-spike",), "reduce.k": ("de-spike",),
    "reduce.i0": ("i0", "good-i0"),
    "reduce.pre": ("pre-post",), "reduce.post": ("pre-post",),
    "preview.sdd": ("sdd",), "preview.sum": ("sdd",), "preview.tey": ("tey",), "preview.i0": ("i0",),
    "preview.spikes": ("de-spike",),
    "check.queue": ("looks-ok",), "check.qcol.ok": ("looks-ok",), "check.qcol.messages": ("chips",),
    "check.qcol.pts": ("pixel",), "check.qcol.bad": ("bad-energy",),
    "check.view": ("i0", "tey"), "check.save": ("reduced",),
    "check.col.kcps": ("count-rate",), "check.col.high": ("count-rate",), "check.col.saturated": ("saturated",),
    "check.col.i0": ("i0",), "check.col.i0_level": ("i0",),
    "compare.col.signal": ("signal", "tey"), "compare.col.shift": ("shift",),
    "compare.col.norm": ("normalisation", "pre-post"),
    "compare.col.pre_lo": ("normalisation", "pre-post"), "compare.col.pre_hi": ("normalisation", "pre-post"),
    "compare.col.post_lo": ("normalisation", "pre-post"), "compare.col.post_hi": ("normalisation", "pre-post"),
    "compare.col.e0_mode": ("e0",), "compare.col.e0": ("e0",),
    "compare.col.fit": ("lcf",), "compare.add_folder": ("reduced",),
    "compare.references": ("reference",), "refs.folder": ("reference",),
    "fit.target": ("lcf", "target-ref"), "fit.components": ("lcf", "target-ref"), "fit.run_lcf": ("lcf",),
    "fit.export_lcf": ("r-factor", "chi2"),
    "fit.col.coefficient": ("lcf",), "fit.col.uncertainty": ("bootstrap",), "fit.bootstrap": ("bootstrap",),
    "fit.n_comp": ("pca-mcr",), "fit.pca": ("pca-mcr",), "fit.run_mcr": ("pca-mcr",),
    "report.groups": ("group",), "report.appendix": ("appendices",), "report.maps": ("appendices",),
    "settings.k": ("de-spike",), "settings.fluo": ("normalisation",), "settings.tey": ("normalisation",),
}


def help_terms(key: str):
    """The glossary terms of a HELP key (aurora.glossary.Term), in the order of HELP_TERMS."""
    from aurora.glossary import term
    return [term(k) for k in HELP_TERMS.get(key, ())]


def tooltip(key: str) -> str:
    """HELP[key]'s tooltip, then a blank line and one 'What is …?' line per glossary term of HELP_TERMS[key]."""
    from aurora.glossary import what_is
    tip = HELP[key][0]
    lines = [what_is(k) for k in HELP_TERMS.get(key, ())]
    return tip + ("\n\n" + "\n".join(lines) if lines else "")


def whats_this(key: str, tip: str | None = None) -> str:
    """The What's This text: the tooltip, then the glossary words and the manual section ('… More: manual §4.5')."""
    _t, _name, anchor = HELP[key]
    tip = tooltip(key) if tip is None else tip
    terms = help_terms(key)
    words = f"Glossary: {', '.join(t.short for t in terms)} · " if terms else ""
    return f"{tip}\n… {words}{manual_ref(anchor)}"


def term_button(key: str, parent: QWidget | None = None):
    """A small '?' next to a word that names a control: its tooltip says what the word means, a click opens the
    glossary there. Left out of the Easy control budget and the manual parity (aurora_help_button)."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QSizePolicy, QToolButton
    from aurora.glossary import definition, term, what_is
    t = term(key)
    b = QToolButton(parent)
    b.setObjectName("termhelp")
    b.setText("?")
    b.setProperty("aurora_help_button", True)
    b.setProperty("aurora_term", key)
    b.setCursor(Qt.PointingHandCursor)
    b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    b.setToolTip(f"{what_is(key)}\n(click: open the glossary)")
    b.setAccessibleName(t.ask)
    b.setAccessibleDescription(definition(key))
    b.setStatusTip(what_is(key))

    def _open() -> None:
        from aurora.app.help import open_glossary
        open_glossary(b, key)
    b.clicked.connect(_open)
    return b


def manual_ref(anchor: str) -> str:
    """'… More: manual §4.5' for an anchor (the section number from MANUAL.md)."""
    from aurora.app.help import section_of
    sec = section_of(anchor)
    return f"More: manual §{sec}" if sec else "More: the manual"


def explain(widget: QWidget, key: str) -> QWidget:
    """Set the tooltip, accessible name, status tip and What's This text of a widget from HELP[key]."""
    from aurora.app.shortcuts import native_text
    _tip, name, _anchor = HELP[key]
    tip = native_text(tooltip(key))
    widget.setToolTip(tip)
    widget.setProperty("aurora_tip", tip)          # widgets.set_enabled keeps it while the widget is disabled
    widget.setAccessibleName(name)
    widget.setStatusTip(native_text(_tip).split("\n")[0])
    widget.setWhatsThis(whats_this(key, tip))
    widget.setProperty("aurora_help_key", key)
    return widget


def explain_all(pairs) -> None:
    """explain() for [(widget, key), ...]; None widgets are skipped."""
    for w, key in pairs:
        if w is not None:
            explain(w, key)


def labelled(text: str, widget: QWidget, key: str | None = None) -> QLabel:
    """A QLabel naming `widget` (its buddy, so screen readers and Alt+key reach the field)."""
    lab = QLabel(text)
    lab.setBuddy(widget)
    if key is not None:
        explain(widget, key)
    if not widget.accessibleName():
        widget.setAccessibleName(text.rstrip(":").strip())
    return lab


def header_tips(view, tips: dict[int, str]) -> None:
    """Tooltips on the header items of a QTableWidget (horizontal) or a QTreeWidget, from HELP keys."""
    from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QTreeWidget
    for col, key in tips.items():
        from aurora.app.shortcuts import native_text
        tip = native_text(tooltip(key)) if key in HELP else key
        if isinstance(view, QTableWidget):
            it = view.horizontalHeaderItem(col)
            if it is None:
                it = QTableWidgetItem(str(view.model().headerData(col, 1) or ""))
                view.setHorizontalHeaderItem(col, it)
            it.setToolTip(tip)
        elif isinstance(view, QTreeWidget):
            view.headerItem().setToolTip(col, tip)


def explain_buttons(box) -> None:
    """Tooltips and accessible names on the buttons of a QDialogButtonBox."""
    for b in box.buttons():
        text = b.text().replace("&", "")
        if not b.toolTip():
            b.setToolTip(BUTTON_TIPS.get(text, text))
        if not b.accessibleName():
            b.setAccessibleName(text)
