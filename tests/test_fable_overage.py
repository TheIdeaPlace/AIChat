"""Fable on a subscription and the pause at usage credits (#722): which
plans are offered Fable, and a turn stopped as soon as Claude Code says it's
charged to usage credits. The rate_limit_event lines are shaped as captured
from Claude Code 2.1.331 on 2026-11-25 (Max plan, usage credits off); the
``isUsingOverage: true`` ones change only that field and ``overageStatus``."""
import json

import pytest

from thechatplace import claude_cli as cli

from test_claude_cli import ev
from test_live_turn import IDLE, RESULT, RUNNING, live_turn_run

FABLE = ["claude", "-p", "--model", "fable"]
FABLE_INIT = ev(type="system", subtype="init", session_id="s1", apiKeySource="none",
                model="claude-fable-5-2")
FABLE_TOOL = ev(type="assistant", message={"model": "claude-fable-5-2", "content": [
    {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "f1.txt"}}]})
FABLE_REPLY = ev(type="assistant", message={"model": "claude-fable-5-2",
                                            "content": [{"type": "text", "text": "Working."}]})
OPUS_REPLY = ev(type="assistant", message={"model": "claude-opus-5-5",
                                           "content": [{"type": "text", "text": "Working."}]})


def limits(overage: bool) -> str:
    info = {"status": "allowed", "resetsAt": 1795852800, "rateLimitType": "seven_day",
            "overageStatus": "allowed" if overage else "rejected",
            "isUsingOverage": overage,
            "unifiedWindows": {"seven_day": {"utilization": 1.0 if overage else 0.34,
                                             "resetsAt": 1795852800, "status": "allowed"}}}
    if not overage:
        info["overageDisabledReason"] = "org_level_disabled"
    return json.dumps({"type": "rate_limit_event", "rate_limit_info": info,
                       "uuid": "9e", "session_id": "s1"})


# -- the rules ---------------------------------------------------------------------------------

def test_only_max_and_ultra_get_fable():
    assert cli.fable_in_plan("max") and cli.fable_in_plan("ultra")
    for plan in ("", "pro", "team", "enterprise", None, "max5"):
        assert not cli.fable_in_plan(plan), plan


def test_overage_problem_only_for_a_paused_model_on_the_subscription_using_overage():
    on, off = json.loads(limits(True))["rate_limit_info"], json.loads(limits(False))["rate_limit_info"]
    text = cli.overage_problem(on, "claude-fable-5-2")
    assert text and "usage credits" in text and "Fable allowance" in text
    assert "claude-fable" not in text
    assert cli.overage_problem(off, "claude-fable-5-2") is None
    assert cli.overage_problem(on, "claude-opus-5-5") is None       # Fable first (#722)
    assert cli.overage_problem(on, "fable", billing="api") is None  # charged that way anyway
    assert cli.overage_problem(on, "fable", allowed=True) is None   # you chose to keep going
    for odd in (None, {}, {"isUsingOverage": "true"}, {"isUsingOverage": 1}, "x"):
        assert cli.overage_problem(odd, "fable") is None, odd


# -- the turn ----------------------------------------------------------------------------------

def test_a_fable_turn_is_stopped_as_soon_as_it_is_charged_to_usage_credits(tmp_path):
    """The first request was in the plan; the second tipped it over, and its
    event comes mid-turn (Claude Code sends one whenever isUsingOverage
    changes). Nothing after it is read."""
    runner, process, _t, events, done = live_turn_run(
        tmp_path, [RUNNING, FABLE_INIT, FABLE_TOOL, limits(False), FABLE_TOOL, limits(True),
                   FABLE_REPLY, RESULT, IDLE], command=FABLE)
    assert done.wait(5)
    final = events[-1]
    assert final.kind == "failed" and final.is_error and "usage credits" in final.text
    assert runner.stopped_for_overage and runner.stopped_mid_turn
    assert not runner.stopped_before_answer       # answered in part: not given back
    assert runner.overage_model == "claude-fable-5-2"
    # Usage still hears both reports, so Tools, Usage says so.
    reported = [e.data["isUsingOverage"] for e in events if e.kind == "limits"]
    assert reported == [False, True]
    assert not any(e.kind == "text" and e.text == "Working." for e in events)


