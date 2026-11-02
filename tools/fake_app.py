"""The real window against fake AIs, for walking it by keyboard in the test VM.
No account is touched, and it keeps its data in a folder of its own.

python tools/fake_app.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import wx  # noqa: E402

from harness import FakeModelServer, acp, claude, codex, fake_definition  # noqa: E402
from thechatplace.contracts import ChatInfo, ChatRef  # noqa: E402
from thechatplace.integrations.openai_chat import OpenAIChatIntegration  # noqa: E402
from thechatplace.service import ChatService  # noqa: E402
from thechatplace.store import Store  # noqa: E402
from thechatplace.ui.main_window import MainWindow  # noqa: E402


def main() -> None:
    data = Path(tempfile.mkdtemp(prefix="tcp-fake-app-"))
    work = data / "work"
    work.mkdir()
    server = FakeModelServer()
    integrations = [
        acp("ask", definition=fake_definition(id="agentask", name="Asking Agent")),
        acp("hang", definition=fake_definition(id="agenthang", name="Slow Agent")),
        acp("ask", definition=fake_definition(id="agentcaution", name="Unchecked Agent", verified_versions=())),
        claude("question", data),
        codex("answer", data),
        OpenAIChatIntegration(id="fakelocal", name="Plain Model", base_url=server.url),
    ]
    store = Store(data / "store")
    for integration in integrations:
        store.set_enabled(integration.id, integration.id != "agentcaution")
    service = ChatService(integrations, store)

    # A row from somewhere else, which can only be read.
    foreign = ChatInfo(ChatRef("agentask", "elsewhere"), "Started elsewhere", str(work), "Asking Agent", "Terminal",
                       read_only_note="Reply to this chat where it started; The Chat Place only reads it.")
    integrations[0].list_foreign = lambda: [foreign]  # type: ignore[method-assign]

    app = wx.App(False)
    window = MainWindow(service)
    for integration, title in ((integrations[0], "Asks permission"), (integrations[1], "Runs until stopped"),
                               (integrations[3], "Asks a question"), (integrations[5], "Plain model chat")):
        options = {"model": "m1"} if integration is integrations[5] else {}
        service.start_chat(integration.id, str(work), title, options)
    window.refresh_chats()
    window.Show()
    app.MainLoop()
    server.close()


if __name__ == "__main__":
    main()
