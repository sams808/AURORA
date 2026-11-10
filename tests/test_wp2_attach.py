"""I13: zones attach to every complete stack of their holder from the stage coordinates, loaded or not, with the full
default path (Settings, Easy rules, providers), stitched stacks included, and no raw binary read at project open."""
import inspect

import numpy as np
import pytest

pytest.importorskip("pyqtgraph")

from aurora.core import testdata  # noqa: E402
from aurora.core.model import Project  # noqa: E402
from wp2_helpers import STACK_B, draw_zone, session, sid_of, two_stack_project  # noqa: E402

SID_A, SID_B = sid_of(testdata.STACK), sid_of(STACK_B)


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


@pytest.fixture
def project2(tmp_path):
    return two_stack_project(tmp_path / "beamtime")


def test_second_stack_is_complete_and_on_the_holder(qapp, project2):
    proj = Project.discover(project2)
    names = [s.name for s in proj.days[0].holders[0].stacks]
    assert names == [testdata.STACK, STACK_B]


def test_a_zone_attaches_to_a_stack_never_loaded(qapp, project2, tmp_path):
    from aurora.core.report import ReportBuilder
    ses = session(project2)
    msgs = []
    ses.status.connect(msgs.append)
    ses.stack(SID_A)                                   # only the first stack is loaded (no Load all)
    ses.touch()
    assert ses.stack_loaded(SID_B) is None
    z = draw_zone(ses, "both")
    assert set(z.settings) == {SID_A, SID_B} and set(z.members) == {SID_A, SID_B}
    assert z.members[SID_B] == list(range(12)) and z.settings[SID_B].n == 12
    assert z.settings[SID_B].stack == STACK_B and z.settings[SID_B].line == z.settings[SID_A].line
    assert "zone 1 attached to 2 scans (1 not loaded)" in msgs
    assert ses.stack_loaded(SID_B) is None             # attaching never loads
    # the report sees both zone x scan pairs
    kw = {k: v for k, v in {"png_dpi": 60, "appendix": False, "maps": False}.items()
          if k in inspect.signature(ReportBuilder).parameters}
    res = ReportBuilder(ses.project, ses.zones, tmp_path / "report", title="I13", **kw).build()
    assert res.entries == 2
    ses._save_timer.stop()


def test_default_path_settings_easy_rules_then_providers(qapp, project2, monkeypatch):
    from aurora.app.settings import settings
    from aurora.core.flags import CHECK_SOURCE, Flag
    settings().setValue("despike_k", 4.5)
    settings().setValue("fluo_norm", "i0pre")
    ses = session(project2)
    ses.add_flag_source(lambda scan: [Flag("SDD saturated: SDD2", "sdd_saturated", CHECK_SOURCE, ("sdd2",))]
                        if scan.name == STACK_B else [])
    order = []

    def provider(scan, st):
        order.append((scan.name, st.k, st.fluo_norm))     # sees the Settings + Easy values, then overrides
        st.tey_norm = "max"
    ses.add_settings_default(provider)
    z = draw_zone(ses)
    sb = z.settings[SID_B]
    assert sb.k == 4.5 and sb.fluo_norm == "i0pre" and sb.tey_norm == "max"
    assert sb.sdd == [True, False, True, True] and z.settings[SID_A].sdd == [True, True, True, True]
    assert (STACK_B, 4.5, "i0pre") in order
    ses._save_timer.stop()


def test_stitched_stack_follows_the_same_defaults(qapp, project2):
    from aurora.app.settings import settings
    from aurora.core.stitch import stitch
    settings().setValue("despike_k", 3.5)
    ses = session(project2)
    sa, sb = ses.stack(SID_A), ses.stack(SID_B)
    st = stitch([sa, sb])
    ses._stacks[st.id] = st                              # as Reduce.add_virtual_stack registers it
    ses.touch()
    z = draw_zone(ses)
    assert st.id in z.settings
    assert z.settings[st.id].k == 3.5 and z.settings[st.id].k == z.settings[SID_A].k
    assert z.settings[st.id].tey and z.settings[st.id].despike and z.settings[st.id].i0_source == "own"
    ses._save_timer.stop()


def test_opening_reads_no_raw_binary(qapp, project2, monkeypatch):
    from aurora.app.session import Session
    from aurora.core.io import raw
    from aurora.core import stack as stack_mod
    # a session with a zone over both stacks, saved
    ses = session(project2)
    draw_zone(ses, "saved")
    ses.flush()
    calls = []
    real = raw.read_point_dir

    def counting(*a, **k):
        calls.append(a)
        return real(*a, **k)
    monkeypatch.setattr(raw, "read_point_dir", counting)
    monkeypatch.setattr(stack_mod, "read_point_dir", counting)
    ses2 = Session()
    ses2.set_project(Project.discover(project2))
    z = ses2.holder_zones().zones[0]
    assert set(z.members) == {SID_A, SID_B} and len(z.settings) == 2
    assert calls == []
    assert ses2._stacks == {}
    ses2._save_timer.stop()


def test_a_cache_with_fewer_pixels_recomputes_the_members(qapp, project2):
    from aurora.core.io.cache import save_npz
    from aurora.core.stack import StackData
    proj = Project.discover(project2)
    full = StackData.load(proj, proj.find(testdata.STACK))           # reduced from raw, cached under .aurora
    n = 8
    cdir = project2 / "processed" / testdata.DAY / testdata.STACK      # an sgm_report cache with 8 of the 12 pixels
    save_npz(cdir / "_reduced_cache.npz", signature="test", i0=full.i0[:, :n], tey=full.tey[:, :n],
             roi_pix_det=full.roi_pix_det[:, :n], roi_det=np.nansum(full.roi_pix_det[:, :n], axis=1), valid=full.valid,
             roi_labels=np.array(full.labels, dtype=object), skipped=np.array([], dtype=object),
             notes=np.array([], dtype=object))
    ses = session(project2)
    z = draw_zone(ses)
    assert z.members[SID_A] == list(range(12))                          # from the .h5 coordinates
    sd = ses.stack(SID_A)
    assert sd.n_pix == n and sd.source.startswith("sgm_report cache")
    ses.touch()
    assert z.members[SID_A] == list(range(n)) and z.settings[SID_A].n == n
    assert ses.coordinates(SID_A)[3] == n
    ses._save_timer.stop()


def test_a_zone_of_another_holder_attaches_to_its_own_stacks(qapp, tmp_path):
    root = testdata.make_fake_project(tmp_path / "two", n_holders=2)
    ses = session(root)
    ses.set_holder("holderB")
    z6 = draw_zone(ses, "on holderB")
    sid6 = sid_of(testdata.STACK2)
    assert set(z6.settings) == {sid6}
    ses.set_holder("holderA")
    z5 = draw_zone(ses, "on holderA")
    assert set(z5.settings) == {SID_A} and set(z6.settings) == {sid6}
    ses._save_timer.stop()
