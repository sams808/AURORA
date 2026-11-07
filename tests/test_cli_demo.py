"""`aurora demo <folder>` writes a beamtime the headless commands and the Project model accept."""
from aurora.cli import main
from aurora.core.model import Project


def test_demo_command(tmp_path, capsys):
    assert main(["demo", str(tmp_path / "demo")]) == 0
    out = capsys.readouterr().out
    assert "demo beamtime written to" in out
    proj = Project.discover(tmp_path / "demo")
    assert len(proj.scans) == 4 and proj.roi_bins is not None and "F Kα" in proj.roi_bins.labels
    from aurora.core.io.config import read_sample_sheet
    rows = read_sample_sheet(tmp_path / "demo" / "demo samples.csv")
    assert [r.sample_id for r in rows] == ["1 alpha", "2 beta"]
    assert all(r.plate == proj.days[0].holders[0].name for r in rows)   # so Reduce offers the IDs for the holder
    assert main(["project", str(tmp_path / "demo")]) == 0
    assert "fixed ROI bins: F Kα, O Kα" in capsys.readouterr().out
