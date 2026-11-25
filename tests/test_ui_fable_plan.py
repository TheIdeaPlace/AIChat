"""Fable in subscription chats on a plan that has it (#722), on a frame
that's never shown: which models New Session and Change Model offer, the
question before a Fable turn once turns are charged to usage credits, and
the question after a turn stopped for it. No real claude or speech."""
import time

import pytest

wx = pytest.importorskip("wx")

from thechatplace import platform_paths, signin  # noqa: E402
from thechatplace.claude_cli import OVERAGE_CARRY_ON, TurnEvent  # noqa: E402
from thechatplace.own_store import OwnSession, OwnSessionStore  # noqa: E402

from records import assistant_block, text_block, user_text  # noqa: E402
import fake_env  # noqa: E402
from fake_env import FakeRunner, add_transcript, now_ms, pump  # noqa: E402

OVERAGE = {"status": "allowed", "rateLimitType": "seven_day", "isUsingOverage": True,
           "overageStatus": "allowed", "resetsAt": time.time() + 86400}


@pytest.fixture
def env(tmp_path, monkeypatch, app):
    return fake_env.install(tmp_path, monkeypatch.setattr)


@pytest.fixture
def folder(tmp_path):
    path = tmp_path / "Work"
    path.mkdir()
    return str(path)


@pytest.fixture
def frame(env, folder, monkeypatch):
    from thechatplace.ui import main_frame
    from thechatplace.ui.main_frame import MainFrame
    FakeRunner.instances = []
    monkeypatch.setattr(main_frame, "TurnRunner", FakeRunner)
    monkeypatch.setattr(platform_paths, "find_claude",
                        lambda: platform_paths.ClaudeLookup("claude.exe"))
    for cli in ("fab-1", "opus-1"):
        add_transcript(env, folder, cli, [user_text("Hello"),
                                          assistant_block(text_block("Hi"), "m1")])
    store = OwnSessionStore(env["tmp"] / "own.json")
    store.add(OwnSession("fab-1", "On Fable", folder, last_activity_ms=now_ms(), model="fable"))
    store.add(OwnSession("opus-1", "On Opus", folder, last_activity_ms=now_ms(), model="opus"))
    window = MainFrame(store=store, check_updates_at_start=False)
    assert pump(lambda: window.session_list.GetCount() == 2)
    yield window
    window._runners.clear()
    window.stop_timers()
    window._pool.shutdown(wait=True)
    wx.GetApp().ProcessPendingEvents()
    window.Destroy()
    wx.GetApp().ProcessPendingEvents()


def signed_in(frame, plan):
    frame._on_sign_in_result(signin.SignIn(True, signed_in=True, method="claude.ai",
                                           plan=plan), manual=False)


def open_chat(frame, session_id):
    frame.open_session(frame.store.get(session_id).to_info())


def asked(frame, monkeypatch, answer):
    """Stand in for "Use usage credits?"; returns what it was asked."""
    calls = []
    monkeypatch.setattr(frame, "_ask_overage",
                        lambda title, model, stopped: calls.append((title, model, stopped))
                        or answer)
    return calls


def send(frame, text="Next step"):
    frame.reply_text.SetValue(text)
    frame.on_send()


# -- which models are offered ------------------------------------------------------------------

def test_the_plan_decides_whether_fable_is_offered(frame):
    opus = frame.store.get("opus-1")
    assert "fable" not in dict(frame._claude_models(opus))      # not known yet
    signed_in(frame, "max")
    assert frame.claude_plan == "max"
    assert ("fable", "Fable") in frame._claude_models(opus)
    signed_in(frame, "pro")
    assert "fable" not in dict(frame._claude_models(opus))
    # A chat already on Fable keeps it on the list rather than losing it.
    assert ("fable", "Fable") in frame._claude_models(frame.store.get("fab-1"))
    # Signed out: no plan.
    frame._on_sign_in_result(signin.SignIn(True, signed_in=False), manual=False)
    assert frame.claude_plan == ""
    # Couldn't ask: what was known stays.
    signed_in(frame, "ultra")
    frame._on_sign_in_result(signin.SignIn(False, problem="timed out"), manual=False)
    assert frame.claude_plan == "ultra"


