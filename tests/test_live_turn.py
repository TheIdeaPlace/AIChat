"""Live control of a running Claude turn, and the turn events The Chat Place
used to ignore (#468): the parser, the runner's control requests, the words
said, and thinking in the transcript. Windowless.

The stream-json lines marked "captured" are as Claude Code 2.1.296 sent
them in real Haiku turns (ids shortened); the rest follow its schema."""
import json
import threading

import pytest

from thechatplace import claude_cli as cli
from thechatplace import export, live_turn, properties
from thechatplace.claude_cli import StreamParser, TurnRunner
from thechatplace.sessions import OWN, SessionInfo
from thechatplace.transcript import (ASSISTANT, THINKING, TOOL, last_message_from_tail,
                                     parse_lines)

from records import assistant_block, lines, text_block, thinking_block, tool_use_block, user_text
from test_claude_cli import FakeTimers, LiveProcess, ev

# -- Captured lines ------------------------------------------------------------------------

STATUS_PLAN = ('{"type":"system","subtype":"status","status":null,"permissionMode":"plan",'
               '"uuid":"da542e45","session_id":"2986d507"}')  # captured
STATUS_DEFAULT = ('{"type":"system","subtype":"status","status":null,'
                  '"permissionMode":"default","uuid":"5","session_id":"d3"}')  # captured
RETRY = ('{"type":"system","subtype":"api_retry","attempt":2,"max_retries":10,'
         '"retry_delay_ms":1018,"error_status":null,"error":"unknown",'
         '"session_id":"6929d593","uuid":"2420d74d"}')  # captured (connection refused)
BACKGROUND = ('{"type":"system","subtype":"background_tasks_changed","tasks":[{"task_id":'
              '"b375k20qt","run_id":"0mvx688zk-83277160","task_type":"local_bash",'
              '"description":"Sleep 40 seconds in background"}],"uuid":"a4","session_id":"29"}')
NO_BACKGROUND = ('{"type":"system","subtype":"background_tasks_changed","tasks":[],'
                 '"uuid":"8c","session_id":"29"}')  # captured


def answer(request_id, ok=True, response=None, error="", **extra):
    """A control_response as Claude Code sends it: set_model's success has no
    response (captured), set_permission_mode's {"mode": …}, stop_task's {}."""
    body = {"subtype": "success" if ok else "error", "request_id": request_id}
    if ok and response is not None:
        body["response"] = response
    if not ok:
        body["error"] = error
    body.update(extra)
    return json.dumps({"type": "control_response", "response": body})


# -- The parser ----------------------------------------------------------------------------


def test_a_status_with_a_mode_is_a_mode_event_and_default_is_manual():
    parser = StreamParser()
    [event] = parser.feed(STATUS_PLAN)
    assert (event.kind, event.text) == ("mode", "plan")
    assert parser.feed(STATUS_DEFAULT)[0].text == "manual"
    # A status without a mode (compacting) is nothing here.
    assert parser.feed(ev(type="system", subtype="status", status="compacting")) == []


def test_api_retry_is_a_retry_event_with_its_counts():
    [event] = StreamParser().feed(RETRY)
    assert event.kind == "retry"
    assert event.data == {"attempt": 2, "max_retries": 10, "retry_delay_ms": 1018,
                          "error_status": None, "error": "unknown"}
    odd = StreamParser().feed(ev(type="system", subtype="api_retry", attempt="x",
                                 error_status="529", error=5))[0]
    assert odd.data == {"attempt": 0, "max_retries": 0, "retry_delay_ms": 0,
                        "error_status": None, "error": ""}


def test_model_fallback_and_memory_saved_events():
    [fallback] = StreamParser().feed(ev(
        type="system", subtype="model_fallback", trigger="overloaded",
        original_model="claude-opus-5-5", fallback_model="claude-sonnet-5-5",
        content="Switched", uuid="u", session_id="s"))
    assert fallback.kind == "fallback"
    assert fallback.data["fallback_model"] == "claude-sonnet-5-5"
    [memory] = StreamParser().feed(ev(
        type="system", subtype="memory_saved", written_paths=["C:/m/build-steps.md", 3],
        uuid="u", session_id="s"))
    assert memory.kind == "memory"
    assert memory.data == {"paths": ["C:/m/build-steps.md"], "verb": "Saved"}


