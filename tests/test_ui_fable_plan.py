"""Fable in subscription chats on a plan that has it (#722), on a frame
that's never shown: which models New Session and Change Model offer, the
plan checked before every Fable turn, and "Use usage credits?" asked only
when you send, once a Fable turn has said turns are charged to usage
credits. No real claude or speech."""
import sys
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
from markers import atk, mac  # noqa: E402


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
    fake_env.close_frame(window)


def signed_in(frame, plan):
    frame._on_sign_in_result(signin.SignIn(True, signed_in=True, method="claude.ai",
                                           plan=plan), manual=False)


def open_chat(frame, session_id):
    frame.open_session(frame.store.get(session_id).to_info())


def asked(frame, monkeypatch, answer):
    """Stand in for "Use usage credits?"; returns what it was asked."""
    calls = []
    monkeypatch.setattr(frame, "_ask_overage",
                        lambda title, model, **k: calls.append((title, model)) or answer)
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


def test_more_into_a_running_fable_turn_is_asked_about_each_time(frame, monkeypatch):
    """A steer, Send Now or a message while it waits on background work:
    each its own question, whatever the turn's first message was given."""
    signed_in(frame, "max")
    calls = asked(frame, monkeypatch, "cancel")
    frame._fable_limits = overage()
    for allowed in (True, False):
        runner = SimpleNamespace(allow_overage=allowed)
        assert not frame._running_overage_ok("fab-1", runner)
    assert len(calls) == 2
    calls = asked(frame, monkeypatch, "credits")
    runner = SimpleNamespace(allow_overage=False)
    assert frame._running_overage_ok("fab-1", runner) and runner.allow_overage is True
    frame._fable_limits = overage(on=False)
    assert frame._running_overage_ok("fab-1", SimpleNamespace(allow_overage=False))
    assert len(calls) == 1
    # A chat changed to Opus whose running turn is still on Fable is asked
    # about by the turn's model.
    frame._fable_limits = overage()
    still_fable = SimpleNamespace(allow_overage=True, _turn_model=lambda: "claude-fable-5-2")
    assert frame._running_overage_ok("opus-1", still_fable)
    assert len(calls) == 2


def test_send_now_on_a_queued_message_asks(frame, monkeypatch):
    signed_in(frame, "max")
    open_chat(frame, "fab-1")
    send(frame)                       # the turn starts
    runner = FakeRunner.instances[-1]
    send(frame, "Queued behind it")   # queued, not asked: it goes later
    assert frame._queued["fab-1"] == ["Queued behind it"]
    calls = asked(frame, monkeypatch, "cancel")
    frame._fable_limits = overage()
    sent = []
    runner.send_now = lambda text: sent.append(text) or True
    runner.waiting_on_background = False
    monkeypatch.setattr(frame, "_selected_queued", lambda: 0)
    frame.send_queued_now()
    assert len(calls) == 1 and sent == []
    assert frame._queued["fab-1"] == ["Queued behind it"]


def test_a_live_change_to_fable_goes_through_its_checks(frame, monkeypatch):
    """Change Model on a running Opus turn to Fable: on a plan without it,
    from the next turn only (which checks again), never into the turn."""
    signed_in(frame, "pro")
    shown = boxes(monkeypatch)
    open_chat(frame, "opus-1")
    send(frame)
    runner = FakeRunner.instances[-1]
    live = []
    monkeypatch.setattr(frame, "_live_change", lambda *a, **k: live.append(a))
    monkeypatch.setattr(frame, "_change_setting", lambda *a, **k: "fable")
    frame.on_change_model(frame.store.get("opus-1").to_info())
    assert frame.store.get("opus-1").model == "fable"
    assert live == [] and "doesn't include Fable" in shown[-1]
    assert runner.allow_overage is False


def test_change_model_from_a_new_chats_first_message_finds_the_chat(frame, monkeypatch):
    """A chat just made isn't in the list's snapshot yet: Change Model
    still opens for it."""
    changed = []
    monkeypatch.setattr(frame, "on_change_model", lambda info=None: changed.append(info))
    monkeypatch.setattr(frame, "_current_info", lambda key: None)
    frame._change_model_of("fab-1")
    assert changed and changed[0].cli_session_id == "fab-1"


def test_a_new_chats_first_message_asks_and_its_yes_goes_to_that_turn(frame, monkeypatch):
    signed_in(frame, "max")
    calls = asked(frame, monkeypatch, "credits")
    frame._fable_limits = overage()
    held, allow = frame._first_message_held(frame.store.get("fab-1"), "First")
    assert (held, allow) == (False, True) and len(calls) == 1
    calls = asked(frame, monkeypatch, "cancel")
    held, allow = frame._first_message_held(frame.store.get("fab-1"), "First")
    assert (held, allow) == (True, False)
    assert frame.reply_text.GetValue() == "First" or frame._drafts.get("fab-1") == "First"


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


