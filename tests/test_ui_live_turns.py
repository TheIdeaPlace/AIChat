"""Live control of a running Claude turn in the window (#468): Change Model
and Change Permission Mode now, Stop Background Task, retries, fallbacks,
memories, Claude's own mode changes and the context breakdown.

These tests construct windows: run in the VM or CI only."""
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


def test_claude_entering_plan_mode_itself_is_kept_and_approval_offers_the_old_mode(frame, env):
    _running(frame)
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("mode", text="plan"))
    assert frame.store.get("own-1").permission_mode == "plan"
    assert frame.session_heading.GetLabel().endswith("plan mode.")
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
    spoken = len(env["spoken"])
    later = {**retry, "attempt": 3}
    frame._on_turn_event({"id": "own-1"}, "Hub probe", TurnEvent("retry", data=later))
    assert len(env["spoken"]) == spoken  # within 30 seconds: status bar only
    assert "Attempt 3 of 10" in frame._status_latest


def test_turn_status_says_claude_is_retrying(frame, env):
    runner = _running(frame)
    runner.retry = {"attempt": 2, "max_retries": 10, "error": "overloaded",
                    "error_status": 529}
    frame.on_turn_status()
    assert env["feedback"][-1].endswith(
        "Claude is retrying: the service is overloaded, attempt 2 of 10.")


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


def test_turns_ask_for_the_context_breakdown(frame, env, fake_runner):
    select(frame, "Hub probe")
    frame.on_open_session()
    frame.reply_text.SetValue("go")
    frame.on_send()
    assert fake_runner.instances[-1].context_usage is True
