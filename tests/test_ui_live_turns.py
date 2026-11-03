"""Live control of a running Claude turn in the window (#468): Change Model
and Change Permission Mode now, Stop Background Task, retries, fallbacks,
memories, Claude's own mode changes and the context breakdown.

These tests construct windows: run in the VM or CI only."""
from types import SimpleNamespace

import pytest

wx = pytest.importorskip("wx")

from test_ui_hidden import _chooser, _usage_dialog, env, fake_runner, frame, select  # noqa: E402,F401
from fake_env import FakeRunner, pump  # noqa: E402
from thechatplace.claude_cli import PERMISSION_MODES, TurnEvent  # noqa: E402
from thechatplace import speech  # noqa: E402


def _running(frame, live=True, ok=True, error=""):
    """Hub probe loaded, with a turn running that takes control requests."""
    select(frame, "Hub probe")
    frame.on_open_session()
    # Loaded first, so its own announcement can't land after what's tested.
    pump(lambda: frame._chat_loaded and not frame._reader_busy)
    runner = FakeRunner([], "C:\\G\\Scratch", "", None)
    runner.live, runner.live_ok, runner.live_error = live, ok, error
    frame._runners["own-1"] = runner
    return runner


def test_change_model_during_a_turn_applies_now(frame, env, monkeypatch):
    runner = _running(frame)
    _chooser(monkeypatch, frame, "Sonnet")
    frame.on_change_model()
    assert runner.controls == [("set_model", "sonnet")]
    assert frame.store.get("own-1").model == "sonnet"  # later turns too
    assert pump(lambda: env["feedback"][-1] ==
                "Hub probe now uses Sonnet, now and for later turns.")


def test_change_model_refused_mid_turn_waits_for_the_next(frame, env, monkeypatch):
    runner = _running(frame, ok=False, error="Model 'sonnet' not found")
    _chooser(monkeypatch, frame, "Sonnet")
    frame.on_change_model()
    assert runner.controls == [("set_model", "sonnet")]
    assert frame.store.get("own-1").model == "sonnet"
    assert pump(lambda: env["feedback"][-1] == (
        "Hub probe now uses Sonnet, from its next turn; the turn running now carries on as "
        "it started."))


def test_change_model_when_claude_code_cant_take_it_says_so_at_once(frame, env, monkeypatch):
    runner = _running(frame, live=False)
    _chooser(monkeypatch, frame, "Opus")
    frame.on_change_model()
    assert runner.controls == []
    assert env["feedback"][-1].startswith("Hub probe now uses Opus, from its next turn; ")


def test_change_permission_mode_during_a_turn_applies_now(frame, env, monkeypatch):
    runner = _running(frame)
    _chooser(monkeypatch, frame, dict(PERMISSION_MODES)["acceptEdits"])
    frame.on_change_permission_mode()
    assert runner.controls == [("set_permission_mode", "acceptEdits")]
    assert frame.store.get("own-1").permission_mode == "acceptEdits"
    assert pump(lambda: env["feedback"][-1] ==
                "Permission mode for Hub probe: accept edits, now and for later turns.")
    assert frame.session_heading.GetLabel().endswith("accept edits mode.")


def test_quick_mode_choices_arent_overwritten_by_their_own_statuses(frame, env, monkeypatch):
    """Manual, accept edits, auto in quick succession: accept edits' status
    arrived with auto saved, looked narrower and overwrote it, and auto's own
    status then looked wider and wasn't kept (#468)."""
    runner = _running(frame)
    frame.store.update("own-1", permission_mode="manual")
    names = dict(PERMISSION_MODES)
    for mode in ("acceptEdits", "auto"):
        _chooser(monkeypatch, frame, names[mode])
        frame.on_change_permission_mode()
    assert [c[1] for c in runner.controls] == ["acceptEdits", "auto"]
    assert frame.store.get("own-1").permission_mode == "auto"
    for mode in ("acceptEdits", "auto"):
        frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("mode", text=mode))
        assert frame.store.get("own-1").permission_mode == "auto"
    assert not any("switched" in said for said in env["feedback"])
    # A change that isn't an echo of yours still counts, and is said.
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("mode", text="manual"))
    assert frame.store.get("own-1").permission_mode == "manual"
    assert env["feedback"][-1] == "Claude Code switched to manual mode."


