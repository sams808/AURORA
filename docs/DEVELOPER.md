# AURORA developer manual

How the code is organised, the data formats, the rules that keep the Qt side stable, and how to add things.
`BRAINSTORM.md` is the feature universe and roadmap, `DESIGN.md` the look and tone, `CREDITS.md` the lineage.

## 1. Two layers

```
aurora/core/   numpy / scipy / h5py only, never imports Qt; every algorithm, reader and writer lives here
aurora/app/    PySide6: the window, the perspectives, the session; calls core, holds no science
aurora/cli.py  entry point `aurora`; the headless commands import only core
```

The rule is strict so that the report, the listing and the reductions run on a server without a display, and so that
every algorithm is testable with plain pytest. A perspective may compute nothing that core cannot compute alone.

## 2. Core modules

| Module | Contents |
|---|---|
| `constants.py` | detector names, channel count, QC thresholds (saturation CV 0.02, I0 variation 0.05, rate ratio 2.0, bad-I0 fraction 0.1, in-progress 25 min, test span 5 eV) |
| `io/h5.py` | tolerant HDF5 reader: attributes, hexapod x/y waves, the stack plan |
| `io/raw.py` | one point folder → `PointData` (mcc columns, four SDD arrays uint32 (n_pix, 256)); energy folders of a stack; stack status |
| `io/cache.py` | npz load/save with retry and fallback, `ReducedCache` (i0, tey, roi_pix_det (nE, npix, nroi, 4), valid, roi_labels), count-rate json |
| `io/config.py` | calibration, ROI bins, plate layout, sample sheet (cp1252), sgm_report summary, and the zones/session dataclasses `ScanSettings`, `Zone`, `HolderZones`, `ZonesFile` with their JSON round-trip |
| `names.py` | scan-name parsing: strip suffixes, holder from survey name, target edge (incl. `se_l3l2`), pretty edge |
| `grid.py` | scattered (x, y, value) → regular `Grid` with pitch detection |
| `model.py` | `ScanInfo`, `Holder`, `Day`, `Project.discover()`: the folder → object tree the whole app hangs on |
| `testdata.py` | `make_fake_project()`: a synthetic beamtime (survey, complete / aborted / test stacks) used by tests, `--distcheck` and `aurora demo` |
| `stack.py` | `StackData.load()`: from the sgm_report cache, else the AURORA cache, else reduced from raw; sum over a window, map values, bbox |
| `survey.py` | `SurveyData`: the survey's values per channel and its grid |
| `zones.py` | the zone tool's maths, ported function for function from the browser tool: grid pitch, membership, running median, de-spike, normalise, `zone_traces`, I0 score, good-I0 curve |
| `qc.py` | count-rate probe, saturation and high-rate detection, I0 validity, SDD consistency, spike summary, I0 candidates, `mcc_curves` (the I0 explorer) |
| `compare.py` | `Spectrum`, `TraceRequest`, CSV readers for every known layout, normalisations (larch / legacy / TEY line), processing, offsets, derivative, difference, peaks, wide export, config save/load |
| `fit.py` | LCF (SLSQP with the sum-to-one constraint, NNLS, bootstrap), PCA (SVD), MCR-ALS (NNLS alternating), ITFA (fixed rows), peak decomposition (`curve_fit`, peaks + arctan step + background) |
| `report.py` | `ReportBuilder`: matplotlib pages, group figures, appendices, CSVs with provenance headers, page PNGs, manifest |
| `hypercube.py` | `Hypercube` (memmapped `cube_sddN.npy`), window maps, ROI spectra and XANES, `pca_kmeans`, PyMca exports |
| `references.py` | reference folder listing with edge guessing and label cleaning |
| `propose.py` | zone proposals: Otsu on the survey (middle of a flat maximum), pads → zones; sgm_report summary → zones |
| `deconv.py` | Gaussian PSF, Richardson-Lucy, Wiener, `deconvolve_grid` (PSF in µm, pitch in mm) |
| `stitch.py` | `Trim`, pixel boxes, `auto_trims` (halve every pairwise overlap), kept mask, `seams`, `stitch` → `StackData` |

