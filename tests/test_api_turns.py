"""Turns and asides on API credits (#658), and the subscription's new check
before sending. Windowless: fake processes, and tests/fake_claude.py for real
child processes. Keys are made up and never stored outside tmp_path."""
import io
import json
import sys
import threading
import time
from pathlib import Path

import pytest

from thechatplace import billing, bugreport, claude_cli as cli, platform_paths, spend_ledger
from thechatplace.aside import AsideRunner
from thechatplace.claude_cli import TurnRunner, child_environment
from thechatplace.own_store import OwnSession, OwnSessionStore, _session_from_dict

FAKE_CLAUDE = Path(__file__).with_name("fake_claude.py")
FAKE_KEY = "sk-ant-test-0000000000000000000000000000"
HELPER = {"tokenSource": "apiKeyHelper", "apiProvider": "firstParty"}  # as 2.1.325 answers (#662)
SUBSCRIPTION = {"email": "someone@example.com", "subscriptionType": "max",
                "apiProvider": "firstParty"}


def ev(**data):
    return json.dumps(data)


def init_answer(account=None, models=None):
    body = {"commands": [], "models": models or [
        {"value": "default", "resolvedModel": "claude-opus-5-5"},
        {"value": "fable", "resolvedModel": "claude-fable-5-2"}]}
    if account is not None:
        body["account"] = account
    return ev(type="control_response", response={
        "subtype": "success", "request_id": cli.INIT_REQUEST_ID, "response": body})


def result(text="ok", cost=0.05, subtype="success", is_error=False, restored_tokens=0,
           **extra):
    """A result as Claude Code 2.1.325 reports one: ``usage`` the turn's own
    tokens, modelUsage the cumulative ones, ``restored_tokens`` more (#663)."""
    extra.setdefault("usage", {"input_tokens": 10})
    return ev(type="result", subtype=subtype, is_error=is_error, result=text, session_id="s1",
              total_cost_usd=cost,
              modelUsage={"claude-opus-5-5": {"costUSD": cost,
                                              "inputTokens": 10 + restored_tokens}}, **extra)


class FakeProcess:
    """Byte pipes, like the real Popen the runner makes."""

    def __init__(self, stdout_lines, returncode=0, stderr_lines=()):
        self.stdout = io.BytesIO("".join(x + "\n" for x in stdout_lines).encode("utf-8"))
        self.stderr = io.BytesIO("".join(x + "\n" for x in stderr_lines).encode("utf-8"))
        self.stdin = io.BytesIO()
        self.pid = None
        self.returncode = None
        self._final = returncode
        self.killed = False
        self.written = b""
        original_close = self.stdin.close

        def close():
            self.written = self.stdin.getvalue()
            original_close()
        self.stdin.close = close

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = -9 if self.killed else self._final
        return self.returncode

    def kill(self):
        self.killed = True

    def sent(self):
        data = self.written or (self.stdin.getvalue() if not self.stdin.closed else b"")
        return [json.loads(x) for x in data.decode("utf-8").splitlines()]


def timers(fire_init=False):
    """Timers that never fire by themselves; with ``fire_init`` the
    initialize wait runs out at once (Claude Code never answered)."""
    def make(interval, function):
        class Timer:
            daemon = False

            def start(self):
                if fire_init and interval == cli.INIT_ANSWER_WAIT:
                    function()

            def cancel(self):
                pass
        return Timer()
    return make


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path / "data")
    return spend_ledger.SpendLedger(tmp_path / "data")


def run(process, tmp_path, book=None, billing_route="api", command=None, fire_init=False,
        **kwargs):
    captured, events, done = {}, [], threading.Event()

    def popen(cmd, **popen_kwargs):
        captured["cmd"] = cmd
        captured.update(popen_kwargs)
        return process

    def on_event(event):
        events.append(event)
        if event.kind in ("finished", "failed"):
            done.set()
    env = kwargs.pop("env", {"PATH": "x"})
    runner = TurnRunner(command or ["claude", "-p", "--resume", "chat-1"], str(tmp_path),
                        "Hello", on_event, popen=popen, env=env,
                        timer=timers(fire_init), billing=billing_route, ledger=book, **kwargs)
    runner.start()
    assert done.wait(10)
    runner.join(10)
    return runner, events, captured


def user_messages(process):
    return [m for m in process.sent() if m.get("type") == "user"]


# -- an API turn, start to finish -----------------------------------------------------------


def test_an_api_turn_gets_the_key_only_through_its_one_settings(tmp_path, book):
    process = FakeProcess([
        init_answer(HELPER),
        ev(type="system", subtype="init", session_id="s1", apiKeySource="apiKeyHelper"),
        ev(type="assistant", message={"content": [{"type": "text", "text": "pong"}]}),
        result("pong", cost=0.05),
    ])
    style = json.dumps({"outputStyle": "Concise"})
    command = ["claude", "-p", "--settings", style, "--resume", "chat-1"]
    runner, events, captured = run(process, tmp_path, book, command=list(command),
                                   disabled_mcp=["github"], mcp_config='{"mcpServers": {}}')
    cmd = captured["cmd"]
    assert cmd.count("--settings") == 1
    settings = json.loads(cmd[cmd.index("--settings") + 1])
    assert settings == {"outputStyle": "Concise",
                        "deniedMcpServers": [{"serverName": "github"}],
                        "apiKeyHelper": platform_paths.api_key_helper_command(),
                        "env": billing.API_ENV}        # where the key may go (#663)
    assert cmd[cmd.index("--max-budget-usd") + 1] == "5.00"
    assert cmd[-2:] == ["--mcp-config", '{"mcpServers": {}}']  # still last
    assert runner.command == command                              # the turn's own, unchanged
    assert "sk-ant" not in " ".join(cmd) and "sk-ant" not in json.dumps(captured["env"])
    assert not any("API_KEY" in name for name in captured["env"])
    assert [m["message"]["content"] for m in user_messages(process)] == ["Hello"]
    final = events[-1]
    assert final.kind == "finished" and not final.is_error
    assert final.data["billing"] == "api" and final.data["cost_usd"] == pytest.approx(0.05)
    assert final.data["model_costs"] == {"claude-opus-5-5": pytest.approx(0.05)}
    totals = book.totals()
    assert totals.spent == pytest.approx(0.05) and totals.held == 0
    assert totals.by_chat == {"chat-1": pytest.approx(0.05)}


@pytest.mark.parametrize("account", [SUBSCRIPTION, None, {**HELPER, "apiKeySource":
                                                          "ANTHROPIC_API_KEY"},
                                     {**HELPER, "apiProvider": "bedrock"}])
def test_an_api_turn_is_stopped_before_sending_unless_on_the_apps_key(tmp_path, book, account):
    process = FakeProcess([
        init_answer(account),
        ev(type="system", subtype="init", session_id="s1", apiKeySource="none"),
        result("billed?"),
    ])
    runner, events, _ = run(process, tmp_path, book)
    assert process.killed and user_messages(process) == []
    assert [e.kind for e in events] == ["failed"]
    assert "Nothing was sent" in events[0].text
    assert runner.stopped_before_answer
    totals = book.totals()
    assert totals.spent == 0 and totals.held == 0     # the reservation was let go


def test_an_api_turn_with_no_initialize_answer_never_sends(tmp_path, book):
    process = FakeProcess([
        ev(type="system", subtype="init", session_id="s1", apiKeySource="apiKeyHelper"),
        result("too soon"),
    ])
    runner, events, _ = run(process, tmp_path, book, fire_init=True)
    assert process.killed and user_messages(process) == []
    assert events[-1].kind == "failed" and events[-1].text == cli.INIT_UNANSWERED_API
    assert runner.stopped_before_answer and book.totals().held == 0


def test_an_api_turn_doesnt_send_on_other_events_before_initialize(tmp_path, book):
    # A subscription turn sends on the first event of the conversation when
    # initialize hasn't answered (older Claude Code); an API turn waits.
    process = FakeProcess([
        ev(type="system", subtype="init", session_id="s1", apiKeySource="apiKeyHelper"),
        init_answer(HELPER),
        result("ok"),
    ])
    _runner, events, _ = run(process, tmp_path, book)
    assert events[-1].kind == "finished"
    assert len(user_messages(process)) == 1