def test_a_refused_mode_choice_isnt_waited_for(frame, env, monkeypatch):
    _running(frame, ok=False, error="no")
    frame.store.update("own-1", permission_mode="manual")
    _chooser(monkeypatch, frame, dict(PERMISSION_MODES)["acceptEdits"])
    frame.on_change_permission_mode()
    assert pump(lambda: "own-1" in frame._chosen_modes_for()._waiting
                and not frame._chosen_modes_for()._waiting["own-1"])
    # So the same mode set from elsewhere later is a change from elsewhere.
    frame.store.update("own-1", permission_mode="manual")
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("mode", text="acceptEdits"))
    assert any("accept edits mode for this turn" in said for said in env["feedback"])


def test_claude_entering_plan_mode_itself_is_kept_and_approval_offers_the_old_mode(frame, env):
    _running(frame)
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("mode", text="plan"))
    assert frame.store.get("own-1").permission_mode == "plan"
    assert frame.session_heading.GetLabel().endswith("plan mode.")
    # Said, not just on the status bar.
    assert env["feedback"][-1] == "Claude Code switched to plan mode."
    assert "switched to plan mode" in frame._status_latest
    assert frame._mode_to_offer_after_plan("own-1", "plan") == "auto"
    # The same mode again (our own change echoed back) changes nothing.
    before = frame._status_latest
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("mode", text="plan"))
    assert frame._status_latest == before
    # A mode The Chat Place doesn't offer is left alone.
    frame._on_turn_event({"id": "own-1"}, "Hub probe",
                         TurnEvent("mode", text="bypassPermissions"))
    assert frame.store.get("own-1").permission_mode == "plan"


def test_a_wider_mode_changed_elsewhere_applies_to_this_turn_only(frame, env):
    """Manual to auto over Remote Control: said, and the chat stays manual."""
    _running(frame)
    frame.store.update("own-1", permission_mode="manual")
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("mode", text="auto"))
    assert frame.store.get("own-1").permission_mode == "manual"
    assert env["feedback"][-1] == ("Claude Code switched to auto mode for this turn. "
                                 "Later turns start in manual mode.")


def test_a_narrower_mode_changed_elsewhere_is_kept_and_said(frame, env):
    _running(frame)
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("mode", text="manual"))
    assert frame.store.get("own-1").permission_mode == "manual"
    assert env["feedback"][-1] == "Claude Code switched to manual mode."


def test_a_mode_change_in_a_chat_not_loaded_names_the_chat(frame, env):
    _running(frame)
    frame._open = None
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("mode", text="plan"))
    assert env["feedback"][-1] == "Hub probe: Claude Code switched to plan mode."


def test_leaving_claudes_own_plan_for_more_goes_back_to_the_mode_you_chose(frame, env):
    _running(frame)
    frame.store.update("own-1", permission_mode="manual")
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("mode", text="plan"))
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("mode", text="auto"))
    assert frame.store.get("own-1").permission_mode == "manual"
    assert frame._mode_to_offer_after_plan("own-1", "plan") == ""


def test_approving_a_plan_saves_its_mode_and_its_echo_says_nothing_more(frame, env):
    """The approval's mode is saved as it's sent, even one wider than the chat
    had, so the status that follows is no change, and doesn't undo it."""
    _running(frame)
    frame.store.update("own-1", permission_mode="manual")
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("mode", text="plan"))
    request = SimpleNamespace(request_id="r1", is_plan=True)
    frame._apply_answer("own-1", request, {"behavior": "allow"}, "Plan approved.",
                        {"permission_mode": "auto"})
    assert frame.store.get("own-1").permission_mode == "auto"
    spoken = list(env["feedback"])
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("mode", text="auto"))
    assert frame.store.get("own-1").permission_mode == "auto"
    assert env["feedback"] == spoken
    assert frame._mode_to_offer_after_plan("own-1", "plan") == ""


