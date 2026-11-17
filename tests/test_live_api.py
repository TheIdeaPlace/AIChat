"""Live checks of API credits (#658, #662) against the real Anthropic API, through
the app's own code: Test Key, a new chat on API credits, a second message in it
(resumed: only that message's cost is counted), and Stop mid-reply.

Skipped unless TCP_LIVE_API=1 and TCP_LIVE_API_KEY_FILE names a file holding a
real key; never in CI. Each run spends about a cent, on Haiku. Run this before
any build with an API-credits change goes to a person: every fake in the other
tests is only as right as what we believed Claude Code says, and #662 shipped
because a believed answer was wrong (initialize names a helper's key as
tokenSource, not apiKeySource)."""
import os
import re
import threading
import uuid
from pathlib import Path

import pytest

from thechatplace import billing, claude_cli, platform_paths, spend_ledger

LIVE = os.environ.get("TCP_LIVE_API") == "1" and os.environ.get("TCP_LIVE_API_KEY_FILE")
pytestmark = pytest.mark.skipif(not LIVE, reason="live API checks: set TCP_LIVE_API=1 and "
                                "TCP_LIVE_API_KEY_FILE")


@pytest.fixture
def world(tmp_path, monkeypatch):
    """The key kept the app's way, in a scratch app-data folder; the real claude."""
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path)
    text = Path(os.environ["TCP_LIVE_API_KEY_FILE"]).read_text(encoding="utf-8")
    key = re.search(r"sk-ant-[A-Za-z0-9_\-]+", text).group(0)
    assert billing.looks_like_key(key)
    platform_paths.save_secret(platform_paths.CLAUDE_API_KEY_SECRET, key)
    lookup = platform_paths.find_claude()
    assert lookup.path, lookup.problem
    folder = tmp_path / "chat"
    folder.mkdir()
    return {"claude": lookup.path, "ledger": spend_ledger.SpendLedger(tmp_path),
            "folder": str(folder)}


def run(command, cwd, prompt, ledger, purpose="turn", on_text=None):
    events, done = [], threading.Event()
    holder = {}

    def on_event(event):
        events.append(event)
        if event.kind == "text" and on_text:
            on_text(holder["runner"])
        if event.kind in ("finished", "failed"):
            done.set()
    runner = claude_cli.TurnRunner(command, cwd, prompt, on_event, billing=billing.ROUTE_API,
                                   purpose=purpose, ledger=ledger)
    holder["runner"] = runner
    runner.start()
    assert done.wait(180), "no answer in three minutes"
    runner.join(30)
    return runner, events


def test_test_key_answers_on_the_key(world):
    runner, events = run(claude_cli.build_key_test_command(world["claude"]), world["folder"],
                         claude_cli.KEY_TEST_PROMPT, world["ledger"], purpose="test")
    last = events[-1]
    assert last.kind == "finished" and not last.is_error, billing.scrub(last.text)
    account = runner.parser.init_body["account"]
    assert billing.key_source(account) == billing.HELPER_SOURCE
    assert 0 < last.data["cost_usd"] < 0.05
    assert world["ledger"].totals().spent == pytest.approx(last.data["cost_usd"])
    assert world["ledger"].totals().held == 0


def test_a_chat_and_its_second_message_are_each_counted_once(world):
    session = str(uuid.uuid4())
    new = claude_cli.build_new_command(world["claude"], session, "Live check", "default",
                                       model="haiku")
    _, first = run(new, world["folder"], "Reply with the single word one.", world["ledger"])
    assert first[-1].kind == "finished" and not first[-1].is_error, billing.scrub(first[-1].text)
    after_first = world["ledger"].totals().spent
    resume = claude_cli.build_resume_command(world["claude"], session, "default", {session},
                                             set(), model="haiku")
    _, second = run(resume, world["folder"], "Reply with the single word two.", world["ledger"])
    assert second[-1].kind == "finished" and not second[-1].is_error
    added = world["ledger"].totals().spent - after_first
    # Claude Code's figure on a resume includes the first message; only the
    # second message's own cost may be added (#658 review).
    assert 0 < added < after_first * 1.5, (after_first, added)
    assert second[-1].data["cost_usd"] == pytest.approx(added, rel=0.01)


def test_stop_counts_what_the_reply_cost_and_holds_nothing(world):
    session = str(uuid.uuid4())
    new = claude_cli.build_new_command(world["claude"], session, "Live stop", "default",
                                       model="haiku")
    stopped = []

    def stop(runner):
        if not stopped:
            stopped.append(True)
            runner.cancel()
    _, events = run(new, world["folder"], "Write a 1500-word story about a lighthouse. No tools.",
                    world["ledger"], on_text=stop)
    assert stopped and events[-1].text == "Stopped."
    totals = world["ledger"].totals()
    assert totals.held == 0 and 0 < totals.spent < 0.05
