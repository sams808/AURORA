# AURORA glossary

The words AURORA shows, in plain language. Each entry says what the word means, why it matters and where it appears in
AURORA. The tooltip of a control that uses one of these words ends with "What is …?", and the underlined words of the
captions open this page at the word.

Contents: [Beamtime](#beamtime) · [SGM beamline](#sgm-beamline) · [Scan](#scan) · [Holder (plate)](#holder-plate) ·
[Survey](#survey) · [Stack](#stack) · [Measured area (footprint)](#measured-area-footprint) · [Sample outline
(zone)](#sample-outline-zone) · [Pixel (point)](#pixel-point) · [Status: complete, in progress, aborted,
test](#status-complete-in-progress-aborted-test) · [Sample sheet](#sample-sheet) · [Group](#group) · [Reduced
spectrum](#reduced-spectrum) · [X-ray absorption spectrum (XAS)](#x-ray-absorption-spectrum-xas) · [Absorption
edge](#absorption-edge) · [Edge names (K, L3, N4,5)](#edge-names-k-l3-n45) · [E0 (edge position)](#e0-edge-position) ·
[Edge step (edge jump)](#edge-step-edge-jump) · [Normalisation](#normalisation) · [Pre-edge and post-edge
ranges](#pre-edge-and-post-edge-ranges) · [I0 (beam intensity)](#i0-beam-intensity) · [Good-I0 mean](#good-i0-mean) ·
[TEY (total electron yield)](#tey-total-electron-yield) · [Fluorescence (PFY)](#fluorescence-pfy) · [Inverse yield
(IPFY)](#inverse-yield-ipfy) · [Self-absorption](#self-absorption) · [SDD1–SDD4 (the four
detectors)](#sdd1sdd4-the-four-detectors) · [Saturated detector](#saturated-detector) · [Count rate
(kcps)](#count-rate-kcps) · [Emission spectrum (XRF)](#emission-spectrum-xrf) · [Emission line (Kα,
Lα)](#emission-line-kα-lα) · [Emission window (XRF window)](#emission-window-xrf-window) · [Fixed
window](#fixed-window) · [Channel](#channel) · [Detector energy scale
(calibration)](#detector-energy-scale-calibration) · [De-spike (spike)](#de-spike-spike) · [Bad
energies](#bad-energies) · [Looks OK](#looks-ok) · [✓ ok, ! check, ✗ bad](#-ok--check--bad) · [The Check
views](#the-check-views) · [Signal (fluorescence, electron yield)](#signal-fluorescence-electron-yield) · [Shift
(eV)](#shift-ev) · [Reference spectrum](#reference-spectrum) · [LCF (linear combination
fit)](#lcf-linear-combination-fit) · [Target and references (components)](#target-and-references-components) ·
[R-factor](#r-factor) · [Reduced chi-square (χ²ν)](#reduced-chi-square-χ²ν) · [Bootstrap (±)](#bootstrap-) · [PCA,
MCR-ALS, ITFA](#pca-mcr-als-itfa) · [Report](#report) · [Appendix A, B and C](#appendix-a-b-and-c) · [Session
(autosave)](#session-autosave) · [Easy and Advanced mode](#easy-and-advanced-mode) · [Working folder](#working-folder)

## The data and where it sits

### Beamtime
The measurements of one visit to the beamline, kept in one folder that contains a folder named `raw`.

*Why it matters:* It is what you open in AURORA; your outlines, spectra and reports are kept with it.
*In AURORA:* Home, Open project…
*Also called:* project, beamtime folder.

### SGM beamline
The soft X-ray beamline of the Canadian Light Source whose files AURORA reads: it maps samples while it sweeps the
X-ray energy.

*Why it matters:* AURORA reads this beamline's folders and file formats only.
*In AURORA:* Home.
*Also called:* SGM.

### Scan
One measurement, stored as one folder in `raw/<day>/`: a survey or a stack.

*Why it matters:* Each scan has a status; only complete stacks give spectra.
*In AURORA:* the Home tree; Reduce step 1.
*Also called:* measurement.

### Holder (plate)
The plate that carries several samples into the beamline at once. AURORA names each holder after its survey.

*Why it matters:* Outlines, sample-sheet names and the report maps belong to a holder.
*In AURORA:* the holder ▾ in the top bar.
*Also called:* plate.

### Survey
A quick map of a whole holder at one X-ray energy, used to find where the samples are.

*Why it matters:* Each holder starts with a survey; the Survey page shows it with the measured areas on top.
*In AURORA:* Survey.
*Also called:* survey map, plate map.

### Stack
A set of maps of one area recorded at many X-ray energies across an edge: every pixel of a stack holds a spectrum.

*Why it matters:* Spectra come from stacks only.
*In AURORA:* Reduce step 1; the rectangles of the Survey map.
*Also called:* XANES stack.

### Measured area (footprint)
The rectangle of the holder that a stack measured, drawn on the survey map.

*Why it matters:* Outline a sample inside a measured area, or it gets no spectrum from that stack.
*In AURORA:* the Survey map.
*Also called:* footprint.

### Sample outline (zone)
The outline of one sample drawn on a map. AURORA adds up the pixels inside it into one spectrum. Easy mode calls it a
sample; Advanced mode and the files call it a zone.

*Why it matters:* One outline gives one spectrum for every stack it covers.
*In AURORA:* Reduce step 3; Survey.
*Also called:* zone, outline, region, sample.

### Pixel (point)
One measured spot of a map. The pitch is the distance between two neighbouring pixels, in mm.

*Why it matters:* More pixels inside an outline give a less noisy spectrum.
*In AURORA:* Reduce, the caption under the map; Check, column Pixels.
*Also called:* point, px, pitch.

### Status: complete, in progress, aborted, test
Complete: every planned energy was measured. In progress: files are still arriving. Aborted: the scan stopped early.
Test: a beamline check, not a sample.

*Why it matters:* Only complete stacks are offered in Reduce; the others are drawn on the Survey map and marked not
used.
*In AURORA:* Home, column Status; the Survey legend.
*Also called:* complete, aborted, in progress, test, dropped, not used.

### Sample sheet
A table of the samples (name, holder, group, composition), kept as a CSV file in the beamtime folder. It is optional.

*Why it matters:* Its names are offered when you name an outline, and its groups organise the report.
*In AURORA:* Survey, the sample sheet card; Reduce, the sample name box.
*Also called:* sheet.

### Group
The category a sample belongs to in the sample sheet, for example one series of glasses.

*Why it matters:* The report has one page per group.
*In AURORA:* Report, Check before building.
*Also called:* sample group.

### Reduced spectrum
The spectrum of one outlined sample on one stack, saved by Check as CSV files in `processed/reduced/<sample>/`.

*Why it matters:* Compare, Fit and other programs read these files.
*In AURORA:* Check, Save ticked → Compare.
*Also called:* saved spectrum.

## The measurement

### X-ray absorption spectrum (XAS)
How strongly a sample absorbs X-rays, energy by energy, across an absorption edge of one element. The shape near the
edge depends on the chemical state and the neighbours of that element.

*Why it matters:* This is what AURORA makes for each sample.
*In AURORA:* Reduce, Preview; Check; Compare.
*Also called:* XAS, XANES, NEXAFS, spectrum.

### Absorption edge
The energy at which an element starts to absorb X-rays strongly, because the X-rays can now remove one of its inner
electrons. Each element has its own edges: the fluorine K-edge is near 697 eV.

*Why it matters:* A stack must cover the edge, with energies well before and well after it, to give a usable spectrum.
*In AURORA:* Home, column Edge; Compare, the dashed line.
*Also called:* edge, edge energy.

### Edge names (K, L3, N4,5)
The letter names the inner electron shell the X-ray empties (K is the innermost, then L, M, N); the number names the
sub-shell. 'F K' is the fluorine K-edge, 'U N4,5' the uranium N4 and N5 edges.

*Why it matters:* It tells which element and which edge a scan measured.
*In AURORA:* Home, column Edge; Reduce step 1.
*Also called:* K-edge, L-edge, K, L3, N4,5.

### E0 (edge position)
The energy of the edge as AURORA measures it: by default, where the spectrum rises fastest.

*Why it matters:* A shift of E0 between samples often follows a change of oxidation state or bonding.
*In AURORA:* Compare, How this spectrum was scaled.
*Also called:* edge position.

### Edge step (edge jump)
The height of the rise at the edge. AURORA scales spectra so that it equals 1.

*Why it matters:* Samples with different amounts of the element become comparable.
*In AURORA:* Reduce step 4; Compare.
*Also called:* edge jump, step.

### Normalisation
Scaling a spectrum so that the part before the edge is 0 and the step at the edge is 1. It needs measured energies
well before and well after the edge.

*Why it matters:* Without it, spectra of different samples cannot be compared by their shape.
*In AURORA:* Reduce step 4; Compare.
*Also called:* normalization, normalised, scaled.

### Pre-edge and post-edge ranges
The energy ranges before and after the edge used for the normalisation. Easy mode uses 20 to 5 eV below E0 and 15 to
35 eV above it.

*Why it matters:* A spectrum that does not reach into both ranges cannot be scaled the standard way.
*In AURORA:* Compare (the range columns are Advanced).
*Also called:* pre-edge window, post-edge window, pre-edge, post-edge, pre, post.

### I0 (beam intensity)
The intensity of the X-ray beam arriving at the sample, measured as the current of a thin gold mesh in the beam.

*Why it matters:* Spectra are divided by I0, so that a change of the beam is not mistaken for absorption.
*In AURORA:* Reduce step 4; Check, the views.
*Also called:* beam intensity, mesh current, Au mesh, mcc ch1.

### Good-I0 mean
The average beam-intensity curve of the scans of a holder marked good, used for a scan whose own I0 reading cannot be
trusted.

*Why it matters:* It keeps a scan with a broken beam reading usable.
*In AURORA:* Check (Advanced), I0 of the holder's scans (per-pixel mean).
*Also called:* good I0, good-I0.

### TEY (total electron yield)
The current of electrons that leave the sample when it absorbs. It follows the absorption of the top few nanometres.

*Why it matters:* A surface spectrum recorded at the same time as the fluorescence; detector saturation does not
affect it.
*In AURORA:* Reduce, Preview; Check; Compare, column Signal.
*Also called:* electron yield, mcc ch4.
*In the report:* TEY (total electron yield): the current of electrons leaving the sample surface, scaled the same way.

### Fluorescence (PFY)
The X-rays the sample emits after it absorbs, counted by the detectors inside the window of one element's emission
line (partial fluorescence yield). It comes from deeper in the sample than the electron yield.

*Why it matters:* The usual spectrum of a sample; on concentrated samples its peaks can be flattened
(self-absorption).
*In AURORA:* Reduce steps 2 and 4; Compare, column Signal.
*Also called:* PFY, partial fluorescence yield, fluo.
*In the report:* Fluorescence (PFY): the X-rays of the emission line named in each panel, counted inside its window,
added over the outlined pixels and the detectors in use, divided by the beam intensity (I0) and scaled so that the
step at the edge is 1 (the caption says when another scaling was used).

### Inverse yield (IPFY)
The beam intensity divided by the counts of another element's emission line (for example oxygen counts on a fluorine
edge). Those counts drop where the edge element absorbs, so the ratio rises at the edge.

*Why it matters:* It avoids the flattening of fluorescence on concentrated samples; its raw detector curves dip at the
edge.
*In AURORA:* Reduce step 4; Check, the messages.
*Also called:* IPFY, inverse partial fluorescence yield.
*In the report:* Inverse yield (IPFY): the beam intensity divided by the counts of another element's line; it rises at
the edge too.

### Self-absorption
In a concentrated sample, the emitted X-rays are partly absorbed on their way out, which flattens the peaks of a
fluorescence spectrum.

*Why it matters:* When peaks look damped, compare the fluorescence with the electron yield or the inverse yield of the
same sample.
*In AURORA:* Compare.
*Also called:* saturation effect.

## Detectors and the emission window

### SDD1–SDD4 (the four detectors)
The four fluorescence detectors (silicon drift detectors) around the sample. Each records an emission spectrum at
every pixel and energy; AURORA adds the four.

*Why it matters:* Their curves should have the same shape; their heights may differ.
*In AURORA:* Reduce step 2 and Preview; Check, the views.
*Also called:* SDD, SDD1, SDD2, SDD3, SDD4, detector, detector 1, silicon drift detector.

### Saturated detector
A detector that receives more X-rays than it can count: its curve flattens and stops following the absorption.

*Why it matters:* AURORA leaves a saturated detector out of the sum; the electron yield (TEY) is not affected.
*In AURORA:* Home, column Flags; Reduce step 4; Check.
*Also called:* saturated, overloaded, saturation.

### Count rate (kcps)
The number of X-rays a detector counts per second, in thousands (kcps).

*Why it matters:* A very high rate means a detector may miss counts (dead time) or saturate.
*In AURORA:* Check (Advanced), Scans of holder …
*Also called:* kcps, high rate, dead time, pile-up.

### Emission spectrum (XRF)
The energy spectrum of the X-rays a detector receives: one peak for each element's emission line.

*Why it matters:* It shows which window holds the signal of your element.
*In AURORA:* Reduce step 2.
*Also called:* XRF, X-ray fluorescence, XRF spectrum.

### Emission line (Kα, Lα)
The X-rays an element emits when an electron falls back into an emptied inner shell. F Kα (about 677 eV) is the main
fluorine line; O Kα (about 525 eV) is the oxygen line.

*Why it matters:* The window is set around the line of the element you study.
*In AURORA:* Reduce step 2, the presets.
*Also called:* Kα, Ka, Lα, line.

### Emission window (XRF window)
The band of the emission spectrum counted as the signal, set around one element's emission line.

*Why it matters:* Only the counts inside the window become the spectrum.
*In AURORA:* Reduce step 2, Which X-rays to count.
*Also called:* XRF window, ROI, window, band.

### Fixed window
A window stored with the beamtime (`config/roi_bins.json`), the same for every scan. Without that file, AURORA counts
all X-rays, or a window set in Reduce step 2.

*Why it matters:* It keeps every sample of a beamtime on the same window.
*In AURORA:* Reduce step 2, the presets.
*Also called:* fixed bins, ROI bins, roi_bins.

### Channel
One step of a detector's energy scale: about 10 eV wide unless the detectors are calibrated.

*Why it matters:* Windows are set in channels; AURORA shows the matching energies in eV.
*In AURORA:* Reduce step 2, from / to.
*Also called:* ch, bin.

### Detector energy scale (calibration)
The conversion from detector channel to X-ray energy, read from `config/sdd_calibration.json` when the beamtime has
that file. Without it AURORA uses 10 eV per channel.

*Why it matters:* The fixed windows do not need it; a window placed by its energy does.
*In AURORA:* Home, the project summary.
*Also called:* calibration, SDD calibration, gain.

## Processing and checking

### De-spike (spike)
A spike is one point far off the curve, for example from a stray electron. De-spiking replaces it by the value of its
neighbours.

*Why it matters:* It removes single-point jumps that are not part of the spectrum.
*In AURORA:* Reduce step 4; Check, the views.
*Also called:* spike, spikes, despike, MAD, threshold k.
*In the report:* Spikes (single points far off the curve) were replaced by the value of their neighbours; the counts
are in Appendix C.

### Bad energies
Energies that were repaired or left out: a spike, a beam intensity below 20 % of its median, or a missing map.

*Why it matters:* Many bad energies mean a noisy or incomplete measurement.
*In AURORA:* Check, column Bad energies.
*Also called:* bad points, bad energy.

### Looks OK
Your verdict on a spectrum in Check. AURORA ticks the spectra its checks found no problem with; untick any that look
wrong to you.

*Why it matters:* Only ticked spectra are saved and go into the report.
*In AURORA:* Check, column Looks OK.

### ✓ ok, ! check, ✗ bad
The result of AURORA's checks on a spectrum: no problem, worth a look, or not usable. Hover the chip to read why.

*Why it matters:* A ✗ bad spectrum is not saved.
*In AURORA:* Check, column Messages.
*Also called:* status chips.

### The Check views
Five ways to look at one spectrum: the four detectors, their sum with the bad energies marked, the beam intensity and
the electron yield, the spectra divided by the beam, and the same scaled 0 to 1.

*Why it matters:* Each view shows a different kind of problem: a stray detector, a spike, a beam drop, a missing edge.
*In AURORA:* Check, view.
*Also called:* view.

## Comparing and fitting

### Signal (fluorescence, electron yield)
Which recorded signal a row of Compare plots: fluorescence (the usual choice), electron yield (TEY), inverse yield
(IPFY) or one detector.

*Why it matters:* Compare like with like: fluorescence with fluorescence.
*In AURORA:* Compare, column Signal.
*Also called:* sdd-scaled, sdd_total.
*In the files:* In the files and in Advanced mode, fluorescence rows are named sdd-scaled (the four detectors each
scaled 0 to 1, summed, divided by I0) or sdd_total (their plain sum divided by I0).

### Shift (eV)
Moves a curve along the energy axis by a fixed amount, to correct an energy offset, for example between two beamtimes.

*Why it matters:* Leave it at 0 unless the offset is known.
*In AURORA:* Compare, column Shift eV.
*Also called:* energy shift.

### Reference spectrum
The spectrum of a known compound, measured or taken from a library.

*Why it matters:* It is what a sample is compared with, and what LCF combines.
*In AURORA:* Compare (Advanced: References…); Fit.
*Also called:* standard, reference compound, reference.

### LCF (linear combination fit)
Describes one spectrum (the target) as a weighted sum of reference spectra.

*Why it matters:* Each coefficient is the share of the element's signal that looks like that reference, roughly the
share of its atoms in that kind of surroundings; it is not a weight or mole fraction of the compound.
*In AURORA:* Fit, tab LCF.
*Also called:* linear combination, mix, linear combination fit.

### Target and references (components)
The target is the spectrum to describe; the references (components) are the spectra it may be made of.

*Why it matters:* Pick references that could really be in the sample: a fit can only combine what it is given.
*In AURORA:* Fit, target and references (tick).
*Also called:* target, component, components.

### R-factor
How far the fit is from the data: the sum of the squared differences divided by the sum of the squared data. Smaller
is closer.

*Why it matters:* Compare the R-factors of different fits of the same spectrum rather than reading one value alone.
*In AURORA:* Fit, under the coefficients.

### Reduced chi-square (χ²ν)
The squared differences between fit and data, divided by the noise and by the number of points minus the free
parameters. Near 1, the fit is as good as the noise allows.

*Why it matters:* It ranks fits and scales the uncertainties.
*In AURORA:* Fit (Advanced detail).
*Also called:* χ², χ²ν, chi-square.

### Bootstrap (±)
The ± of each coefficient: the spread of the coefficients over many refits (200 in Easy mode) on resampled residuals.

*Why it matters:* It tells how firmly the data fix each share.
*In AURORA:* Fit, column ±.
*Also called:* uncertainty, ±.

### PCA, MCR-ALS, ITFA
Methods for a set of spectra: PCA counts how many distinct shapes it contains; MCR-ALS finds those shapes and their
amounts in each spectrum; ITFA refines them with some amounts known.

*Why it matters:* They need no reference spectra.
*In AURORA:* Fit, the Advanced tabs.
*Also called:* PCA, MCR, MCR-ALS, ITFA.

## The report and the application

### Report
A PDF with one page per sample group of the spectra ticked Looks OK, plus appendices, figures and CSV files, written
to a new dated folder at each build.

*Why it matters:* It is the record of the beamtime to share; earlier builds are never overwritten.
*In AURORA:* Report, Build the report.
*Also called:* PDF.
*In the report:* Traces are offset. Colour never carries the meaning alone: the line style and the label at the right
end identify each trace.

### Appendix A, B and C
A: each detector separately, per sample and scan. B: the holder maps with the outlines. C: the quality notes, the
check before building and what was left out.

*Why it matters:* They show how each spectrum was obtained.
*In AURORA:* Report, Options.
*Also called:* Appendix A, Appendix B, Appendix C.

### Session (autosave)
AURORA's record of your outlines and settings, saved in the beamtime folder (`.aurora/session.json`), or in your
working folder, within a second of every change.

*Why it matters:* Nothing needs saving by hand; Ctrl+Z undoes the last change.
*In AURORA:* the save indicator in the top bar.
*Also called:* autosave, session.json.

### Easy and Advanced mode
Easy mode shows the main steps with standard settings and states the values it uses. Advanced mode shows every option.
Switching never changes a value.

*Why it matters:* Start in Easy mode; switch when you need another setting.
*In AURORA:* the mode button; View > Advanced mode (Ctrl+Shift+A).
*Also called:* Easy mode, Advanced mode.

### Working folder
A folder where AURORA keeps your outlines, spectra and reports when the beamtime folder itself cannot be written (a
locked drive, a read-only share).

*Why it matters:* Your work is kept even when the data are read-only.
*In AURORA:* the read-only banner, Choose a working folder…