def test_stop_background_task_lists_confirms_and_stops_one(frame, env, monkeypatch):
    runner = _running(frame)
    runner.background_entries = [
        {"task_id": "b1", "description": "Windows build", "since": 0.0},
        {"task_id": "b2", "description": "Watch CI", "since": 60.0}]
    runner.background_tasks = ["Windows build", "Watch CI"]
    frame._update_send_state()
    assert frame.stop_background_item.IsEnabled()
    offered = []

    def choose(title, prompt, choices, selection=None):
        offered.append((title, prompt, choices))
        return 0
    monkeypatch.setattr(frame, "_choose", choose)
    frame.on_stop_background_task()
    assert offered == [("Stop Background Task", "Background work in Hub probe. Stop which?",
                        ["Windows build, running 1 minute 15 seconds",
                         "Watch CI, running 15 seconds"])]
    assert env["boxes"][-1].startswith("Stop Windows build? ")  # Yes, from the stub
    assert runner.controls == [("stop_task", "b1")]
    assert pump(lambda: env["feedback"][-1] == "Stopped Windows build.")
    frame._update_send_state()
    assert frame.stop_background_item.IsEnabled()  # Watch CI is still running


def _two_tasks(runner):
    runner.background_entries = [
        {"task_id": "b1", "description": "Windows build", "since": 0.0},
        {"task_id": "b2", "description": "Watch CI", "since": 60.0}]


def test_a_task_that_ends_while_the_list_is_open_isnt_stopped(frame, env, monkeypatch):
    runner = _running(frame)
    _two_tasks(runner)
    boxes = len(env["boxes"])

    def choose(*a, **k):
        runner.background_entries = runner.background_entries[1:]
        return 0
    monkeypatch.setattr(frame, "_choose", choose)
    frame.on_stop_background_task()
    assert runner.controls == []
    assert len(env["boxes"]) == boxes  # no Yes or No about work that's over
    assert env["feedback"][-1] == "Windows build has already finished."


def test_a_task_that_ends_while_yes_or_no_is_open_isnt_stopped(frame, env, monkeypatch):
    runner = _running(frame)
    _two_tasks(runner)
    monkeypatch.setattr(frame, "_choose", lambda *a, **k: 0)

    def box(*a, **k):
        runner.background_entries = runner.background_entries[1:]
        return wx.YES
    monkeypatch.setattr(wx, "MessageBox", box)
    frame.on_stop_background_task()
    assert runner.controls == []
    assert env["feedback"][-1] == "Windows build has already finished."
    assert "Stopped Windows build." not in env["feedback"]


def test_stop_background_task_no_stops_nothing(frame, env, monkeypatch):
    runner = _running(frame)
    runner.background_entries = [{"task_id": "b1", "description": "Windows build",
                                  "since": 0.0}]
    monkeypatch.setattr(frame, "_choose", lambda *a, **k: 0)
    monkeypatch.setattr(wx, "MessageBox", lambda *a, **k: wx.NO)
    frame.on_stop_background_task()
    assert runner.controls == []


def test_stop_background_task_with_nothing_running(frame, env):
    runner = _running(frame)
    frame._update_send_state()
    assert not frame.stop_background_item.IsEnabled()
    frame.on_stop_background_task()
    assert env["feedback"][-1] == "No background work is running in Hub probe."
    assert runner.controls == []


def test_a_retry_is_said_once_then_kept_on_the_status_bar(frame, env):
    _running(frame)
    retry = {"attempt": 2, "max_retries": 10, "retry_delay_ms": 8000,
             "error_status": 529, "error": "overloaded"}
    text = "Claude is retrying: the service is overloaded. Attempt 2 of 10, next in 8 seconds."
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("retry", data=retry))
    assert frame._last_announcement == text
    spoken = len(env["feedback"])
    later = {**retry, "attempt": 3}
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("retry", data=later))
    assert len(env["feedback"]) == spoken  # the same reason: status bar only
    assert "Attempt 3 of 10" in frame._status_latest
    frame._on_turn_event({"id": "own-1"}, "Hub probe",
                         TurnEvent("retry", data={**retry, "error": "rate_limit"}))
    assert len(env["feedback"]) == spoken + 1  # a new reason is said