def test_answers_to_our_control_requests_are_matched_and_others_ignored():
    parser = StreamParser()
    rid = cli.CONTROL_REQUEST_PREFIX + "set_model-1"
    [ok] = parser.feed(answer(rid))
    assert ok.kind == "control_answer"
    assert ok.data == {"request_id": rid, "ok": True, "response": {}, "error": ""}
    [bad] = parser.feed(answer(rid, ok=False, error="Model 'x' not found",
                               error_code="catalog_unknown"))  # captured shape
    assert bad.data["ok"] is False and bad.data["error"] == "Model 'x' not found"
    assert parser.feed(answer("someone-else")) == []


def test_background_entries_carry_the_task_id():
    [event] = StreamParser().feed(BACKGROUND)
    assert event.data["entries"] == [{"task_id": "b375k20qt", "task_type": "local_bash",
                                      "description": "Sleep 40 seconds in background"}]


def test_control_lines():
    line = json.loads(cli.control_line("r1", "set_model", model="sonnet"))
    assert line == {"type": "control_request", "request_id": "r1",
                    "request": {"subtype": "set_model", "model": "sonnet"}}
    assert json.loads(cli.control_line("r2", "get_context_usage"))["request"] == {
        "subtype": "get_context_usage"}


# -- The runner ----------------------------------------------------------------------------

RUNNING = ev(type="system", subtype="session_state_changed", state="running")
IDLE = ev(type="system", subtype="session_state_changed", state="idle")
INIT = ev(type="system", subtype="init", session_id="s1", apiKeySource="none",
          model="claude-haiku-5-5")
REPLY = ev(type="assistant", message={"model": "claude-haiku-5-5",
                                      "content": [{"type": "text", "text": "Working."}]})
RESULT = ev(type="result", subtype="success", result="Done.")


def last_request(process, subtype):
    """The id of the latest control request of ``subtype`` written."""
    for line in reversed(process.sent()):
        if line.get("type") == "control_request" and line["request"]["subtype"] == subtype:
            return line["request_id"], line["request"]
    raise AssertionError(f"no {subtype} request written")


def live_turn_run(tmp_path, lines_, command=None, **kwargs):
    process = LiveProcess(lines_)
    timers = FakeTimers()
    events, done = [], threading.Event()

    def on_event(event):
        events.append(event)
        if event.kind in ("finished", "failed"):
            done.set()
    runner = TurnRunner(command or ["claude", "-p", "--model", "haiku"], str(tmp_path),
                        "Build it", on_event, popen=lambda cmd, **k: process,
                        env={"PATH": "x"}, timer=timers, **kwargs)
    runner.start()
    return runner, process, timers, events, done


def wait(condition):
    for _ in range(250):
        if condition():
            return True
        threading.Event().wait(0.02)
    return False


def test_set_model_goes_into_the_running_turn_and_its_answer_comes_back(tmp_path):
    go, answered = threading.Event(), threading.Event()
    lines_ = [RUNNING, INIT, REPLY, go,
              lambda p: answer(last_request(p, "set_model")[0]), answered, RESULT, IDLE]
    runner, process, _timers, events, done = live_turn_run(tmp_path, lines_)
    assert wait(lambda: any(e.kind == "text" for e in events))
    heard = []
    assert runner.set_model("sonnet", lambda ok, response, error: (heard.append(ok),
                                                                  answered.set()))
    _rid, request = last_request(process, "set_model")
    assert request == {"subtype": "set_model", "model": "sonnet"}
    go.set()
    assert done.wait(5)
    assert heard == [True]
    assert runner.chosen == "sonnet"  # the Fable checks now read the new choice
    assert "control_answer" not in [e.kind for e in events]  # to its caller only


