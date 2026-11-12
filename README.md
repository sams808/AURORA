# AURORA

Soft X-ray XAS and XRF stack analysis for the SGM beamline (ambxas endstation) of the Canadian Light Source. Desktop
application for Windows and macOS.

AURORA is version 2 of John Bussey's SGM XAS GUI, based on James J. Dynes' SGM_Beamline_Analysis notebooks.

Status 0.4.1 (2026-11-12): every perspective is in place (Home, Survey, Reduce, Check, Compare, Fit, Maps, Report).
AURORA starts in **Easy mode**, the basic path of John Bussey's GUI (open the beamtime, draw the samples, check the
spectra, compare, fit with LCF, build the report) in plain words, for people new to X-ray absorption: a glossary
(Help > Glossary), a *Getting started* row on Home and a quick start in the user guide. **Advanced mode** (View >
Advanced mode, the button in the top bar, or Ctrl+Shift+A) shows every option, Maps included. Samples are drawn and
erased on the Survey and in Reduce with one tool bar, and their spectrum shows while the outline is drawn. Zones
attach to every stack that covers them, every edit can be undone, the session is backed up, and the report says what
it will contain before it is built. See `CHANGELOG.md`.

AURORA is blank: it ships no lab's names, samples or settings, and works on your own beamtime. Only the scan folders
are needed (`raw/<day>/<scan>/`, or a loose folder of scan folders); the sample sheet, the fixed emission lines
(`config/roi_bins.json`), the SDD calibration, a plate layout and hand-drawn holder sketches are optional, and the
manual says what each adds. A beamtime on a read-only share works through a working folder of your own.

## Downloads