# -- "Use usage credits?" itself (UsageCreditsDialog) -------------------------------------------
# Shown for real, as Send asks it, and answered with real keys: Cancel must be
# the default and have the focus on every platform, so neither Return nor
# Escape can spend (#722).

def _usage_dialog():
    from thechatplace.ui.dialogs import UsageCreditsDialog
    return next((w for w in wx.GetTopLevelWindows()
                 if isinstance(w, UsageCreditsDialog) and w.IsShown() and w.IsModal()), None)


def answer_for_real(frame, drive, aside=False):
    """``frame._ask_overage`` with its dialog shown; ``drive(dialog, seen)``
    runs once the dialog is up and its focus has settled. Returns what
    _ask_overage returned and what was seen. A dialog that never closes is
    closed after a while and said to have timed out, rather than hanging."""
    seen = {}
    finished = []

    def when_up(tries=200):
        if finished:  # never left to drive the next test's dialog
            return
        dialog = _usage_dialog()
        focus = wx.Window.FindFocus()
        # A Mac with Full Keyboard Access off (its default, and CI's) gives
        # buttons no focus at all, so there's no focus to wait for.
        settled = dialog is not None and (
            (focus is not None and focus.GetTopLevelParent() is dialog)
            or (not dialog.cancel.CanAcceptFocusFromKeyboard() and tries < 190))
        if not settled and tries:
            wx.CallLater(25, when_up, tries - 1)
            return
        if dialog is None:
            return
        seen.update(focus=focus, default=dialog.GetDefaultItem(),
                    cancel=dialog.cancel, credits=dialog.credits,
                    change_model=dialog.change_model, question=dialog.question.GetLabel(),
                    keyboard_focus=dialog.cancel.CanAcceptFocusFromKeyboard())
        try:
            drive(dialog, seen)
        except Exception as error:  # noqa: BLE001 - raised again once the dialog is closed
            # wx swallows a CallLater's exception: the test would only time out.
            seen["error"] = error
            dialog.EndModal(wx.ID_ABORT)

    def watchdog():
        dialog = _usage_dialog()
        if dialog is not None:
            seen["timed out"] = True
            dialog.EndModal(wx.ID_ABORT)
    wx.CallLater(50, when_up)
    guard = wx.CallLater(10000, watchdog)
    try:
        answer = frame._ask_overage("On Fable", "fable", aside=aside)
    finally:
        guard.Stop()
        finished.append(True)
    if "error" in seen:
        raise seen["error"]
    return answer, seen


def shown(frame):
    """The frame in front, its app active (a Mac gives an inactive app no
    focus at all), so the dialog over it gets the focus and the keys."""
    from thechatplace.ui import mac_a11y
    frame.Show()
    frame.Raise()
    mac_a11y.activate_app()
    pump(lambda: False, timeout=0.2)
    return frame


def _key(code):
    def press(dialog, seen):
        wx.UIActionSimulator().Char(code)
    return press


#: A Mac's CI runner may not let a test post keys (that needs Accessibility
#: permission), and gives buttons no focus with Full Keyboard Access off.
#: There, Return is checked as AppKit handles it, by Cancel's key equivalent
#: (test_on_a_mac_return_presses_cancel...), and Escape by the escape ID.
real_keys = pytest.mark.skipif(sys.platform == "darwin",
                               reason="a Mac's Return is checked by its key equivalent")


@real_keys
@pytest.mark.parametrize("code", [wx.WXK_RETURN, wx.WXK_ESCAPE], ids=["Return", "Escape"])
def test_usage_credits_opens_on_cancel_and_return_or_escape_cancels(frame, code):
    shown(frame)
    answer, seen = answer_for_real(frame, _key(code))
    assert "timed out" not in seen
    assert seen["default"] is seen["cancel"]
    assert seen["focus"] is seen["cancel"]
    assert answer == "cancel"


def test_usage_credits_cancel_is_the_default_and_escape(frame):
    from thechatplace.ui.dialogs import UsageCreditsDialog
    dialog = UsageCreditsDialog(frame, "Spend?")
    try:
        assert dialog.GetDefaultItem() is dialog.cancel
        assert dialog.GetEscapeId() == wx.ID_CANCEL == dialog.cancel.GetId()
    finally:
        dialog.Destroy()


@pytest.mark.parametrize("button,expected", [("credits", "credits"),
                                             ("change_model", "model"),
                                             ("cancel", "cancel")])
def test_usage_credits_buttons_give_their_answers(frame, button, expected):
    def press(dialog, seen):
        target = getattr(dialog, button)
        click = wx.CommandEvent(wx.wxEVT_BUTTON, target.GetId())
        click.SetEventObject(target)
        target.GetEventHandler().ProcessEvent(click)
    shown(frame)
    answer, seen = answer_for_real(frame, press)
    assert "timed out" not in seen
    assert answer == expected


def test_closing_usage_credits_cancels(frame):
    shown(frame)
    answer, seen = answer_for_real(frame, lambda dialog, seen: dialog.Close())
    assert "timed out" not in seen
    assert answer == "cancel"


