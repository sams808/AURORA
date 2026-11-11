"""Getting started (Easy mode): the steps of a first spectrum and the next one, following where the user is.

Pure functions first (no Qt): `next_step(GuideState)` returns the page, the sentence and the button of the next step,
`done_steps` the steps shown ticked, `getting_started_html` the panel Home shows before a project is open. Home's
guide strip and the top bar's Next link (aurora.app.main) read them; MainWindow.guide_state() fills the state.
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field

STEPS = (("home", "Open"), ("survey", "Survey"), ("reduce", "Reduce"), ("check", "Check"),
         ("compare", "Compare"), ("fit", "Fit"), ("report", "Report"))
PAGE_TITLES = {"home": "Home", "survey": "Survey", "reduce": "Reduce", "check": "Check", "compare": "Compare",
               "fit": "Fit", "maps": "Maps", "report": "Report"}
# one line per step: the list of steps (getting_started_html), the chips' tooltips and the rail tooltips
STEP_LINES = {
    "home": "Open the beamtime folder you received (it contains a folder named raw), or the demo.",
    "survey": "Find your samples on the map of each holder.",
    "reduce": "Outline each sample on its map: AURORA adds up the pixels inside into one spectrum. Press Add to Check.",
    "check": "Look at each spectrum, untick the ones that look wrong, save the rest.",
    "compare": "Plot saved spectra on one graph.",
    "fit": "Optional: describe one spectrum as a mix of reference spectra.",
    "report": "One PDF with every kept spectrum, one page per sample group.",
}
# the purpose of each page, in the rail tooltips ('Survey (Ctrl+2): the map of each holder: …')
RAIL_TIPS = {
    "home": "open a beamtime and see its days, holders and scans",
    "survey": "the map of each holder: where the samples and the measured areas are",
    "reduce": "outline a sample on its map and get its spectrum",
    "check": "look at each spectrum before keeping it",
    "compare": "plot saved spectra on one graph",
    "fit": "describe one spectrum as a mix of reference spectra",
    "maps": "every emission window of a stack as maps, colour composites and clusters (Advanced mode)",
    "report": "one PDF with every kept spectrum, one page per sample group",
}
# True since the zones work: a sample can be drawn on the Survey map itself (the sentence of row 6 says so).
SURVEY_DRAWS = True

READ_ONLY_SENTENCE = ("This beamtime folder is read-only: choose a working folder so that your outlines and spectra "
                      "can be kept.")
READ_ONLY_COPY = ("This beamtime folder is read-only: copy it to this computer and open the copy so that your outlines "
                  "and spectra can be kept.")
WORK_FOLDER_ACTION = "Choose a working folder…"


@dataclass(frozen=True)
class GuideState:
    has_project: bool = False
    opening: str = ""                 # name of the folder being read ('' = none)
    read_only: bool = False           # the beamtime cannot be written and no working folder is set
    n_complete_stacks: int = 0
    n_zones: int = 0                  # zones of the project with at least one pixel on some stack
    n_queued: int = 0                 # rows of Check's queue
    n_saved: int = 0                  # spectra saved by Check in this session
    n_library: int = 0                # rows in Compare's library
    fit_ran: bool = False
    report_folder: str = ""           # the last build of this session ('' = none)
    visited: frozenset = field(default_factory=frozenset)
    current: str = "home"
    can_choose_work_folder: bool = False   # the window offers 'Choose a working folder…'


@dataclass(frozen=True)
class NextStep:
    page: str                                  # the page the next action is on
    sentence: str                              # shown on Home and in the top bar
    actions: tuple = ()                        # ((button text, target), …): a page key, 'open', 'demo', 'reload',
    #                                            'folder' (the report folder), 'workfolder'
    done: frozenset = field(default_factory=frozenset)


def done_steps(s: GuideState) -> frozenset:
    """The steps shown ticked."""
    done = set()
    if s.has_project:
        done.add("home")
    if "survey" in s.visited or s.n_zones:
        done.add("survey")
    if s.n_queued or s.n_saved:
        done.add("reduce")
    if s.n_saved:
        done.add("check")
    if "compare" in s.visited and s.n_library:
        done.add("compare")
    if s.fit_ran:
        done.add("fit")
    if s.report_folder:
        done.add("report")
    return frozenset(done)


def _go(page: str) -> tuple:
    return ((f"Go to {PAGE_TITLES[page]}", page),)


def next_step(s: GuideState) -> NextStep:
    """The first row of the table that holds (MANUAL §2.6 'Getting started')."""
    done = done_steps(s)

    def step(page, sentence, actions=()) -> NextStep:
        return NextStep(page, sentence, tuple(actions), done)

    if not s.has_project and s.opening:
        return step("home", f"Reading {s.opening} … this can take a minute for a large beamtime.")
    if not s.has_project:
        return step("home", "Open the folder of your beamtime (it contains a folder named raw), or try the demo first.",
                    (("Open project…", "open"), ("Open the demo beamtime", "demo")))
    if s.read_only:
        if s.can_choose_work_folder:
            return step("home", READ_ONLY_SENTENCE, ((WORK_FOLDER_ACTION, "workfolder"),))
        return step("home", READ_ONLY_COPY)
    if s.n_complete_stacks == 0:
        return step("home", "This beamtime has no complete stack yet, and only complete stacks give spectra. Reload "
                            "(F5) when new scans arrive.", (("Reload", "reload"),))
    if s.n_zones == 0 and "survey" not in s.visited:
        return step("survey", "Next: find your samples on the map of each holder.", _go("survey"))
    if s.n_zones == 0:
        if SURVEY_DRAWS:                        # the drawing happens on the Survey: the step is there (M23, S-25)
            return step("survey", "Next: draw around your first sample (Draw, then drag) on the Survey map, or on its "
                                  "stack map in Reduce.", _go("survey"))
        return step("reduce", "Next: outline your first sample: drag over it on its map in Reduce.", _go("reduce"))
    if s.n_queued == 0 and s.n_saved == 0:
        return step("reduce", "Next: select the sample and press Add to Check.", _go("reduce"))
    if s.n_queued > s.n_saved:
        return step("check", "Next: look at each spectrum in Check, then press Save ticked → Compare.", _go("check"))
    if "compare" not in s.visited:
        return step("compare", "Next: plot your saved spectra together in Compare.", _go("compare"))
    if not s.report_folder:
        return step("report", "Next: build the report (or compare and fit more first).", _go("report"))
    return step("report", "The report is built. Outline more samples, or open the report folder.",
                (("Open folder", "folder"),))


def short_sentence(n: NextStep) -> str:
    """The sentence without its 'Next: ' (the top bar's link reads 'Next: Check – look at each spectrum …')."""
    t = n.sentence
    return t[len("Next: "):] if t.startswith("Next: ") else t


# ----------------------------------------------------------------------------- the panel before a project is open
FOLDER_PICTURE = (
    "my_beamtime/                    ← open this folder\n"
    "  raw/\n"
    "    2025-06-03/                 ← one folder per day\n"
    "      101500_plate1_survey/     ← a survey: a quick map of one holder\n"
    "      102300_sampleA_stack/     ← a stack: maps of one area at many energies\n"
    "  config/                       (optional) emission windows, detector calibration\n"
    "  samples.csv                   (optional) your sample names and groups"
)


def getting_started_html(advanced: bool, tokens: dict | None = None) -> str:
    """The right panel of Home while no project is open: Getting started in Easy mode, the list of what opens in
    Advanced mode. No lab, person or folder name of any real beamtime."""
    if tokens is None:
        from aurora.app.theme import TOKENS as tokens
    muted = tokens["muted"]
    link = tokens["link"]
    e = html.escape
    if advanced:
        return ("<h3>No project open</h3>"
                "<p>Open one of these with <b>Open project…</b>, by dropping it on the window, or from the command "
                "line (<code>aurora &lt;folder&gt;</code>):</p><ul>"
                "<li>a beamtime folder that holds <code>raw/&lt;day&gt;/&lt;scan&gt;</code> (the folder downloaded "
                "from the beamline), or its day folders without <code>raw/</code>;</li>"
                "<li>one day or scan folder of a beamtime: that day opens alone, and <b>Show every day</b> opens "
                "the others;</li>"
                "<li>a loose folder of scan folders, the layout of the earlier SGM XAS GUI;</li>"
                "<li>one scan's <code>.h5</code> file, dropped on the window or given on the command line (the "
                "folder dialog lists folders only): its scan folder's project opens.</li></ul>"
                "<p>The demo beamtime is a small made-up beamtime: every perspective has data without a real "
                "beamtime. The tutorials walk through it.</p>")
    steps = "".join(f"<li><b>{e(PAGE_TITLES[k] if k != 'home' else 'Home')}</b> – {e(_step_item(k))}</li>"
                    for k, _title in STEPS)
    a = f"style='color:{link};text-decoration:underline'"
    return ("<h3>Getting started</h3>"
            "<p>AURORA turns the maps recorded at the SGM beamline (Canadian Light Source) into one X-ray absorption "
            "spectrum per sample: you outline each sample on its map, AURORA adds up its pixels, checks the result "
            "and builds a report.</p>"
            "<h4>What a beamtime folder looks like</h4>"
            f"{folder_table(muted)}"
            "<p>A day folder or one scan folder opens too (that day alone), and a scan's <code>.h5</code> file when "
            "it is dropped on the window or given on the command line. AURORA never writes into <code>raw</code>. "
            "Your outlines and spectra are kept next to it (<code>.aurora/</code> and <code>processed/</code>).</p>"
            "<h4>The steps (the pages on the left, in order)</h4>"
            f"<ol>{steps}</ol>"
            f"<p><b>New to X-ray absorption?</b> The <a href='glossary:' {a}>glossary</a> explains every word AURORA "
            "uses (I0, TEY, edge, stack …), and Help > Quick start explains these steps for non-specialists "
            f"(<a href='aurora:help:USER_GUIDE.md#quick-start-for-non-specialists' {a}>open it</a>). The demo "
            "beamtime is a small made-up beamtime with the same layout: try every step on it first.</p>")


def folder_table(muted: str) -> str:
    """FOLDER_PICTURE as a two-column table (indented names, the notes in the muted colour): it reads in any font."""
    e = html.escape
    rows = []
    for line in FOLDER_PICTURE.splitlines():
        name = line.rstrip()
        note = ""
        for sep in ("←", "(optional)"):
            if sep in name:
                i = name.index(sep)
                name, note = name[:i].rstrip(), name[i:].lstrip("← ").strip()
                break
        indent = len(name) - len(name.lstrip())
        rows.append(f"<tr><td style='padding:1px 14px 1px {indent * 6}px'><b>{e(name.strip())}</b></td>"
                    f"<td style='color:{muted};padding:1px 0'>{e(note)}</td></tr>")
    return "<table>" + "".join(rows) + "</table>"


def _step_item(key: str) -> str:
    """The step line as the list shows it ('open the beamtime folder you received, or the demo.')."""
    if key == "home":
        return "open the beamtime folder you received, or the demo."
    line = STEP_LINES[key]
    return line[:1].lower() + line[1:] if not line.startswith("One ") else line[:1].lower() + line[1:]


def welcome_plain(advanced: bool) -> str:
    """The panel's text without markup (tests, the parity collector)."""
    import re
    return html.unescape(re.sub(r"<[^>]+>", " ", getting_started_html(advanced)))
