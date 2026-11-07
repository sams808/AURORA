# AURORA

Soft X-ray XAS and XRF stack analysis for the SGM beamline (ambxas endstation) of the Canadian Light Source. Desktop application for Windows and macOS.

Status: planning (2026-11-06). See `docs/BRAINSTORM.md` for the full feature universe, architecture, cross-platform plan and roadmap, `docs/DESIGN.md` for the design language, and `branding/` for the logo.

## Lineage and credits

AURORA builds on three pieces of work:

- **SGM XAS GUI** by **John Bussey**, Washington State University (2026): the reduce / check / compare / LCF / MCR-ALS / ITFA workflow, the reduced-CSV layout with its provenance header and the settings files. AURORA keeps that workflow and stays file-compatible with it. Original program: `legacy/CLS_GUI_JohnBussey/`.
- **SGM_Beamline_Analysis** by **James J. Dynes and the SGM beamline staff**, Canadian Light Source (github.com/Beamlines-CanadianLightSource/SGM_Beamline_Analysis): raw-data parsing, SDD calibration, alignment, hypercube dashboard, PCA and clustering, PyMca export, stitching. Original toolkit: `legacy/SGM_Beamline_Analysis/`.
- **sgm_report** beamtime reporting pipeline by **Sami Soudani**, Washington State University: survey pad detection, plate layouts, fixed ROI bins, per-SDD checks, saturation detection, holder reports.

Edge and emission-line energies come from xraydb. Data acquired at the SGM beamline of the Canadian Light Source.

## Layout

```
branding/   logo (SVG, PNG 16-1024, .ico), wordmark
docs/       BRAINSTORM.md, DESIGN.md, sources/ (inventories of the parents)
legacy/     the two parent code bases, kept as received
```

Author: Sami Soudani.
