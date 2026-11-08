# Credits

The text below (aurora/credits.py, CREDITS) is printed in the About dialog and on the cover of every report; the README opens its credits with the first sentence, and every report page carries the short footer.

> AURORA is version 2 of John Bussey's SGM XAS GUI (Washington State University), based on James J. Dynes' SGM_Beamline_Analysis Jupyter notebooks (Canadian Light Source). It adds the beamtime reporting pipeline of Sami Soudani (Washington State University). Edge and emission-line energies from xraydb. Data from the SGM beamline, Canadian Light Source.

## Detail

| Who | What AURORA takes from it |
|---|---|
| John Bussey, WSU | Tabs Reduce, Check scans, Compare & plot, LCF, MCR-ALS, ITFA; per-detector ROI pairs; shared bad-point mask; larch-style and legacy normalisation; wide comparison CSV; `_full_reduced.csv` / `_absorption.csv` / `_gui_settings.json` / `_polygon_roi.json` formats; worker-thread + cancel pattern; exceptions shown in a dialog. |
| James J. Dynes and the SGM staff, CLS | Loader for the bluesky h5 + bin + csv layout; SDD calibration JSON; alignment (roll, trim); masked-triangulation rendering; hypercube dashboard; auto element ROI; external I0 preview; IPFY; PCA / K-Means / cluster merger; PyMca 3D and 4D exports; stitching; RIXS-like map; emission peak tracking; lmfit layer; guides. `sf.py` (aXis2000 port, GPL-3) is not reused; xraydb replaces it. |
| Sami Soudani, WSU | Survey pad and hole detection; plate layouts and rotation; fixed ROI bins; SDD recalibration from the day's sum; per-SDD consistency check; saturation detection; stack status; holder summary and technical reports; email draft; the AURORA application, name, logo and design. |

Names on the CLS side to be confirmed with the beamline before the first public release.

Third-party libraries (licences to list in the About dialog): PySide6 (LGPL), numpy, scipy, pandas, matplotlib, h5py, lmfit, pymcr, scikit-learn, scikit-image, xraydb, platformdirs, pyqtgraph; fonts Inter and JetBrains Mono (OFL).