Everything that reads a file is tolerant: missing attributes, half-written energy folders and damaged npz files
return partial data with a reason instead of raising, and the reason is printed (the rule: print what you drop).

## 3. App modules

| Module | Contents |
|---|---|
| `theme.py` | colour tokens and the QSS |
| `icons.py` | SVG icons built on demand (no module-level cache of QIcons, see section 6) |
| `settings.py`, `settings_dialog.py` | QSettings with the `AURORA_SETTINGS_DIR` escape hatch; the defaults dictionary |
| `widgets.py` | `Card`, `Chip`, `Worker` (QThread with result / error signals) |
| `session.py` | `Session`: the project, holders, zones, loaded stacks (`StackData` by id), good-I0 choices, autosave, zones import/export, virtual (stitched) stacks |
| `mapview.py` | pyqtgraph image with mm axes, rect / brush tools, overlays, data-driven range |
| `preview.py` | the three-panel preview plot |
| `home.py`, `survey.py`, `reduce.py`, `check.py`, `library.py`, `compare.py`, `fit.py`, `maps.py`, `report.py` | the perspectives |
| `dialogs.py` | `ReferencesDialog`, `StitchDialog` |
| `about.py` | versions text and the About dialog |
| `main.py` | `MainWindow` (rail, menus, perspectives, wiring), `create_app`, `run`, `shutdown_app` |

Perspectives talk through the session's signals (`changed`, `zone_selected`, `stack_selected`, …) and a few direct
calls from `MainWindow` (open a scan in Reduce, send traces to Compare). A perspective never reaches into another
perspective's widgets.

## 4. Formats

### 4.1 Session / zones file

`<project>/.aurora/session.json` is a `ZonesFile` (aurora/core/io/config.py). The exported `zones_all.json` is the
same object written with `to_json(export=True)`: version 1, without the header (`format`, `schema`, `app`) and
without the `ui` and `rules` blocks, which is the browser zone tool's format. The example is the demo beamtime's
session with its first two zones (generated by `developer_example()` in tests/test_wp2_schema.py, which also checks
that this block loads and round-trips):

```json
{
  "format": "aurora-session",
  "schema": 2,
  "app": "AURORA 0.4.0",
  "version": 1,
  "exported": "2026-11-08T09:00:00",
  "project": "demo_beamtime",
  "built": "",
  "notes": "",
  "goodI0": {},
  "holders": {
    "N5": {
      "holder": "N5",
      "day": "2026-11-05",
      "survey": "010013_holderA_survey",
      "zones": [
        {
          "id": 1,
          "label": "1 alpha",
          "group": "Minerals",
          "comment": "",
          "rects": [[-0.625, -0.125, 4.875, 5.625]],
          "erects": [],
          "paint": [],
          "erase": [],
          "st": {
            "2026-11-05_092758_alpha_beta_f_k_stack": {
              "inc": true,
              "n": 6,
              "sdd": [true, true, true, true],
              "line": "total XRF",
              "fn": "i0jump",
              "tey": true,
              "tn": "i0jump",
              "ds": true,
              "k": 6.0,
              "i0src": "own",
              "pre": null,
              "post": null,
              "stack": "092758_alpha_beta_f_k_stack"
            }
          },
          "members": {"2026-11-05_092758_alpha_beta_f_k_stack": [0, 1, 6, 7, 8, 9]}
        },
        {
          "id": 2,
          "label": "2 beta",
          "group": "",
          "comment": "",
          "rects": [[-0.125, 0.375, 4.875, 5.625]],
          "erects": [],
          "paint": [],
          "erase": [],
          "st": {
            "2026-11-05_092758_alpha_beta_f_k_stack": {
              "inc": true,
              "n": 6,
              "sdd": [true, false, true, false],
              "line": "total XRF",
              "fn": "max",
              "tey": true,
              "tn": "i0jump",
              "ds": false,
              "k": 6.0,
              "i0src": "own",
              "pre": null,
              "post": null,
              "stack": "092758_alpha_beta_f_k_stack"
            }
          },
          "members": {"2026-11-05_092758_alpha_beta_f_k_stack": [2, 3, 4, 5, 10, 11]}
        }
      ]
    }
  }
}
```