def test_a_refused_set_model_says_why_and_keeps_the_choice(tmp_path):
    go, answered = threading.Event(), threading.Event()
    lines_ = [RUNNING, INIT, REPLY, go,
              lambda p: answer(last_request(p, "set_model")[0], ok=False,
                               error="Model 'x' not found"), answered, RESULT, IDLE]
    runner, _process, _timers, events, done = live_turn_run(tmp_path, lines_)
    assert wait(lambda: any(e.kind == "text" for e in events))
    heard = []
    runner.set_model("opus", lambda ok, response, error: (heard.append((ok, error)),
                                                         answered.set()))
    go.set()
    assert done.wait(5)
    assert heard == [(False, "Model 'x' not found")]
    assert runner.chosen == "haiku"


def test_default_is_sent_as_default_and_never_to_a_fable_default(tmp_path):
    go = threading.Event()
    runner, process, _t, events, done = live_turn_run(tmp_path, [RUNNING, INIT, REPLY, go,
                                                                 RESULT, IDLE])
    assert wait(lambda: any(e.kind == "text" for e in events))
    runner.parser.resolved_models = {"default": "claude-opus-5-5"}
    assert runner.set_model("", lambda *a: None)
    assert last_request(process, "set_model")[1]["model"] == "default"
    runner.parser.resolved_models = {"default": "claude-fable-5"}
    assert runner.set_model("", lambda *a: None) is False  # waits for the next turn's check
    runner.parser.resolved_models = {}
    # Before initialize is answered, what the default is isn't known.
    assert runner.set_model("", lambda *a: None) is False
    assert runner.set_model("sonnet", lambda *a: None)
    go.set()
    assert done.wait(5)


def test_a_status_repeating_the_turns_mode_isnt_a_change(tmp_path):
    same = ('{"type":"system","subtype":"status","status":"compacting",'
            '"permissionMode":"acceptEdits"}')
    _r, _p, _t, events, done = live_turn_run(
        tmp_path, [RUNNING, ev(type="system", subtype="init", session_id="s1",
                               apiKeySource="none", permissionMode="acceptEdits"),
                   same, STATUS_PLAN, STATUS_PLAN, REPLY, RESULT, IDLE],
        command=["claude", "-p", "--permission-mode", "acceptEdits"])
    assert done.wait(5)
    assert [e.text for e in events if e.kind == "mode"] == ["plan"]


def test_a_steer_while_the_end_waits_for_the_context_keeps_the_turn_going(tmp_path):
    """Sent inside the short wait for get_context_usage: answered, then the
    next idle asks again and the turn ends on that answer."""
    go = threading.Event()
    usage = {"categories": [{"name": "Messages", "tokens": 5, "kind": "used"}]}
    lines_ = [RUNNING, INIT, REPLY, RESULT, IDLE, go,
              lambda p: answer(last_request(p, "get_context_usage")[0], response=usage),
              lambda p: _echo_last(p), RUNNING, REPLY, RESULT, IDLE,
              lambda p: answer(last_request(p, "get_context_usage")[0], response=usage)]
    runner, process, _t, events, done = live_turn_run(tmp_path, lines_, context_usage=True)
    assert wait(lambda: sum(1 for l in process.sent()
                            if l.get("request", {}).get("subtype") == "get_context_usage"))
    assert runner.steer("Also the README")
    go.set()
    assert done.wait(5)
    asked = [l for l in process.sent() if l.get("request", {}).get("subtype")
             == "get_context_usage"]
    assert len(asked) == 2
    assert events[-1].kind == "finished"


def _echo_last(process):
    """Claude Code echoing the latest user message we wrote."""
    sent = [l for l in process.sent() if l.get("type") == "user"]
    message = sent[-1]
    return json.dumps({"type": "user", "isReplay": True, "uuid": message["uuid"],
                       "message": message["message"]})


