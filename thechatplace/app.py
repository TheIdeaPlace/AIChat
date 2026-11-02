"""Starts the window. ``--check out.json`` proves the imports without one."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import __version__, platform_paths
from .integrations.registry import default_integrations
from .service import ChatService
from .store import Store


def build_service() -> ChatService:
    return ChatService(default_integrations(), Store(platform_paths.app_data_dir()))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--check"] and len(argv) == 2:
        report = {"version": __version__, "integrations": [i.id for i in default_integrations()]}
        Path(argv[1]).write_text(json.dumps(report), encoding="utf-8")
        return 0
    import wx

    from .ui.main_window import MainWindow

    app = wx.App(False)
    window = MainWindow(build_service())
    window.Show()
    app.MainLoop()
    return 0
