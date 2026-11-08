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
    "main.mode": ("Easy mode: the basic processing path of John Bussey's GUI (reduce, check, compare, fit, report).\n"
                  "Advanced mode: every option. Ctrl+Shift+A switches.", "Easy or Advanced mode", "1-the-window"),
    "main.holder": ("The holder on screen. Choose another one here, or with Ctrl+PgUp / Ctrl+PgDown.", "holder",
                    "1-the-window"),
    "main.save_state": ("Whether the zones are saved: 'unsaved' while a change waits for the autosave (under a "
                        "second), 'saved HH:MM' once it is written, 'read-only' when edits here cannot be saved.",
                        "save state", "1-the-window"),
    "main.undo": ("Undo the last zone, settings or good-I0 edit (Ctrl+Z). A brush stroke, or a run of edits of the "
                  "same kind, undoes at once.", "Undo", "1-the-window"),
    "main.redo": ("Redo the edit just undone (Ctrl+Y or Ctrl+Shift+Z).", "Redo", "1-the-window"),
    "main.history": ("Every edit since the project was opened (the last 100); click a line to go back to that point.",
                     "Edit history", "1-the-window"),
    "main.history_list": ("The edits, oldest first; the highlighted line is the current state.", "edit history",
                          "1-the-window"),
    "main.restore": ("Put back the zones of a backup: one every 10 minutes while you edit, one before every import, "
                     "proposal, rule and restore, and the previous save (session.json.bak).",
                     "Restore zones from a backup", "12-files-aurora-reads-and-writes"),
    "restore.table": ("The backups of this project, newest first, with the holders and zones each holds.", "backups",
                      "12-files-aurora-reads-and-writes"),
    "import.table": ("The holders of the file that already have other zones in this session.", "holders to import",
                     "3-survey"),
    "import.policy": ("Merge: add the file's zones with new numbers, skipping those with the same rectangles, painted "
                      "pixels and polygons as a zone here. Replace: the file's zones replace this holder's. Skip: keep "
                      "this holder as it is.", "what to do with this holder", "3-survey"),
    # ------------------------------------------------------------------ home
    "home.open": ("Open a beamtime folder (the one that holds raw/), a day or scan folder, or one .h5 file (Ctrl+O).",
                  "Open project", "2-home"),
    "home.reload": ("Read the project folder again: scans that arrived since the last read appear (F5).", "Reload",
                    "2-home"),
    "home.col.scan": ("The scan folder name without its time prefix.", "Scan", "2-home"),
    "home.col.time": ("HH:MM of the scan folder.", "Time", "2-home"),
    "home.col.kind": ("survey, stack or other.", "Kind", "2-home"),
    "home.col.edge": ("The target edge read from the plan and the name (F K, O K, Se L3/L2, …).", "Edge", "2-home"),
    "home.col.status": ("complete, in progress, aborted or test (see the status rules in the manual).", "Status",
                        "2-home"),
    "home.col.energies": ("First and last energy (eV) and the number of energy folders.", "Energies", "2-home"),
    "home.col.points": ("Pixels per energy.", "Points", "2-home"),
    "home.col.flags": ("From the sgm_report summary when present: saturated SDDs, I0 invalid.", "Flags", "2-home"),
    # ------------------------------------------------------------------ survey
    "survey.holder": ("The holder whose plate map is shown (also chosen in the top bar).", "holder", "3-survey"),
    "survey.channel": ("A fixed emission line, total XRF, TEY or I0.", "channel", "3-survey"),
    "survey.sdd": ("The sum of the four detectors, or one detector.", "detector", "3-survey"),
    "survey.per_i0": ("Divide by the mesh current (when the survey's I0 is usable).", "divide by I0", "3-survey"),
    "survey.log": ("Show log10 of the values.", "log scale", "3-survey"),
    "survey.invert": ("Flip the colour scale (high-energy surveys, where samples are darker than the plate).",
                      "invert the colour scale", "3-survey"),
    "survey.clip": ("Trim the colour range at both ends by this percentage (0.5 % by default).", "clip", "3-survey"),
    "survey.footprints": ("Show the stacks of the holder as rectangles: click a complete one to open it in Reduce.",
                          "show the scan footprints", "3-survey"),
    "survey.zones": ("Show the zones of the holder with their labels.", "show the zones", "3-survey"),
    "survey.import": ("Read a zones_all.json written by the browser zone tool or by another AURORA.", "Import zones",
                      "3-survey"),
    "survey.export": ("Write the zones of every holder as zones_all.json (the browser zone tool's format).",
                      "Export zones", "3-survey"),
    # ------------------------------------------------------------------ reduce
    "reduce.load_all": ("Read every complete stack of the holder, so the zones attach to all of them.", "Load all",
                        "41-scans-of-the-holder"),
    "reduce.scans": ("The complete stacks of the holder: pick one to load it (cached after the first read).",
                     "scans of the holder", "41-scans-of-the-holder"),
    "reduce.new_zone": ("Start a new, empty zone; draw it with rect+ on the stack map.", "New sample", "43-zones"),
    "reduce.delete_zone": ("Delete the selected zone (its settings for every scan go with it).", "Delete", "43-zones"),
    "reduce.propose": ("Zones from sgm_report's summary of the day, else from the bright pads of the survey map, as "
                       "a proposal to edit or delete.", "Propose zones", "43-zones"),
    "reduce.stitch": ("Join overlapping stacks (tiles) of the holder into one stack (after Load all).", "Stitch tiles",
                      "46-stitching-tiles"),
    "reduce.tool.navigate": ("Pan with the left button, zoom with the wheel.", "navigate", "42-stack-map"),
    "reduce.tool.rect+": ("Drag a rectangle to add to the zone (a new zone when none is selected).", "add a rectangle",
                          "42-stack-map"),
    "reduce.tool.rect-": ("Drag a rectangle to remove from the zone.", "remove a rectangle", "42-stack-map"),
    "reduce.tool.brush+": ("Click or drag to paint pixels into the zone.", "paint pixels", "42-stack-map"),
    "reduce.tool.brush-": ("Click or drag to erase pixels from the zone.", "erase pixels", "42-stack-map"),
    "reduce.brush": ("Brush size: n × n pixels.", "brush size", "42-stack-map"),
    "reduce.target_new": ("Every rectangle starts a new zone (drawing many samples in a row).",
                          "rect+ starts a new zone", "42-stack-map"),
    "reduce.map_line": ("The emission line shown on the stack map.", "map line", "42-stack-map"),
    "reduce.map_sdd": ("The sum of the four detectors, or one detector.", "map detector", "42-stack-map"),
    "reduce.map_mode": ("What the map integrates: above the edge, all energies, the edge jump, or one energy.",
                        "map mode", "42-stack-map"),
    "reduce.map_energy": ("The energy shown when the map mode is 'at energy'.", "map energy", "42-stack-map"),
    "reduce.map_i0": ("Divide the map by I0.", "map divided by I0", "42-stack-map"),
    "reduce.map_log": ("Show log10 of the map.", "map log scale", "42-stack-map"),
    "reduce.label": ("The sample label: the sample-sheet IDs of the holder are offered; type anything.", "zone label",
                     "44-the-zone-card"),
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
                      "partial fluorescence yield). The choice is stored with the zone; this version still computes "
                      "the traces as PFY.", "signal", "45-one-card-per-attached-scan"),
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
    "reduce.pre": ("Pre-edge window in eV, '680-684' style; blank = the first 15 % of the energies.", "pre-edge window",
                   "45-one-card-per-attached-scan"),
    "reduce.post": ("Post-edge window in eV, '700-720' style; blank = the last 20 % of the energies.",
                    "post-edge window", "45-one-card-per-attached-scan"),
    "preview.sdd": ("Show the four detectors.", "show the detectors", "47-preview"),
    "preview.sum": ("Show the sum of the ticked detectors.", "show the sum", "47-preview"),
    "preview.tey": ("Show the TEY panel's curve.", "show TEY", "47-preview"),
    "preview.i0": ("Show the I0 panel's curve.", "show I0", "47-preview"),
    "preview.spikes": ("Mark the interpolated spikes.", "show spikes", "47-preview"),
    "preview.all_scans": ("Overlay every included scan of the sample.", "all scans of the sample", "47-preview"),
    # Reduce, WP3: map tools, XRF windows (step 2), Easy steps, batch settings
    "reduce.tool.lasso+": ("Drag freehand around the sample: the outline closes when you let go (Esc cancels). Pixels "
                           "whose centre lies inside belong to the zone.", "lasso", "42-stack-map"),
    "reduce.tool.polygon+": ("Click the corners of a polygon, double-click to close it (Esc cancels). Pixels whose centre "
                             "lies inside belong to the zone.", "polygon", "42-stack-map"),
    "reduce.clear": ("Remove the drawing of the selected sample (rectangles, polygons, painted pixels), to draw it again.",
                     "Clear the region", "42-stack-map"),
    "reduce.zones": ("The samples (zones) of the holder: pick one to edit it. A click inside a zone on the map also "
                     "selects it.", "samples of the holder", "43-zones"),
    "reduce.load_polygon": ("Read a polygon ROI saved by John Bussey's GUI (<sample>_polygon_roi.json, at least three "
                            "[x, y] vertices in mm) into a new sample.", "Load polygon", "43-zones"),
    "reduce.save_polygon": ("Write the selected sample as John Bussey's <sample>_polygon_roi.json (a zone made of exactly "
                            "one polygon).", "Save polygon", "43-zones"),
    "reduce.window.preset": ("The emission window: a fixed line of config/roi_bins.json, the line of the edge (needs an "
                             "SDD calibration), or custom windows set with the regions or the lo / hi boxes.",
                             "XRF window preset", "4-reduce"),
    "reduce.window.lo": ("First channel of the window (included).", "window first channel", "4-reduce"),
    "reduce.window.hi": ("End of the window (excluded): 60 to 83 sums channels 60 to 82, as in John Bussey's GUI.",
                         "window end channel", "4-reduce"),
    "reduce.window.source": ("The emission spectrum shown: the whole stack summed over its energies, the survey of the "
                             "holder, or the pixels of the selected sample.", "emission spectrum source", "4-reduce"),
    "reduce.window.same": ("One window for all four detectors: dragging one region moves the four. Untick to set each "
                           "detector's window apart.", "same range for all detectors", "4-reduce"),
    "reduce.window.det": ("The detector whose window the lo / hi boxes edit when the windows are set apart.",
                          "detector edited", "4-reduce"),
    "reduce.window.remember": ("Save these windows as a default rule for the edge of this stack: scans of that edge "
                               "attached later use them (Rules…).", "Remember these windows", "4-reduce"),
    "reduce.add_to_check": ("Queue the selected sample on this scan for Check, with the Easy settings listed above "
                            "(Ctrl+Return).", "Add to Check", "4-reduce"),
    "reduce.reset_easy": ("Rewrite the settings that differ from the Easy defaults (named in the chip) in one step; Undo "
                          "brings them back.", "Reset to Easy defaults", "45-one-card-per-attached-scan"),
    "reduce.copy": ("Copy this card's settings to all scans of the zone, to this scan in every zone of the holder, or "
                    "to every scan with the same edge. The ticked fields travel; include and the pixel count never do. "
                    "Ctrl+Shift+C / Ctrl+Shift+V copy and paste between cards.", "Copy to", "45-one-card-per-attached-scan"),
    "reduce.rules": ("Rules that write the same settings into many cards (scope, scan filter, fields), with a table of "
                     "what would change; saved rules also apply to scans attached later.", "Rules",
                     "45-one-card-per-attached-scan"),
    "reduce.rules.list": ("The rules, in the order they run (a later rule wins). Ticked rules are used.", "rules",
                          "45-one-card-per-attached-scan"),
    "reduce.rules.add": ("Add an empty rule at the end of the list.", "Add a rule", "45-one-card-per-attached-scan"),
    "reduce.rules.remove": ("Remove the selected rule.", "Remove the rule", "45-one-card-per-attached-scan"),
    "reduce.rules.up": ("Run the selected rule earlier.", "Move the rule up", "45-one-card-per-attached-scan"),
    "reduce.rules.down": ("Run the selected rule later.", "Move the rule down", "45-one-card-per-attached-scan"),
    "reduce.rules.name": ("A name for the rule (shown in the list).", "rule name", "45-one-card-per-attached-scan"),
    "reduce.rules.scope": ("The zones the rule reaches: the whole project, some holders, some sample-sheet groups, or "
                           "the selected zone.", "rule scope", "45-one-card-per-attached-scan"),
    "reduce.rules.scope_text": ("Holders or groups, separated by commas.", "holders or groups",
                                "45-one-card-per-attached-scan"),
    "reduce.rules.edge": ("Only scans of this edge ('F K', 'Se L3,2'); blank: every edge.", "edge filter",
                          "45-one-card-per-attached-scan"),
    "reduce.rules.plan": ("Only scans whose folder name matches this regular expression ('_TEY'); blank: every scan.",
                          "scan name filter", "45-one-card-per-attached-scan"),
    "reduce.rules.span": ("Only scans whose energies lie inside this range; 'any' leaves that end open.",
                          "energy range filter", "45-one-card-per-attached-scan"),
    "reduce.rules.flags": ("Only scans with this flag (sgm_report summary or AURORA's check).", "flag filter",
                           "45-one-card-per-attached-scan"),
    "reduce.rules.field": ("Tick the fields the rule writes, then set their value. Pre / post windows may be written "
                           "from the edge energy: 'E0-15..E0-5'.", "field written by the rule",
                           "45-one-card-per-attached-scan"),
    "reduce.rules.override": ("Also replace fields set by hand on a card (pinned); otherwise they are kept.",
                              "override pinned fields", "45-one-card-per-attached-scan"),
    "reduce.rules.apply": ("Write the ticked rules into the existing cards now (one undo step).", "Apply now",
                           "45-one-card-per-attached-scan"),
    "reduce.rules.save": ("Keep the rules with the session: zone x scan settings created later inherit them.",
                          "Save as default", "45-one-card-per-attached-scan"),
    "reduce.rules.col.0": ("The holder of the card.", "Holder", "45-one-card-per-attached-scan"),
    "reduce.rules.col.1": ("The zone (number and label).", "Zone", "45-one-card-per-attached-scan"),
    "reduce.rules.col.2": ("The scan (time and folder name).", "Scan", "45-one-card-per-attached-scan"),
    "reduce.rules.col.3": ("The field that would change.", "Field", "45-one-card-per-attached-scan"),
    "reduce.rules.col.4": ("Its value now.", "Now", "45-one-card-per-attached-scan"),
    "reduce.rules.col.5": ("Its value once the rules are applied.", "After", "45-one-card-per-attached-scan"),
    # ------------------------------------------------------------------ check
    "check.run": ("Probe seven energies of every stack of the holder (the brightest pixels only) and fill the table: "
                  "count rate, saturation, I0 validity.", "Run the checks", "5-check"),
    "check.i0_norm": ("Overlay the I0 curves on one scale (each divided by its level).", "normalise to the level",
                      "5-check"),
    "check.auto": ("Tick the scans whose I0 looks good (level, steps, roughness, non-positive points).",
                   "Auto-select good I0", "5-check"),
    "check.x_scan": ("Any stack of the holder, including the photodiode tests hidden elsewhere.", "stack to explore",
                     "5-check"),
    "check.x_read": ("Read the mcc CSVs of the stack: mesh, photodiode, their ratio and TEY.", "Read the I0 curves",
                     "5-check"),
    "check.col.time": ("HH:MM of the scan folder.", "Time", "5-check"),
    "check.col.scan": ("The scan's sample label.", "Scan", "5-check"),
    "check.col.edge": ("The target edge.", "Edge", "5-check"),
    "check.col.status": ("complete, in progress, aborted or test.", "Status", "5-check"),
    "check.col.i0_level": ("Median mesh current.", "I0 level", "5-check"),
    "check.col.i0": ("I0 verdict: ok, or invalid when the mesh current is ≤ 0 or below 10 % of the other scans.",
                     "I0", "5-check"),
    "check.col.kcps": ("Count rate of the brightest pixels of this detector (kcps).", "count rate", "5-check"),
    "check.col.saturated": ("Same total at every energy while I0 varied, rate ≥ 2× the holder median, output no longer "
                            "following the input: fluorescence unusable, TEY fine.", "Saturated", "5-check"),
    "check.col.high": ("≥ 2× the holder median without the flat signature: dead time or pile-up likely.", "High rate",
                       "5-check"),
    "check.col.notes": ("The reasons in words.", "Notes", "5-check"),
    # ------------------------------------------------------------------ compare
    "compare.add_folder": ("Add the spectra saved in a folder: John Bussey's _full_reduced.csv / _absorption.csv, "
                           "sgm_report _xanes.csv, AURORA zone CSVs.", "Add saved spectra", "61-loading"),
    "compare.add_csv": ("Add any column file; the energy column is found by name or taken as the first column.",
                        "Add CSV", "61-loading"),
    "compare.references": ("Pick spectra from the reference folder (Settings), filtered by element.", "References",
                           "65-reference-spectra"),
    "compare.save_config": ("Save the whole library (files, shifts, normalisation, ticks) as a JSON file.",
                            "Save config", "61-loading"),
    "compare.load_config": ("Load a library saved with Save config….", "Load config", "61-loading"),
    "compare.export": ("Write a wide CSV of the shown traces as processed, a PNG, an SVG and the config JSON.",
                       "Export", "64-diagnostics-and-export"),
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
    "compare.signal_in_label": ("Add the signal (sdd-scaled, TEY, …) to the labels.", "signal in label",
                                "63-stacked-plot"),
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
    "compare.col.show": ("Plotted.", "Show", "62-the-table"),
    "compare.col.label": ("Editable; Rename and Duplicate act on the selected rows.", "Label", "62-the-table"),
    "compare.col.signal": ("sdd-scaled, TEY, sdd1…, as read from the file.", "Signal", "62-the-table"),
    "compare.col.shift": ("Added to the energy axis (calibration offsets), eV.", "Shift eV", "62-the-table"),
    "compare.col.norm": ("larch (two lines, E0 at the maximum of the derivative), legacy (constant pre / post), tey "
                         "(pre-edge line only), none.", "Norm", "62-the-table"),
    "compare.col.pre_lo": ("Start of the pre-edge window, eV from E0.", "Pre lo", "62-the-table"),
    "compare.col.pre_hi": ("End of the pre-edge window, eV from E0.", "Pre hi", "62-the-table"),
    "compare.col.post_lo": ("Start of the post-edge window, eV from E0.", "Post lo", "62-the-table"),
    "compare.col.post_hi": ("End of the post-edge window, eV from E0.", "Post hi", "62-the-table"),
    "compare.col.flatten": ("Post-edge line flattened to 1.", "Flatten", "62-the-table"),
    "compare.col.spacing": ("Offset after this row in the stacked plot (blank = the common spacing).", "Spacing",
                            "62-the-table"),
    "compare.col.scale": ("Multiplier of this row in the stacked plot.", "Scale", "62-the-table"),
    "compare.col.fit": ("The row is available in Fit.", "Fit", "62-the-table"),
    "compare.col.diag": ("The row's raw signal and fitted lines are shown in the diagnostics panel.", "Diag",
                         "62-the-table"),
    # ------------------------------------------------------------------ fit
    "fit.target": ("The spectrum to fit (a row ticked Fit in Compare).", "target", "71-lcf-linear-combination-fit"),
    "fit.components": ("The candidate components: tick the ones to combine.", "components",
                       "71-lcf-linear-combination-fit"),
    "fit.lcf_lo": ("Lower end of the fit range (eV); blank = the Compare x range, else the whole common range.",
                   "fit range from", "71-lcf-linear-combination-fit"),
    "fit.lcf_hi": ("Upper end of the fit range (eV); blank = the Compare x range, else the whole common range.",
                   "fit range to", "71-lcf-linear-combination-fit"),
    "fit.sum_one": ("Force the coefficients to sum to 1.", "force sum to 1", "71-lcf-linear-combination-fit"),
    "fit.allow_neg": ("Allow negative coefficients.", "allow negative coefficients", "71-lcf-linear-combination-fit"),
    "fit.bootstrap": ("Residual resampling for the uncertainties; 0 disables it.", "bootstrap draws",
                      "71-lcf-linear-combination-fit"),
    "fit.run_lcf": ("Fit the target with the ticked components.", "Run LCF", "71-lcf-linear-combination-fit"),
    "fit.export_lcf": ("Write the coefficients and the curves as CSV, and the figure as PNG and SVG.",
                       "Export figure and coefficients", "71-lcf-linear-combination-fit"),
    "fit.col.component": ("The component spectrum.", "component", "71-lcf-linear-combination-fit"),
    "fit.col.coefficient": ("Its weight in the linear combination.", "coefficient", "71-lcf-linear-combination-fit"),
    "fit.col.uncertainty": ("Standard deviation over the bootstrap draws.", "uncertainty",
                            "71-lcf-linear-combination-fit"),
    "fit.mcr_lo": ("Lower end of the energy range (eV); blank = the whole common range.", "range from",
                   "72-pca--mcr-als"),
    "fit.mcr_hi": ("Upper end of the energy range (eV); blank = the whole common range.", "range to",
                   "72-pca--mcr-als"),
    "fit.n_comp": ("Number of MCR-ALS components.", "components", "72-pca--mcr-als"),
    "fit.max_iter": ("Maximum number of alternating least-squares iterations.", "maximum iterations",
                     "72-pca--mcr-als"),
    "fit.tol": ("Stop when the relative change of the error falls below this value.", "tolerance on the error change",
                "72-pca--mcr-als"),
    "fit.clip_neg": ("Set negative values to 0 before the fit.", "clip negative values", "72-pca--mcr-als"),
    "fit.pca": ("Explained variance of the ticked rows over the range.", "Update PCA scree", "72-pca--mcr-als"),
    "fit.run_mcr": ("Alternating least squares with non-negative spectra and concentrations.", "Run MCR-ALS",
                    "72-pca--mcr-als"),
    "fit.export_mcr": ("Write MCR_component_spectra.csv and MCR_component_makeup.csv to a folder.", "Export MCR-ALS",
                       "72-pca--mcr-als"),
    "fit.col.percent": ("Share of this component in the sample (%).", "component percent", "72-pca--mcr-als"),
    "fit.itfa_sample": ("The sample whose component percentages are held fixed.", "anchored sample", "73-itfa"),
    "fit.itfa_pct": ("The percentages, comma separated, one per component.", "percentages", "73-itfa"),
    "fit.add_target": ("Add the sample and its percentages to the anchored targets.", "Add target", "73-itfa"),
    "fit.remove_target": ("Remove the selected anchored targets.", "Remove target", "73-itfa"),
    "fit.run_itfa": ("Refine the MCR-ALS concentration profiles with the anchored targets held fixed.", "Run ITFA",
                     "73-itfa"),
    "fit.export_itfa": ("Write ITFA_component_spectra.csv and ITFA_component_makeup.csv to a folder.", "Export ITFA",
                        "73-itfa"),
    "fit.pk_trace": ("The trace to decompose (a row ticked Fit in Compare).", "trace", "74-peaks"),
    "fit.pk_n": ("Number of peaks (1 to 8).", "peaks", "74-peaks"),
    "fit.pk_shape": ("Peak shape: gaussian, lorentzian or pseudo-voigt.", "shape", "74-peaks"),
    "fit.pk_step": ("Add an arctan edge step under the peaks.", "edge step", "74-peaks"),
    "fit.pk_bg": ("Background: linear or constant.", "background", "74-peaks"),
    "fit.pk_lo": ("Lower end of the range (eV); blank = the whole trace.", "range from", "74-peaks"),
    "fit.pk_hi": ("Upper end of the range (eV); blank = the whole trace.", "range to", "74-peaks"),
    "fit.pk_centres": ("Initial peak centres in eV, comma separated; blank = guesses from the local maxima.",
                       "initial centres", "74-peaks"),
    "fit.run_peaks": ("Fit peaks + arctan step + background with least squares.", "Run the decomposition", "74-peaks"),
    "fit.export_peaks": ("Write every parameter and the curves as CSV with a provenance header.",
                         "Export the decomposition", "74-peaks"),
    "fit.col.parameter": ("p1_amp, p1_centre, p1_width, …, step_height, step_centre, step_width, the background.",
                          "parameter", "74-peaks"),
    "fit.col.value": ("The fitted value.", "value", "74-peaks"),
    "fit.col.error": ("Standard error from the covariance of the fit.", "uncertainty", "74-peaks"),
    # ------------------------------------------------------------------ maps
    "maps.stack": ("The stack whose hypercube is loaded.", "stack", "8-maps"),
    "maps.load": ("Read the raw .bin files of the stack once and cache them (a few hundred MB per stack).",
                  "Load hypercube", "8-maps"),
    "maps.det": ("The sum of the four detectors, or one detector.", "detector", "8-maps"),
    "maps.line": ("A fixed line, or custom with the eV bounds.", "window", "8-maps"),
    "maps.w_lo": ("Lower end of the emission window (eV).", "window from", "8-maps"),
    "maps.w_hi": ("Upper end of the emission window (eV).", "window to", "8-maps"),
    "maps.per_i0": ("Divide by I0.", "divide by I0", "8-maps"),
    "maps.log": ("Show log10 of the map.", "log scale", "8-maps"),
    "maps.deconv": ("Sharpen a map blurred by a beam larger than the pixel pitch (a view, not data: exports keep the "
                    "measured cube).", "deconvolve", "8-maps"),
    "maps.deconv_method": ("Richardson-Lucy (non-negative, 10-50 iterations) or Wiener (one-shot filter).",
                           "deconvolution method", "8-maps"),
    "maps.psf": ("Full width at half maximum of the beam (µm).", "beam width", "8-maps"),
    "maps.iter": ("Richardson-Lucy iterations.", "deconvolution iterations", "8-maps"),
    "maps.roi": ("The region of the spectra: the whole map, the current zone, or a rectangle drawn on the map.", "ROI",
                 "8-maps"),
    "maps.energy": ("The incident energy of the map.", "energy", "8-maps"),
    "maps.avg": ("Average the map over every valid energy.", "average over energies", "8-maps"),
    "maps.draw_elements": ("One map per fixed line (config/roi_bins.json).", "Draw the elemental maps", "8-maps"),
    "maps.export_4d": ("Write the PyMca 4D stack _Elemental_PyMca.h5.", "Export PyMca 4D", "8-maps"),
    "maps.rgb": ("The window shown in this colour.", "colour window", "8-maps"),
    "maps.draw_rgb": ("Three windows as a colour composite.", "Draw the RGB composite", "8-maps"),
    "maps.n_pc": ("Number of PCA components kept before k-means.", "PCA components", "8-maps"),
    "maps.n_k": ("Number of k-means clusters.", "clusters", "8-maps"),
    "maps.run_clusters": ("PCA on the per-pixel XANES, then k-means: the cluster map and the mean spectra.",
                          "Run the clustering", "8-maps"),
    "maps.export_xanes": ("Write the PyMca XANES stack _PCA-CA.h5 (y, x, E).", "Export PyMca XANES stack", "8-maps"),
    # ------------------------------------------------------------------ report
    "report.title": ("The report title (default 'AURORA report · <project>').", "report title", "9-report"),
    "report.out": ("The output folder (default <project>/reports/aurora).", "output folder", "9-report"),
    "report.browse": ("Choose the output folder.", "Browse for the output folder", "9-report"),
    "report.appendix": ("Appendix A: the four detectors per zone and scan.", "Appendix A", "9-report"),
    "report.maps": ("Appendix B: plate maps with the zones.", "Appendix B", "9-report"),
    "report.groups": ("The sample-sheet groups found in the zones: tick the ones to include.", "groups", "9-report"),
    "report.all": ("Tick every group.", "All groups", "9-report"),
    "report.none": ("Untick every group.", "No group", "9-report"),
    "report.build": ("Build the PDF, the figures (PNG and SVG) and one CSV per zone and scan, in the background.",
                     "Build the report", "9-report"),
    "report.prev": ("Previous page of the preview.", "Previous page", "9-report"),
    "report.next": ("Next page of the preview.", "Next page", "9-report"),
    "report.open_folder": ("Open the report folder.", "Open folder", "9-report"),
    "report.open_pdf": ("Open the PDF.", "Open PDF", "9-report"),
    # ------------------------------------------------------------------ settings and dialogs
    "settings.appearance": ("Follow the system, light or dark. Applied at once.", "appearance", "10-settings"),
    "settings.text_size": ("Normal, large or larger text (13, 15 or 17 px). Applied at once.", "text size",
                           "10-settings"),
    "settings.author": ("Printed in the report footer and the PDF metadata. Blank: resolved when a report is built.",
                        "report author", "10-settings"),
    "settings.open_last": ("Open the last project when AURORA starts (on by default).",
                           "open the last project at start", "10-settings"),
    "settings.advanced": ("Every option (Advanced mode); Easy mode shows the basic workflow (Ctrl+Shift+A).",
                          "advanced mode", "10-settings"),
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

# standard dialog buttons -> tooltip (by their text, without the mnemonic)
BUTTON_TIPS = {
    "OK": "Apply and close", "Cancel": "Close without changing anything", "Close": "Close this window",
    "Help": "Open the manual at this dialog's section", "Bake": "Build the stitched stack from the ticked tiles",
    "Apply": "Apply without closing", "Save": "Save and close", "Yes": "Yes", "No": "No",
}


def manual_ref(anchor: str) -> str:
    """'… More: manual §4.5' for an anchor (the section number from MANUAL.md)."""
    from aurora.app.help import section_of
    sec = section_of(anchor)
    return f"More: manual §{sec}" if sec else "More: the manual"


def explain(widget: QWidget, key: str) -> QWidget:
    """Set the tooltip, accessible name, status tip and What's This text of a widget from HELP[key]."""
    tip, name, anchor = HELP[key]
    widget.setToolTip(tip)
    widget.setProperty("aurora_tip", tip)          # widgets.set_enabled keeps it while the widget is disabled
    widget.setAccessibleName(name)
    widget.setStatusTip(tip.split("\n")[0])
    widget.setWhatsThis(f"{tip}\n… {manual_ref(anchor)}")
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
        tip = HELP[key][0] if key in HELP else key
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
