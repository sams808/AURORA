# Credits

Text for the About dialog, the splash screen, the README and the footer of every report.

> AURORA builds on the SGM XAS GUI by John Bussey (Washington State University), on the SGM_Beamline_Analysis toolkit by James J. Dynes and the SGM beamline staff (Canadian Light Source), and on the beamtime reporting pipeline by Sami Soudani (Washington State University). Edge and emission-line energies from xraydb. Data from the SGM beamline, Canadian Light Source.

## Detail

| Who | What AURORA takes from it |
|---|---|
| John Bussey, WSU | Tabs Reduce, Check scans, Compare & plot, LCF, MCR-ALS, ITFA; per-detector ROI pairs; shared bad-point mask; larch-style and legacy normalisation; wide comparison CSV; `_full_reduced.csv` / `_absorption.csv` / `_gui_settings.json` / `_polygon_roi.json` formats; worker-thread + cancel pattern; exceptions shown in a dialog. |
| James J. Dynes and the SGM staff, CLS | Loader for the bluesky h5 + bin + csv layout; SDD calibration JSON; alignment (roll, trim); masked-triangulation rendering; hypercube dashboard; auto element ROI; external I0 preview; IPFY; PCA / K-Means / cluster merger; PyMca 3D and 4D exports; stitching; RIXS-like map; emission peak tracking; lmfit layer; guides. `sf.py` (aXis2000 port, GPL-3) is not reused; xraydb replaces it. |
| Sami Soudani, WSU | Survey pad and hole detection; plate layouts and rotation; fixed ROI bins; SDD recalibration from the day's sum; per-SDD consistency check; saturation detection; stack status; holder summary and technical reports; email draft; the AURORA application, name, logo and design. |

Names on the CLS side to be confirmed with the beamline before the first public release.

Third-party libraries (licences to list in the About dialog): PySide6 (LGPL), numpy, scipy, pandas, matplotlib, h5py, lmfit, pymcr, scikit-learn, scikit-image, xraydb, platformdirs, pyqtgraph; fonts Inter and JetBrains Mono (OFL).