def test_background_work_started_while_the_end_waits_waits_for_it(tmp_path):
    go = threading.Event()
    lines_ = [RUNNING, INIT, REPLY, RESULT, IDLE, go, BACKGROUND, NO_BACKGROUND, RUNNING,
              REPLY, RESULT, IDLE,
              lambda p: answer(last_request(p, "get_context_usage")[0], response={})]
    runner, process, timers, events, done = live_turn_run(tmp_path, lines_,
                                                          context_usage=True)
    assert wait(lambda: any(t.interval == cli.CONTEXT_WAIT for t in timers.made))
    go.set()
    assert done.wait(5)
    kinds = [e.kind for e in events]
    assert "waiting" in kinds  # the reply said with the work, as without the wait
    assert kinds[-1] == "finished"


def test_set_permission_mode_sends_the_mode_and_status_reaches_the_window(tmp_path):
    go, answered = threading.Event(), threading.Event()
    lines_ = [RUNNING, INIT, REPLY, go,
              lambda p: answer(last_request(p, "set_permission_mode")[0],
                               response={"mode": "acceptEdits"}),
              '{"type":"system","subtype":"status","status":null,"permissionMode":'
              '"acceptEdits","uuid":"0","session_id":"s1"}', answered, RESULT, IDLE]
    runner, process, _timers, events, done = live_turn_run(tmp_path, lines_)
    assert wait(lambda: any(e.kind == "text" for e in events))
    heard = []
    assert runner.set_permission_mode("acceptEdits", lambda ok, r, e: (heard.append(r),
                                                                      answered.set()))
    assert last_request(process, "set_permission_mode")[1]["mode"] == "acceptEdits"
    go.set()
    assert done.wait(5)
    assert heard == [{"mode": "acceptEdits"}]
    assert [e.text for e in events if e.kind == "mode"] == ["acceptEdits"]
    assert runner.set_permission_mode("bogus", lambda *a: None) is False


def test_control_requests_fail_when_unanswered_or_the_turn_ends(tmp_path):
    go = threading.Event()
    runner, _p, timers, events, done = live_turn_run(tmp_path, [RUNNING, INIT, REPLY, go,
                                                                RESULT, IDLE])
    assert wait(lambda: any(e.kind == "text" for e in events))
    heard = []
    runner.set_model("sonnet", lambda ok, r, e: heard.append(("first", ok, e)))
    timers.made[-1].function()  # its timeout: no answer in time
    runner.set_model("opus", lambda ok, r, e: heard.append(("second", ok, e)))
    go.set()
    assert done.wait(5)
    assert heard == [("first", False, "Claude Code didn't answer"),
                     ("second", False, "the turn ended")]
    assert runner.set_model("opus", lambda *a: heard.append("late")) is False
    assert "late" not in heard


def test_stop_task_names_the_task_and_the_turn_ends_once_its_work_is_gone(tmp_path):
    """As captured: the background list empties before stop_task's empty
    success, then Claude Code says idle again and the turn ends."""
    go = threading.Event()
    lines_ = [RUNNING, INIT, BACKGROUND, REPLY, RESULT, IDLE, go, NO_BACKGROUND,
              lambda p: answer(last_request(p, "stop_task")[0], response={}), IDLE]
    runner, process, _timers, events, done = live_turn_run(tmp_path, lines_)
    assert wait(lambda: runner.waiting_on_background)
    [entry] = runner.background_entries
    assert entry["task_id"] == "b375k20qt" and isinstance(entry["since"], float)
    heard = []
    assert runner.stop_task("b375k20qt", lambda ok, r, e: heard.append(ok))
    assert last_request(process, "stop_task")[1] == {"subtype": "stop_task",
                                                      "task_id": "b375k20qt"}
    go.set()
    assert done.wait(5)
    assert heard == [True]
    assert runner.background_entries == []
    assert runner.stop_task("", lambda *a: None) is False