def test_a_fable_turn_inside_the_plan_runs_to_the_end(tmp_path):
    runner, _p, _t, events, done = live_turn_run(
        tmp_path, [RUNNING, FABLE_INIT, FABLE_TOOL, limits(False), FABLE_REPLY, RESULT, IDLE],
        command=FABLE)
    assert done.wait(5)
    assert events[-1].kind == "finished" and not runner.stopped_for_overage


def test_keep_going_runs_that_one_turn_on_usage_credits(tmp_path):
    runner, _p, _t, events, done = live_turn_run(
        tmp_path, [RUNNING, FABLE_INIT, FABLE_REPLY, limits(True), RESULT, IDLE],
        command=FABLE, allow_overage=True)
    assert done.wait(5)
    assert events[-1].kind == "finished" and not runner.stopped_for_overage


def test_overage_after_the_answer_keeps_the_answer(tmp_path):
    """Answered, then a later request (a suggested message, say) went over
    while the turn waited to be idle: the answer stands, and the process is
    stopped so nothing more is charged; the next Send asks."""
    runner, _p, _t, events, done = live_turn_run(
        tmp_path, [RUNNING, FABLE_INIT, FABLE_REPLY, RESULT, limits(True), IDLE],
        command=FABLE)
    assert done.wait(5)
    assert events[-1].kind == "finished" and not events[-1].is_error
    assert runner.stopped_for_overage and not runner.stopped_mid_turn


def test_other_models_are_not_paused_yet(tmp_path):
    """Opus past the limits with usage credits on: noted in Usage as before,
    not stopped. Adding it to OVERAGE_PAUSED is a separate issue."""
    init = ev(type="system", subtype="init", session_id="s1", apiKeySource="none",
              model="claude-opus-5-5")
    runner, _p, _t, events, done = live_turn_run(
        tmp_path, [RUNNING, init, OPUS_REPLY, limits(True), RESULT, IDLE],
        command=["claude", "-p", "--model", "opus"])
    assert done.wait(5)
    assert events[-1].kind == "finished" and not runner.stopped_for_overage


def test_the_chosen_model_counts_when_an_agent_writes_on_it(tmp_path):
    """A chat on Fable whose reply so far came from another model (an agent
    of its own, or plan mode's): the chat chose Fable, so the pause holds."""
    runner, _p, _t, events, done = live_turn_run(
        tmp_path, [RUNNING, FABLE_INIT, OPUS_REPLY, limits(True), RESULT, IDLE],
        command=FABLE)
    assert done.wait(5)
    assert events[-1].kind == "failed" and runner.stopped_for_overage


def test_unchosen_fable_is_still_stopped_on_a_max_plan(tmp_path):
    """#58 stands: only a Fable you chose runs."""
    runner, _p, _t, events, done = live_turn_run(
        tmp_path, [RUNNING, FABLE_INIT, FABLE_REPLY, RESULT, IDLE],
        command=["claude", "-p", "--model", "opus"])
    assert done.wait(5)
    assert events[-1].kind == "failed" and "Fable" in events[-1].text
    assert not runner.stopped_for_overage


@pytest.mark.parametrize("lines_", [
    [RUNNING, FABLE_INIT, FABLE_REPLY, RESULT, IDLE],
    [RUNNING, FABLE_INIT, FABLE_REPLY, limits(False), RESULT, IDLE],
])
def test_a_chosen_fable_turn_runs(tmp_path, lines_):
    _runner, _p, _t, events, done = live_turn_run(tmp_path, lines_, command=FABLE)
    assert done.wait(5)
    assert events[-1].kind == "finished" and not events[-1].is_error
