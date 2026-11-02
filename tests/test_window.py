"""The window, hidden, against fake AIs. Builds windows, so only with TCP_GUI_TESTS=1."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from harness import acp, fake_definition
from thechatplace.contracts import (AskPermission, Capability, ChatInfo, ChatRef, Decision, Message, OptionGroup, OptionValue,
                                    PermissionRequest, QuestionItem, QuestionRequest, TurnEnded, TurnStatus)
from thechatplace.service import ChatService
from thechatplace.store import Store

pytestmark = pytest.mark.gui
wx = pytest.importorskip("wx")

from thechatplace.ui import dialogs  # noqa: E402
from thechatplace.ui.main_window import MainWindow  # noqa: E402


class Recorder:
    def __init__(self):
        self.spoken = []

    def say(self, text, interrupt=False):
        self.spoken.append(text)


@pytest.fixture(scope="module")
def app():
    application = wx.App(False)
    yield application


def pump(until, timeout=15.0):
    deadline = time.monotonic() + timeout
    app = wx.GetApp()
    while time.monotonic() < deadline:
        app.Yield(True) if hasattr(app, "Yield") else wx.Yield()
        wx.YieldIfNeeded()
        if until():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def window(app, tmp_path):
    windows = []

    def make(*integrations):
        store = Store(tmp_path / "store")
        for integration in integrations:
            store.set_enabled(integration.id, True)
        recorder = Recorder()
        frame = MainWindow(ChatService(list(integrations), store), speaker=recorder, auto_refresh=False)
        frame.recorder = recorder
        windows.append(frame)
        return frame

    yield make
    for frame in windows:
        frame.Close(True)
        frame.Destroy()
    wx.Yield()


def _started(frame, tmp_path, integration_id="fakeagent"):
    info = frame.service.start_chat(integration_id, str(tmp_path), "Chat one", {})
    frame._add_chat(info)
    frame.open_chat(info)
    assert pump(lambda: frame.service.state(info.ref).loaded)
    return info


def test_a_chat_runs_end_to_end_and_is_announced(window, tmp_path):
    frame = window(acp("answer"))
    info = _started(frame, tmp_path)
    assert frame.reply.IsShown() and not frame.note.IsShown()
    frame.reply.SetValue("Hello")
    frame.send()
    assert frame.reply.GetValue() == ""
    assert pump(lambda: not frame.service.state(info.ref).busy and frame.message_list.GetCount() >= 2)
    assert pump(lambda: any("Fake Agent:" in s and "Hello" in s for s in frame.recorder.spoken))
    assert frame.message_list.GetString(0) == "You: Hello"


def test_a_read_only_chat_has_a_note_and_no_reply_box(window):
    frame = window(acp("answer"))
    info = ChatInfo(ChatRef("fakeagent", "elsewhere"), "Elsewhere", "", "Fake Agent", "Terminal",
                    read_only_note="Reply where it started.")
    frame.show_reply_for(info)
    assert not frame.reply.IsShown() and frame.note.IsShown()
    assert frame.note.GetValue() == "Reply where it started."
    assert not frame.send_button.IsEnabled()
    assert frame._parts()[2] is frame.note


def test_a_refused_message_stays_in_the_box_and_says_why(window, tmp_path):
    frame = window(acp("answer"))
    _started(frame, tmp_path)
    frame.reply.SetValue("/allow-all on")
    frame.send()
    assert frame.reply.GetValue() == "/allow-all on"
    assert any("slash" in s for s in frame.recorder.spoken)


def test_stop_brings_waiting_messages_back_to_the_reply_box(window, tmp_path):
    frame = window(acp("hang"))
    info = _started(frame, tmp_path)
    frame.reply.SetValue("First")
    frame.send()
    frame.reply.SetValue("Second")
    frame.send()
    assert any("when this turn ends" in s for s in frame.recorder.spoken)
    frame.stop_turn()
    assert frame.reply.GetValue() == "Second"
    assert pump(lambda: not frame.service.state(info.ref).busy)


def test_options_follow_the_description_for_any_integration(window, tmp_path):
    frame = window(acp("answer"))
    info = _started(frame, tmp_path)
    groups = frame.service.chat_options(info.ref)
    dialog = dialogs.OptionsDialog(frame, "Chat one", groups)
    assert set(dialog.controls.choices) == {g.id for g in groups}
    assert dialog.controls.values()["mode"] == groups[0].current
    assert dialog.controls.choices["mode"].GetName() == "Mode"
    dialog.Destroy()


def test_permission_dialog_uses_our_labels_and_escape_denies(app):
    request = PermissionRequest("1", "Asked.", "echo", (Decision.ALLOW_ONCE, Decision.DENY), "Careful.")
    dialog = dialogs.PermissionDialog(None, "Chat", request)
    assert [b.GetLabel().replace("&", "") for b in dialog.buttons.values()] == ["Allow Once", "Deny"]
    assert dialog.decision is Decision.DENY
    for word in ("Asked.", "echo", "Careful."):
        assert word in dialog.text.GetValue()
    dialog.Destroy()


def test_permission_dialog_with_only_deny_has_no_allow_button(app):
    dialog = dialogs.PermissionDialog(None, "Chat", PermissionRequest("1", "Asked.", "", (Decision.DENY,)))
    assert list(dialog.buttons) == [Decision.DENY]
    dialog.Destroy()


def test_question_dialog_needs_every_answer(app):
    items = (QuestionItem("a", "One?", (OptionValue("Red", "Red", "warm"), OptionValue("Blue", "Blue"))),
             QuestionItem("b", "Two?"))
    dialog = dialogs.QuestionDialog(None, "Chat", QuestionRequest("1", items))
    assert dialog.collect() is None
    item, choices, other = dialog._controls[0]
    choices.SetSelection(1)
    assert dialog.collect() is None
    dialog._controls[1][2].SetValue("Typed")
    assert dialog.collect() == {"a": "Blue", "b": "Typed"}
    other.SetValue("Own answer")
    assert dialog.collect()["a"] == "Own answer"
    assert choices.GetString(0) == "Red: warm"
    dialog.Destroy()


def test_new_chat_loads_the_chosen_ais_options(app, tmp_path):
    groups = (OptionGroup("mode", "Mode", (OptionValue("x", "X"), OptionValue("y", "Y")), "y"),)
    dialog = dialogs.NewChatDialog(None, [("one", "One"), ("two", "Two")], str(tmp_path),
                                   lambda provider: groups if provider == "two" else ())
    assert dialog.provider_id() == "one"
    dialog.provider.SetSelection(1)
    dialog._reload_options()
    assert pump(lambda: dialog.option_controls is not None and dialog.option_controls.groups == groups)
    assert dialog.options() == {"mode": "y"}
    dialog.Destroy()


def test_providers_dialog_turns_one_on_and_shows_what_wasnt_understood(app):
    state = {"a": False}
    dialog = dialogs.ProvidersDialog(None, [("a", "Alpha")], lambda p: state[p],
                                     lambda p, v: state.__setitem__(p, v), lambda p: "Details here")
    assert dialog.list.GetString(0) == "Alpha, off"
    assert pump(lambda: dialog.detail.GetValue() == "Details here")
    dialog._on_toggle(None)
    assert state["a"] is True and dialog.list.GetString(0) == "Alpha, on"
    assert dialog.toggle.GetLabel().replace("&", "") == "Turn Off"
    dialog.Destroy()


def test_a_request_behind_an_open_dialog_is_said_and_asked_after_it_closes(window, tmp_path, monkeypatch):
    frame = window(acp("ask"))
    info = _started(frame, tmp_path)
    asked = []
    monkeypatch.setattr(frame, "_ask", lambda ref: asked.append(ref))
    dialog = dialogs.TextDialog(frame, "Reading", "Some text")
    frame._dialog = dialog
    frame.reply.SetValue("Hi")
    frame.send()
    assert pump(lambda: frame.service.state(info.ref).pending_permission is not None)
    assert pump(lambda: any("needs you" in s for s in frame.recorder.spoken))
    assert asked == []
    frame._dialog = None
    dialog.Destroy()
    frame._ask_if_waiting(info.ref)
    assert pump(lambda: asked == [info.ref])
    frame.service.stop(info.ref)
    assert pump(lambda: not frame.service.state(info.ref).busy)


def test_a_dialog_ended_by_its_turn_ending_never_allows(window, tmp_path, monkeypatch):
    frame = window(acp("ask"))
    info = _started(frame, tmp_path)
    answered = []
    real = frame.service.answer_permission
    monkeypatch.setattr(frame.service, "answer_permission",
                        lambda ref, request, decision: (answered.append(decision), real(ref, request, decision)))
    seen = {}

    def while_it_is_open():
        # A modal runs its own event loop, so this is the only place code runs
        # until it ends. As a turn ending does, first another chat's, then its own.
        dialog = frame._dialog
        if not (isinstance(dialog, dialogs.PermissionDialog) and dialog.IsModal()):
            wx.CallLater(50, while_it_is_open)
            return
        frame._end_dialog(ChatRef("somewhere", "else"))
        seen["left_open"] = frame._dialog is dialog and dialog.IsModal()
        frame.stop_turn()  # clears the request, then ends its dialog
    wx.CallLater(50, while_it_is_open)
    frame.reply.SetValue("Hi")
    frame.send()
    assert pump(lambda: "left_open" in seen and frame._dialog is None)
    assert seen["left_open"], "another chat's ending must leave it open"
    assert answered == []
    assert pump(lambda: not frame.service.state(info.ref).busy)
    # Told to stop while asking, it never heard an Allow.
    assert not any(m.role == "assistant" and "Allowed" in m.text for m in frame.service.messages(info.ref))


def test_a_chat_starting_behind_a_dialog_doesnt_change_the_chat(window, tmp_path):
    frame = window(acp("answer"))
    one = _started(frame, tmp_path)
    other = frame.service.start_chat("fakeagent", str(tmp_path), "Chat two", {})
    dialog = dialogs.TextDialog(frame, "Reading", "Some text")
    frame._dialog = dialog
    frame._chat_started(other)
    assert frame.current.ref == one.ref
    assert any("Chat two" in s for s in frame.recorder.spoken)
    frame._dialog = None
    dialog.Destroy()


def test_a_draft_stays_with_its_chat(window, tmp_path):
    frame = window(acp("answer"))
    one = _started(frame, tmp_path)
    frame.reply.SetValue("For the first")
    two = frame.service.start_chat("fakeagent", str(tmp_path), "Chat two", {})
    frame._add_chat(two)
    frame.open_chat(two)
    assert frame.reply.GetValue() == ""
    frame.open_chat(one)
    assert frame.reply.GetValue() == "For the first"


def test_a_turn_with_no_reply_doesnt_read_out_an_old_one(window, tmp_path):
    frame = window(acp("answer"))
    info = _started(frame, tmp_path)
    frame.service.state(info.ref).messages.append(Message("assistant", "An old reply"))
    frame._announce_end(info.ref, TurnEnded(TurnStatus.COMPLETED))
    assert not any("An old reply" in s for s in frame.recorder.spoken)


def test_stopping_with_nothing_running_says_so(window, tmp_path):
    frame = window(acp("answer"))
    _started(frame, tmp_path)
    frame.stop_turn()
    assert "Nothing to stop." in frame.recorder.spoken


def test_new_chat_cant_start_until_the_ais_choices_load(app, tmp_path):
    import threading
    release = threading.Event()
    groups = (OptionGroup("mode", "Mode", (OptionValue("x", "X"),), "x"),)

    def slow(provider):
        release.wait(10)
        return groups
    dialog = dialogs.NewChatDialog(None, [("one", "One")], str(tmp_path), slow)
    assert not dialog.ok.IsEnabled()
    release.set()
    assert pump(dialog.ok.IsEnabled)
    assert dialog.options() == {"mode": "x"}
    dialog.Destroy()


def test_new_chat_hides_the_choices_when_an_ai_has_none(app, tmp_path):
    dialog = dialogs.NewChatDialog(None, [("one", "One")], str(tmp_path), lambda provider: ())
    assert pump(dialog.ok.IsEnabled)
    assert not dialog.options_panel.IsShown()
    dialog.Destroy()


def test_controls_are_named(window):
    frame = window(acp("answer"))
    assert frame.reply.GetName() == "Reply"
    assert frame.note.GetName() == "About this chat"


def test_a_row_shows_when_a_chat_needs_you(window, tmp_path):
    frame = window(acp("ask"))
    info = frame.service.start_chat("fakeagent", str(tmp_path), "Chat one", {})
    frame._add_chat(info)
    frame.service.send(info.ref, "Hi")
    assert pump(lambda: frame.service.state(info.ref).pending_permission is not None)
    assert pump(lambda: "Needs you" in frame.chat_list.GetString(0))
    assert any("needs you" in s for s in frame.recorder.spoken)
    frame.service.stop(info.ref)
    assert pump(lambda: not frame.service.state(info.ref).busy)


def test_a_dialogs_labels_come_before_their_controls(app, tmp_path):
    """Alt keys and screen readers both go by creation order: the walk found
    Alt+A landing on the wrong control when a label came after."""
    dialog = dialogs.NewChatDialog(None, [("one", "One")], str(tmp_path), lambda provider: ())
    children = list(dialog.GetChildren())
    for label, control in (("&AI:", dialog.provider), ("&Title:", dialog.title_box), ("&Folder:", dialog.folder)):
        index = children.index(control)
        assert children[index - 1].GetLabel() == label
    dialog.Destroy()
