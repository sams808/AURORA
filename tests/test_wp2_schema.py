"""I16: a versioned session schema. session.json carries {'format', 'schema', 'app'}; unknown keys at every level
are written back; an older schema is migrated with a one-time session.v<old>.bak.json; a newer one opens read-only;
the exported zones_all.json stays version 1 without the AURORA-only keys; the DEVELOPER.md example round-trips."""
import copy
import json
import re
from pathlib import Path

import pytest

pytest.importorskip("pyqtgraph")

from aurora import __version__  # noqa: E402
from aurora.core import testdata  # noqa: E402
from aurora.core.io.config import SESSION_FORMAT, SESSION_SCHEMA, ZonesFile  # noqa: E402
from aurora.core.model import Project  # noqa: E402
from wp2_helpers import draw_zone, session  # noqa: E402

DEVELOPER = Path(__file__).resolve().parents[1] / "docs" / "DEVELOPER.md"


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def _with_unknown_keys(d: dict) -> dict:
    d = copy.deepcopy(d)
    d["top_future"] = {"keep": [1, 2]}
    h = d["holders"]["holderA"]
    h["holder_future"] = "h"
    z = h["zones"][0]
    z["zone_future"] = 3.5
    st = next(iter(z["st"].values()))
    st["settings_future"] = {"x": None}
    return d


def _without_exported(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "exported"}


def test_session_json_has_the_header(qapp, fake_project):
    ses = session(fake_project)
    draw_zone(ses)
    ses.flush()
    d = json.loads(ses.session_file.read_text(encoding="utf-8"))
    assert (d["format"], d["schema"], d["app"]) == (SESSION_FORMAT, SESSION_SCHEMA, f"AURORA {__version__}")
    assert SESSION_SCHEMA == 2 and d["version"] == 1
    assert list(d)[:3] == ["format", "schema", "app"]


def test_copy_diagnostics_names_the_session_schema(qapp, fake_project):
    """Help > Report a problem > Copy diagnostics: the schema and writer of session.json (the header fields)."""
    from types import SimpleNamespace
    from aurora.app.diagnostics import diagnostics_text
    from aurora.app.session import Session
    ses = session(fake_project)
    draw_zone(ses)
    ses.flush()
    s2 = Session()
    s2.set_project(Project.discover(fake_project))
    text = diagnostics_text(SimpleNamespace(session=s2))
    assert f"session schema {SESSION_SCHEMA} (AURORA {__version__})" in text, text
    s2.read_only = True
    assert f"session schema {SESSION_SCHEMA} (AURORA {__version__}) · read-only" in diagnostics_text(SimpleNamespace(session=s2))
    s2._save_timer.stop()
    ses._save_timer.stop()


def test_unknown_keys_at_every_level_survive_load_and_save(qapp, fake_project, tmp_path):
    from aurora.app.session import Session
    ses = session(fake_project)
    draw_zone(ses)
    ses.set_ui_value("view", {"tab": 2})
    ses.flush()
    src = _with_unknown_keys(json.loads(ses.session_file.read_text(encoding="utf-8")))
    ses.session_file.write_text(json.dumps(src, indent=1, ensure_ascii=False), encoding="utf-8")
    # the file level
    zf = ZonesFile.load(ses.session_file)
    zf.save(tmp_path / "again.json")
    assert json.loads((tmp_path / "again.json").read_text(encoding="utf-8")) == src
    # the session level: open, save, compare (all but the time stamp)
    s2 = Session()
    s2.set_project(Project.discover(fake_project))
    assert s2.ui_value("view") == {"tab": 2}                     # the 'ui' block is restored
    assert s2.save()
    out = json.loads(s2.session_file.read_text(encoding="utf-8"))
    assert _without_exported(out) == _without_exported(src)
    s2._save_timer.stop()


def test_a_newer_schema_opens_read_only(qapp, fake_project):
    from aurora.app.session import NEWER_SCHEMA_TEXT, Session
    ses = session(fake_project)
    draw_zone(ses)
    ses.flush()
    d = json.loads(ses.session_file.read_text(encoding="utf-8"))
    d["schema"] = 99
    d["new_block"] = {"something": "AURORA 9 knows"}
    ses.session_file.write_text(json.dumps(d, indent=1), encoding="utf-8")
    before = ses.session_file.read_bytes()
    s2 = Session()
    states, msgs = [], []
    s2.save_state.connect(states.append)
    s2.status.connect(msgs.append)
    s2.set_project(Project.discover(fake_project))
    assert s2.read_only and states[-1] == "read-only" and NEWER_SCHEMA_TEXT in msgs
    assert NEWER_SCHEMA_TEXT == "written by a newer AURORA: edits here are not saved; export them to a file"
    assert [z.label for z in s2.holder_zones().zones] == ["zone 1"]          # shown, editable in memory
    draw_zone(s2, "in memory")
    assert s2.save() is False and states[-1] == "read-only"
    s2.flush()
    assert ses.session_file.read_bytes() == before
    assert not list(ses.session_file.parent.glob("session.corrupt-*"))      # a newer file is never 'damaged'
    assert s2.backup_now("x") is None
    s2._save_timer.stop()


