# AURORA installation and sharing

AURORA is version 2 of John Bussey's SGM XAS GUI, based on James J. Dynes' SGM_Beamline_Analysis notebooks. Every
release on the GitHub releases page of the repository (github.com/sams808/AURORA) carries five files:

| File | For |
|---|---|
| `AURORA-<version>-windows-x64-setup.exe` | Windows 10 or 11, installed for the current user (no administrator rights) |
| `AURORA-<version>-windows-x64.zip` | Windows, the same application without an installer (portable) |
| `AURORA-<version>-macos-arm64.dmg` | macOS on Apple silicon (M1 and later) |
| `AURORA-<version>-macos-x86_64.dmg` | macOS on an Intel Mac |
| `SHA256SUMS.txt` | the SHA-256 checksum of each file above |

## 1. Requirements

| | |
|---|---|
| Operating system | Windows 10 (1809) or 11, 64-bit; macOS 15 Sequoia or later, Apple silicon or Intel |
| Memory | 8 GB; a loaded hypercube of a large stack takes a few hundred MB |
| Disk | 400 MB for the application; `.aurora/cache` beside a beamtime grows with the stacks and hypercubes you load |
| Data | a beamtime folder with `raw/` readable; AURORA never writes into `raw/` |

No Python is needed for the installers. The minimum macOS is measured on every build from the bundled binaries
(`packaging/min_macos.txt`, 15.0 today, set by the PySide6 6.11.3 binaries): macOS 14 and older cannot open this
version. Windows 11 on ARM runs the x64 build through its x64 emulation.

## 2. Windows

Download `AURORA-<version>-windows-x64-setup.exe` and run it.

- The installer needs no administrator rights: AURORA goes to `%LOCALAPPDATA%\Programs\AURORA` for the current user,
  with a Start-menu entry and, if ticked, a desktop shortcut. An optional, unticked task adds *Open in AURORA* to the
  right-click menu of folders in Explorer. An account that can elevate is offered an all-users install in the wizard.
- SmartScreen may say "Windows protected your PC" because the installer is not code-signed: click *More info*, then
  *Run anyway*.
- Upgrading: run the setup of the newer version. It finds the installed copy (same folder, same choices), closes a
  running AURORA and replaces the application; settings, logs and beamtime folders are not touched.
- Uninstalling: *Settings > Apps > Installed apps > AURORA > Uninstall*, or the *Uninstall AURORA* entry of the Start
  menu. The application folder, its shortcuts and the folder verb go; the settings and the log folder stay
  (section 5), so a later install finds them again.
- Silent install for IT: `AURORA-<version>-windows-x64-setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER`
  (add `/TASKS="desktopicon"` for the desktop shortcut); silent uninstall:
  `%LOCALAPPDATA%\Programs\AURORA\unins000.exe /VERYSILENT`.

Without an installer, `AURORA-<version>-windows-x64.zip` holds the same application: unzip it anywhere (for example
`C:\Users\<you>\Apps\AURORA`) and run `AURORA.exe`. To update the portable copy, unzip the new version into a new folder
and delete the old one, never over it (files of the old runtime would stay); settings, sessions and caches are not
inside the application folder.

The folder holds two executables: `AURORA.exe`, the windowed application, and `aurora-cli.exe`, its console twin for a
terminal (cmd or PowerShell), which prints its output and returns an exit code.

## 3. macOS

Download the disk image for your Mac: `AURORA-<version>-macos-arm64.dmg` for Apple silicon (M1 and later),
`AURORA-<version>-macos-x86_64.dmg` for an Intel Mac (Apple menu > *About This Mac* names the chip). AURORA needs macOS
15 Sequoia or later.

