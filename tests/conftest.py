from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

import _pytest.pathlib
import _pytest.tmpdir
import pytest

_TEST_TMP_ROOT = Path.home() / ".codex" / "memories" / "codo-pytest-temp"
_TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("TMP", str(_TEST_TMP_ROOT))
os.environ.setdefault("TEMP", str(_TEST_TMP_ROOT))
os.environ.setdefault("TMPDIR", str(_TEST_TMP_ROOT))
tempfile.tempdir = str(_TEST_TMP_ROOT)

def _stable_getbasetemp(factory: _pytest.tmpdir.TempPathFactory):
    if factory._basetemp is None:
        base = (_TEST_TMP_ROOT / "basetemp").resolve()
        if base.exists():
            shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True, exist_ok=True)
        factory._basetemp = base
        factory._trace("new basetemp", base)
    return factory._basetemp

_pytest.tmpdir.TempPathFactory.getbasetemp = _stable_getbasetemp
_pytest.tmpdir.cleanup_dead_symlinks = lambda *args, **kwargs: None
_pytest.pathlib.cleanup_dead_symlinks = lambda *args, **kwargs: None

@pytest.fixture
def tmp_path() -> Path:
    path = (_TEST_TMP_ROOT / "cases" / uuid4().hex).resolve()
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)

@pytest.fixture(autouse=True)
def _stable_home(monkeypatch: pytest.MonkeyPatch) -> Path:
    home = (_TEST_TMP_ROOT / "home").resolve()
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home