def test_the_limit_reached_stops_the_turn_before_claude_starts(tmp_path, book):
    book.set_limits(5, 5)
    book.reserve("other")                      # all of it held by another chat
    started = []
    process = FakeProcess([])
    runner = TurnRunner(["claude", "-p"], str(tmp_path), "Hello", lambda e: started.append(e),
                        popen=lambda *a, **k: started.append("popen") or process,
                        env={"PATH": "x"}, billing="api", ledger=book)
    runner.start()
    runner.join(10)
    assert "popen" not in started
    assert started[-1].kind == "failed" and "limit of $5.00" in started[-1].text
    assert runner.stopped_before_answer


def test_over_the_per_message_limit_is_said_plainly(tmp_path, book):
    process = FakeProcess([
        init_answer(HELPER),
        ev(type="system", subtype="init", session_id="s1", apiKeySource="apiKeyHelper"),
        ev(type="result", subtype="error_max_budget_usd", is_error=True, session_id="s1",
           errors=["Reached maximum budget"], total_cost_usd=5.03,
           modelUsage={"claude-opus-5-5": {"costUSD": 5.03}}),
    ])
    # A new chat, so the whole total is this turn's, overrun and all.
    _runner, events, _ = run(process, tmp_path, book,
                             command=["claude", "-p", "--session-id", "chat-1"])
    final = events[-1]
    assert final.is_error and "per-message limit of $5.00" in final.text
    assert "$5.03" in final.text and final.data["cost_usd"] == pytest.approx(5.03)
    totals = book.totals()
    assert totals.spent == pytest.approx(5.03) and totals.held == 0


@pytest.mark.parametrize("said,expected", [
    ("Credit balance is too low to access the Anthropic API.", "no credit left"),
    ("Invalid API key · Fix external API key", "refused your Claude API key")])
def test_no_credit_and_a_refused_key_are_said_plainly(tmp_path, book, said, expected):
    process = FakeProcess([
        init_answer(HELPER),
        ev(type="system", subtype="init", session_id="s1", apiKeySource="apiKeyHelper"),
        result(said, cost=0, is_error=True),
    ])
    _runner, events, _ = run(process, tmp_path, book)
    assert events[-1].is_error and expected in events[-1].text


def test_several_results_in_one_process_settle_the_difference(tmp_path, book):
    # Claude Code's total_cost_usd is the process's running total.
    process = FakeProcess([
        init_answer(HELPER),
        ev(type="system", subtype="init", session_id="s1", apiKeySource="apiKeyHelper"),
        result("first", cost=0.10),
        result("second", cost=0.25),
    ])
    _runner, events, _ = run(process, tmp_path, book)
    assert events[-1].data["cost_usd"] == pytest.approx(0.25)
    assert book.totals().spent == pytest.approx(0.25)


def _turn_lines(cost, session="s1", restored=0):
    """A turn's lines. ``restored``: tokens modelUsage has beyond the
    result's own usage, as Claude Code 2.1.325 reports a restored chat's
    cost state (#663); 0 for a turn that restored nothing."""
    return [init_answer(HELPER),
            ev(type="system", subtype="init", session_id=session, apiKeySource="apiKeyHelper"),
            ev(type="result", subtype="success", is_error=False, result="ok", session_id=session,
               total_cost_usd=cost, usage={"input_tokens": 100, "output_tokens": 5},
               modelUsage={"claude-haiku-5-5": {"costUSD": cost, "inputTokens": 100 + restored,
                                                "outputTokens": 5}})]


def test_a_resumed_turn_counts_only_its_own_cost_not_the_chats_earlier_turns(tmp_path, book):
    # Measured with Claude Code 2.1.325 on the real API (#658): a resumed
    # turn's total_cost_usd includes the chat's earlier turns.
    run(FakeProcess(_turn_lines(0.00125, "chat-1")), tmp_path, book,
        command=["claude", "-p", "--session-id", "chat-1"])
    assert book.baseline("chat-1")[0] == pytest.approx(0.00125)
    _runner, events, _ = run(FakeProcess(_turn_lines(0.00153, "chat-1", restored=900)),
                             tmp_path, book, command=["claude", "-p", "--resume", "chat-1"])
    assert events[-1].data["cost_usd"] == pytest.approx(0.00028)
    assert book.totals().spent == pytest.approx(0.00153)      # 0.00125 + 0.00028
    assert book.baseline("chat-1")[0] == pytest.approx(0.00153)


def test_a_fork_counts_from_its_sources_baseline(tmp_path, book):
    book.set_baseline("source-1", 0.40, {"claude-haiku-5-5": 0.40}, clean=True)
    _runner, events, _ = run(FakeProcess(_turn_lines(0.43, "copy-1", restored=5000)),
                             tmp_path, book,
                             command=["claude", "-p", "--resume", "source-1", "--fork-session",
                                      "--session-id", "copy-1"])
    assert events[-1].data["cost_usd"] == pytest.approx(0.03)
    assert book.totals().by_chat == {"copy-1": pytest.approx(0.03)}
    # The copy has its own baseline now; the source's is untouched.
    assert book.baseline("copy-1")[0] == pytest.approx(0.43)
    assert book.baseline("source-1")[0] == pytest.approx(0.40)


def test_a_turn_with_no_baseline_counts_up_to_its_reservation(tmp_path, book):
    # Continue Here from a desktop session the app never ran: its whole
    # history is in the first total, and only the budget bounds this turn.
    book.set_limits(190, 2)
    _runner, events, _ = run(FakeProcess(_turn_lines(7.5, "copy-2", restored=90000)),
                             tmp_path, book,
                             command=["claude", "-p", "--resume", "desktop-1", "--fork-session",
                                      "--session-id", "copy-2"])
    assert book.totals().spent == pytest.approx(2.0)
    assert book.totals().held == 0
    # Counted conservatively, but that's no cost to say (#663): not a
    # made-up "$2.00" for what may have been a 2-cent message.
    assert events[-1].data["cost_usd"] is None
    assert events[-1].data["cost_complete"] is False
    assert billing.cost_words(events[-1].data["cost_usd"],
                              events[-1].data["cost_complete"]) == "cost not known"
    assert book.baseline("copy-2")[0] == pytest.approx(7.5)   # the next turn counts from here


def _resume(session):
    return ["claude", "-p", "--resume", session]


@pytest.mark.parametrize("own", [0.001003, 0.005])
def test_a_resumed_turn_after_a_stop_counts_from_the_last_clean_end(tmp_path, book, own):
    # Checked live with Claude Code 2.1.325 (#663): a stopped (killed) turn's
    # cost isn't saved, so the next resume restores the cost at the last
    # clean end. Turn 1 ended cleanly at 0.003942; turn 2 was stopped at
    # 0.007856; turn 3 restored 0.003942 and added its own. A cheap turn 3
    # came in below the latest baseline, a dearer one above it: either way
    # only its own cost is counted, never less.
    run(FakeProcess(_turn_lines(0.003942, "chat-s")), tmp_path, book,
        command=["claude", "-p", "--session-id", "chat-s"])
    _r, events, _ = run(FakeProcess(_turn_lines(0.007856, "chat-s", restored=105),
                                    returncode=-9), tmp_path, book, command=_resume("chat-s"))
    assert events[-1].data["cost_usd"] == pytest.approx(0.003914)
    latest, clean = book.baselines("chat-s")
    assert latest.total == pytest.approx(0.007856) and clean.total == pytest.approx(0.003942)
    _r, events, _ = run(FakeProcess(_turn_lines(0.003942 + own, "chat-s", restored=105)),
                        tmp_path, book, command=_resume("chat-s"))
    assert events[-1].data["cost_usd"] == pytest.approx(own)
    assert events[-1].data["cost_complete"] is True
    assert book.totals().spent == pytest.approx(0.003942 + 0.003914 + own)
    latest, clean = book.baselines("chat-s")
    assert latest.total == clean.total == pytest.approx(0.003942 + own)   # a clean end again


def test_without_tokens_to_match_the_clean_baseline_is_counted_from(tmp_path, book):
    # Tokens that match neither baseline (a side request, say): the clean
    # one, which only ever over-counts.
    book.set_baseline("chat-t", 0.10, {"claude-haiku-5-5": 0.10}, tokens=500, clean=True)
    book.set_baseline("chat-t", 0.30, {"claude-haiku-5-5": 0.30}, tokens=900)
    _r, events, _ = run(FakeProcess(_turn_lines(0.35, "chat-t", restored=777)), tmp_path, book,
                        command=_resume("chat-t"))
    assert events[-1].data["cost_usd"] == pytest.approx(0.25)


