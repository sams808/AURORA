# Changelog

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
