# Credits

The text below (aurora/credits.py, CREDITS) is printed in the About dialog and on the cover of every report; the README opens its credits with the first sentence, and every report page carries the short footer.

> AURORA is version 2 of John Bussey's SGM XAS GUI (Washington State University), based on James J. Dynes' SGM_Beamline_Analysis Jupyter notebooks (Canadian Light Source). It adds the beamtime reporting pipeline of Sami Soudani (Washington State University). Edge and emission-line energies from xraydb. Data from the SGM beamline, Canadian Light Source.

## Detail

| Who | What AURORA takes from it | Left in the original |
|---|---|---|
| John Bussey, WSU | Tabs Reduce, Check scans, Compare & plot, LCF, MCR-ALS, ITFA; per-detector ROI pairs; shared bad-point mask; the checks of Check scans with their thresholds; larch-style and legacy normalisation; wide comparison CSV; `_full_reduced.csv` / `_absorption.csv` / `_gui_settings.json` / `_polygon_roi.json` formats; worker-thread + cancel pattern; exceptions shown in a dialog. | |
| James J. Dynes and the SGM staff, CLS | Loader for the bluesky h5 + bin + csv layout; SDD calibration JSON; hypercube dashboard (maps of any window and energy, ROI spectra, elemental maps, RGB composites); external I0 preview (the I0 explorer, mesh vs photodiode); IPFY; PCA / K-Means / cluster merger; PyMca 3D and 4D exports; stitching. | Alignment (roll, trim); masked-triangulation rendering; auto element ROI; RIXS-like map; emission peak tracking; the lmfit layer; the guides. `sf.py` (aXis2000 port, GPL-3) is not reused; xraydb replaces it. |
| Sami Soudani, WSU | Survey pad detection (zone proposals); plate layouts and rotation; fixed ROI bins; per-SDD consistency check; saturation detection; stack status; holder summary and technical reports (the AURORA report); the AURORA application, name, logo and design. | Hole detection; SDD recalibration from the day's sum; email draft. |

The licences of the third-party packages bundled with the installers are in THIRD_PARTY_LICENSES.txt, next to this file in an installation and under Help > Licences.
