import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolated_leak_store(tmp_path_factory, monkeypatch):
    """Point the default leak store at a temp file so tests never write
    runtime history into the repo's data/ directory."""
    tmp = tmp_path_factory.mktemp("leak-store")
    monkeypatch.setenv("SCADA_LEAK_STORE", str(tmp / "leaks.json"))
    yield
