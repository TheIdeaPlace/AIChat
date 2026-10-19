"""File, Chat Permissions without windows (#307, #329): rows, notes, kept-rule
times in the session store, and each provider's host. Synthetic data only."""
from types import SimpleNamespace

import pytest

from thechatplace import chat_permissions as cp
from thechatplace.own_store import OwnSession, OwnSessionStore
from thechatplace.providers.codex_permissions import CodexChatRule

ALLOWED_MS = 1_792_400_000_000


@pytest.mark.parametrize("rule, words", [
    ("Bash(git push:*)", "Run git push (any arguments)."),
    ("Bash(npm test)", "Run npm test."),
    ("Bash", "Run any command."),
    ("PowerShell(npm init -y)", "Use PowerShell for npm init -y."),
    ("Read(//c/work/**)", "Read //c/work/**."),
    ("Edit", "Edit any file."),
    ("WebFetch(domain:docs.python.org)", "Fetch pages from docs.python.org."),
    ("WebSearch", "Search the web."),
    ("mcp__github__create_issue", "Use the create_issue tool from github."),
    ("mcp__github", "Use any tool from github."),
])
def test_claude_rules_read_in_plain_words(rule, words):
    assert cp.describe_claude_rule(rule) == words


def test_row_reads_the_whole_rule_and_when_it_was_allowed():
    timed, untimed = cp.claude_rows(["Bash(git push:*)", "WebSearch"],
                                    {"Bash(git push:*)": ALLOWED_MS})
    assert timed.text().startswith("Run git push (any arguments). Bash(git push:*). Allowed ")
    assert " at " in timed.text()
    # A rule kept before times were: never an invented time.
    assert untimed.text() == "Search the web. WebSearch. Allowed: time not recorded."
    assert cp.allowed_when(0) == cp.allowed_when(-5) == "Allowed: time not recorded."


def test_codex_rows_show_prefix_time_and_access_but_never_the_scope():
    rule = CodexChatRule("a" * 32, ("git", "push"), "/work", "SECRET-SCOPE", ALLOWED_MS,
                         ("Network access to github.com (HTTPS)",))
    row, = cp.codex_rows([rule])
    assert row.rule_id == "a" * 32
    text = row.text()
    assert text.startswith(rule.description + " Prefix: git push. Allowed ")
    assert text.endswith("Network access to github.com (HTTPS).")
    assert "SECRET-SCOPE" not in text


def test_confirmations_name_what_goes_and_when_it_counts():
    row = cp.claude_rows(["Bash(git push:*)"])[0]
    assert cp.confirm_text(row, 1, "claude") == (
        "Remove this rule? Run git push (any arguments). Claude will ask again from the next turn.")
    assert cp.confirm_text(None, 3, "codex") == (
        "Remove all 3 rules from this chat? Codex will ask again from its next request.")


def test_native_rule_file_count_reads_like_properties():
    assert cp.native_rule_files(pending=True).endswith("checking...")
    assert cp.native_rule_files(2).startswith("Saved Codex rule files in effect: 2. ")
    assert cp.CODEX_NATIVE in cp.native_rule_files(0)
    assert cp.native_rule_files(None) == "Saved Codex rule files: not checked."
    assert "couldn't be checked. Busy" in cp.native_rule_files(error="Busy")


def test_notice_identity_keeps_id_types_apart():
    assert cp.notice_key("chat", ("turn", 7, "x")) != cp.notice_key("chat", ("turn", "7", "x"))
    assert cp.notice_key("chat", ("turn", 7, "a")) == cp.notice_key("chat", ("turn", 7, "b"))
    assert cp.notice_key("one", ("turn", 7, "a")) != cp.notice_key("two", ("turn", 7, "a"))


def test_codex_answer_button_and_note_promise_no_unrestricted_arguments():
    rule = CodexChatRule("a" * 32, ("git", "commit"), "/work", "scope", ALLOWED_MS)
    name = cp.codex_chat_rule_name(rule)
    assert name.startswith("Allow git commit for the rest of this chat")
    assert "anything else still asks" in name and "any arguments" not in name
    note = cp.codex_chat_rule_note(rule)
    assert "Anything else still asks" in note and "Codex's own session and saved rules stay off" in note