def test_a_tasks_start_time_is_kept_across_list_changes(tmp_path):
    second = ('{"type":"system","subtype":"background_tasks_changed","tasks":['
              '{"task_id":"b375k20qt","task_type":"local_bash","description":"Sleep"},'
              '{"task_id":"b2","task_type":"local_bash","description":"Build"}]}')
    go, more = threading.Event(), threading.Event()
    now = [100.0]
    runner, _p, _t, _events, done = live_turn_run(
        tmp_path, [RUNNING, INIT, BACKGROUND, go, second, more, REPLY, NO_BACKGROUND,
                   RESULT, IDLE], clock=lambda: now[0])
    assert wait(lambda: len(runner.background_entries) == 1)
    assert runner.background_entries[0]["since"] == 100.0
    now[0] = 160.0
    go.set()
    assert wait(lambda: len(runner.background_entries) == 2)
    assert [e["since"] for e in runner.background_entries] == [100.0, 160.0]
    more.set()
    assert done.wait(5)


def test_the_context_breakdown_is_asked_for_as_the_turn_ends(tmp_path):
    usage = {"categories": [{"name": "System prompt", "tokens": 2362, "kind": "used"}],
             "totalTokens": 2362, "maxTokens": 1000000}
    lines_ = [RUNNING, INIT, REPLY, RESULT, IDLE,
              lambda p: answer(last_request(p, "get_context_usage")[0], response=usage)]
    runner, process, timers, events, done = live_turn_run(tmp_path, lines_, context_usage=True)
    assert done.wait(5)
    assert runner.context_usage == usage
    context = [e for e in events if e.kind == "context"]
    assert context and context[0].data == usage
    assert events[-1].kind == "finished"
    assert any(t.interval == cli.CONTEXT_WAIT for t in timers.made)


def test_a_refused_or_unanswered_context_request_still_ends_the_turn(tmp_path):
    lines_ = [RUNNING, INIT, REPLY, RESULT, IDLE,
              lambda p: answer(last_request(p, "get_context_usage")[0], ok=False,
                               error="Unsupported control request subtype")]
    runner, _p, _timers, events, done = live_turn_run(tmp_path, lines_, context_usage=True)
    assert done.wait(5)
    assert runner.context_usage is None and events[-1].kind == "finished"
    # No answer at all: the short wait ends it.
    runner2, _p2, timers2, events2, done2 = live_turn_run(tmp_path, [RUNNING, INIT, REPLY,
                                                                     RESULT, IDLE],
                                                          context_usage=True)
    assert wait(lambda: any(t.interval == cli.CONTEXT_WAIT for t in timers2.made))
    [wait_timer] = [t for t in timers2.made if t.interval == cli.CONTEXT_WAIT]
    wait_timer.function()
    assert done2.wait(5)
    assert events2[-1].kind == "finished"


def test_no_context_request_unless_asked_for(tmp_path):
    runner, process, _t, _events, done = live_turn_run(tmp_path, [RUNNING, INIT, REPLY,
                                                                  RESULT, IDLE])
    assert done.wait(5)
    assert not any(line.get("request", {}).get("subtype") == "get_context_usage"
                   for line in process.sent())


def test_a_retry_is_kept_for_turn_status_until_the_turn_moves_on(tmp_path):
    go = threading.Event()
    runner, _p, _t, events, done = live_turn_run(tmp_path, [RUNNING, INIT, RETRY, go, REPLY,
                                                            RESULT, IDLE])
    assert wait(lambda: runner.retry is not None)
    assert runner.retry["attempt"] == 2 and runner.last_activity == "retrying"
    go.set()
    assert done.wait(5)
    assert runner.retry is None
    assert [e.kind for e in events].count("retry") == 1


