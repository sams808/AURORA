"""Beamline constants of the SGM ambxas endstation (bluesky acquisition, 2026).

Everything the readers and checks assume about the raw data lives here, with the date switches, so that a change at the
beamline is one edit. Values come from sgm_report.py v2.5 and the CLS SGM_Beamline_Analysis loader.
"""
import datetime as dt

DETS = ("sdd1", "sdd2", "sdd3", "sdd4")
N_DETS = len(DETS)
N_CH = 256                              # channels per SDD spectrum
BYTES_PER_PIXEL = N_CH * 4              # sdd*_0.bin: uint32 little-endian, one 256-channel spectrum per pixel

# mcc_flyer_0.csv columns (0-based): ch1 = Au-mesh I0 upstream of the KB mirrors, ch2 = photodiode behind the sample,
# ch3 = auxiliary, ch4 = sample TEY; ch5-ch8 read -10.2 V and are unused.
I0_COL, PD_COL, AUX_COL, TEY_COL = 0, 1, 2, 3
MCC_NAMES = {0: "I0 (Au mesh, mcc1)", 1: "photodiode (mcc2)", 2: "aux (mcc3)", 3: "TEY (mcc4)"}
N_MCC = 8

UNCALIBRATED_EV_PER_CH = 10.0           # nominal SDD gain before calibration

# SDD positions changed on 2026-08-12 (CLS loader, inclusive on the "before" side)
SDD_GEOMETRY_CUTOFF = dt.date(2026, 8, 12)
SDD_GEOMETRY_BEFORE = {"sdd1": "90 deg, top", "sdd2": "45 deg, front, in plane",
                       "sdd3": "90 deg, side", "sdd4": "45 deg, front, out of plane"}
SDD_GEOMETRY_AFTER = {"sdd1": "90 deg, side", "sdd2": "45 deg, front, in plane",
                      "sdd3": "45 deg, front, out of plane", "sdd4": "90 deg, top"}


def sdd_geometry(date: dt.date | None) -> dict:
    """Detector position labels for a scan date (post-cutoff labels when the date is unknown, like the CLS loader)."""
    if date is not None and date <= SDD_GEOMETRY_CUTOFF:
        return dict(SDD_GEOMETRY_BEFORE)
    return dict(SDD_GEOMETRY_AFTER)


# stack status (sgm_report v2.5)
MIN_SPAN_EV = 5.0                       # shorter stacks are tests
IN_PROGRESS_MIN = 25                    # incomplete and touched less than this many minutes ago = still running
TEST_NAME_PATTERN = r"(?i)photodiode|_diode|i0_?test|beam_?test|dark"

# quality checks (sgm_report v2.5): see docs/HANDOFF.md section 2.4
SAT_TOP_CV = 0.02                       # brightest-pixel total counts: spread over the probed energies below this ...
SAT_I0_VAR = 0.05                       # ... while I0 varied by more than this ...
SAT_RATE_RATIO = 2.0                    # ... and the rate is at least this many times the day's median -> saturated
I0_BAD_FRAC = 0.1                       # median I0 below this fraction of the day's other scans -> I0 invalid
I0_REF_WINDOW_EV = 150.0                # ... the other scans being those whose energy range overlaps within +/- this
I0_LOW_FRAC = 0.2                       # energies with I0 below this fraction of the scan median are NaN in /I0 traces
DESPIKE_K = 6.0                         # |residual to the running median| > k * 1.4826 * MAD -> spike
# coherent de-spike (aurora.core.spectra.despike_coherent): a sum or TEY outlier that is also a local maximum (a white
# line) or, for a dip, a local minimum, in at least this many of the four SDDs is a real feature, never a spike
COHERENT_MIN_SDDS = 3
EDGE_SPIKE_STEPS = 3                    # spikes within this many energy steps of the edge get a 'check the preview' note

# sanity checks of a zone x scan (aurora.core.qc.check_zone), John Bussey's `check_reduced` with his values: a region
# of fewer than 3 points, I0 max / min above 3, more than 10 % of the energies flagged bad (checked on his
# SGM_XAS_GUI.exe, 2026-11-10)
FEW_POINTS = 3                          # a region with fewer measurement points than this: 'spectra will be noisy'
I0_VARIES_FACTOR = 3.0                  # max(I0) / min(I0) over the scan above this: 'I0 varies by a factor X'
BAD_POINT_FRACTION = 0.1                # more than this fraction of the energies flagged bad: warn
N_SIGMA = 1.5                           # fixed ROI half-width in fitted peak sigmas
COUNT_RATE_PROBE_N = 7                  # energies probed for the count-rate check

BEAMLINE = "SGM (11ID-1), Canadian Light Source, ambxas endstation"