def test_tokens_that_match_the_latest_baseline_count_from_it(tmp_path, book):
    # Should a later Claude Code save a stopped turn's cost, the restored
    # tokens say so, and the turn isn't over-counted.
    book.set_baseline("chat-u", 0.10, {"claude-haiku-5-5": 0.10}, tokens=500, clean=True)
    book.set_baseline("chat-u", 0.30, {"claude-haiku-5-5": 0.30}, tokens=900)
    _r, events, _ = run(FakeProcess(_turn_lines(0.35, "chat-u", restored=900)), tmp_path, book,
                        command=_resume("chat-u"))
    assert events[-1].data["cost_usd"] == pytest.approx(0.05)


def test_a_legacy_baseline_is_no_baseline_until_a_clean_end(tmp_path, book):
    # The review (#663): an earlier build's baseline has no clean record, so
    # it fell back to the latest, which may include a stopped turn's cost
    # Claude Code never saved: the under-count. Now it's no usable baseline.
    book.path.parent.mkdir(parents=True, exist_ok=True)
    book.baselines_path.write_text(
        json.dumps({"chat-w": {"total": 0.30, "models": {"claude-haiku-5-5": 0.30}}}),
        encoding="utf-8")
    book.set_limits(190, 2)
    assert book.baselines("chat-w") is None
    _r, events, _ = run(FakeProcess(_turn_lines(0.35, "chat-w", restored=900)), tmp_path, book,
                        command=_resume("chat-w"))
    assert events[-1].data["cost_usd"] is None                 # "cost not known"
    assert events[-1].data["cost_complete"] is False
    assert book.totals().spent == pytest.approx(0.35)          # all of it, up to the $2 hold
    # That process ended cleanly: from now on there's a baseline to count from.
    latest, clean = book.baselines("chat-w")
    assert latest.total == clean.total == pytest.approx(0.35)
    _r, events, _ = run(FakeProcess(_turn_lines(0.40, "chat-w", restored=900)), tmp_path, book,
                        command=_resume("chat-w"))
    assert events[-1].data["cost_usd"] == pytest.approx(0.05)


def test_a_legacy_baseline_stays_legacy_through_a_stopped_turn(tmp_path, book):
    book.path.parent.mkdir(parents=True, exist_ok=True)
    book.baselines_path.write_text(json.dumps({"chat-x": {"total": 0.30}}), encoding="utf-8")
    run(FakeProcess(_turn_lines(0.50, "chat-x", restored=900), returncode=-9), tmp_path, book,
        command=_resume("chat-x"))
    assert book.baseline("chat-x")[0] == pytest.approx(0.50)   # recorded,
    assert book.baselines("chat-x") is None                     # but not counted from


def test_a_failed_or_killed_process_never_sets_the_clean_baseline(tmp_path, book):
    run(FakeProcess(_turn_lines(0.2, "chat-v")), tmp_path, book,
        command=["claude", "-p", "--session-id", "chat-v"])
    run(FakeProcess(_turn_lines(0.5, "chat-v", restored=105), returncode=1), tmp_path, book,
        command=_resume("chat-v"))
    latest, clean = book.baselines("chat-v")
    assert latest.total == pytest.approx(0.5) and clean.total == pytest.approx(0.2)


def test_a_subscription_turn_sets_the_baseline_continue_on_api_credits_uses(tmp_path, book):
    lines = [init_answer(SUBSCRIPTION),
             ev(type="system", subtype="init", session_id="mine", apiKeySource="none"),
             _turn_lines(0.9, "mine")[-1]]
    _runner, events, _ = run(FakeProcess(lines), tmp_path, None, billing_route="subscription",
                             command=["claude", "-p", "--resume", "mine"], cost_baselines=book)
    assert events[-1].kind == "finished" and "cost_usd" not in events[-1].data
    assert book.baseline("mine")[0] == pytest.approx(0.9)
    assert book.totals().spent == 0 and not book.path.exists()   # never spending
    # Continue on API Credits: a fork of it, charged to the key.
    _runner, events, _ = run(FakeProcess(_turn_lines(0.95, "paid", restored=8000)),
                             tmp_path, book,
                             command=["claude", "-p", "--resume", "mine", "--fork-session",
                                      "--session-id", "paid"])
    assert events[-1].data["cost_usd"] == pytest.approx(0.05)
    assert book.totals().spent == pytest.approx(0.05)


def test_a_process_that_saves_nothing_sets_no_baseline(tmp_path, book):
    run(FakeProcess(_turn_lines(0.01, "t")), tmp_path, book,
        command=["claude", "-p", "--no-session-persistence"])
    assert book.baseline("t") is None


def test_a_turn_that_dies_mid_reply_keeps_its_reservation_counted(tmp_path, book):
    process = FakeProcess([
        init_answer(HELPER),
        ev(type="system", subtype="init", session_id="s1", apiKeySource="apiKeyHelper"),
        ev(type="assistant", message={"content": [{"type": "text", "text": "working"}]}),
    ], returncode=1, stderr_lines=["boom"])
    _runner, events, _ = run(process, tmp_path, book)
    assert events[-1].kind == "failed"
    assert book.totals().held == 5.0           # what it spent isn't known


def test_fable_is_allowed_on_api_credits_and_still_refused_on_the_subscription(tmp_path, book):
    lines = [ev(type="system", subtype="init", session_id="s1", model="claude-fable-5-2",
                apiKeySource="apiKeyHelper"),
             ev(type="assistant", message={"model": "claude-fable-5-2",
                                           "content": [{"type": "text", "text": "hi"}]}),
             result("hi")]
    process = FakeProcess([init_answer(HELPER)] + lines)
    _runner, events, _ = run(process, tmp_path, book, command=["claude", "-p"])
    assert events[-1].kind == "finished" and not events[-1].is_error
    # The same unchosen Fable on the subscription is stopped before sending.
    process = FakeProcess([init_answer(SUBSCRIPTION, models=[
        {"value": "default", "resolvedModel": "claude-fable-5-2"}])] + lines)
    _runner, events, _ = run(process, tmp_path, None, billing_route="subscription",
                             command=["claude", "-p"])
    assert events[-1].kind == "failed" and "Fable" in events[-1].text
    assert cli.fable_problem("claude-fable-5-2", "", billing="api") is None
    assert cli.fable_problem("claude-fable-5-2", "", billing="subscription")


# -- the subscription, now checked before sending too ---------------------------------------


def test_a_subscription_turn_reporting_a_key_is_stopped_before_sending(tmp_path):
    process = FakeProcess([
        init_answer({**SUBSCRIPTION, "apiKeySource": "ANTHROPIC_API_KEY"}),
        ev(type="system", subtype="init", session_id="s1", apiKeySource="ANTHROPIC_API_KEY"),
        result("billed!"),
    ])
    runner, events, captured = run(process, tmp_path, billing_route="subscription")
    assert process.killed and user_messages(process) == []
    assert events[-1].kind == "failed" and "Nothing was sent" in events[-1].text
    assert runner.stopped_before_answer
    assert "--max-budget-usd" not in captured["cmd"] and "--settings" not in captured["cmd"]


def test_a_subscription_turn_never_touches_the_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(spend_ledger, "default_ledger",
                        lambda: pytest.fail("a subscription turn reached the ledger"))
    process = FakeProcess([init_answer(SUBSCRIPTION),
                           ev(type="system", subtype="init", session_id="s1",
                              apiKeySource="none"),
                           result("ok", cost=0.9)])
    runner, events, _ = run(process, tmp_path, billing_route="subscription")
    assert runner.ledger is None and runner.reservation is None
    assert events[-1].kind == "finished" and "cost_usd" not in events[-1].data
    # What Claude Code said it would have cost is there, but not as spending.
    assert events[-1].data["reported_cost_usd"] == pytest.approx(0.9)


def test_a_subscription_turn_without_an_initialize_answer_still_sends(tmp_path):
    process = FakeProcess([ev(type="system", subtype="init", session_id="s1",
                              apiKeySource="none"), result("ok")])
    _runner, events, _ = run(process, tmp_path, billing_route="subscription", fire_init=True)
    assert events[-1].kind == "finished" and len(user_messages(process)) == 1