@pytest.mark.parametrize("line", [
    ev(type="system", subtype="model_fallback", trigger="overloaded",
       original_model="claude-opus-5-5", fallback_model="claude-fable-5", content=""),
    ev(type="assistant", message={"model": "claude-fable-5",
                                  "content": [{"type": "text", "text": "Hi"}]}),
])
def test_a_turn_that_moves_to_fable_mid_turn_is_stopped(tmp_path, line):
    """A fallback or plan mode can change the model mid-turn, with no new
    system/init; Fable unchosen is stopped all the same (#58)."""
    runner, _p, _t, events, done = live_turn_run(tmp_path, [RUNNING, INIT, line, RESULT, IDLE],
                                                 command=["claude", "-p", "--model", "opus"])
    assert done.wait(5)
    assert events[-1].kind == "failed" and "Fable" in events[-1].text
    assert runner.stopped_mid_turn and not runner.stopped_before_answer
    assert "when it moved to Fable" in events[-1].text


def test_a_move_to_fable_after_the_reply_was_said_keeps_its_explanation(tmp_path):
    """Claude answered with background work running; the turn it starts when
    the work finishes falls back to Fable. The Fable explanation is the end,
    not a generic exit error, and the long-answered message isn't given back."""
    background = ev(type="system", subtype="background_tasks_changed", tasks=[
        {"task_id": "b1", "task_type": "local_bash", "description": "Build"}])
    fallback = ev(type="system", subtype="model_fallback", trigger="overloaded",
                  original_model="claude-opus-5-5", fallback_model="claude-fable-5")
    runner, _p, _t, events, done = live_turn_run(
        tmp_path, [RUNNING, INIT, background, REPLY, RESULT, IDLE, NO_BACKGROUND, RUNNING,
                   fallback, RESULT, IDLE], command=["claude", "-p", "--model", "opus"])
    assert done.wait(5)
    assert "waiting" in [e.kind for e in events]
    assert events[-1].kind == "failed" and "Fable" in events[-1].text
    assert runner.stopped_mid_turn and not runner.stopped_before_answer


def test_a_fallback_to_another_model_is_passed_on(tmp_path):
    line = ev(type="system", subtype="model_fallback", trigger="overloaded",
              original_model="claude-opus-5-5", fallback_model="claude-sonnet-5-5")
    _r, _p, _t, events, done = live_turn_run(tmp_path, [RUNNING, INIT, line, REPLY, RESULT,
                                                        IDLE])
    assert done.wait(5)
    assert [e.kind for e in events if e.kind == "fallback"] == ["fallback"]
    assert events[-1].kind == "finished"


# -- The words -----------------------------------------------------------------------------


def test_retry_words():
    data = StreamParser().feed(RETRY)[0].data
    assert live_turn.retry_text(data) == ("Claude is retrying: it couldn't reach the service. "
                                          "Attempt 2 of 10, next in 1 second.")
    assert live_turn.retry_text({"attempt": 3, "max_retries": 10, "retry_delay_ms": 8000,
                                 "error": "overloaded", "error_status": 529}) == (
        "Claude is retrying: the service is overloaded. Attempt 3 of 10, next in 8 seconds.")
    assert live_turn.retry_text({"error_status": 500}) == \
        "Claude is retrying: the service answered 500."
    assert live_turn.retry_status_text(data) == \
        "Claude is retrying: it couldn't reach the service, attempt 2 of 10."
    assert live_turn.retry_status_text(None) == ""


def test_retries_are_said_first_then_at_most_every_30_seconds():
    throttle = live_turn.RetryThrottle()
    assert throttle.should_speak("a", 100.0)
    assert not throttle.should_speak("a", 110.0)
    assert throttle.should_speak("b", 110.0)  # another chat's turn
    assert not throttle.should_speak("a", 129.9)
    assert throttle.should_speak("a", 130.0)
    throttle.turn_ended("a")
    assert throttle.should_speak("a", 131.0)  # a new turn's first