def test_a_heartbeat_retry_while_a_background_agent_waits_is_said_once(frame, env):
    """Every 30 seconds for hours while a subagent waits on a rate limit."""
    runner = _running(frame)
    runner.waiting_on_background = True
    runner.background_tasks = ["Research"]
    retry = {"attempt": 1, "max_retries": 10, "retry_delay_ms": 5400000,
             "error_status": 429, "error": "rate_limit"}
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("retry", data=retry))
    spoken = len(env["feedback"])
    assert env["feedback"][-1].endswith("Attempt 1 of 10, next in 1 hour 30 minutes.")
    for delay in (5370000, 5340000, 5310000):
        frame._on_turn_event({"id": "own-1"}, "Hub probe",
                             TurnEvent("retry", data={**retry, "retry_delay_ms": delay}))
    assert len(env["feedback"]) == spoken
    assert "next in 1 hour 28 minutes" in frame._status_latest


def test_turn_status_says_claude_is_retrying(frame, env):
    runner = _running(frame)
    runner.retry = {"attempt": 2, "max_retries": 10, "error": "overloaded",
                    "error_status": 529}
    frame.on_turn_status()
    assert env["feedback"][-1].endswith(
        "last starting. Claude is retrying: the service is overloaded, attempt 2 of 10.")
    assert env["feedback"][-1].count("retrying") == 1


def test_fallback_and_memory_are_said(frame, env):
    _running(frame)
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("fallback", data={
        "trigger": "overloaded", "original_model": "claude-opus-5-5",
        "fallback_model": "claude-sonnet-5-5"}))
    assert frame._last_announcement == ("Claude switched to Sonnet 5.5 because Opus 5.5 was "
                                        "overloaded.")
    frame.speech.announce = speech.ANNOUNCE_FULL
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("memory", data={
        "paths": ["C:/u/memory/build-steps.md"], "verb": "Saved"}))
    assert frame._last_announcement == "Claude saved a memory: build steps."
    frame.speech.announce = speech.ANNOUNCE_SUMMARY
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("memory", data={
        "paths": ["C:/u/memory/release.md"], "verb": "Saved"}))
    assert frame._last_announcement != "Claude saved a memory: release."  # status bar only
    assert frame._status_latest == "Claude saved a memory: release."


def test_usage_and_context_lists_what_fills_the_context(frame, env, monkeypatch):
    _running(frame)
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("context", data={
        "categories": [{"name": "System prompt", "tokens": 2362, "kind": "used"},
                       {"name": "MCP tools (deferred)", "tokens": 608, "isDeferred": True,
                        "kind": "deferred"},
                       {"name": "Free space", "tokens": 900000, "kind": "free"}]}))
    seen = _usage_dialog(frame, monkeypatch)
    assert seen["rows"][0].startswith("Hub probe: ")
    assert seen["rows"][1:5] == ["What fills the context, when the latest turn ended:",
                                 "System prompt: 2,362 tokens",
                                 "MCP tools: 608 tokens (loaded when needed)",
                                 "Free space: 900,000 tokens"]
    assert seen["rows"][5].startswith("Usage limits")


def test_a_turn_ending_before_the_answer_is_said_as_it_is(frame, env, monkeypatch):
    from thechatplace.claude_cli import TURN_ENDED
    runner = _running(frame, ok=False, error=TURN_ENDED)
    runner.background_entries = [{"task_id": "b1", "description": "Windows build",
                                  "since": 0.0}]
    monkeypatch.setattr(frame, "_choose", lambda *a, **k: 0)
    frame.on_stop_background_task()
    assert pump(lambda: "Stopped Windows build." in env["feedback"])  # with the turn
    _chooser(monkeypatch, frame, "Opus")
    frame.on_change_model()
    assert pump(lambda: "Hub probe now uses Opus, from its next turn." in env["feedback"])


def test_choosing_a_mode_yourself_forgets_the_one_before_claudes_plan(frame, env, monkeypatch):
    _running(frame, live=False)
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("mode", text="plan"))
    assert frame._mode_to_offer_after_plan("own-1", "plan") == "auto"
    _chooser(monkeypatch, frame, dict(PERMISSION_MODES)["manual"])
    frame.on_change_permission_mode()
    assert frame._mode_to_offer_after_plan("own-1", "plan") == ""


def test_turns_ask_for_the_context_breakdown(frame, env, fake_runner):
    select(frame, "Hub probe")
    frame.on_open_session()
    frame.reply_text.SetValue("go")
    frame.on_send()
    assert fake_runner.instances[-1].context_usage is True
