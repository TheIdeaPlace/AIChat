"""Live checks of API credits (#658, #662, #663) against the real Anthropic API,
through the app's own code: Test Key, a new chat on API credits, a second
message in it (resumed: only that message's cost is counted), Stop mid-reply,
two chats alternating in one folder and the app's fork path (each message
counted once), and, with a made-up key (free), that a settings file can't send
the key elsewhere and that a refused key is said at once.

Skipped unless TCP_LIVE_API=1 and TCP_LIVE_API_KEY_FILE names a file holding a
real key; never in CI. A run spends a few cents at most, on Haiku. Run this before
any build with an API-credits change goes to a person: every fake in the other
tests is only as right as what we believed Claude Code says, and #662 shipped
because a believed answer was wrong (initialize names a helper's key as
tokenSource, not apiKeySource)."""
import json
import os
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


def run(command, cwd, prompt, ledger, purpose="turn", on_text=None, env=None, wait=180):
    events, done = [], threading.Event()
    holder = {}

    def on_event(event):
        events.append(event)
        if event.kind == "text" and on_text:
            on_text(holder["runner"])
        if event.kind in ("finished", "failed"):
            done.set()
    runner = claude_cli.TurnRunner(command, cwd, prompt, on_event, billing=billing.ROUTE_API,
                                   purpose=purpose, ledger=ledger, env=env,
                                   cost_baselines=ledger)
    holder["runner"] = runner
    runner.start()
    assert done.wait(wait), f"no answer in {wait} seconds"
    runner.join(30)
    return runner, events


def run_ok(command, cwd, prompt, ledger):
    runner, events = run(command, cwd, prompt, ledger)
    assert events[-1].kind == "finished" and not events[-1].is_error, \
        billing.scrub(events[-1].text)
    return runner, events[-1]


# -- a made-up key: nothing is spent ----------------------------------------------------------

MADE_UP_KEY = "sk-ant-api03-" + "madeupmadeupmadeup" * 4 + "AA"


@pytest.fixture
def made_up(tmp_path, monkeypatch):
    """A made-up key kept the app's way, and a scratch Claude Code config."""
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path / "data")
    platform_paths.save_secret(platform_paths.CLAUDE_API_KEY_SECRET, MADE_UP_KEY)
    lookup = platform_paths.find_claude()
    assert lookup.path, lookup.problem
    config = tmp_path / "config"
    config.mkdir()
    folder = tmp_path / "chat"
    folder.mkdir()
    return {"claude": lookup.path, "ledger": spend_ledger.SpendLedger(tmp_path / "data"),
            "folder": str(folder), "config": config}


class _Listener:
    """A local "API" that records each request (never its headers) and
    refuses it, as Anthropic refuses a bad key."""

    def __init__(self):
        self.requests = []
        listener = self

        class Handler(BaseHTTPRequestHandler):
            def _refuse(self):
                listener.requests.append((self.command, self.path))
                body = json.dumps({"type": "error", "error": {
                    "type": "authentication_error", "message": "invalid x-api-key"}}).encode()
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_HEAD = _refuse

            def log_message(self, *args):
                pass  # never the request, which carries the (made-up) key
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _config_pointing_at(config: Path, url: str) -> dict:
    """A CLAUDE_CONFIG_DIR whose settings.json sends Claude Code elsewhere,
    as a cloned project's settings or a user's proxy could (#663)."""
    (config / "settings.json").write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": url}}),
                                          encoding="utf-8")
    return {**claude_cli.child_environment(), "CLAUDE_CONFIG_DIR": str(config)}


def test_a_settings_files_address_never_gets_the_key(made_up, monkeypatch):
    listener = _Listener()
    try:
        env = _config_pointing_at(made_up["config"], listener.url)
        test_key = claude_cli.build_key_test_command(made_up["claude"])
        # Unpinned first, to show the settings file really would send it there.
        pinned = billing.API_ENV
        monkeypatch.setattr(billing, "API_ENV", {})
        _runner, events = run(test_key, made_up["folder"], claude_cli.KEY_TEST_PROMPT,
                              made_up["ledger"], purpose="test", env=env, wait=60)
        assert listener.requests, "the settings file's address wasn't used: the check proves nothing"
        assert MADE_UP_KEY not in json.dumps([[e.kind, e.text] for e in events])
        monkeypatch.setattr(billing, "API_ENV", pinned)
        listener.requests.clear()
        # Pinned, as every API turn is.
        _runner, events = run(test_key, made_up["folder"], claude_cli.KEY_TEST_PROMPT,
                              made_up["ledger"], purpose="test", env=env, wait=60)
        assert listener.requests == []
        # It went to Anthropic, which refused the made-up key: so the turn ran,
        # NODE_EXTRA_CA_CERTS "" and all.
        assert events[-1].kind == "failed" and events[-1].text == billing.KEY_REFUSED
        assert made_up["ledger"].totals().held == 0
    finally:
        listener.close()