def test_a_schema_1_file_is_kept_once_before_the_first_save(qapp, fake_project):
    from aurora.app.session import Session
    zf = testdata.make_fake_zones(fake_project)            # the zone tool's format: no header (schema 1)
    sf = fake_project / ".aurora" / "session.json"
    zf.save(sf)
    original = sf.read_bytes()
    assert "schema" not in json.loads(original)
    ses = Session()
    ses.set_project(Project.discover(fake_project))
    v1 = sf.with_name("session.v1.bak.json")
    assert not v1.exists() and [z.label for z in ses.holder_zones().zones] == ["1 alpha", "2 beta", "3 gamma"]
    with ses.edit("rename zone 1"):
        ses.holder_zones().zones[0].label = "renamed"
    ses.flush()
    assert v1.read_bytes() == original
    assert json.loads(sf.read_text(encoding="utf-8"))["schema"] == SESSION_SCHEMA
    stamp = v1.stat().st_mtime_ns
    with ses.edit("rename zone 1"):
        ses.holder_zones().zones[0].label = "renamed again"
    ses.flush()
    assert v1.read_bytes() == original and v1.stat().st_mtime_ns == stamp
    assert sorted(p.name for p in sf.parent.glob("session.v*.bak.json")) == ["session.v1.bak.json"]
    ses._save_timer.stop()


def test_the_export_is_the_zone_tools_version_1(qapp, fake_project, tmp_path):
    ses = session(fake_project)
    draw_zone(ses)
    ses.set_ui_value("view", 1)
    ses.zones.rules = [{"scope": "project", "patch": {"tn": "i0pre"}}]
    ses.zones.version = 7                                   # whatever the session holds, the export is version 1
    ses.flush()
    out = tmp_path / "zones_all.json"
    ses.export_zones(out)
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["version"] == 1
    assert not {"ui", "rules", "format", "schema", "app"} & set(d)
    s = json.loads(ses.session_file.read_text(encoding="utf-8"))
    assert {"ui", "rules", "format", "schema", "app"} <= set(s)
    ses._save_timer.stop()


# ----------------------------------------------------------------------------- the DEVELOPER.md example
def developer_example(tmp_path) -> str:
    """The session.json of the demo beamtime with its first two zones, as DEVELOPER.md section 4.1 shows it."""
    from aurora.app.session import Session
    from aurora.cli import cmd_demo
    root = tmp_path / "demo_beamtime"
    cmd_demo(root)
    ses = Session()
    ses.set_project(Project.discover(root))
    demo = testdata.make_fake_zones(ses.project).holders["holderA"].zones[:2]
    with ses.edit("demo zones"):
        ses.holder_zones().zones.extend(demo)
    ses.clock = lambda: __import__("datetime").datetime(2026, 11, 8, 9, 0, 0)
    ses.save()
    ses._save_timer.stop()
    d = json.loads(ses.session_file.read_text(encoding="utf-8"))
    d["app"] = "AURORA 0.4.0"
    return compact_json(d)


def compact_json(obj, indent: int = 0, width: int = 116) -> str:
    """JSON with every value that fits on one line kept on one line (valid JSON, easier to read in a manual)."""
    flat = json.dumps(obj, ensure_ascii=False)
    if not isinstance(obj, (dict, list)) or len(flat) + indent <= width or not obj:
        return flat
    pad = " " * (indent + 2)
    if isinstance(obj, dict):
        items = [f"{pad}{json.dumps(k, ensure_ascii=False)}: {compact_json(v, indent + 2, width)}" for k, v in obj.items()]
        return "{\n" + ",\n".join(items) + "\n" + " " * indent + "}"
    items = [pad + compact_json(v, indent + 2, width) for v in obj]
    return "[\n" + ",\n".join(items) + "\n" + " " * indent + "]"


def test_the_generated_developer_example_round_trips(qapp, tmp_path):
    text = developer_example(tmp_path)
    p = tmp_path / "example.json"
    p.write_text(text, encoding="utf-8")
    zf = ZonesFile.load(p)
    assert zf.to_json() == json.loads(text)
    assert zf.schema == SESSION_SCHEMA and len(zf.holders["holderA"].zones) == 2 and zf.holders["holderA"].zones[0].settings


def _developer_block() -> str | None:
    if not DEVELOPER.exists():
        return None
    md = DEVELOPER.read_text(encoding="utf-8")
    m = re.search(r"### 4\.1[^\n]*\n(.*?)\n### 4\.2", md, re.S)
    block = re.search(r"```json\n(.*?)\n```", m.group(1), re.S) if m else None
    return block.group(1) if block else None


def test_the_developer_md_example_loads_and_round_trips(tmp_path):
    block = _developer_block()
    if block is None:
        pytest.skip("docs/DEVELOPER.md section 4.1 has no JSON example")
    if '"version": "..."' in block:
        pytest.skip("DEVELOPER.md 4.1 still shows the 0.3 placeholder example; the docs pass replaces it with the "
                    "generated one (notes/WP2.md)")
    p = tmp_path / "developer_example.json"
    p.write_text(block, encoding="utf-8")
    zf = ZonesFile.load(p)
    assert zf.to_json() == json.loads(block)
    zf.save(tmp_path / "again.json")
    assert json.loads((tmp_path / "again.json").read_text(encoding="utf-8")) == json.loads(block)