Keys:

| Level | Keys |
|---|---|
| file | `format` ('aurora-session'), `schema` (2), `app` ('AURORA x.y.z') — session.json only; `version` (1, the zone tool's), `exported` (ISO time of the write), `project`, `built`, `notes`, `goodI0` ({stack id: bool}), `holders` ({holder key: holder}), `ui` (view memory, Session.ui_value), `rules` (batch-settings rules) |
| holder | `holder`, `day`, `survey`, `zones` |
| zone | `id`, `label`, `group`, `comment`, `rects` and `erects` ([x0, x1, y0, y1] mm), `paint` and `erase` ([cx, cy, pitch x, pitch y] mm), `polys` ([[x, y], …] mm, written when used), `st` ({stack id: settings}), `members` ({stack id: pixel indices}) |
| settings (`st`) | `inc`, `n`, `sdd` (4 bools), `line`, `fn` and `tn` (raw, i0, i0pre, i0jump, area, max), `tey`, `ds`, `k`, `i0src` (own, good), `pre`, `post` (null = automatic), `stack`; written only when set: `roi` ({sdd: [lo, hi)} channels), `sig` (pfy, ipfy), `ipm`, `dsm` (classic, coherent), `pin` |

Holder keys are the holder name, or `<name>_<day>` when a name repeats in the project; a key already used by the
session file is kept. Stack ids are `<day>_<scan folder>`. Keys a version does not know are kept at every level and
written back. A file without `schema` is schema 1 (AURORA ≤ 0.3, the zone tool): it is read as is and kept once as
`session.v1.bak.json` before the first save; a higher schema opens read-only. Coordinates are mm on the hexapod. The
browser zone tool writes the same keys, which is why the two agree pixel for pixel on the Nov 2026 beamtime (224
zone-scan pairs verified).

### 4.2 Caches

`<project>/.aurora/cache/<day>/<scan>/`: `_reduced_cache.npz` (AURORA format `aurora2|…`: numbers and text only, read
with `allow_pickle=False`, signed with the XRF windows, the energy count, the newest energy-folder time and the pixel
count; a cache that does not match is reduced again, a damaged one is renamed `.bad`), `_mcc_curves.json`,
`_emission_cache.npz`, `_xrf_windows/`, and the hypercube in `hypercube-<key>/` (uint16 or uint32 memmaps per SDD,
written in a `.building-*` folder and renamed when complete). MANUAL §12 lists every file the application writes.

### 4.3 Report CSV

Header lines start with `#`: application and version, zone (label, holder, id, group), scan (name, day, edge, line,
pixels), detectors in the sum, normalisations, windows and I0 source, de-spike and spike counts, flags, calibration,
then the column list: `energy_eV, I0_zone_sum, I0_used, TEY_raw, TEY_norm, fluo_sum_raw, fluo_sum_norm` followed by
`sddN_raw, sddN_norm` for each detector. Compare's reader recognises the header.

### 4.4 Reduced CSV layouts Compare reads

AURORA zone CSV (above); sgm_report `_xanes.csv`; John Bussey's `_full_reduced.csv` (`# sample_label:` line, columns
`energy, sdd1, sdd1_roi_lo, sdd1_roi_hi, …, I0, TEY, sdd_total, sdd_mean, bad_point`) and `_absorption.csv`; any
column file with an energy column found by name or taken as the first column.

## 5. Tests

```
python -m pytest -q
```

