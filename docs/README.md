# AURORA documentation

AURORA is version 2 of John Bussey's SGM XAS GUI, based on James J. Dynes' SGM_Beamline_Analysis notebooks. The four
user manuals ship with the application (Help menu) and live in `aurora/resources/docs/`; the developer documents are
in this folder.

| Document | For whom | Content |
|---|---|---|
| [USER_GUIDE.md](../aurora/resources/docs/USER_GUIDE.md) | everyone | the short guide: Easy and Advanced mode, one section per perspective |
| [MANUAL.md](../aurora/resources/docs/MANUAL.md) | users | the full manual: every control, every file read and written, troubleshooting |
| [TUTORIALS.md](../aurora/resources/docs/TUTORIALS.md) | new users | nine tutorials: eight on the demo beamtime (`aurora demo <folder>`), then the real-beamtime recipe |
| [INSTALL.md](../aurora/resources/docs/INSTALL.md) | users, administrators | the Windows setup and zip, the macOS disk images, the command line, storage locations, source install, building, CI, releases |
| [DEVELOPER.md](DEVELOPER.md) | developers | the two layers, modules, formats, tests, Qt rules, how to add a perspective |
| [BRAINSTORM.md](BRAINSTORM.md) | developers | feature universe, architecture, roadmap |
| [DESIGN.md](DESIGN.md) | developers | design language |
| [make_screenshots.py](make_screenshots.py) | developers | regenerates the screenshots of the manuals in `aurora/resources/docs/img/` (`python docs/make_screenshots.py`; `--scheme dark --out DIR` for a dark set) |
