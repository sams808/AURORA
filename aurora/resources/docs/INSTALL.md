# AURORA installation and administration

## 1. Requirements

| | |
|---|---|
| Operating system | Windows 10/11 (x64) or macOS 12+ (Apple silicon or Intel, per the build) |
| Memory | 8 GB; a loaded hypercube of a large stack takes a few hundred MB |
| Disk | 300 MB for the application; `.aurora/cache` beside a beamtime grows with the hypercubes you load |
| Data | a beamtime folder with `raw/` readable; AURORA never writes into `raw/` |

No Python is needed for the frozen builds.

## 2. Frozen builds (recommended)

Download the zip of the latest release from the GitHub releases page of the repository.

Windows:

1. Unzip `AURORA-windows.zip` anywhere (for example `C:\Users\<you>\Apps\AURORA`).
2. Run `AURORA.exe`. SmartScreen may ask once because the executable is not signed: *More info* → *Run anyway*.
3. Optional: right-click `AURORA.exe` → *Send to* → *Desktop (create shortcut)*.

macOS:

1. Unzip `AURORA-macos.zip`; it contains `AURORA.app`.
2. The app is not notarised. Either right-click → *Open* and confirm, or remove the quarantine flag once:

   ```
   xattr -dr com.apple.quarantine /path/to/AURORA.app
   ```

3. Drag it into Applications if you like.

Check an installation from a terminal (opens nothing, prints versions and `distcheck ok`):

```
AURORA.exe --distcheck
AURORA.app/Contents/MacOS/AURORA --distcheck
```

## 3. From source

Python 3.12 (3.11 works) in its own virtual environment. PySide6 6.10 or newer is required: 6.9 paints no
pyqtgraph curves or images, which looks like a broken installation.

```
git clone https://github.com/sams808/AURORA
cd AURORA
py -3.12 -m venv .venv            # macOS / Linux: python3.12 -m venv .venv
.venv\Scripts\activate            # macOS / Linux: source .venv/bin/activate
python -m pip install -e ".[dev,build]"
aurora --distcheck
aurora
```

Dependencies: numpy, scipy, h5py, PySide6, pyqtgraph, matplotlib, xraydb, platformdirs. `dev` adds pytest, `build`
adds pyinstaller.

## 4. Where things are stored

| What | Where |
|---|---|
| settings (defaults, author, reference folder, recent projects) | Windows registry `HKCU\Software\<org>\AURORA`; macOS `~/Library/Preferences`; or `AURORA_SETTINGS_DIR/aurora.ini` when that environment variable is set |
| session of a beamtime | `<project>/.aurora/session.json` |
| caches | `<project>/.aurora/cache/<day>/<scan>/` (reduced stack, hypercube memmaps, mcc curves) |
| reports | the output folder chosen in Report (default `<project>/reports/aurora`) |

Deleting `.aurora/cache` is always safe; it is rebuilt from `raw/` (or from the sgm_report caches in `processed/`)
on demand. Deleting `session.json` loses the zones and settings unless they were exported as `zones_all.json`.

A beamtime folder on a network share works; the first read of a stack is slow, the caches live beside the data.
For a read-only share, copy the beamtime locally or expect the caches to fail silently and the stacks to be re-read.

## 5. Updating

Frozen builds: unzip the new version over the old folder, or beside it. Settings, sessions and caches are not inside
the application folder, so nothing is lost. From source: `git pull` then `python -m pip install -e ".[dev,build]"`.

The session file is versioned by content, not by number; a newer AURORA reads every older session.

## 6. Building the frozen application

Always build from a clean virtual environment: PyInstaller follows every import it finds in site-packages, and a
global Python with torch or scikit-learn installed turns a 300 MB bundle into 1 GB. The scripts create
`.venv-build` for that purpose.

```
packaging\build_win.bat          -> dist\AURORA\AURORA.exe   (Windows)
packaging/build_mac.sh           -> dist/AURORA.app          (macOS)
```

Both run the frozen `--distcheck` at the end and fail if it fails. The spec file `packaging/aurora.spec` lists the
hidden imports (numpy, scipy.interpolate, sqlalchemy for xraydb, pyqtgraph) and the data files (xraydb's database,
the icons). A build fails when AURORA is running from `dist/`: close it first.

## 7. Continuous integration and releases

`.github/workflows/ci.yml` runs on every push to `main`, every pull request and every `v*` tag, on `windows-latest`
and `macos-latest`: install, pytest, headless `--distcheck`, PyInstaller build, frozen `--distcheck`, zip, upload
as a workflow artifact (`AURORA-Windows`, `AURORA-macOS`).

Release procedure:

1. Bump `version` in `pyproject.toml` and `__version__` in `aurora/__init__.py`; add the entry to `CHANGELOG.md`.
2. Commit, `git tag v<version>`, `git push && git push --tags`.
3. When the tag's workflow is green, download the two zips from the run's artifacts and attach them to a GitHub
   release named `v<version>` (or `gh release create v<version> AURORA-windows.zip AURORA-macos.zip`).

## 8. Running the tests

```
python -m pytest -q                 # synthetic data, offscreen Qt, about a minute
python -m pytest tests/test_app_phase5.py -q
AURORA_REAL_PROJECT=<beamtime folder> python -m pytest tests/test_real_data.py tests/test_real_zones.py
```

The real-data tests are read-only and skip when the folder is absent. The test session sets `QT_QPA_PLATFORM=offscreen`
and `AURORA_SETTINGS_DIR` so that no real settings are touched.

## 9. Known limits

- The frozen builds are unsigned (SmartScreen / Gatekeeper prompts once).
- The hypercube reader loads one stack at a time; very large stacks (tens of thousands of pixels × hundreds of
  energies) need the memory of the cube plus one working copy.
- Linux is not built in CI; the source install works there with the same commands.
