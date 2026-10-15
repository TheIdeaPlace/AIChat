"""Synthetic end-to-end history preview through the production adapter."""
import json

import pytest

from tools import read_codex
from thechatplace.providers.codex import CodexProvider


class Connection:
    def __init__(self, partial=False):
        self.methods = []
        self.partial = partial

    def request(self, method, params):
        self.methods.append(method)
        thread = {"id": "synthetic", "name": "Preview", "cwd": "/synthetic",
                  "createdAt": 1, "updatedAt": 2, "status": {"type": "idle"}}
        if method == "thread/list":
            return {"data": [] if params["archived"] else [thread], "nextCursor": None}
        thread["turns"] = [{"id": "turn", "items": [
            {"id": "user", "type": "userMessage", "content": [{"type": "text", "text": "Hello"}]},
            {"id": "answer", "type": "agentMessage", "text": "Synthetic reply"}]}]
        if method == "thread/read":
            assert params["includeTurns"] is False
            return {"thread": {k: v for k, v in thread.items() if k != "turns"}}
        if self.partial:
            return {"data": None}
        if method == "thread/turns/list":
            return {"data": [{"id": "turn", "items": [], "itemsView": "notLoaded"}]}
        assert method == "thread/items/list"
        return {"data": [{"turnId": "turn", "item": i} for i in thread["turns"][0]["items"]]}


@pytest.mark.parametrize("fmt", ["txt", "md", "html"])
def test_preview_lists_and_exports_history_with_codex_labels(fmt):
    connection = Connection()
    provider = CodexProvider(connection)
    assert json.loads(read_codex.read(provider))[0]["id"] == "synthetic"
    rendered = read_codex.read(provider, "synthetic", fmt)
    assert "Synthetic reply" in rendered and "Codex" in rendered and "Hello" in rendered
    assert set(connection.methods) == {"thread/list", "thread/read", "thread/turns/list", "thread/items/list"}


def test_preview_rejects_partial_history_and_unknown_ids():
    with pytest.raises(ValueError):
        read_codex.read(CodexProvider(Connection(True)), "synthetic")
    connection = Connection()
    with pytest.raises(ValueError):
        read_codex.read(CodexProvider(connection), "absent")
    assert "thread/read" not in connection.methods


def test_main_reports_redacted_read_and_cleanup_failures(monkeypatch, capsys):
    class BrokenProvider:
        def __init__(self, **kwargs):
            pass

        def collect(self):
            raise RuntimeError("private read payload")

        def close(self):
            raise RuntimeError("private cleanup payload")

    monkeypatch.setattr(read_codex, "CodexProvider", BrokenProvider)
    assert read_codex.main(["--codex", "synthetic-native"]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert "private" not in captured.err
    assert "cleanup" in captured.err and "Could not read" in captured.err


def test_main_strips_terminal_controls_and_closes_provider(monkeypatch, capsys):
    closed = []

    class Provider:
        def __init__(self, **kwargs):
            pass

        def close(self):
            closed.append(True)

    monkeypatch.setattr(read_codex, "CodexProvider", Provider)
    monkeypatch.setattr(read_codex, "read", lambda *args: "reply\x1b]52;payload\x07\nnext\x9bline")
    assert read_codex.main(["--codex", "synthetic-native"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "reply]52;payload\nnextline\n"
    assert not captured.err and closed == [True]
