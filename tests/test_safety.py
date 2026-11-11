"""AURORA never writes into a beamline raw folder or into a protected folder (the real beamtime during the tests)."""
import numpy as np
import pytest

from aurora.core.io.cache import save_npz
from aurora.core.io.safety import ProtectedPathError, assert_writable, is_protected


def test_raw_and_env_protection(fake_project, tmp_path, monkeypatch):
    raw = fake_project / "raw"
    assert is_protected(raw) and is_protected(raw / "2025-03-14" / "x.npz")
    assert not is_protected(fake_project / ".aurora" / "session.json")
    other = tmp_path / "raw" / "notes"            # a folder named raw without day folders is ordinary
    other.mkdir(parents=True)
    assert not is_protected(other / "a.txt")
    monkeypatch.setenv("AURORA_PROTECT_DIRS", str(fake_project))
    assert is_protected(fake_project / ".aurora" / "session.json")
    with pytest.raises(ProtectedPathError):
        assert_writable(fake_project / "reports")
    with pytest.raises(PermissionError):
        save_npz(fake_project / ".aurora" / "cache" / "a.npz", x=np.zeros(3))
    assert not (fake_project / ".aurora").exists()


def test_protected_project_still_works_without_writing(fake_project, monkeypatch):
    from aurora.core import testdata
    from aurora.core.model import Project
    from aurora.core.stack import StackData
    monkeypatch.setenv("AURORA_PROTECT_DIRS", str(fake_project))
    proj = Project.discover(fake_project)
    sd = StackData.load(proj, proj.find(testdata.STACK))
    assert sd.n_pix > 0 and np.isfinite(sd.i0).any()
    assert not (fake_project / ".aurora").exists()


def test_test_session_protects_the_real_beamtime():
    import os
    from tests.conftest import REAL_PROJECT
    assert str(REAL_PROJECT) in os.environ["AURORA_PROTECT_DIRS"]
    assert is_protected(REAL_PROJECT / ".aurora" / "session.json")