1. Open the disk image and drag **AURORA** onto the **Applications** shortcut beside it.
2. Open AURORA from Applications. AURORA is not notarised by Apple, so the first time Gatekeeper stops it ("Apple could
   not verify AURORA is free of malware"): click *Done* (not *Move to Trash*), open *System Settings > Privacy &
   Security*, scroll to *Security* and click *Open Anyway* next to the message about AURORA, then confirm with your
   password or Touch ID. This is needed once; afterwards AURORA opens normally.
3. The same in Terminal, instead of step 2:

   ```
   xattr -dr com.apple.quarantine /Applications/AURORA.app
   ```

The note *If macOS blocks AURORA.txt* inside the disk image repeats these steps; `LICENSE`, `CREDITS.md`,
`THIRD_PARTY_LICENSES.txt` and `LICENSES/` are beside it. If macOS says the app "is damaged and can't be opened", the
download was altered on the way: download it again and compare it with `SHA256SUMS.txt` of the release
(`shasum -a 256 <file>`). To update, drag the newer AURORA into Applications over the old one; to uninstall, move
`AURORA.app` to the Trash (the settings and logs of section 5 stay).

On macOS the executable inside the app, `AURORA.app/Contents/MacOS/AURORA`, is both the application and the command
line. Finder's *Open With > AURORA* on a beamtime folder, or a folder dropped on AURORA's Dock icon, opens it.

The macOS builds are tested on GitHub's hosted runners (macOS 15 and macOS 26 on Apple silicon, macOS 15 on Intel):
each disk image is mounted read-only, its app is started directly and the way Finder starts it, the self-test runs,
and the quarantine workaround above is exercised. They have not been run on a physical Mac.

## 4. Checking an installation and the command line

```
aurora-cli.exe --version
aurora-cli.exe --selftest --out selftest.json        every core path and perspective on a synthetic beamtime
aurora-cli.exe project "D:\beamtimes\my_beamtime"    list days, holders and scans
aurora-cli.exe report "D:\beamtimes\my_beamtime"     build the report from the saved session
aurora-cli.exe report "D:\beamtimes\my_beamtime" --holders holderA --preset draft --author "A. Name"
/Applications/AURORA.app/Contents/MacOS/AURORA --selftest --out ~/Desktop/selftest.json
```

`--selftest` checks every core path and every perspective on a synthetic beamtime in a temporary folder (with
`--platform native` it uses the real window system instead of an offscreen one, `--no-gui` skips the perspectives,
`--keep` keeps the temporary folder); its exit code is the number of failed checks, 0 when all passed. `--distcheck`
is a faster smoke test. Every command writes to the log file (section 5). A command that fails prints the error (a
wrong path or file in one line, anything else with its traceback), writes its traceback to the log and returns exit
code 1; it never waits on an error window. `AURORA.exe <folder>`
(or a folder dropped on the shortcut, or *Open in AURORA*) opens that beamtime. MANUAL §11 lists every command and
option.

**Help > Run self-test…** runs the same self-test from inside AURORA in the background, then shows the summary (*N of
M checks ok*) with **Copy** and **Open folder**; the JSON report is written to the self-test folder of section 5. To
report a problem, send that JSON file together with the text of **Help > Report a problem…** > **Copy diagnostics**
(versions, operating system, project, settings location and the last 200 lines of the log); the same dialog has **Open
the log folder** and the address of the GitHub issues page. GitHub issues are public: the JSON and the diagnostics name
folders, and the log lines name projects, scans and samples (the diagnostics write the home folder as ~), so read them
before posting.

## 5. Where things are stored

| What | Windows | macOS | Linux (from source) |
|---|---|---|---|
| the application | `%LOCALAPPDATA%\Programs\AURORA` (setup) or the unzipped folder | `/Applications/AURORA.app` | the source checkout |
| settings (author, defaults, reference folder, recent projects, mode, last folders, figure options) | registry `HKCU\Software\AURORA\AURORA` | `~/Library/Preferences/com.aurora.AURORA.plist` | `~/.config/AURORA/AURORA.conf` |
| log files (`aurora.log`, `faults.log`) | `%LOCALAPPDATA%\AURORA\Logs` | `~/Library/Logs/AURORA` | `~/.local/state/AURORA/log` |
| self-test reports (**Help > Run self-test…**) | `%LOCALAPPDATA%\AURORA\selftest` | `~/Library/Application Support/AURORA/selftest` | `~/.local/share/AURORA/selftest` |
| the demo beamtime of **Help > Open the demo beamtime** | `%LOCALAPPDATA%\AURORA\demo_beamtime` | `~/Library/Application Support/AURORA/demo_beamtime` | `~/.local/share/AURORA/demo_beamtime` |
| matplotlib's font cache for the report figures (installed copies) | `%LOCALAPPDATA%\AURORA\Cache\matplotlib` | `~/Library/Caches/AURORA/matplotlib` | matplotlib's own default |
| hypercubes of projects whose folder cannot be written (a read-only share) | `%LOCALAPPDATA%\AURORA\Cache\hypercubes` | `~/Library/Caches/AURORA/hypercubes` | `~/.cache/AURORA/hypercubes` |
| session, backups and checks of a beamtime | `<project>/.aurora/` (`session.json`, `session.json.bak`, `backups/`, `qc.json`, `library.json`, `session.lock` while an AURORA has it open) | the same | the same |
| caches of a beamtime | `<project>/.aurora/cache/<day>/<scan>/` | the same | the same |
| reports | the output folder chosen in Report (default `<project>/reports/aurora/<date>_<time>_<scope>/`) | the same | the same |

Three environment variables move these folders: `AURORA_SETTINGS_DIR` (settings in `aurora.ini` there),
`AURORA_LOG_DIR` (the log files) and `AURORA_CACHE_DIR` (the per-user hypercube cache).

Deleting `.aurora/cache` is always safe: it is rebuilt from `raw/` (or from the sgm_report caches in `processed/`) on
demand. A cache that no longer matches `config/roi_bins.json` or the energy folders is rebuilt by itself, and a damaged
one is set aside as `_reduced_cache.npz.bad` (MANUAL §12). Deleting `session.json` loses the zones and settings unless
a backup in `.aurora/backups/` or an exported `zones_all.json` is kept; **File > Restore zones from a backup…**
(Advanced mode) brings back a backup.

A beamtime folder on a network share works; the first read of a stack is slow, and the caches live beside the data.
On a read-only share the hypercubes go to the per-user cache above; the session cannot be saved there, so copy the
beamtime locally to keep zones with it. Such a project opens read-only and says so; closing the window offers to
export the zones. Two AURORAs on one beamtime (two computers on a share, or AURORA started twice): the second opens it
read-only and names the first.

A session written by an older AURORA opens and is upgraded (the original is kept once as `session.v1.bak.json`); a
session written by a newer AURORA opens read-only.

## 6. From source

Python 3.12 (3.11 and 3.13 are tested too) in its own virtual environment. PySide6 6.10 or newer is required: 6.9
paints no pyqtgraph curves or images, which looks like a broken installation.

```
git clone https://github.com/sams808/AURORA
cd AURORA
py -3.12 -m venv .venv            # macOS / Linux: python3.12 -m venv .venv
.venv\Scripts\activate            # macOS / Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
aurora --selftest
aurora
```

Dependencies: numpy, scipy, h5py, PySide6, pyqtgraph, matplotlib, xraydb, platformdirs. `dev` adds pytest and PyYAML,
`build` adds PyInstaller. Updating: `git pull`, then `python -m pip install -e ".[dev]"` again. On Linux the Qt runtime
needs the usual X11 / xcb libraries of the distribution.

## 7. Building the installers

The builds use Python 3.12 in a clean environment, `.venv-build`, created with uv (`uv venv --python 3.12`) or the py
launcher (`py -3.12`) and filled with the pins of `packaging/constraints.txt`; it is recreated when its Python,
`constraints.txt` or `pyproject.toml` changed. A global Python with torch or scikit-learn installed would turn a
300 MB bundle into 1 GB.

```
packaging\build_win.bat            dist\AURORA\, AURORA-<v>-windows-x64-setup.exe, AURORA-<v>-windows-x64.zip
packaging\build_win.bat /install   the same, then installs the new setup for the current user
packaging/build_mac.sh             dist/AURORA.app, AURORA-<v>-macos-<arm64|x86_64>.dmg
```

The Windows installer needs Inno Setup 6 (`winget install --id JRSoftware.InnoSetup -e`). The version comes from
`aurora/__init__.py` only. Each build runs the frozen self-test and fails if it fails; the macOS build also measures the
minimum macOS of every bundled binary (`packaging/macos_minos.py`) and fails when it exceeds `packaging/min_macos.txt`.
A build fails when AURORA is running from `dist/`: close it first.

## 8. Continuous integration and releases

`.github/workflows/ci.yml` runs on every push to `main`, every pull request and by hand:

| Job | Runners | What |
|---|---|---|
| tests | windows-latest and macos-15 (Python 3.11, 3.12 and 3.13); macos-15-intel, macos-26 and ubuntu-24.04 (Python 3.12) | the test suite and the self-test from source |
| build-windows | windows-latest | the frozen app and `aurora-cli.exe` (version resource, licences, self-tests, the headless command line, a frozen report), the installer installed, upgraded and uninstalled, the portable zip |
| test-windows | windows-2022, windows-11-arm | the installer installed for the user, the installed self-tests, the portable zip unpacked and self-tested, the uninstall |
| build-macos | macos-15 (arm64), macos-15-intel (x86_64) | the app (signature, architecture, measured minimum macOS, self-tests direct and through `open`, a frozen report) and its disk image |
| test-macos | macos-15, macos-26 (arm64), macos-15-intel (x86_64) | each disk image mounted read-only, self-tests from the image directly and the Finder way, a copy in Applications with the quarantine workaround |

Every job stops after 30 minutes (the tests after 45) and every step that runs the frozen app after 5, so an app that
waits on a window fails the run.

Release procedure:

1. Set `__version__` in `aurora/__init__.py` (the only place) and add the `## <version>` section at the top of
   `CHANGELOG.md`.
2. Commit, `git tag v<version>`, `git push && git push --tags`.
3. `.github/workflows/release.yml` checks that the tag matches the version, runs the whole of ci.yml, and creates a
   **draft** release with the four installers, `SHA256SUMS.txt` and notes taken from the CHANGELOG section.
4. Read the draft on GitHub and publish it.

## 9. Running the tests

```
python -m pytest -q                 # synthetic data, offscreen Qt, a few minutes
AURORA_REAL_PROJECT=<beamtime folder> python -m pytest tests/test_real_data.py tests/test_real_zones.py
```

The real-data tests are read-only and skip when the folder is absent; without the variable they read it from the
untracked `tests/local_paths.json` (copy `tests/local_paths.example.json`). On any beamtime they check its structure;
the checks of one beamtime's own facts (holder and scan names, counts) read them from an `aurora_expectations.json`
kept with that beamtime (template `tests/real_expectations.example.json`) and skip without it. The test session sets `QT_QPA_PLATFORM=offscreen`
and `AURORA_SETTINGS_DIR`, and logs into a temporary folder, so that no real settings or logs are touched.

## 10. Known limits

- The installers are not signed or notarised (SmartScreen and Gatekeeper ask once).
- The macOS builds are verified on GitHub's runners only (section 3).
- The hypercube reader loads one stack at a time; very large stacks need the memory of the cube plus one working copy.
- Linux is tested in CI from source but not built as an installer; the source install works there with the same
  commands.
