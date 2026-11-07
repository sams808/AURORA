# Changelog

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