def test_a_key_in_stderr_never_reaches_the_window(tmp_path):
    process = FakeProcess([], returncode=1,
                          stderr_lines=[f"Error: request failed for key {FAKE_KEY}"])
    _runner, events, _ = run(process, tmp_path, billing_route="subscription")
    assert events[-1].kind == "failed"
    assert FAKE_KEY not in events[-1].text and billing.HIDDEN_KEY in events[-1].text


# -- real child processes (tests/fake_claude.py) --------------------------------------------


def fake_env(tmp_path, **variables):
    base = {**child_environment(), "FAKE_CLAUDE_LOG": str(tmp_path / "fake.log"),
            # A key in the app's own environment must never reach the child.
            "ANTHROPIC_API_KEY": FAKE_KEY}
    return child_environment({**base, **variables})


def run_real(tmp_path, book, command=None, purpose="turn", **variables):
    events, done = [], threading.Event()

    def on_event(event):
        events.append(event)
        if event.kind in ("finished", "failed"):
            done.set()
    runner = TurnRunner(command or [sys.executable, str(FAKE_CLAUDE), "--resume", "chat-9"],
                        str(tmp_path), "Hello", on_event,
                        env=fake_env(tmp_path, **variables), billing="api", ledger=book,
                        purpose=purpose)
    runner.start()
    assert done.wait(30)
    runner.join(10)
    log = tmp_path / "fake.log"
    return events, (json.loads(log.read_text(encoding="utf-8")) if log.exists() else None)


def test_a_real_api_turn_settles_what_claude_code_reports(tmp_path, book):
    events, log = run_real(tmp_path, book, FAKE_CLAUDE_ACCOUNT="api")
    assert events[-1].kind == "finished" and events[-1].data["cost_usd"] == pytest.approx(0.0123)
    assert log["prompt"] == "Hello" and not log["key_in_env"] and log["claude_env"] == []
    args = log["args"]
    assert json.loads(args[args.index("--settings") + 1]) == {
        "apiKeyHelper": platform_paths.api_key_helper_command(), "env": billing.API_ENV}
    assert args[args.index("--max-budget-usd") + 1] == "5.00"
    assert book.totals().spent == pytest.approx(0.0123)


@pytest.mark.parametrize("account", ["subscription", "apikey_env", "bedrock", "none"])
def test_a_real_api_turn_on_the_wrong_account_sends_nothing(tmp_path, book, account):
    events, log = run_real(tmp_path, book, FAKE_CLAUDE_ACCOUNT=account)
    assert events[-1].kind == "failed" and "Nothing was sent" in events[-1].text
    assert log is None or log.get("prompt") is None
    assert book.totals().held == 0 and book.totals().spent == 0


@pytest.mark.parametrize("mode,expected", [("max-budget", "per-message limit"),
                                           ("credit-low", "no credit left"),
                                           ("bad-key", "refused your Claude API key")])
def test_real_api_failures_in_plain_words(tmp_path, book, mode, expected):
    events, _log = run_real(tmp_path, book, FAKE_CLAUDE_ACCOUNT="api", FAKE_CLAUDE_MODE=mode)
    assert events[-1].is_error and expected in events[-1].text


def test_stop_on_api_credits_settles_what_the_turn_really_cost(tmp_path, book):
    # Stop interrupts first, so Claude Code says what the turn cost and only
    # that is counted, not the whole per-message reservation (#658).
    events, done, ended = [], threading.Event(), {}

    def on_event(event):
        events.append(event)
        if event.kind == "text" and not ended:
            ended["at"] = time.monotonic()
            runner.cancel()
        if event.kind in ("finished", "failed"):
            done.set()
    runner = TurnRunner([sys.executable, str(FAKE_CLAUDE), "--resume", "chat-9"],
                        str(tmp_path), "Hello", on_event,
                        env=fake_env(tmp_path, FAKE_CLAUDE_ACCOUNT="api",
                                     FAKE_CLAUDE_MODE="interruptible",
                                     FAKE_CLAUDE_COST="0.0034"),
                        billing="api", ledger=book)
    runner.start()
    assert done.wait(30)
    runner.join(10)
    assert events[-1].kind == "failed" and events[-1].text == "Stopped."
    assert events[-1].data["cost_usd"] == pytest.approx(0.0034)
    # Ended on the interrupt's result, not by waiting out STOP_COST_WAIT.
    assert time.monotonic() - ended["at"] < cli.STOP_COST_WAIT
    totals = book.totals()
    assert totals.spent == pytest.approx(0.0034) and totals.held == 0


def test_stop_on_the_subscription_still_ends_the_turn_at_once(tmp_path):
    process = FakeProcess([init_answer(SUBSCRIPTION),
                           ev(type="system", subtype="init", session_id="s1",
                              apiKeySource="none")])
    runner = TurnRunner(["claude", "-p", "--resume", "chat-1"], str(tmp_path), "Hello",
                        lambda event: None, popen=lambda cmd, **kw: process,
                        env={"PATH": "x"}, timer=timers())
    runner._process = process
    runner.cancel()
    assert process.killed and not process.sent()


def test_test_key_runs_as_a_tiny_api_turn_and_is_counted(tmp_path, book):
    command = cli.build_key_test_command(sys.executable)
    assert command[command.index("--model") + 1] == "haiku"
    assert command[command.index("--tools") + 1] == ""
    assert "--no-session-persistence" in command and "--resume" not in command
    events, log = run_real(tmp_path, book, command=[sys.executable, str(FAKE_CLAUDE),
                                                    *command[1:]],
                           purpose="test", FAKE_CLAUDE_ACCOUNT="api", FAKE_CLAUDE_COST="0.0043")
    assert events[-1].kind == "finished" and log["prompt"] == "Hello"
    settings = json.loads(log["args"][log["args"].index("--settings") + 1])
    assert settings["disableAllHooks"] is True and "apiKeyHelper" in settings
    assert book.totals().spent == pytest.approx(0.0043)
    reserve = json.loads((book.path).read_text(encoding="utf-8").splitlines()[0])
    assert reserve["purpose"] == "test"


# -- asides follow their chat --------------------------------------------------------------


def run_aside(tmp_path, book, billing_route="api", **variables):
    events, done = [], threading.Event()

    def on_event(event):
        events.append(event)
        if event.kind in ("done", "failed"):
            done.set()
    runner = AsideRunner([sys.executable, str(FAKE_CLAUDE), "--settings",
                          '{"disableAllHooks": true}', "--resume", "chat-5", "--fork-session"],
                         str(tmp_path), "What's the code word?", on_event,
                         env=fake_env(tmp_path, FAKE_CLAUDE_MODE="aside", **variables),
                         billing=billing_route, ledger=book)
    runner.start()
    assert done.wait(30)
    runner.join(10)
    log = tmp_path / "fake.log"
    return events, (json.loads(log.read_text(encoding="utf-8")) if log.exists() else None)


def test_an_aside_on_an_api_chat_is_charged_to_it(tmp_path, book):
    events, log = run_aside(tmp_path, book, FAKE_CLAUDE_ACCOUNT="api")
    assert events[-1].kind == "done" and events[-1].cost_usd == pytest.approx(0.0123)
    args = log["args"]
    assert args.count("--settings") == 1
    settings = json.loads(args[args.index("--settings") + 1])
    assert settings == {"disableAllHooks": True,
                        "apiKeyHelper": platform_paths.api_key_helper_command(),
                        "env": billing.API_ENV}
    assert "--max-budget-usd" in args and not log["key_in_env"]
    totals = book.totals()
    assert totals.spent == pytest.approx(0.0123) and totals.by_chat == {
        "chat-5": pytest.approx(0.0123)}
    reserve = json.loads(book.path.read_text(encoding="utf-8").splitlines()[0])
    assert reserve["purpose"] == "aside"