@pytest.mark.parametrize("aside,what", [(False, "Send this message to On Fable"),
                                        (True, "Ask this question about On Fable")])
def test_usage_credits_asks_in_the_dialogs_text(frame, aside, what):
    shown(frame)
    answer, seen = answer_for_real(frame, lambda dialog, seen: dialog.Close(), aside=aside)
    assert answer == "cancel"
    assert seen["question"] == (
        "Turns on Fable are now charged to usage credits, past your plan's Fable allowance. "
        f"{what} on usage credits, change the model, or cancel?")


def test_use_usage_credits_has_no_access_key(frame):
    from thechatplace.ui.dialogs import UsageCreditsDialog
    dialog = UsageCreditsDialog(frame, "Spend?")
    try:
        assert dialog.credits.GetLabel() == "Use Usage Credits"
        assert "&" not in dialog.credits.GetLabel()
        assert dialog.change_model.GetLabel() == "Change &Model"
        assert dialog.GetEscapeId() == wx.ID_CANCEL
        # The buttons in the order Tab reaches them, Cancel last.
        buttons = [w for w in dialog.GetChildren() if isinstance(w, wx.Button)]
        assert buttons == [dialog.credits, dialog.change_model, dialog.cancel]
    finally:
        dialog.Destroy()


@atk
def test_orca_hears_the_question_on_cancel(frame):
    """Orca reads only the title and the focused button as it opens, so the
    question is Cancel's description."""
    from thechatplace.ui import gtk_a11y
    from thechatplace.ui.dialogs import UsageCreditsDialog
    dialog = UsageCreditsDialog(frame, "Spend?")
    try:
        assert gtk_a11y.get_description(dialog.cancel) == "Spend?"
    finally:
        dialog.Destroy()


@mac
def test_on_a_mac_return_presses_cancel_and_voiceover_hears_the_question(frame):
    """What AppKit itself does with Return: the default button's key
    equivalent. A native message box's CANCEL_DEFAULT was never confirmed to
    reach it (#722)."""
    from thechatplace.ui import mac_a11y
    from thechatplace.ui.dialogs import UsageCreditsDialog
    dialog = UsageCreditsDialog(frame, "Spend?")
    try:
        assert mac_a11y.key_equivalent(dialog.cancel)[0] == "\r"
        for button in (dialog.credits, dialog.change_model):
            key, mask = mac_a11y.key_equivalent(button)
            assert key != "\r" and not mac_a11y.is_command_letter(key, mask), button.GetLabel()
        assert mac_a11y.get_help(dialog.cancel) == "Spend?"
    finally:
        dialog.Destroy()


def test_ask_aside_asks_about_each_question(frame, monkeypatch):
    """The plan is checked as Ask Aside opens; usage credits for each
    question asked, so a yes is one question's alone."""
    from thechatplace.ui import claude_extras
    signed_in(frame, "max")
    opened = []

    class Dialog:
        def __init__(self, parent, title, ask, **k):
            opened.append(ask)

        def ShowModal(self):
            return wx.ID_CANCEL

        def settle(self):
            pass

        def Destroy(self):
            pass
    monkeypatch.setattr(claude_extras, "AskAsideDialog", Dialog)
    started = []
    monkeypatch.setattr(claude_extras, "_AsideAtFinishedTurn",
                        lambda *a, **k: SimpleNamespace(begin=lambda submit: started.append(a)
                                                        or "started"))
    open_chat(frame, "fab-1")
    frame.on_ask_aside()
    ask = opened[0]
    calls = asked(frame, monkeypatch, "cancel")
    assert ask("Why?", lambda e: None) == "started" and calls == []   # inside the allowance
    frame._fable_limits = overage()
    assert ask("Why?", lambda e: None) == "Not asked." and len(calls) == 1
    assert len(started) == 1
    calls = asked(frame, monkeypatch, "credits")
    assert ask("And?", lambda e: None) == "started" and len(calls) == 1
    assert ask("Again?", lambda e: None) == "started" and len(calls) == 2
    command = started[-1][1]()
    assert command[command.index("--model") + 1] == "fable"
    # Change Model to Opus with the dialog still open: the next question
    # runs on Opus, the model it was checked for, and isn't asked about.
    frame.store.update("fab-1", model="opus")
    assert ask("Now?", lambda e: None) == "started" and len(calls) == 2
    command = started[-1][1]()
    assert command[command.index("--model") + 1] == "opus"


def test_ask_aside_on_a_plan_without_fable_doesnt_open(frame, monkeypatch):
    from thechatplace.ui import claude_extras
    signed_in(frame, "pro")
    shown = boxes(monkeypatch)
    monkeypatch.setattr(claude_extras, "AskAsideDialog",
                        lambda *a, **k: pytest.fail("opened"))
    open_chat(frame, "fab-1")
    frame.on_ask_aside()
    assert "doesn't include Fable" in shown[-1]