| File | Covers |
|---|---|
| `test_core.py` | readers, names, grid, project discovery on the synthetic beamtime |
| `test_zones.py`, `test_qc.py`, `test_compare_fit.py`, `test_hypercube.py`, `test_phase5_core.py` | the core algorithms against known answers (membership, de-spike, normalisations, LCF on a known mixture, Otsu, auto trims, deconvolution, peaks) |
| `test_report.py` | a full report build, its files and manifest |
| `test_app*.py` | the perspectives offscreen: loading, drawing zones, checks, compare and fit, maps, navigation, phase 5 (I0 explorer, references dialog, proposals, stitch dialog, deconvolution, peaks tab) |
| `test_real_data.py`, `test_real_zones.py` | the Nov 2026 beamtime, read-only, skipped when `AURORA_REAL_PROJECT` is absent; the zone membership of every manual zone-scan pair and the reproduction of the sgm_report CSVs |

Rules: a Qt test never touches the real settings (`AURORA_SETTINGS_DIR` from `conftest.py`); a real-data test never
writes into the beamtime (Session.save is monkeypatched there); and a green suite is not verification, the real-data
comparison or a physical invariant is.

## 6. Qt rules learned the hard way

- **PySide6 ≥ 6.10.** 6.9 paints no pyqtgraph items. The dependency pin enforces it.
- **Never cache Qt objects at module level** (`lru_cache` of a QIcon, a QPixmap in a global). PySide6 6.12 segfaults
  at interpreter exit when such objects outlive the QApplication. Tests pass, the process exits 139. Build on demand.
- **Deterministic teardown.** `shutdown_app()` closes widgets, waits for workers, runs gc and deletes the
  application; `run()`, `--distcheck` and the test session fixture all use it. Check `${PIPESTATUS[0]}` in shell
  pipelines, not the exit code of `tail`.
- **Workers.** `Worker` is a QThread; signals connected to plain functions run on the worker thread. Build dialogs
  and touch widgets only in slots of QObjects living on the GUI thread, and `wait()` the worker before dropping it.
- **pyqtgraph auto-range before the view has geometry is garbage.** Every plot sets its range from the data.
  `enableAutoSIPrefix(False)` on axes with mm or eV.
- **Categorical colour maps** for cluster maps, continuous ones for everything else; the colour map name comes from
  Settings.

## 7. Adding things

### A perspective

1. Create `aurora/app/<name>.py` with a `QWidget` subclass that takes the `Session` and builds itself from `Card`s.
2. Register it in `MainWindow.__init__` (`self.pages[...]`, the rail button with an icon from `icons.py`).
3. Subscribe to the session signals you need; compute with core functions only.
4. Add `tests/test_app_<name>.py` that builds the window offscreen on `make_fake_project` and exercises every button.
5. Document it in `MANUAL.md` and add a tutorial step.

### A reader for another CSV layout

Add a detector function in `core/compare.py` (`load_spectra` tries the layouts in order), a sample file in the test,
and a line in section 4.4 above.

### A QC rule

Thresholds go in `constants.py` with their unit in the name; the rule in `qc.py` returns a verdict and a reason in
words; `check.py` only displays. Add the case to `test_qc.py` with a synthetic stack that triggers it.

### A screenshot set for the manuals

```
python docs/make_screenshots.py
```

writes `aurora/resources/docs/img/*.png` (the screenshots of the bundled manuals) from the demo beamtime in Advanced
mode (temporary folder, synthetic spectra, nothing else touched); `--scheme dark --out DIR` makes a dark set elsewhere.
Run it after a visual change and commit the images with the code.

## 8. Conventions

- Author "Sami Soudani". Code, comments and commits in English. Neutral voice in the public docs.
- `pathlib` everywhere, raw data read-only, units in names (`fwhm_um`, `pitch_mm`, `pre_ev`), no invented values,
  print what you drop, say "not verified" when it is not.
- The public repository excludes `legacy/CLS_GUI_JohnBussey/`, `legacy/SGM_Beamline_Analysis/`, `docs/HANDOFF.md`
  and `docs/sources/` (collaborator names, endpoint ids).
- Versions: `__version__` in `aurora/__init__.py` is the only source (`pyproject.toml` reads it); a `## <version>`
  section at the top of `CHANGELOG.md` per release; tag `v<version>` (INSTALL §8).