def test_fallback_and_memory_words():
    assert live_turn.fallback_text({"trigger": "overloaded", "original_model": "claude-opus-5-5",
                                    "fallback_model": "claude-sonnet-5-5"}) == \
        "Claude switched to Sonnet 5.5 because Opus 5.5 was overloaded."
    assert live_turn.fallback_text({"fallback_model": "claude-sonnet-5-5"}) == \
        "Claude switched to Sonnet 5.5 for this turn."
    assert live_turn.memory_text({"paths": ["C:\\u\\memory\\build-steps.md"]}) == \
        "Claude saved a memory: build steps."
    assert live_turn.memory_text({"paths": ["/m/a_b.md", "/m/c.md"], "verb": "Updated"}) == \
        "Claude updated 2 memories: a b, c."
    assert live_turn.memory_text({}) == "Claude saved a memory."


def test_background_rows_and_stop_results():
    assert live_turn.background_choice({"description": "Windows build", "since": 10.0},
                                       190.0) == "Windows build, running 3 minutes"
    assert live_turn.background_choice({"description": "Watch CI"}, 5.0) == "Watch CI"
    assert live_turn.stop_result_text("Windows build", True, False) == "Stopped Windows build."
    assert "still running" in live_turn.stop_result_text("X", True, True)
    assert live_turn.stop_result_text("X", False, True, "nope") == "Couldn't stop X: nope."


def test_context_lines_from_a_captured_answer():
    usage = {"categories": [
        {"name": "System prompt", "tokens": 3183, "kind": "used"},
        {"name": "MCP tools (deferred)", "tokens": 30577, "isDeferred": True, "kind": "deferred"},
        {"name": "Memory files", "tokens": 1279, "kind": "used"},
        {"name": "Skills", "tokens": 9056, "kind": "used"},
        {"name": "Empty", "tokens": 0, "kind": "used"},
        {"name": "Autocompact buffer", "tokens": 33000, "kind": "buffer"},
        {"name": "Free space", "tokens": 927833, "kind": "free"}],
        "totalTokens": 39167, "maxTokens": 1000000}
    assert live_turn.context_lines(usage) == [
        "What fills the context, when the latest turn ended:",
        "System prompt: 3,183 tokens",
        "MCP tools: 30,577 tokens (loaded when needed)",
        "Memory files: 1,279 tokens",
        "Skills: 9,056 tokens",
        "Autocompact buffer: 33,000 tokens (kept free for compacting)",
        "Free space: 927,833 tokens"]
    assert live_turn.context_lines(None) == []
    assert live_turn.context_lines({"categories": "x"}) == []
    assert live_turn.context_lines({"categories": [{"name": "x", "tokens": "1"}]}) == []


# -- Thinking in the transcript ----------------------------------------------------------


def thinking_chat():
    return parse_lines(lines(
        user_text("Why is the build red?"),
        assistant_block(thinking_block(), "m1"),                      # signed, empty
        assistant_block(thinking_block("Check the CI log first."), "m1"),
        assistant_block(text_block("Looking."), "m1"),
        assistant_block(tool_use_block("Bash", {"command": "gh run view"}), "m1"),
        assistant_block(text_block("It's a flaky test."), "m2"),
        assistant_block(thinking_block("Maybe mention the retry."), "m2"),
        assistant_block(text_block("Rerun it."), "m2"),
    ))


def test_thinking_with_text_is_activity_and_empty_thinking_is_left_out():
    tr = thinking_chat()
    thoughts = [m for m in tr.messages if m.kind == THINKING]
    assert [m.text for m in thoughts] == ["Check the CI log first.", "Maybe mention the retry."]
    assert all(m.is_activity for m in thoughts)
    assert thoughts[0].label == "Claude, thinking"
    assert thoughts[0].list_line() == "Claude, thinking: Check the CI log first."
    assert THINKING not in [m.kind for m in tr.visible()]
    assert THINKING in [m.kind for m in tr.visible(show_activity=True)]


def test_thinking_doesnt_split_a_reply_or_count_as_one():
    tr = thinking_chat()
    replies = [m.text for m in tr.visible() if m.kind == ASSISTANT]
    assert replies == ["Looking.", "It's a flaky test.\n\nRerun it."]
    assert tr.last_reply().text == "It's a flaky test.\n\nRerun it."
    assert len({m.key for m in tr.messages}) == len(tr.messages)