# -- kept-rule times in the session store ------------------------------------------------------


def _store(tmp_path):
    return OwnSessionStore(tmp_path / "sessions.json")


def test_rule_times_survive_a_restart_and_older_stores_have_none(tmp_path):
    store = _store(tmp_path)
    store.add(OwnSession("own-1", "Chat", str(tmp_path), allowed_tools=["Bash(git push:*)"]))
    # As an older release left it: rules, no times.
    assert _store(tmp_path).get("own-1").allowed_tools_at == {}
    store.update("own-1", allowed_tools=["Bash(git push:*)", "WebSearch"],
                 allowed_tools_at={"WebSearch": ALLOWED_MS})
    again = _store(tmp_path).get("own-1")
    assert again.allowed_tools == ["Bash(git push:*)", "WebSearch"]
    assert again.allowed_tools_at == {"WebSearch": ALLOWED_MS}


def test_bad_or_orphaned_rule_times_are_dropped_on_load(tmp_path):
    from thechatplace.own_store import _session_from_dict
    loaded = _session_from_dict({
        "cli_session_id": "own-1", "title": "Chat", "cwd": str(tmp_path),
        "allowed_tools": ["WebSearch"],
        "allowed_tools_at": {"WebSearch": ALLOWED_MS, "Bash(gone:*)": ALLOWED_MS,
                             "--bad flag": ALLOWED_MS}})
    assert loaded.allowed_tools_at == {"WebSearch": ALLOWED_MS}
    for times in ({"WebSearch": "soon"}, {"WebSearch": True}, {"WebSearch": 0}, ["WebSearch"]):
        loaded = _session_from_dict({"cli_session_id": "own-1", "title": "Chat",
                                     "cwd": str(tmp_path), "allowed_tools": ["WebSearch"],
                                     "allowed_tools_at": times})
        assert loaded.allowed_tools_at == {}


def test_a_codex_record_never_carries_claude_rule_times(tmp_path):
    from thechatplace.own_store import _session_from_dict
    loaded = _session_from_dict({"cli_session_id": "codex-1", "title": "Chat", "cwd": str(tmp_path),
                                 "provider": "codex", "allowed_tools": ["WebSearch"],
                                 "allowed_tools_at": {"WebSearch": ALLOWED_MS}})
    assert loaded.allowed_tools == [] and loaded.allowed_tools_at == {}


# -- the hosts, with a stand-in window ---------------------------------------------------------


class Frame:
    def __init__(self, tmp_path, writes=True):
        self.store = _store(tmp_path)
        self.store.add(OwnSession("own-1", "Chat", str(tmp_path),
                                  allowed_tools=["Bash(git push:*)", "WebSearch"],
                                  allowed_tools_at={"WebSearch": ALLOWED_MS}))
        self._runners = {}
        self._codex_states = {}
        self._codex_policy_pending = set()
        self.said = []
        self.writes = writes
        self.work = []
        self.sessions = SimpleNamespace(owned_action=lambda *a: self.work.append(a))

    def _store_write(self, method, *args, **kwargs):
        if not self.writes:
            return False
        method(*args, **kwargs)
        return True

    def _feedback(self, text):
        self.said.append(text)

    def _codex_work(self, action, done):
        try:
            result, error = action(), False
        except Exception as exc:  # noqa: BLE001 - as the real worker reports it
            result, error = str(exc), True
        done(result, error)


def _results():
    seen = []
    return seen, lambda rows, error: seen.append((rows, error))