def test_stop_on_an_api_aside_settles_what_it_really_cost(tmp_path, book):
    # As a turn's Stop (#658): interrupted first, so only what the question
    # cost is counted, not its whole reservation.
    events, done = [], threading.Event()

    def on_event(event):
        events.append(event)
        if event.kind in ("done", "failed"):
            done.set()
    runner = AsideRunner([sys.executable, str(FAKE_CLAUDE), "--resume", "chat-5",
                          "--fork-session", "--no-session-persistence"],
                         str(tmp_path), "What's the code word?", on_event,
                         env=fake_env(tmp_path, FAKE_CLAUDE_ACCOUNT="api",
                                      FAKE_CLAUDE_MODE="interruptible",
                                      FAKE_CLAUDE_COST="0.0034"),
                         billing="api", ledger=book)
    runner.start()
    log = tmp_path / "fake.log"

    def logged():
        try:
            return json.loads(log.read_text(encoding="utf-8"))
        except (OSError, ValueError):   # not there yet, or being written
            return {}
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not logged().get("prompt"):
        time.sleep(0.05)
    assert logged()["prompt"] == "What's the code word?"   # the question went
    stopped_at = time.monotonic()
    runner.cancel()
    assert done.wait(30)
    runner.join(10)
    assert events[-1].kind == "failed" and events[-1].text == "Stopped."
    assert events[-1].cost_usd == pytest.approx(0.0034)
    assert time.monotonic() - stopped_at < cli.STOP_COST_WAIT     # ended on the result
    assert "interrupt" in logged().get("controls", [])
    totals = book.totals()
    assert totals.spent == pytest.approx(0.0034) and totals.held == 0


def test_stop_on_a_subscription_aside_ends_it_at_once(tmp_path):
    process = FakeProcess([])
    runner = AsideRunner(["claude", "-p", "--resume", "chat-5"], str(tmp_path), "Q?",
                         lambda event: None, popen=lambda cmd, **kw: process, env={"PATH": "x"})
    runner._process = process
    runner._cost_known = False
    runner.cancel()
    assert process.killed and not process.sent()


def test_an_aside_on_an_api_chat_asks_nothing_on_the_wrong_account(tmp_path, book):
    events, log = run_aside(tmp_path, book, FAKE_CLAUDE_ACCOUNT="subscription")
    assert events[-1].kind == "failed" and "Nothing was sent" in events[-1].text
    assert "then ask again" in events[-1].text
    assert log is None or log.get("prompt") is None
    assert book.totals().held == 0


def test_an_aside_on_a_subscription_chat_stops_for_a_key(tmp_path):
    events, log = run_aside(tmp_path, None, billing_route="subscription",
                            FAKE_CLAUDE_ACCOUNT="apikey_env")
    assert events[-1].kind == "failed" and "didn't ask your question" in events[-1].text
    assert events[-1].cost_usd is None
    assert log is None or log.get("prompt") is None


def test_an_aside_with_no_initialize_answer_on_api_credits_asks_nothing(tmp_path, book):
    events, done = [], threading.Event()

    def on_event(event):
        events.append(event)
        if event.kind in ("done", "failed"):
            done.set()
    runner = AsideRunner([sys.executable, str(FAKE_CLAUDE), "--resume", "chat-5"],
                         str(tmp_path), "Q?", on_event,
                         env=fake_env(tmp_path, FAKE_CLAUDE_MODE="no-init",
                                      FAKE_CLAUDE_ACCOUNT="api"),
                         timer=timers(fire_init=True), billing="api", ledger=book)
    runner.start()
    assert done.wait(30)
    runner.join(10)
    assert events[-1].kind == "failed" and "didn't ask it on API credits" in events[-1].text
    assert book.totals().held == 0


# -- the chat's record ------------------------------------------------------------------------


def test_billing_is_kept_and_anything_odd_is_the_subscription(tmp_path):
    store = OwnSessionStore(tmp_path / "sessions.json")
    store.add(OwnSession("aaaa-1", "On credits", str(tmp_path), billing="api", model="fable"))
    store.add(OwnSession("aaaa-2", "Plan", str(tmp_path)))
    again = OwnSessionStore(tmp_path / "sessions.json")
    assert again.get("aaaa-1").billing == "api" and again.get("aaaa-1").model == "fable"
    assert again.get("aaaa-2").billing == "subscription"
    for odd in ("API", "credits", None, 5, ""):
        session = _session_from_dict({"cli_session_id": "bbbb-1", "title": "t", "cwd": "c",
                                      "billing": odd, "model": "fable"})
        assert session.billing == "subscription" and session.model == ""


def test_fable_is_kept_only_for_api_chats(tmp_path):
    store = OwnSessionStore(tmp_path / "sessions.json")
    with pytest.raises(ValueError):
        store.add(OwnSession("aaaa-3", "t", str(tmp_path), model="fable"))
    assert cli.model_allowed("fable", "api") and not cli.model_allowed("fable", "subscription")
    assert ("fable", "Fable") in cli.models_for("api")
    assert "fable" not in dict(cli.models_for("subscription"))
    assert cli.MODELS == [m for m in cli.API_MODELS if m[0] != "fable"]
    assert cli.model_label("fable") == "Fable"


def test_a_chats_billing_never_changes(tmp_path):
    store = OwnSessionStore(tmp_path / "sessions.json")
    store.add(OwnSession("aaaa-4", "t", str(tmp_path)))
    with pytest.raises(ValueError):
        store.update("aaaa-4", billing="api")
    store.update("aaaa-4", billing="subscription", title="renamed")   # unchanged: fine
    assert store.get("aaaa-4").billing == "subscription"


def test_other_providers_are_never_on_api_credits():
    session = _session_from_dict({"cli_session_id": "cccc-1", "title": "t", "cwd": "c",
                                  "provider": "codex", "billing": "api"})
    assert session.billing == "subscription"


def test_an_older_release_never_sees_an_api_chat(tmp_path):
    # It reads sessions.json without knowing billing, and would run the chat
    # on the subscription; the chat is kept where it never looks (#663).
    from legacy_016_own_store import OwnSessionStore as OldStore
    store = OwnSessionStore(tmp_path / "sessions.json")
    store.add(OwnSession("aaaa-5", "t", str(tmp_path), billing="api", model="fable"))
    store.add(OwnSession("aaaa-6", "plan", str(tmp_path)))
    old = OldStore(tmp_path / "sessions.json")
    assert old.get("aaaa-5") is None and old.get("aaaa-6") is not None
    assert "aaaa-5" not in (tmp_path / "sessions.json").read_text(encoding="utf-8")
    kept = json.loads((tmp_path / "provider-sessions-claude-api.json").read_text(encoding="utf-8"))
    assert [s["cli_session_id"] for s in kept["sessions"]] == ["aaaa-5"]
    # The older release saving its list leaves the API chat as it was.
    old.add(old.get("aaaa-6").__class__("aaaa-7", "older", str(tmp_path)))
    again = OwnSessionStore(tmp_path / "sessions.json")
    assert again.load_error == ""
    assert again.get("aaaa-5").billing == "api" and again.get("aaaa-5").model == "fable"
    assert again.get("aaaa-7") is not None


def test_an_api_chat_written_into_sessions_json_is_moved_out(tmp_path):
    # As a build before #663 wrote it: in the file older releases read.
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({"version": 1, "sessions": [
        {"cli_session_id": "aaaa-8", "title": "paid", "cwd": str(tmp_path), "billing": "api",
         "model": "fable", "later_field": 7},
        {"cli_session_id": "aaaa-9", "title": "plan", "cwd": str(tmp_path)}]}),
        encoding="utf-8")
    store = OwnSessionStore(path)
    assert store.load_error == "" and store.migration_backup is not None
    assert store.get("aaaa-8").billing == "api"
    legacy = json.loads(path.read_text(encoding="utf-8"))
    assert [s["cli_session_id"] for s in legacy["sessions"]] == ["aaaa-9"]
    kept = json.loads((tmp_path / "provider-sessions-claude-api.json").read_text(encoding="utf-8"))
    assert kept["sessions"][0]["later_field"] == 7          # a newer release's field kept
    assert OwnSessionStore(path).get("aaaa-8").model == "fable"


@pytest.mark.parametrize("entry", [
    {"cli_session_id": "aaaa-1", "title": "t", "cwd": "c"},             # not an API chat
    {"cli_session_id": "aaaa-1", "title": "t", "cwd": "c", "billing": "api", "provider": "codex"},
    "not a record"])
def test_a_wrong_record_in_the_api_chats_file_blocks_saving(tmp_path, entry):
    (tmp_path / "provider-sessions-claude-api.json").write_text(
        json.dumps({"version": 2, "sessions": [entry]}), encoding="utf-8")
    store = OwnSessionStore(tmp_path / "sessions.json")
    assert "claude-api" in store.load_error
    with pytest.raises(OSError):
        store.add(OwnSession("bbbb-1", "t", str(tmp_path)))