def test_thinking_isnt_the_last_message(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(lines(user_text("Go"),
                                    assistant_block(text_block("Done."), "m1"),
                                    assistant_block(thinking_block("Anything else?"), "m2")))
                    + "\n", encoding="utf-8")
    assert last_message_from_tail(path).text == "Done."


def test_thinking_exports_with_activity_and_counts_in_properties():
    tr = thinking_chat()
    shown = tr.visible(show_activity=True)
    text = export.to_markdown("Build", shown)
    assert "## Claude, thinking, " in text and "\n\nCheck the CI log first.\n" in text
    assert "thinking" not in export.to_markdown("Build", tr.visible())
    info = SessionInfo(source=OWN, key="own:a", title="Build", cwd="/work",cli_session_id="a")
    lines_ = properties.session_properties(info, tr)
    assert "Thinking you can read: 2" in lines_
    assert not any(line.startswith("Thinking")
                   for line in properties.session_properties(info, parse_lines(lines(
                       user_text("Hi"), assistant_block(text_block("Hello"), "m1")))))
    assert sum(1 for m in tr.messages if m.kind == TOOL) == 1


# -- With a real child process (tests/fake_claude.py) -------------------------------------


def run_fake(tmp_path, mode, act=None, refuse="", context_usage=False):
    """A turn against fake_claude.py in ``mode``; ``act(runner, event)`` is
    called on each event, from the worker thread."""
    import sys
    from test_claude_cli import FAKE_CLAUDE
    log = tmp_path / "fake.log"
    env = cli.child_environment({**cli.child_environment(), "FAKE_CLAUDE_LOG": str(log),
                                 "FAKE_CLAUDE_MODE": mode, "FAKE_CLAUDE_REFUSE": refuse})
    events, done, box = [], threading.Event(), {}

    def on_event(event):
        events.append(event)
        if act:
            act(box["runner"], event)
        if event.kind in ("finished", "failed"):
            done.set()
    runner = TurnRunner([sys.executable, str(FAKE_CLAUDE)], str(tmp_path), "go", on_event,
                        env=env, context_usage=context_usage)
    box["runner"] = runner
    runner.start()
    assert done.wait(30)
    runner.join(10)
    return runner, events, json.loads(log.read_text(encoding="utf-8"))


@pytest.mark.parametrize("refuse, expected", [("", True), ("set_model", False)])
def test_fake_claude_takes_set_model_mid_turn_or_refuses_it(tmp_path, refuse, expected):
    heard = []

    def act(runner, event):
        if event.kind == "text" and event.text == "Working on it.":
            runner.set_model("sonnet", lambda ok, r, e: heard.append((ok, e)))
    runner, events, seen = run_fake(tmp_path, "live", act, refuse=refuse)
    assert seen["controls"] == ["set_model"]
    assert heard[0][0] is expected
    assert events[-1].kind == "finished"
    assert runner.chosen == ("sonnet" if expected else "")


def test_fake_claude_retries_are_events(tmp_path):
    _runner, events, _seen = run_fake(tmp_path, "retry")
    assert [e.data["attempt"] for e in events if e.kind == "retry"] == [1, 2]
    assert events[-1].kind == "finished"


def test_fake_claude_background_task_stopped_by_id_ends_the_turn(tmp_path):
    heard = []

    def act(runner, event):
        if event.kind == "waiting":
            [entry] = runner.background_entries
            runner.stop_task(entry["task_id"], lambda ok, r, e: heard.append(
                (ok, list(runner.background_entries))))
    runner, events, seen = run_fake(tmp_path, "background", act, context_usage=True)
    assert heard == [(True, [])]
    assert seen["controls"][:2] == ["stop_task", "get_context_usage"]
    assert events[-1].kind == "finished" and events[-1].data == {"after_background": True}
    assert runner.context_usage["categories"][0]["name"] == "System prompt"
