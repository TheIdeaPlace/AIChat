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


def result(text="ok", cost=0.05, subtype="success", is_error=False, **extra):
    return ev(type="result", subtype=subtype, is_error=is_error, result=text, session_id="s1",
              total_cost_usd=cost,
              modelUsage={"claude-opus-5-5": {"costUSD": cost, "inputTokens": 10}}, **extra)


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
    runner = TurnRunner(command or ["claude", "-p", "--resume", "chat-1"], str(tmp_path),
                        "Hello", on_event, popen=popen, env={"PATH": "x"},
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
                        "apiKeyHelper": platform_paths.api_key_helper_command()}
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


def _turn_lines(cost, session="s1"):
    return [init_answer(HELPER),
            ev(type="system", subtype="init", session_id=session, apiKeySource="apiKeyHelper"),
            ev(type="result", subtype="success", is_error=False, result="ok", session_id=session,
               total_cost_usd=cost, modelUsage={"claude-haiku-5-5": {"costUSD": cost}})]


def test_a_resumed_turn_counts_only_its_own_cost_not_the_chats_earlier_turns(tmp_path, book):
    # Measured with Claude Code 2.1.325 on the real API (#658): a resumed
    # turn's total_cost_usd includes the chat's earlier turns.
    run(FakeProcess(_turn_lines(0.00125, "chat-1")), tmp_path, book,
        command=["claude", "-p", "--session-id", "chat-1"])
    assert book.baseline("chat-1")[0] == pytest.approx(0.00125)
    _runner, events, _ = run(FakeProcess(_turn_lines(0.00153, "chat-1")), tmp_path, book,
                             command=["claude", "-p", "--resume", "chat-1"])
    assert events[-1].data["cost_usd"] == pytest.approx(0.00028)
    assert book.totals().spent == pytest.approx(0.00153)      # 0.00125 + 0.00028
    assert book.baseline("chat-1")[0] == pytest.approx(0.00153)


def test_a_fork_counts_from_its_sources_baseline(tmp_path, book):
    book.set_baseline("source-1", 0.40, {"claude-haiku-5-5": 0.40})
    _runner, events, _ = run(FakeProcess(_turn_lines(0.43, "copy-1")), tmp_path, book,
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
    _runner, events, _ = run(FakeProcess(_turn_lines(7.5, "copy-2")), tmp_path, book,
                             command=["claude", "-p", "--resume", "desktop-1", "--fork-session",
                                      "--session-id", "copy-2"])
    assert events[-1].data["cost_usd"] == pytest.approx(2.0)
    assert book.totals().spent == pytest.approx(2.0)
    assert book.baseline("copy-2")[0] == pytest.approx(7.5)   # the next turn counts from here


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
    _runner, events, _ = run(FakeProcess(_turn_lines(0.95, "paid")), tmp_path, book,
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
        "apiKeyHelper": platform_paths.api_key_helper_command()}
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
                        "apiKeyHelper": platform_paths.api_key_helper_command()}
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


def test_an_older_release_keeps_billing_but_runs_on_the_subscription(tmp_path):
    from legacy_016_own_store import OwnSessionStore as OldStore
    store = OwnSessionStore(tmp_path / "sessions.json")
    store.add(OwnSession("aaaa-5", "t", str(tmp_path), billing="api", model="fable"))
    old = OldStore(tmp_path / "sessions.json").get("aaaa-5")
    assert old is not None and old.model == ""             # no Fable for it
    assert not hasattr(old, "billing")                     # it doesn't know the field


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