def test_new_session_offers_fable_on_max_with_its_note(frame, folder):
    from thechatplace.ui.dialogs import FABLE_PLAN_NOTE, NewSessionDialog
    signed_in(frame, "max")
    dialog = NewSessionDialog(frame, folder)
    try:
        assert "Fable" in dialog.model.GetStrings()
        assert FABLE_PLAN_NOTE in dialog.note.GetValue()
        dialog.model.SetSelection(dialog.model.GetStrings().index("Fable"))
        assert dialog.values()[4] == "fable"
    finally:
        dialog.Destroy()
    signed_in(frame, "pro")
    dialog = NewSessionDialog(frame, folder)
    try:
        assert "Fable" not in dialog.model.GetStrings()
        assert FABLE_PLAN_NOTE not in dialog.note.GetValue()
    finally:
        dialog.Destroy()


# -- before a turn -----------------------------------------------------------------------------

def test_no_question_while_fable_is_inside_the_plan(frame, monkeypatch):
    calls = asked(frame, monkeypatch, "cancel")
    frame._limits = {**OVERAGE, "isUsingOverage": False}
    open_chat(frame, "fab-1")
    send(frame)
    assert calls == []
    assert FakeRunner.instances[-1].allow_overage is False


def test_cancel_keeps_the_message_and_sends_nothing(frame, env, monkeypatch):
    calls = asked(frame, monkeypatch, "cancel")
    frame._limits = dict(OVERAGE)
    open_chat(frame, "fab-1")
    send(frame)
    assert calls == [("On Fable", "fable", False)]
    assert FakeRunner.instances == []
    assert frame.reply_text.GetValue() == "Next step"
    assert env["feedback"][-1] == "Not sent."


def test_use_usage_credits_sends_that_turn_only_on_them(frame, monkeypatch):
    calls = asked(frame, monkeypatch, "credits")
    frame._limits = dict(OVERAGE)
    open_chat(frame, "fab-1")
    send(frame)
    assert len(calls) == 1
    assert FakeRunner.instances[-1].allow_overage is True
    # The next turn asks again: never remembered.
    frame._runners.clear()
    send(frame, "And another")
    assert len(calls) == 2


def test_change_model_opens_change_model_for_that_chat(frame, monkeypatch):
    asked(frame, monkeypatch, "model")
    changed = []
    monkeypatch.setattr(frame, "on_change_model", lambda info=None: changed.append(info))
    frame._limits = dict(OVERAGE)
    open_chat(frame, "fab-1")
    send(frame)
    assert pump(lambda: changed)
    assert changed[0].cli_session_id == "fab-1"
    assert FakeRunner.instances == []


def test_other_models_and_a_reset_limit_arent_asked(frame, monkeypatch):
    calls = asked(frame, monkeypatch, "cancel")
    frame._limits = dict(OVERAGE)
    open_chat(frame, "opus-1")
    send(frame)
    assert calls == [] and FakeRunner.instances
    frame._runners.clear()
    frame._limits = {**OVERAGE, "resetsAt": time.time() - 60}
    open_chat(frame, "fab-1")
    send(frame)
    assert calls == []


def test_a_message_that_doesnt_ask_isnt_sent_on_usage_credits(frame, monkeypatch):
    calls = asked(frame, monkeypatch, "credits")
    frame._limits = dict(OVERAGE)
    problem = frame._send_now("fab-1", "Queued one", queued=True)
    assert problem and "usage credits" in problem
    assert calls == [] and FakeRunner.instances == []


# -- after a turn stopped for it ---------------------------------------------------------------

def stop_for_overage(frame, runner):
    runner.stopped_for_overage = True
    runner.stopped_mid_turn = True
    runner.overage_model = "claude-fable-5-2"
    runner.session_started = True
    frame._on_turn_event({"id": "fab-1"}, "On Fable",
                         TurnEvent("failed", text="Charged to usage credits.", is_error=True))


def test_keep_going_sends_a_carry_on_message_on_usage_credits(frame, monkeypatch):
    calls = asked(frame, monkeypatch, "credits")
    open_chat(frame, "fab-1")
    send(frame)
    stop_for_overage(frame, FakeRunner.instances[-1])
    assert pump(lambda: len(FakeRunner.instances) == 2)
    assert calls == [("On Fable", "claude-fable-5-2", True)]
    again = FakeRunner.instances[-1]
    assert again.prompt == OVERAGE_CARRY_ON and again.allow_overage is True


def test_leave_stopped_sends_nothing(frame, monkeypatch):
    calls = asked(frame, monkeypatch, "cancel")
    open_chat(frame, "fab-1")
    send(frame)
    stop_for_overage(frame, FakeRunner.instances[-1])
    assert pump(lambda: calls)
    assert len(FakeRunner.instances) == 1