Each release on the [GitHub releases page](https://github.com/sams808/AURORA/releases) carries:

| File | For |
|---|---|
| `AURORA-<version>-windows-x64-setup.exe` | Windows 10 (1809) or 11, 64-bit: per-user install, no administrator rights |
| `AURORA-<version>-windows-x64.zip` | Windows, the same application without an installer |
| `AURORA-<version>-macos-arm64.dmg` | macOS 15 Sequoia or later on Apple silicon |
| `AURORA-<version>-macos-x86_64.dmg` | macOS 15 Sequoia or later on an Intel Mac |
| `SHA256SUMS.txt` | the checksums of the four files above |

The installers are not code-signed: Windows SmartScreen asks once (*More info* > *Run anyway*), and macOS asks once
(*System Settings > Privacy & Security > Open Anyway*, or `xattr -dr com.apple.quarantine /Applications/AURORA.app`).
The macOS builds are tested on GitHub's macOS runners, not on a physical Mac. The installation guide covers both
systems, the command line (`aurora-cli.exe` on Windows) and where settings, logs and caches are stored.

Measured on the last local Windows x64 build (2026-11-11, PyInstaller 6.22.3, PySide6 6.11.3): the setup is 107 MiB,
the zip 146 MiB, and the installed application folder 308 MiB.

## Documentation

The manuals live in `aurora/resources/docs/` and open inside the application from the Help menu:
[user guide](aurora/resources/docs/USER_GUIDE.md) (short), [manual](aurora/resources/docs/MANUAL.md) (every control),
[tutorials](aurora/resources/docs/TUTORIALS.md) (nine tutorials, eight on the demo beamtime: `aurora demo <folder>` or
Help > Open the demo beamtime), [installation](aurora/resources/docs/INSTALL.md). For developers:
[developer manual](docs/DEVELOPER.md) (architecture, modules, formats, tests),
[design](docs/DESIGN.md), and the [documentation index](docs/README.md). Logo in `branding/`.

## Quick start (developers)

```
py -3.12 -m venv .venv && .venv\Scripts\activate   # own environment: PySide6 >= 6.10 is required (6.9 paints nothing)
python -m pip install -e ".[dev]"
python -m pytest                       # synthetic data; the real-data tests run when AURORA_REAL_PROJECT names a beamtime
aurora                                 # the application
aurora <beamtime folder>               # the application on that beamtime
aurora project <beamtime folder>       # headless listing of days, holders, scans, status and flags
aurora scan <scan folder>              # one scan's metadata and first energy
aurora report <beamtime folder>        # the group-organised report, headless, from the saved session or a zones file
aurora demo <folder>                   # a synthetic beamtime for the tutorials (a new or empty folder only)
aurora --selftest                      # every core path and perspective; the exit code is the number of failures
packaging\build_win.bat                # Windows: dist\AURORA, the per-user setup and the zip (Inno Setup 6)
packaging/build_mac.sh                 # macOS: dist/AURORA.app and the disk image of this Mac's architecture
```

The build scripts create a clean `.venv-build` (Python 3.12, the pins of `packaging/constraints.txt`): PyInstaller
follows every import it finds in site-packages, and a global Python with torch or scikit-learn installed would turn a
300 MB bundle into 1 GB.

The beamtime folder layout is `raw/<day>/<HHMMSS>_<name>_<plan>/` with the optional `processed/`, `config/`,
`reports/` beside it; a loose folder of scan folders (John Bussey's `CLS GUI/`) opens too. `aurora.core` never imports Qt; `aurora.app` holds
the PySide6 interface.

## Continuous integration

`.github/workflows/ci.yml`, on every push to `main`, every pull request and by hand:

- **tests**: the suite and the self-test from source on windows-latest and macos-15 (Python 3.11, 3.12, 3.13) and on
  macos-15-intel, macos-26 and ubuntu-24.04 (Python 3.12);
- **build-windows** (windows-latest): the frozen app and `aurora-cli.exe`, their self-tests, a frozen report, the
  per-user installer installed, upgraded and uninstalled, the portable zip;
- **test-windows**: the installer and the portable zip (unpacked, self-tested) on windows-2022 and windows-11-arm;
- **build-macos**: the app and its disk image on macos-15 (arm64) and macos-15-intel (x86_64), with the measured
  minimum macOS (`packaging/min_macos.txt`);
- **test-macos**: each disk image on macos-15 and macos-26 (arm64) and macos-15-intel (x86_64).

A `v*` tag runs `.github/workflows/release.yml`: it checks the tag against the version, runs the whole of ci.yml and
opens a draft GitHub release with the four installers, `SHA256SUMS.txt` and the notes of the CHANGELOG section.

## Lineage and credits

AURORA is version 2 of John Bussey's SGM XAS GUI (Washington State University), based on James J. Dynes' SGM_Beamline_Analysis Jupyter notebooks (Canadian Light Source).

- **SGM XAS GUI** by **John Bussey**, Washington State University (2026): the reduce / check / compare / LCF / MCR-ALS / ITFA workflow, the reduced-CSV layout with its provenance header and the settings files. AURORA keeps that workflow and its file formats, and its Easy mode follows the GUI's basic path. The original program is not part of this repository.
- **SGM_Beamline_Analysis** by **James J. Dynes and the SGM beamline staff**, Canadian Light Source (github.com/Beamlines-CanadianLightSource/SGM_Beamline_Analysis): the notebook toolkit for raw-data parsing, SDD calibration, alignment, the hypercube dashboard, PCA and clustering, PyMca export and stitching. The original notebooks are on GitHub: https://github.com/Beamlines-CanadianLightSource/SGM_Beamline_Analysis.
- **sgm_report**, the beamtime reporting pipeline by **Sami Soudani**, Washington State University: survey pad detection, plate layouts, fixed ROI bins, per-SDD checks, saturation detection and holder reports.

Edge and emission-line energies come from xraydb. Data acquired at the SGM beamline of the Canadian Light Source.
The full credits text is in `CREDITS.md`.

Licence: MIT (`LICENSE`), copyright 2026 John Bussey and Sami Soudani. The installers carry `THIRD_PARTY_LICENSES.txt` for the bundled packages; Qt for Python is
used under the LGPL-3.0. Help > Licences shows all of them.

## Layout

```
aurora/core/        readers (h5, raw bin/csv, sgm_report caches, config, zones), names, grid, project model,
                    stack / survey / zones (the zone tool's maths), XRF windows, rules, qc, spectra, compare, fit,
                    report and provenance, hypercube (maps, clusters, PyMca), references, propose, deconv, stitch,
                    synthetic test data
aurora/app/         PySide6 shell: theme, icons, rail, session (undo, backups), jobs; perspectives Home, Survey, Reduce,
                    Check, Compare, Fit, Maps, Report; help browser and diagnostics
aurora/resources/   icons, ui/ and docs/ (the four manuals and their img/ screenshots, made by
                    docs/make_screenshots.py)
aurora/cli.py       aurora, aurora <folder>, project, scan, report, demo, --selftest, --distcheck
aurora/selftest.py  the deep self-test (also Help > Run self-test…)
tests/              pytest (offscreen Qt); the real-data folders come from the environment or tests/local_paths.json
packaging/          PyInstaller spec, Inno Setup script, build scripts, disk image, constraints, licences
.github/workflows/  ci.yml (tests, builds, installer and disk-image tests) and release.yml (draft releases)
branding/           logo (SVG, PNG 16-1024, .ico, .icns), wordmark
docs/               DEVELOPER, DESIGN, make_screenshots.py
```

Authors: John Bussey and Sami Soudani.