def test_a_refused_key_is_said_at_once_and_nothing_stays_held(made_up):
    # Claude Code retries a refused key for about four minutes (#663).
    started = time.monotonic()
    runner, events = run(claude_cli.build_key_test_command(made_up["claude"]),
                         made_up["folder"], claude_cli.KEY_TEST_PROMPT, made_up["ledger"],
                         purpose="test", wait=60)
    took = time.monotonic() - started
    assert events[-1].kind == "failed" and events[-1].text == billing.KEY_REFUSED, events[-1].text
    assert took < 30, f"took {took:.0f} seconds"
    assert runner.stopped_before_answer
    totals = made_up["ledger"].totals()
    assert totals.held == 0 and totals.spent == 0
    assert MADE_UP_KEY not in json.dumps([[e.kind, e.text, e.data] for e in events])


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


def test_two_chats_alternating_in_one_folder_are_each_counted_once(world):
    # The review's case (#663): Claude Code restores each chat's own cost on
    # --resume even with another chat run in between, and the ledger must
    # count each message once: in all, what the two chats' totals say.
    ledger, folder, exe = world["ledger"], world["folder"], world["claude"]
    chats = {name: str(uuid.uuid4()) for name in "AB"}
    last = {}
    counted = []
    for turn, name in enumerate("ABAB"):
        session = chats[name]
        if name in last:
            command = claude_cli.build_resume_command(exe, session, "default", set(chats.values()),
                                                      set(), model="haiku")
        else:
            command = claude_cli.build_new_command(exe, session, f"Live {name}", "default",
                                                   model="haiku")
        before = ledger.totals().spent
        runner, final = run_ok(command, folder, f"Reply with the single word {turn}.", ledger)
        added = ledger.totals().spent - before
        counted.append(added)
        assert 0 < added < 0.01, (name, added)
        assert final.data["cost_usd"] == pytest.approx(added, rel=0.01)
        total = ledger.baseline(session)[0]
        if name in last:
            # Restored, as checked: what's counted is what this chat's own
            # total grew by, this message alone, not its history again.
            assert runner.costs.restored is True
            assert added == pytest.approx(total - last[name], rel=0.01)
        else:
            assert runner.costs.restored is False and added == pytest.approx(total, rel=0.01)
        last[name] = total
    # Every message counted once: the two chats' own running totals.
    assert ledger.totals().spent == pytest.approx(last["A"] + last["B"], rel=0.01)
    assert ledger.totals().held == 0


def test_the_apps_fork_path_counts_only_the_copys_own_message(world):
    # Continue Here, Continue on API Credits and branches copy a chat with
    # build_fork_command; its first total includes the source's (#663).
    ledger, folder, exe = world["ledger"], world["folder"], world["claude"]
    source, copy = str(uuid.uuid4()), str(uuid.uuid4())
    run_ok(claude_cli.build_new_command(exe, source, "Live source", "default", model="haiku"),
           folder, "Remember the word lantern. Reply OK.", ledger)
    source_total = ledger.baseline(source)[0]
    before = ledger.totals().spent
    runner, final = run_ok(claude_cli.build_fork_command(exe, source, copy, "Live copy", "default",
                                                         model="haiku"),
                           folder, "What word did I ask you to remember? One word.", ledger)
    added = ledger.totals().spent - before
    copy_total = ledger.baseline(copy)[0]
    assert runner.costs.restored is True
    assert added == pytest.approx(copy_total - source_total, rel=0.01)
    assert 0 < added < copy_total
    assert "lantern" in final.text.lower()
    assert ledger.totals().held == 0
