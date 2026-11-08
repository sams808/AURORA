"""I10: holders continue across midnight. Holders are grouped over the chronological sequence of every day, so a stack
recorded after midnight, before any survey of its day, joins the last holder of the previous day; 'unknown' only
when no survey came before anywhere in the project."""
import numpy as np
import pytest

from aurora.core import testdata
from aurora.core.model import Project

DAY1, DAY2 = "2026-11-05", "2026-11-06"
LATE_SURVEY = "233000_holderA_survey"
AFTER_MIDNIGHT = "000500_NaF_f_k_stack"
DAY2_SURVEY = "010000_holderB_survey"
DAY2_STACK = "011502_CaF2_f_k_stack"


def midnight_project(root, day2_survey: bool = False, day1_survey: bool = True):
    """raw/DAY1/23:30 survey of N5, raw/DAY2/00:05 complete stack; optionally a DAY2 01:00 survey of N6 and a stack."""
    rng = np.random.default_rng(11)
    x, y = testdata.snake_grid()
    d1, d2 = root / "raw" / DAY1, root / "raw" / DAY2
    if day1_survey:
        testdata._write_survey(d1 / LATE_SURVEY, "holderA", "233000", rng)
    else:
        testdata._write_stack(d1 / "230000_alpha_beta_f_k_stack", "alpha_beta_f_k", testdata.ENERGIES,
                              len(testdata.ENERGIES), x, y, rng, created="Thu Nov  5 23:00:00 2026")
    testdata._write_stack(d2 / AFTER_MIDNIGHT, "NaF_f_k", testdata.ENERGIES, len(testdata.ENERGIES), x, y,
                          rng, created="Fri Nov  6 00:05:00 2026")
    if day2_survey:
        testdata._write_survey(d2 / DAY2_SURVEY, "holderB", "010000", rng)
        testdata._write_stack(d2 / DAY2_STACK, "CaF2_f_k", testdata.ENERGIES, len(testdata.ENERGIES), x, y,
                              rng, created="Fri Nov  6 01:15:02 2026")
    (root / "processed").mkdir(parents=True, exist_ok=True)
    (root / "config").mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def test_a_stack_after_midnight_joins_the_last_holder_of_the_day_before(qapp, tmp_path):
    from aurora.app.session import Session
    proj = Project.discover(midnight_project(tmp_path / "bt"))
    assert [d.date for d in proj.days] == [DAY1, DAY2]
    hs = [h for d in proj.days for h in d.holders]
    assert [h.name for h in hs] == ["N5"] and hs[0].day == DAY1
    n5 = hs[0]
    assert [s.name for s in n5.stacks] == [AFTER_MIDNIGHT] and [s.name for s in n5.scans] == [LATE_SURVEY, AFTER_MIDNIGHT]
    st = proj.find(AFTER_MIDNIGHT)
    assert st.day == DAY2 and st.holder == "N5" and st.continues == DAY1
    assert [s.name for s in proj.days[1].continued] == [AFTER_MIDNIGHT] and proj.days[1].holders == []
    assert [s.name for s in proj.scans].count(AFTER_MIDNIGHT) == 1          # listed once, under its holder
    ses = Session()
    ses.set_project(proj)
    assert list(ses.holder_keys) == ["N5"] and "unknown" not in ses.holder_keys
    sid = ses.stack_id(st)
    assert sid == f"{DAY2}_{AFTER_MIDNIGHT}" and [ses.stack_id(s) for s in ses.stacks_of("N5")] == [sid]
    assert ses.holder_of_stack(sid) == "N5" and ses.stack(sid).n_energies == len(testdata.ENERGIES)


def test_a_survey_of_the_next_day_still_starts_a_new_holder(qapp, tmp_path):
    from aurora.app.session import Session
    proj = Project.discover(midnight_project(tmp_path / "bt", day2_survey=True))
    by_day = {d.date: [h.name for h in d.holders] for d in proj.days}
    assert by_day == {DAY1: ["N5"], DAY2: ["N6"]}
    assert proj.find(AFTER_MIDNIGHT).holder == "N5" and proj.find(DAY2_STACK).holder == "N6"
    assert not proj.find(DAY2_STACK).continues
    ses = Session()
    ses.set_project(proj)
    assert list(ses.holder_keys) == ["N5", "N6"] and ses.holder_day("N6") == DAY2


def test_unknown_only_when_no_survey_came_before(qapp, tmp_path):
    proj = Project.discover(midnight_project(tmp_path / "bt", day1_survey=False))
    hs = [h for d in proj.days for h in d.holders]
    assert [h.name for h in hs] == ["unknown"]                  # nothing before the first stack of the project
    assert sorted(s.name for s in hs[0].stacks) == sorted(["230000_alpha_beta_f_k_stack", AFTER_MIDNIGHT])
    # the beamtime of the other tests starts with its survey: no 'unknown' anywhere
    fake = Project.discover(testdata.make_fake_project(tmp_path / "fake", n_holders=2))
    assert "unknown" not in [h.name for d in fake.days for h in d.holders]


def test_a_retaken_survey_replaces_the_earlier_one_on_the_same_day_only(qapp, tmp_path):
    root = midnight_project(tmp_path / "bt")
    rng = np.random.default_rng(5)
    testdata._write_survey(root / "raw" / DAY1 / "233500_holderA_survey", "holderA", "233500", rng)     # re-taken
    testdata._write_survey(root / "raw" / DAY2 / "000100_holderA_survey", "holderA", "000100", rng)     # next day
    proj = Project.discover(root)
    d1, d2 = proj.days
    assert [h.name for h in d1.holders] == ["N5"] and d1.holders[0].survey.name == "233500_holderA_survey"
    assert [s.name for s in d1.holders[0].superseded_surveys] == [LATE_SURVEY]
    # a new day's survey starts its own holder (the 00:05 stack then belongs to it, not to the one before)
    assert [h.name for h in d2.holders] == ["N5"] and [s.name for s in d2.holders[0].stacks] == [AFTER_MIDNIGHT]


def test_home_marks_the_stack_of_the_next_day(qapp, tmp_path):
    from aurora.app.home import HomePerspective
    proj = Project.discover(midnight_project(tmp_path / "bt"))
    home = HomePerspective()
    home.set_project(proj)
    day1, day2 = home.tree.topLevelItem(0), home.tree.topLevelItem(1)
    assert day1.text(0) == DAY1 and day2.text(0) == DAY2
    holder = day1.child(0)
    assert holder.text(0) == "holder N5"
    texts = {holder.child(i).text(0): " ".join(holder.child(i).text(c) for c in range(8))
             for i in range(holder.childCount())}
    assert "(next day)" in texts[AFTER_MIDNIGHT] and "(next day)" not in texts[LATE_SURVEY]
    assert "continues holder N5" in day2.text(4) and day2.childCount() == 0
    home.tree.setCurrentItem(next(holder.child(i) for i in range(holder.childCount())
                                  if holder.child(i).text(0) == AFTER_MIDNIGHT))
    assert "continues holder N5" in home.details.toPlainText()
    home.shutdown()
    home.deleteLater()
    qapp.processEvents()
