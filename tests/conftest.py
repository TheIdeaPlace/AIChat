"""Window tests run only when asked: on Kelly's PC, a test window can steal focus
and be read aloud by his screen reader while he's working. CI and the test VM
set TCP_GUI_TESTS=1."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))


def pytest_configure(config):
    config.addinivalue_line("markers", "gui: builds wx windows; needs TCP_GUI_TESTS=1")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("TCP_GUI_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="builds windows; set TCP_GUI_TESTS=1 (CI and the test VM do)")
    for item in items:
        if "gui" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _experiment_data(tmp_path, monkeypatch):
    """No test touches a real data folder."""
    monkeypatch.setenv("TCP_EXPERIMENT_DATA", str(tmp_path / "app-data"))
