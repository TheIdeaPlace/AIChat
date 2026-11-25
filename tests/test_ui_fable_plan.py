"""Fable in subscription chats on a plan that has it (#722), on a frame
that's never shown: which models New Session and Change Model offer, the
plan checked before every Fable turn, and "Use usage credits?" asked only
when you send, once a Fable turn has said turns are charged to usage
credits. No real claude or speech."""
import time
from types import SimpleNamespace

import pytest

wx = pytest.importorskip("wx")

from thechatplace import platform_paths, signin  # noqa: E402
from thechatplace.claude_cli import TurnEvent  # noqa: E402
from thechatplace.own_store import OwnSession, OwnSessionStore  # noqa: E402

from records import assistant_block, text_block, user_text  # noqa: E402
import fake_env  # noqa: E402
from fake_env import FakeRunner, add_transcript, now_ms, pump  # noqa: E402


def overage(on=True, resets_in=86400):
    return {"status": "allowed", "rateLimitType": "seven_day", "isUsingOverage": on,
            "overageStatus": "allowed", "resetsAt": time.time() + resets_in}


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
                        lambda title, model: calls.append((title, model)) or answer)
    return calls


def boxes(monkeypatch):
    shown = []
    monkeypatch.setattr(wx, "MessageBox", lambda text, *a, **k: shown.append(text) or wx.OK)
    return shown


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


# -- the plan, before every Fable turn ---------------------------------------------------------

@pytest.mark.parametrize("plan, says", [("pro", "Claude Pro plan doesn't include Fable"),
                                        ("", "hasn't confirmed")])
def test_a_fable_chat_isnt_sent_without_a_plan_that_includes_it(frame, monkeypatch, plan, says):
    shown = boxes(monkeypatch)
    calls = asked(frame, monkeypatch, "credits")
    signed_in(frame, plan) if plan else None
    open_chat(frame, "fab-1")
    send(frame)
    assert FakeRunner.instances == [] and calls == []
    assert says in shown[-1] and "Change Model" in shown[-1]
    assert frame.reply_text.GetValue() == "Next step"
    # Nor queued, Other Machines' way or an agent's relay.
    assert says in frame._send_now("fab-1", "Queued one", queued=True)
    assert not frame._send_claude(frame.store.get("fab-1").to_info(), "Relayed", [],
                                  announce_send=False)
    assert FakeRunner.instances == []


def test_other_models_dont_need_the_plan(frame, monkeypatch):
    signed_in(frame, "pro")
    open_chat(frame, "opus-1")
    send(frame)
    assert FakeRunner.instances


# -- usage credits, asked when you send --------------------------------------------------------

def test_no_question_while_fable_is_inside_the_plan(frame, monkeypatch):
    signed_in(frame, "max")
    calls = asked(frame, monkeypatch, "cancel")
    frame._fable_limits = overage(on=False)
    open_chat(frame, "fab-1")
    send(frame)
    assert calls == []
    assert FakeRunner.instances[-1].allow_overage is False


def test_cancel_keeps_the_message_and_sends_nothing(frame, env, monkeypatch):
    signed_in(frame, "max")
    calls = asked(frame, monkeypatch, "cancel")
    frame._fable_limits = overage()
    open_chat(frame, "fab-1")
    send(frame)
    assert calls == [("On Fable", "fable")]
    assert FakeRunner.instances == []
    assert frame.reply_text.GetValue() == "Next step"
    assert env["feedback"][-1] == "Not sent."


def test_use_usage_credits_sends_that_turn_only_on_them(frame, monkeypatch):
    signed_in(frame, "max")
    calls = asked(frame, monkeypatch, "credits")
    frame._fable_limits = overage()
    open_chat(frame, "fab-1")
    send(frame)
    assert len(calls) == 1
    assert FakeRunner.instances[-1].allow_overage is True
    # The next turn asks again: never remembered.
    frame._runners.clear()
    send(frame, "And another")
    assert len(calls) == 2


def test_change_model_opens_change_model_for_that_chat(frame, monkeypatch):
    signed_in(frame, "max")
    asked(frame, monkeypatch, "model")
    changed = []
    monkeypatch.setattr(frame, "on_change_model", lambda info=None: changed.append(info))
    frame._fable_limits = overage()
    open_chat(frame, "fab-1")
    send(frame)
    assert pump(lambda: changed)
    assert changed[0].cli_session_id == "fab-1"
    assert FakeRunner.instances == []


