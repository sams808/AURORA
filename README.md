# AURORA

Soft X-ray XAS and XRF stack analysis for the SGM beamline (ambxas endstation) of the Canadian Light Source. Desktop application for Windows and macOS.

Status: Phase 0 (2026-11-06): package skeleton, raw-data readers, project discovery, Qt shell with the Home perspective, headless CLI, tests, Windows + macOS builds in CI. See `docs/BRAINSTORM.md` for the feature universe, architecture and roadmap, `docs/DESIGN.md` for the design language, and `branding/` for the logo.

User guide: `docs/USER_GUIDE.md` (one section per perspective). Changes: `CHANGELOG.md`.

## Quick start (developers)

```
py -3.12 -m venv .venv && .venv\Scripts\activate   # own environment: PySide6 >= 6.10 is required (6.9 paints nothing)
python -m pip install -e ".[dev,build]"
python -m pytest                       # synthetic data; the Nov 2026 beamtime tests run when AURORA_REAL_PROJECT exists
aurora                                 # the application
aurora project <beamtime folder>       # headless listing of days, holders, scans, status and flags
aurora scan <scan folder>              # one scan's metadata and first energy
aurora --distcheck                     # offscreen smoke test (used by CI on the frozen build)
packaging\build_win.bat                # frozen build from a clean venv -> dist/AURORA (Windows)
packaging/build_mac.sh                 # same on macOS -> dist/AURORA.app
```

Always build from a clean virtual environment (the scripts create `.venv-build`): PyInstaller follows every import it
finds in site-packages, and a global Python with torch or scikit-learn installed turns a 100 MB bundle into 1 GB.

The beamtime folder layout is `raw/<day>/<HHMMSS>_<name>_<plan>/` with `processed/`, `config/`, `reports/` beside it.
`aurora.core` never imports Qt; `aurora.app` holds the PySide6 interface.

## Lineage and credits

AURORA builds on three pieces of work:

- **SGM XAS GUI** by **John Bussey**, Washington State University (2026): the reduce / check / compare / LCF / MCR-ALS / ITFA workflow, the reduced-CSV layout with its provenance header and the settings files. AURORA keeps that workflow and stays file-compatible with it. Original program: `legacy/CLS_GUI_JohnBussey/`.
- **SGM_Beamline_Analysis** by **James J. Dynes and the SGM beamline staff**, Canadian Light Source (github.com/Beamlines-CanadianLightSource/SGM_Beamline_Analysis): raw-data parsing, SDD calibration, alignment, hypercube dashboard, PCA and clustering, PyMca export, stitching. Original toolkit: `legacy/SGM_Beamline_Analysis/`.
- **sgm_report** beamtime reporting pipeline by **Sami Soudani**, Washington State University: survey pad detection, plate layouts, fixed ROI bins, per-SDD checks, saturation detection, holder reports.

Edge and emission-line energies come from xraydb. Data acquired at the SGM beamline of the Canadian Light Source.

## Layout

```
aurora/core/    readers (h5, raw bin/csv, sgm_report caches, config, zones), names, grid, project model, synthetic test data
aurora/core/    + stack/survey/zones (the zone tool's maths), qc (count rate, saturation, I0, SDD agreement),
                compare (readers, normalisations, peaks), fit (LCF, PCA, MCR-ALS, ITFA), report (group-organised PDF)
aurora/app/     PySide6 shell: theme, icons, rail; perspectives Home, Survey, Reduce, Check, Compare, Fit, Report
aurora/cli.py   aurora / aurora project / aurora scan / aurora report / aurora --distcheck
tests/          pytest (offscreen Qt); tests/test_real_data.py needs the beamtime folder
packaging/      PyInstaller spec; .github/workflows/ci.yml builds on windows-latest and macos-14
branding/       logo (SVG, PNG 16-1024, .ico), wordmark
docs/           BRAINSTORM.md, DESIGN.md
legacy/         sgm_report v2.5 (the Nov 2026 beamtime scripts); the two parent code bases stay local, not committed
```

Author: Sami Soudani.