def test_claude_host_lists_removes_one_then_all_and_keeps_times_in_step(tmp_path):
    from thechatplace.ui.permission_hosts import ClaudePermissions
    frame = Frame(tmp_path)
    host = ClaudePermissions(frame, frame.store.get("own-1").to_info())
    seen, done = _results()
    host.load(done)
    assert [r.rule_id for r in seen[-1][0]] == ["Bash(git push:*)", "WebSearch"]
    host.remove("WebSearch", done)
    rows, error = seen[-1]
    assert error is None and [r.rule_id for r in rows] == ["Bash(git push:*)"]
    kept = _store(tmp_path).get("own-1")
    assert kept.allowed_tools == ["Bash(git push:*)"] and kept.allowed_tools_at == {}
    assert frame.said[-1] == "Removed. Claude will ask again from the next turn."
    host.remove("WebSearch", done)
    assert seen[-1] == (None, "That rule is no longer kept.")
    host.remove(None, done)
    assert seen[-1] == ([], None)
    assert _store(tmp_path).get("own-1").allowed_tools == []


def test_claude_host_never_claims_a_removal_it_could_not_save(tmp_path):
    from thechatplace.ui.permission_hosts import ClaudePermissions
    frame = Frame(tmp_path, writes=False)
    host = ClaudePermissions(frame, frame.store.get("own-1").to_info())
    seen, done = _results()
    host.remove("WebSearch", done)
    assert seen[-1][0] is None and "still keeps it" in seen[-1][1]
    assert not frame.said
    assert _store(tmp_path).get("own-1").allowed_tools == ["Bash(git push:*)", "WebSearch"]


def test_claude_host_says_a_running_turn_keeps_its_rules(tmp_path):
    from thechatplace.ui.permission_hosts import ClaudePermissions
    frame = Frame(tmp_path)
    host = ClaudePermissions(frame, frame.store.get("own-1").to_info())
    assert host.note() == cp.CLAUDE_TAKES_EFFECT
    frame._runners["own-1"] = object()
    assert host.note() == cp.CLAUDE_RUNNING
    assert host.can_change_mode() == (True, "")


def _codex(tmp_path, frame):
    from thechatplace.ui.permission_hosts import CodexPermissions
    record = OwnSession("codex-1", "Codex chat", str(tmp_path), provider="codex",
                        provider_options={"sandbox": "workspace-write", "approval_policy": "on-request"})
    return CodexPermissions(frame, record.to_info(), record), record


def test_codex_host_uses_worker_actions_and_reports_failures(tmp_path):
    frame = Frame(tmp_path)
    host, record = _codex(tmp_path, frame)
    rule = CodexChatRule("a" * 32, ("git", "commit"), str(tmp_path), "scope", ALLOWED_MS)
    calls = []

    def action(name, rec, *args):
        calls.append((name, rec, args))
        if name == "chat_rules":
            return (rule,)
        if name == "remove_chat_rule":
            if args[0] == "missing":
                raise ValueError("That chat rule no longer exists")
            return ()
    frame.sessions.owned_action = action
    seen, done = _results()
    host.load(done)
    assert seen[-1][0][0].rule_id == "a" * 32 and calls[-1][:2] == ("chat_rules", record)
    host.remove("a" * 32, done)
    assert seen[-1] == ([], None) and calls[-1] == ("remove_chat_rule", record, ("a" * 32,))
    assert frame.said[-1] == "Removed. Codex will ask again from its next request."
    host.remove("missing", done)
    assert seen[-1] == (None, "That chat rule no longer exists")
    host.remove(None, done)
    assert calls[-1] == ("remove_chat_rule", record, (None,))
    assert host.mode_text() == "Can edit files in this folder; asks before anything else"


def test_codex_host_refuses_a_mode_change_while_working(tmp_path):
    from thechatplace.sessions import IDLE, WORKING
    frame = Frame(tmp_path)
    host, record = _codex(tmp_path, frame)
    key = record.to_info().key
    frame._codex_states[key] = SimpleNamespace(state=WORKING, outcome="inProgress")
    allowed, why = host.can_change_mode()
    assert not allowed and "finish or stop the turn first" in why
    frame._codex_states[key] = SimpleNamespace(state=IDLE, outcome="completed")
    assert host.can_change_mode() == (True, "")
    frame._codex_policy_pending.add(key)
    assert not host.can_change_mode()[0]
