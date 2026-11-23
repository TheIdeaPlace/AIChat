"""Prompt suggestions (#474) without windows: Claude Code's prompt_suggestion
message, the runner's flag and environment, what's said, and the setting.
Synthetic data only, shaped like Claude Code 2.1.326's output."""
import json
import threading

from thechatplace import announce
from thechatplace.claude_cli import (SUGGESTION_FLAGS, SUGGESTION_VAR, StreamParser,
                                     TurnRunner)
from thechatplace.speech import SpeechSettings

from test_claude_cli import IDLE, INIT, RUNNING, FakeTimers, LiveProcess, ev  # noqa: E402


def test_the_parser_reads_a_suggestion():
    parser = StreamParser()
    [event] = parser.feed(json.dumps({"type": "prompt_suggestion", "suggestion": "  write the script ",
                                      "uuid": "u1", "session_id": "s1"}))
    assert event.kind == "suggestion" and event.text == "write the script"
    # Empty, missing or not text: nothing.
    for odd in ({"type": "prompt_suggestion"}, {"type": "prompt_suggestion", "suggestion": " "},
                {"type": "prompt_suggestion", "suggestion": ["x"]}):
        assert parser.feed(json.dumps(odd)) == []
    # A very long one is cut to one message's worth.
    [long] = parser.feed(json.dumps({"type": "prompt_suggestion", "suggestion": "x" * 5000}))
    assert len(long.text) == 2000


def _runner(**kwargs):
    seen = {}

    def popen(command, **k):
        seen["command"], seen["env"] = command, k.get("env")
        raise FileNotFoundError("not run")
    runner = TurnRunner(["claude", "-p", "--resume", "s1"], ".", "hi", lambda e: None,
                        popen=popen, env={"PATH": "x"}, **kwargs)
    runner._run()
    return seen


def test_the_runner_asks_for_suggestions_only_when_told():
    on = _runner(suggestions=True)
    assert on["env"][SUGGESTION_VAR] == "1"
    assert on["command"][-2:] == list(SUGGESTION_FLAGS)
    off = _runner()
    assert SUGGESTION_VAR not in off["env"] and "--prompt-suggestions" not in off["command"]


def test_never_on_api_credits():
    # Another request the result doesn't count: its cost would be missing.
    runner = TurnRunner(["claude", "-p"], ".", "hi", lambda e: None, env={"PATH": "x"},
                        suggestions=True, billing="api", ledger=object())
    assert runner.suggestions is False and SUGGESTION_VAR not in runner._env


def test_a_suggestion_after_the_result_reaches_the_window_before_the_end(tmp_path):
    suggestion = ev(type="prompt_suggestion", suggestion="write the script")
    process = LiveProcess([RUNNING, INIT, ev(type="result", subtype="success", result="Done."),
                           IDLE, suggestion])
    events, done = [], threading.Event()

    def record(event):
        events.append(event)
        if event.kind in ("finished", "failed"):
            done.set()
    runner = TurnRunner(["claude", "-p"], str(tmp_path), "Name it", record,
                        popen=lambda cmd, **k: process, env={"PATH": "x"}, timer=FakeTimers(),
                        suggestions=True)
    runner.start()
    assert done.wait(5)
    kinds = [e.kind for e in events]
    assert "suggestion" in kinds and kinds.index("suggestion") < kinds.index("finished")
    assert events[kinds.index("suggestion")].text == "write the script"


def test_what_is_said():
    assert announce.suggestion_text("write the script") == (
        "Suggestion: write the script. Down Arrow in the message box gets to it.")
    assert announce.suggestion_text("Is it done?").startswith("Suggestion: Is it done? Down")
    long = announce.suggestion_text("word " * 100)
    assert "…" in long and len(long) < 260


def test_the_setting_is_off_until_turned_on(tmp_path):
    path = tmp_path / "speech.json"
    assert SpeechSettings.load(path).prompt_suggestions is False
    settings = SpeechSettings.load(path)
    settings.prompt_suggestions = True
    settings.save(path)
    assert SpeechSettings.load(path).prompt_suggestions is True
    path.write_text(json.dumps({"prompt_suggestions": "yes"}), encoding="utf-8")
    assert SpeechSettings.load(path).prompt_suggestions is False