def test_other_models_and_a_reset_limit_arent_asked(frame, monkeypatch):
    signed_in(frame, "max")
    calls = asked(frame, monkeypatch, "cancel")
    frame._fable_limits = overage()
    open_chat(frame, "opus-1")
    send(frame)
    assert calls == [] and FakeRunner.instances
    frame._runners.clear()
    frame._fable_limits = overage(resets_in=-60)
    open_chat(frame, "fab-1")
    send(frame)
    assert calls == []


def test_only_a_fable_turns_report_counts_for_fable(frame):
    """Fable has its own weekly allowance: an Opus turn saying it isn't on
    usage credits doesn't mean Fable isn't."""
    frame._runners["fab-1"] = SimpleNamespace(overage_model="", _turn_model=lambda: "claude-fable-5-2")
    frame._note_limits("fab-1", overage())
    frame._runners["opus-1"] = SimpleNamespace(overage_model="", _turn_model=lambda: "claude-opus-5-5")
    frame._note_limits("opus-1", overage(on=False))
    assert frame._overage_now()
    assert frame._limits["isUsingOverage"] is False  # Usage shows the latest, as before
    frame._note_limits("fab-1", overage(on=False))
    assert not frame._overage_now()


def test_what_isnt_you_pressing_send_never_goes_on_usage_credits(frame, monkeypatch):
    signed_in(frame, "max")
    calls = asked(frame, monkeypatch, "credits")
    frame._fable_limits = overage()
    problem = frame._send_now("fab-1", "Queued one", queued=True)
    assert problem and "usage credits" in problem
    assert not frame._send_claude(frame.store.get("fab-1").to_info(), "Relayed", [],
                                  announce_send=False)
    assert calls == [] and FakeRunner.instances == []


def test_other_machines_asks_as_send_does(frame, monkeypatch):
    signed_in(frame, "max")
    calls = asked(frame, monkeypatch, "credits")
    frame._fable_limits = overage()
    frame._send_generated(frame.store.get("fab-1").to_info(), "Send this on", "Sending.")
    assert len(calls) == 1 and FakeRunner.instances[-1].allow_overage is True


def test_a_turn_allowed_on_usage_credits_asks_again_before_a_steer(frame, monkeypatch):
    signed_in(frame, "max")
    calls = asked(frame, monkeypatch, "cancel")
    frame._fable_limits = overage()
    runner = SimpleNamespace(allow_overage=True)
    assert not frame._running_overage_ok("fab-1", runner)
    assert len(calls) == 1
    assert frame._running_overage_ok("fab-1", SimpleNamespace(allow_overage=False))
    assert len(calls) == 1


def test_a_stopped_turn_doesnt_pop_up_a_question(frame, monkeypatch):
    """Stopped at usage credits: said as the turn's end, and the question
    waits for your next Send, so no key you're typing elsewhere answers it."""
    signed_in(frame, "max")
    calls = asked(frame, monkeypatch, "credits")
    open_chat(frame, "fab-1")
    send(frame)
    runner = FakeRunner.instances[-1]
    runner.stopped_for_overage = runner.stopped_mid_turn = runner.session_started = True
    frame._on_turn_event({"id": "fab-1"}, "On Fable",
                         TurnEvent("failed", text="Charged to usage credits.", is_error=True))
    wx.GetApp().ProcessPendingEvents()
    assert calls == [] and len(FakeRunner.instances) == 1


def test_the_answers_map_and_escape_cancels():
    from thechatplace.ui.main_frame import MainFrame
    assert MainFrame._overage_answer(wx.ID_YES) == "credits"
    assert MainFrame._overage_answer(wx.ID_NO) == "model"
    for other in (wx.ID_CANCEL, wx.ID_CLOSE, -1):
        assert MainFrame._overage_answer(other) == "cancel"


def test_ask_aside_on_a_fable_chat_asks_first(frame, monkeypatch):
    signed_in(frame, "max")
    calls = asked(frame, monkeypatch, "cancel")
    frame._fable_limits = overage()
    opened = []
    from thechatplace.ui import claude_extras
    monkeypatch.setattr(claude_extras, "AskAsideDialog",
                        lambda *a, **k: opened.append(a) or pytest.fail("opened"))
    open_chat(frame, "fab-1")
    frame.on_ask_aside()
    assert calls == [("On Fable", "fable")] and opened == []