def test_an_api_chats_rename_and_removal_stay_in_its_file(tmp_path):
    store = OwnSessionStore(tmp_path / "sessions.json")
    store.add(OwnSession("aaaa-1", "t", str(tmp_path), billing="api"))
    store.rename_id("aaaa-1", "aaaa-2")
    again = OwnSessionStore(tmp_path / "sessions.json")
    assert again.get("aaaa-1") is None and again.get("aaaa-2").billing == "api"
    again.remove("aaaa-2")
    kept = json.loads((tmp_path / "provider-sessions-claude-api.json").read_text(encoding="utf-8"))
    assert kept["sessions"] == []


# -- bug reports ------------------------------------------------------------------------------


def test_a_bug_report_says_whether_a_key_is_set_never_the_key(monkeypatch):
    from thechatplace.speech import SpeechSettings
    monkeypatch.setattr(platform_paths, "has_secret", lambda name: True)
    monkeypatch.setattr(platform_paths, "load_secret", lambda name: FAKE_KEY)
    facts = dict(bugreport.environment(SpeechSettings(), {}, claude_version="x"))
    assert facts["Claude API key"] == "set"
    assert FAKE_KEY not in json.dumps(facts)
    monkeypatch.setattr(platform_paths, "has_secret", lambda name: False)
    assert dict(bugreport.environment(SpeechSettings(), {}, claude_version="x"))[
        "Claude API key"] == "not set"
    assert dict(bugreport.environment(SpeechSettings(), {}, claude_version="x",
                                      api_key=True))["Claude API key"] == "set"


def test_the_version_check_gets_no_key(monkeypatch):
    import subprocess
    seen = {}

    def fake_run(args, **kwargs):
        seen.update(kwargs)

        class Out:
            stdout, stderr = "2.1.325 (Claude Code)\n", ""
        return Out()
    monkeypatch.setattr(platform_paths, "claude_executable", lambda: "claude.exe")
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert bugreport.cli_version("claude") == "2.1.325 (Claude Code)"
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert FAKE_KEY not in json.dumps(seen["env"])


# -- the review's findings (#663) -------------------------------------------------------------

STARTED = ev(type="system", subtype="init", session_id="s1", apiKeySource="apiKeyHelper")


def _bad_cost_result(cost):
    record = {"type": "result", "subtype": "success", "is_error": False, "result": "ok",
              "session_id": "s1", "modelUsage": {"claude-opus-5-5": {"costUSD": 0.01}}}
    if cost != "missing":
        record["total_cost_usd"] = cost
    return json.dumps(record)


BAD_COSTS = ["missing", None, float("nan"), float("inf"), -0.5, 10 ** 400, "0.05", True]


@pytest.mark.parametrize("cost", BAD_COSTS)
def test_a_result_without_a_usable_cost_keeps_the_reservation_held(tmp_path, book, cost):
    process = FakeProcess([init_answer(HELPER), STARTED, _bad_cost_result(cost)])
    runner, events, _ = run(process, tmp_path, book)
    final = events[-1]
    assert final.kind == "finished" and not runner.cost_complete
    assert final.data["cost_complete"] is False and final.data["sent"] is True
    totals = book.totals()
    assert totals.spent == 0 and totals.held == pytest.approx(5.0)
    assert book.can_spend() is None          # held, not damaged


def run_fake_aside(tmp_path, book, lines, billing_route="api", on_event=None):
    process = FakeProcess(lines)
    events, done = [], threading.Event()

    def got(event):
        events.append(event)
        if on_event:
            on_event(event)
        if event.kind in ("done", "failed"):
            done.set()
    runner = AsideRunner(["claude", "-p", "--resume", "chat-5", "--fork-session"],
                         str(tmp_path), "Q?", got, popen=lambda cmd, **kw: process,
                         env={"PATH": "x"}, timer=timers(), billing=billing_route, ledger=book)
    runner.start()
    assert done.wait(10)
    runner.join(10)
    return runner, events, process


@pytest.mark.parametrize("cost", BAD_COSTS)
def test_an_aside_without_a_usable_cost_keeps_its_reservation_held(tmp_path, book, cost):
    _runner, events, _ = run_fake_aside(tmp_path, book, [init_answer(HELPER), STARTED,
                                                         _bad_cost_result(cost)])
    assert events[-1].kind == "done" and events[-1].cost_complete is False
    assert book.totals().held == pytest.approx(5.0) and book.totals().spent == 0


