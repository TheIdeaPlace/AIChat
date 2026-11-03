"""View, Read Whole Chat as a Page (#538). These tests construct windows: run
them in the VM or CI only."""
import pytest

wx = pytest.importorskip("wx")

from test_ui_hidden import env, fake_runner, frame, select, settle  # noqa: F401,E402
from test_ui_hidden import _load_reply  # noqa: E402
from fake_env import pump  # noqa: E402


def _viewer(monkeypatch, result):
    from thechatplace.ui import main_frame
    shown = []

    class FakeViewer:
        def __init__(self, parent, title, page, start_at=""):
            shown.append((title, page, start_at))

        def ShowModal(self):
            return result

        def Destroy(self):
            pass
    monkeypatch.setattr(main_frame, "formatted_view_available", lambda: True)
    monkeypatch.setattr(main_frame, "FormattedMessageDialog", FakeViewer)
    return shown


def test_opens_on_the_selected_message_after_building_off_the_window(frame, env, monkeypatch):
    _load_reply(frame, env, "## Result")
    shown = _viewer(monkeypatch, wx.ID_CANCEL)
    visible = frame._visible_messages()
    index = len(visible) - 1
    frame.chat_list.SetSelection(index)
    frame.on_read_whole_chat()
    assert "Preparing the whole chat." in env["feedback"]
    assert shown == []  # not on the window's thread
    assert pump(lambda: shown)
    title, page, start_at = shown[0]
    assert title.startswith("Whole chat: ")
    assert start_at == f"m{index + 1}" and f'id="{start_at}"' in page
    assert "<h3>Result</h3>" in page


def test_read_as_plain_text_gives_the_whole_chat_in_a_text_box(frame, env, monkeypatch):
    from thechatplace.ui.dialogs import ID_PLAIN_TEXT
    _load_reply(frame, env, "A reply")
    _viewer(monkeypatch, ID_PLAIN_TEXT)
    plain = []
    monkeypatch.setattr(frame, "_modal", lambda dialog: plain.append(dialog))
    frame.on_read_whole_chat()
    assert pump(lambda: plain)
    assert plain[0].GetTitle().startswith("Whole chat: ")
    plain[0].Destroy()


def test_says_why_with_nothing_to_show(frame, env):
    frame._open = None
    frame.on_read_whole_chat()
    assert env["feedback"][-1] == "Load a chat first."


def test_a_second_press_while_building_says_so(frame, env, monkeypatch):
    _load_reply(frame, env, "A reply")
    shown = _viewer(monkeypatch, wx.ID_CANCEL)
    frame.on_read_whole_chat()
    frame.on_read_whole_chat()
    assert "Still preparing the whole chat." in env["feedback"]
    assert pump(lambda: shown) and len(shown) == 1


def test_moving_on_while_it_builds_doesnt_open_it(frame, env, monkeypatch):
    _load_reply(frame, env, "A reply")
    shown = _viewer(monkeypatch, wx.ID_CANCEL)
    frame.on_read_whole_chat()
    frame.reply_text.SetValue("typing something else")
    assert pump(lambda: not frame._whole_chat_building)
    assert shown == []
    assert any("you've moved on" in f for f in env["feedback"])


def test_another_chat_loading_while_it_builds_doesnt_open_it(frame, env, monkeypatch):
    _load_reply(frame, env, "A reply")
    shown = _viewer(monkeypatch, wx.ID_CANCEL)
    frame.on_read_whole_chat()
    frame._open_generation += 1
    assert pump(lambda: not frame._whole_chat_building)
    assert shown == []