def test_an_api_turn_never_shows_a_key_in_anything_it_says(tmp_path, book):
    # A <synthetic> error and Claude's own text can quote a key (#663).
    process = FakeProcess([
        init_answer(HELPER), STARTED,
        ev(type="assistant", message={"model": "<synthetic>", "content": [
            {"type": "text", "text": f"Invalid API key {FAKE_KEY}"}]}),
        ev(type="assistant", message={"model": "claude-opus-5-5", "content": [
            {"type": "text", "text": f"The file says {FAKE_KEY}."},
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": f"echo {FAKE_KEY}"}}]}),
        ev(type="system", subtype="api_retry", attempt=1, max_retries=10, retry_delay_ms=5,
           error_status=529, error=f"overloaded {FAKE_KEY}"),
        result(f"Done with {FAKE_KEY}", cost=0.01),
    ])
    _runner, events, _ = run(process, tmp_path, book)
    shown = json.dumps([[e.kind, e.text, e.detail, e.denials, e.data] for e in events])
    assert FAKE_KEY not in shown and billing.HIDDEN_KEY in shown
    assert {e.kind for e in events} >= {"text", "tool", "retry", "finished"}


def test_a_subscription_turn_leaves_claudes_own_text_alone(tmp_path):
    process = FakeProcess([init_answer(SUBSCRIPTION),
                           ev(type="system", subtype="init", session_id="s1", apiKeySource="none"),
                           ev(type="assistant", message={"content": [
                               {"type": "text", "text": "sk-ant-example in the docs"}]}),
                           result("ok")])
    _runner, events, _ = run(process, tmp_path, billing_route="subscription")
    assert [e.text for e in events if e.kind == "text"] == ["sk-ant-example in the docs"]


def test_an_api_aside_never_shows_a_key(tmp_path, book):
    delta = ev(type="stream_event", event={"type": "content_block_delta", "delta": {
        "type": "text_delta", "text": f"It is {FAKE_KEY}"}})
    _runner, events, _ = run_fake_aside(tmp_path, book, [
        init_answer(HELPER), STARTED, delta, result(f"It is {FAKE_KEY}", cost=0.01)])
    shown = json.dumps([[e.kind, e.text] for e in events])
    assert FAKE_KEY not in shown and billing.HIDDEN_KEY in shown
    assert any(e.kind == "text" for e in events)


def test_sent_on_api_credits_comes_only_once_the_message_is_written(tmp_path, book):
    process = FakeProcess([init_answer(HELPER), STARTED, result("ok", cost=0.01)])
    _runner, events, _ = run(process, tmp_path, book)
    kinds = [e.kind for e in events]
    assert kinds.count("api_sent") == 1 and kinds.index("api_sent") < kinds.index("started")
    assert events[kinds.index("api_sent")].data == {"kind": "prompt"}
    assert events[-1].data["sent"] is True


@pytest.mark.parametrize("account", [SUBSCRIPTION, None])
def test_a_refused_api_turn_never_says_sent(tmp_path, book, account):
    _runner, events, _ = run(FakeProcess([init_answer(account), STARTED, result("x")]),
                             tmp_path, book)
    assert "api_sent" not in [e.kind for e in events]
    assert events[-1].data["sent"] is False


def test_a_subscription_turn_never_says_sent_on_api_credits(tmp_path):
    process = FakeProcess([init_answer(SUBSCRIPTION),
                           ev(type="system", subtype="init", session_id="s1", apiKeySource="none"),
                           result("ok")])
    _runner, events, _ = run(process, tmp_path, billing_route="subscription")
    assert "api_sent" not in [e.kind for e in events]


def _live_runner(tmp_path, book, events, billing_route="api"):
    """A runner whose process is up, as mid-turn, to send into."""
    process = FakeProcess([])
    runner = TurnRunner(["claude", "-p", "--resume", "chat-1"], str(tmp_path), "Hello",
                        events.append, popen=lambda cmd, **kw: process, env={"PATH": "x"},
                        timer=timers(), billing=billing_route, ledger=book)
    runner._process, runner._stdin_open = process, True
    return runner, process


def test_every_way_a_message_goes_in_says_sent_once_written(tmp_path, book):
    events = []
    runner, process = _live_runner(tmp_path, book, events)
    assert runner.steer("early") and events == []          # waiting for initialize
    runner._send_prompt()                                   # the account was checked
    assert [e.data["kind"] for e in events] == ["prompt", "steer"]
    assert runner.steer("steered") and runner.send_now("now")
    runner.waiting_on_background = True
    assert runner.send_follow_up("follow")
    assert [e.data["kind"] for e in events if e.kind == "api_sent"] == [
        "prompt", "steer", "steer", "now", "follow_up"]
    assert runner.messages_written == 5
    process.stdin.close()                                   # Claude Code gone
    runner.waiting_on_background = True
    assert not runner.send_follow_up("lost") and len(events) == 5


def test_waiting_for_background_work_carries_the_cost_so_far(tmp_path, book):
    process = FakeProcess([
        init_answer(HELPER), STARTED,
        ev(type="system", subtype="session_state_changed", state="running"),
        ev(type="system", subtype="background_tasks_changed",
           tasks=[{"task_id": "b1", "description": "the build"}]),
        ev(type="assistant", message={"content": [{"type": "text", "text": "Started it."}]}),
        result("Started it.", cost=0.02),
        ev(type="system", subtype="session_state_changed", state="idle"),
    ])
    _runner, events, _ = run(process, tmp_path, book)
    [waiting] = [e for e in events if e.kind == "waiting"]
    assert waiting.data["billing"] == "api" and waiting.data["cost_usd"] == pytest.approx(0.02)
    assert waiting.data["tasks"] == ["the build"] and waiting.data["sent"] is True


class _StuckStdin:
    """A pipe Claude Code has stopped reading: a write waits until the
    process is killed."""

    def __init__(self, process):
        self.process, self.writing = process, threading.Event()

    def write(self, data):
        self.writing.set()
        while not self.process.killed:
            time.sleep(0.01)
        raise OSError("broken pipe")

    def flush(self):
        pass

    def close(self):
        pass


def _recording_timers(started):
    def make(interval, function):
        class Timer:
            daemon = False

            def start(self):
                started.append((interval, function))

            def cancel(self):
                pass
        return Timer()
    return make


def test_stop_never_blocks_even_with_a_write_stuck(tmp_path, book):
    started = []
    process = FakeProcess([])
    runner = TurnRunner(["claude", "-p"], str(tmp_path), "Hello", lambda e: None,
                        popen=lambda *a, **k: process, env={"PATH": "x"},
                        timer=_recording_timers(started), billing="api", ledger=book)
    runner._process, runner._stdin_open, runner._cost_known = process, True, False
    process.stdin = _StuckStdin(process)
    # Another write is stuck holding the lock (a Send Now into a full pipe).
    holder = threading.Thread(target=lambda: runner.send_now("x"), daemon=True)
    runner._prompt_sent = True
    holder.start()
    assert process.stdin.writing.wait(5)
    at = time.monotonic()
    runner.cancel()
    assert time.monotonic() - at < 0.5 and runner.cancelled
    assert [interval for interval, _ in started] == [cli.STOP_COST_WAIT]
    started[0][1]()                                 # the timer ends it
    assert process.killed
    holder.join(5)
    assert not holder.is_alive()


def test_an_asides_stop_never_blocks_either(tmp_path, book):
    started = []
    process = FakeProcess([])
    runner = AsideRunner(["claude", "-p", "--resume", "c"], str(tmp_path), "Q?", lambda e: None,
                         popen=lambda *a, **k: process, env={"PATH": "x"},
                         timer=_recording_timers(started), billing="api", ledger=book)
    runner._process, runner._cost_known = process, False
    process.stdin = _StuckStdin(process)
    at = time.monotonic()
    runner.cancel()
    assert time.monotonic() - at < 0.5
    assert [interval for interval, _ in started] == [cli.STOP_COST_WAIT]
    assert process.stdin.writing.wait(5)            # the interrupt, off the caller's thread
    started[0][1]()
    assert process.killed


def _stop_with(tmp_path, book, send):
    """A turn stopped mid-reply just after ``send(runner)`` wrote another
    message, with the interrupt's result the only one to come."""
    holder = {}

    def on_text(event):
        if event.kind == "text" and "stopped" not in holder:
            holder["stopped"] = True
            assert send(holder["runner"])
            holder["runner"].cancel()
    process = FakeProcess([
        init_answer(HELPER), STARTED,
        ev(type="assistant", message={"content": [{"type": "text", "text": "working"}]}),
        result("", cost=0.02, subtype="error_during_execution", is_error=True),
        # Anything after it is Claude Code taking the new message in: it
        # must never be read, as the process is ended at the result.
        ev(type="assistant", message={"content": [{"type": "text", "text": "new paid work"}]}),
    ])
    events, done = [], threading.Event()

    def got(event):
        events.append(event)
        on_text(event)
        if event.kind in ("finished", "failed"):
            done.set()
    runner = TurnRunner(["claude", "-p", "--resume", "chat-1"], str(tmp_path), "Hello", got,
                        popen=lambda cmd, **kw: process, env={"PATH": "x"}, timer=timers(),
                        billing="api", ledger=book)
    holder["runner"] = runner
    runner.start()
    assert done.wait(10)
    runner.join(10)
    return runner, events, process


def test_stop_with_a_steer_not_taken_in_ends_at_once_and_gives_it_back(tmp_path, book):
    # The review's case (#663): waiting after Stop let Claude Code take the
    # steer in and start a new paid request, and the steer, echoed as taken,
    # was lost. Now the process ends at the interrupt's result.
    runner, events, process = _stop_with(tmp_path, book,
                                         lambda r: r.steer("Change course"))
    assert process.killed
    assert "new paid work" not in [e.text for e in events]
    assert events[-1].text == "Stopped." and events[-1].data["cost_complete"] is False
    assert runner.steering == ["Change course"]       # the window gives it back
    assert runner.untaken() == [("steer", "Change course")]
    assert runner.echoes is False                     # so it says "may not have been"
    totals = book.totals()
    assert totals.spent == pytest.approx(0.02)
    assert totals.held == pytest.approx(5.0 - 0.02)   # whether it went in isn't known


def test_stop_after_send_now_ends_at_once_and_holds_the_new_messages_cost(tmp_path, book):
    # The first result after Stop can be the replaced work's (#663): its
    # cost is counted, and the new message's, not known, stays held.
    runner, events, process = _stop_with(tmp_path, book,
                                         lambda r: r.send_now("Do this instead"))
    assert process.killed
    assert events[-1].text == "Stopped." and events[-1].data["cost_complete"] is False
    # The result was the replaced work's: the Send Now goes back (#663).
    assert runner.untaken() == [("now", "Do this instead")]
    totals = book.totals()
    assert totals.spent == pytest.approx(0.02)
    assert totals.held == pytest.approx(5.0 - 0.02)


AUTH_RETRY = ev(type="system", subtype="api_retry", attempt=3, max_retries=10,
                retry_delay_ms=2432, error_status=401, error="authentication_failed")


def test_a_refused_key_stops_the_turn_at_once(tmp_path, book):
    # Checked with 2.1.325 and a made-up key (#663): api_retry with 401 and
    # "authentication_failed", then ten retries over about four minutes.
    process = FakeProcess([init_answer(HELPER), STARTED, AUTH_RETRY, AUTH_RETRY,
                           result("Invalid API key · Fix external API key", cost=0,
                                  is_error=True)])
    runner, events, _ = run(process, tmp_path, book)
    assert process.killed and events[-1].kind == "failed"
    assert events[-1].text == billing.KEY_REFUSED
    assert runner.stopped_before_answer               # the message goes back to you
    assert [e.kind for e in events].count("retry") == 0
    totals = book.totals()
    assert totals.held == 0 and totals.spent == 0


def test_a_key_refused_after_claude_answered_keeps_the_hold(tmp_path, book):
    # A request went through first, uncounted by any result: held.
    process = FakeProcess([
        init_answer(HELPER), STARTED,
        ev(type="assistant", message={"model": "claude-opus-5-5", "content": [
            {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a"}}]}),
        AUTH_RETRY])
    runner, events, _ = run(process, tmp_path, book)
    assert events[-1].text == billing.KEY_REFUSED and runner.stopped_mid_turn
    assert not runner.stopped_before_answer and book.totals().held == pytest.approx(5.0)


def test_other_retries_are_left_to_claude_code(tmp_path, book):
    process = FakeProcess([init_answer(HELPER), STARTED,
                           ev(type="system", subtype="api_retry", attempt=1, max_retries=10,
                              retry_delay_ms=500, error_status=529, error="overloaded"),
                           result("ok", cost=0.01)])
    _runner, events, _ = run(process, tmp_path, book)
    assert events[-1].kind == "finished" and not process.killed
    assert "retry" in [e.kind for e in events]


def test_a_subscription_turn_waits_out_auth_retries_as_before(tmp_path):
    process = FakeProcess([init_answer(SUBSCRIPTION),
                           ev(type="system", subtype="init", session_id="s1", apiKeySource="none"),
                           AUTH_RETRY, result("ok")])
    _runner, events, _ = run(process, tmp_path, billing_route="subscription")
    assert events[-1].kind == "finished" and not process.killed


def test_an_aside_with_a_refused_key_stops_at_once(tmp_path, book):
    _runner, events, process = run_fake_aside(tmp_path, book, [
        init_answer(HELPER), STARTED, AUTH_RETRY, AUTH_RETRY])
    assert events[-1].kind == "failed" and "refused your Claude API key" in events[-1].text
    assert "then ask again" in events[-1].text and process.killed
    assert book.totals().held == 0


def test_an_aside_says_sent_once_its_question_is_written(tmp_path, book):
    _runner, events, _ = run_fake_aside(tmp_path, book, [init_answer(HELPER), STARTED,
                                                         result("ZEPHYR", cost=0.01)])
    kinds = [e.kind for e in events]
    assert kinds == ["sent", "done"] and events[-1].sent is True
    _runner, events, _ = run_fake_aside(tmp_path, book, [init_answer(SUBSCRIPTION), STARTED,
                                                         result("x")])
    assert "sent" not in [e.kind for e in events] and events[-1].sent is False


def test_fable_runs_on_the_key_on_api_credits(tmp_path, book):
    # Checked live with 2.1.325 (#663): Fable 5.2 answers through apiKeyHelper.
    process = FakeProcess([
        init_answer(HELPER),
        ev(type="system", subtype="init", session_id="s1", model="claude-fable-5-2",
           apiKeySource="apiKeyHelper"),
        ev(type="assistant", message={"model": "claude-fable-5-2", "content": [
            {"type": "text", "text": "hi"}]}),
        result("hi", cost=0.03)])
    _runner, events, _ = run(process, tmp_path, book,
                             command=["claude", "-p", "--model", "fable", "--session-id", "f1"])
    assert events[-1].kind == "finished" and book.totals().spent == pytest.approx(0.03)


# -- the fake's usage reaches CostTracker's branches (#663) ---------------------------------


def _fake_resume(session):
    return [sys.executable, str(FAKE_CLAUDE), "--resume", session]


def test_a_real_resumed_turn_that_restored_cost_counts_from_the_baseline(tmp_path, book):
    book.set_baseline("abc-1", 0.0100, {"claude-haiku-5-5": 0.0100}, clean=True)
    events, _log = run_real(tmp_path, book, _fake_resume("abc-1"), FAKE_CLAUDE_ACCOUNT="api",
                            FAKE_CLAUDE_RESTORED_TOKENS="4000")
    assert events[-1].data["cost_usd"] == pytest.approx(0.0023)
    assert events[-1].data["cost_complete"] is True
    assert book.totals().spent == pytest.approx(0.0023)


def test_a_real_resumed_turn_that_restored_nothing_counts_in_full(tmp_path, book):
    book.set_baseline("abc-1", 0.0100, {"claude-haiku-5-5": 0.0100}, clean=True)
    events, _log = run_real(tmp_path, book, _fake_resume("abc-1"), FAKE_CLAUDE_ACCOUNT="api")
    assert events[-1].data["cost_usd"] == pytest.approx(0.0123)
    assert book.totals().spent == pytest.approx(0.0123)


def test_a_real_resumed_turn_with_restored_cost_and_no_baseline_says_not_known(tmp_path, book):
    events, _log = run_real(tmp_path, book, FAKE_CLAUDE_ACCOUNT="api",
                            FAKE_CLAUDE_RESTORED_TOKENS="4000")
    assert events[-1].data["cost_usd"] is None and events[-1].data["cost_complete"] is False
    assert book.totals().spent == pytest.approx(0.0123)   # still counted, up to the cap
    assert book.totals().held == 0


# -- the certificate variables Node reads at startup (#663) ---------------------------------


def test_an_api_turn_gets_no_certificate_authority_from_the_environment(tmp_path, book):
    env = {"PATH": "x", "NODE_EXTRA_CA_CERTS": "C:/corp-proxy.pem",
           "node_tls_reject_unauthorized": "0", "HTTPS_PROXY": "http://proxy:8080"}
    process = FakeProcess([init_answer(HELPER), STARTED, result()])
    _r, _e, captured = run(process, tmp_path, book, env=env)
    child = {name.upper() for name in captured["env"]}
    assert "NODE_EXTRA_CA_CERTS" not in child and "NODE_TLS_REJECT_UNAUTHORIZED" not in child
    assert captured["env"]["HTTPS_PROXY"] == "http://proxy:8080"   # still used: TLS protects


def test_a_subscription_turn_keeps_its_certificate_settings(tmp_path):
    env = {"PATH": "x", "NODE_EXTRA_CA_CERTS": "C:/corp-proxy.pem"}
    process = FakeProcess([init_answer(SUBSCRIPTION), STARTED, result()])
    _r, _e, captured = run(process, tmp_path, None, billing_route="subscription", env=env)
    assert captured["env"]["NODE_EXTRA_CA_CERTS"] == "C:/corp-proxy.pem"


def test_an_api_aside_gets_no_certificate_authority_from_the_environment(tmp_path, book):
    runner = AsideRunner(["claude", "-p", "--resume", "c"], str(tmp_path), "Q?", lambda e: None,
                         env={"PATH": "x", "NODE_EXTRA_CA_CERTS": "C:/corp.pem"},
                         billing="api", ledger=book)
    assert "NODE_EXTRA_CA_CERTS" not in runner._env


# -- permission requests on API credits (#663) ----------------------------------------------


def test_a_permission_request_shows_no_key_but_answers_with_what_claude_sent():
    command = f"curl -H 'x-api-key: {FAKE_KEY}' https://example.com"
    request = cli.PermissionRequest("r1", "Bash", {"command": command}, description=FAKE_KEY)
    assert FAKE_KEY in request.detail()                   # the subscription: as it was
    request.hide_keys = True
    assert FAKE_KEY not in request.summary() and FAKE_KEY not in request.detail()
    assert billing.HIDDEN_KEY in request.detail()
    assert cli.allow_response(request)["updatedInput"] == {"command": command}
    plan = cli.PermissionRequest("r2", cli.PLAN_TOOL, {"plan": f"Use {FAKE_KEY}"}, hide_keys=True)
    assert FAKE_KEY not in plan.plan() and plan.input["plan"] == f"Use {FAKE_KEY}"


def test_an_api_turns_permission_request_is_shown_without_the_key(tmp_path, book):
    asked = ev(type="control_request", request_id="req-1", request={
        "subtype": "can_use_tool", "tool_name": "Bash", "tool_use_id": "t1",
        "input": {"command": f"echo {FAKE_KEY}"}})
    seen, holder = {}, {}
    process = FakeProcess([init_answer(HELPER), STARTED, asked, result()])
    events, done = [], threading.Event()

    def got(event):
        events.append(event)
        if event.kind == "permission":
            seen["summary"] = event.request.summary()
            seen["activity"] = holder["runner"].last_activity
            holder["runner"].respond("req-1", cli.allow_response(event.request))
        if event.kind in ("finished", "failed"):
            done.set()
    runner = TurnRunner(["claude", "-p", "--resume", "chat-1"], str(tmp_path), "Hello", got,
                        popen=lambda cmd, **kw: process, env={"PATH": "x"}, timer=timers(),
                        billing="api", ledger=book)
    holder["runner"] = runner
    runner.start()
    assert done.wait(10)
    runner.join(10)
    permission = next(e for e in events if e.kind == "permission")
    assert FAKE_KEY not in permission.text and FAKE_KEY not in seen["summary"]
    assert FAKE_KEY not in seen["activity"]
    answer = next(m for m in process.sent() if m.get("type") == "control_response")
    assert answer["response"]["response"]["updatedInput"] == {"command": f"echo {FAKE_KEY}"}
