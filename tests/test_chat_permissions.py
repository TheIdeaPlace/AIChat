"""File, Chat Permissions without windows (#307, #329): rows, notes, kept-rule
times in the session store, and each provider's host. Synthetic data only."""
from types import SimpleNamespace

import pytest

from thechatplace import chat_permissions as cp
from thechatplace.own_store import OwnSession, OwnSessionStore

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


def test_confirmations_name_what_goes_and_when_it_counts():
    row = cp.claude_rows(["Bash(git push:*)"])[0]
    assert cp.confirm_text(row, 1) == (
        "Remove this rule? Run git push (any arguments). Claude will ask again from the next turn.")
    assert cp.confirm_text(None, 3) == (
        "Remove all 3 rules from this chat? Claude will ask again from the next turn.")


def test_native_rule_file_count_reads_like_properties():
    from thechatplace.ui_text import CODEX_SAVED_RULES_NOTE
    assert cp.native_rule_files(pending=True) == "Saved user/system rule files: checking..."
    two = cp.native_rule_files(2)
    # Properties' words, and what such rules let run without asking.
    assert two.startswith("Saved user/system rule files in effect: 2. ")
    assert CODEX_SAVED_RULES_NOTE in two and cp.CODEX_NATIVE in two
    assert cp.native_rule_files(0) == "Saved user/system rule files in effect: 0."
    assert cp.native_rule_files(None) == "Saved user/system rule files: not checked."
    assert "couldn't be checked. Busy" in cp.native_rule_files(error="Busy")


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


def test_claude_host_changes_the_mode_of_its_own_chat_not_the_selected_one(tmp_path):
    from thechatplace.ui.permission_hosts import ClaudePermissions
    frame = Frame(tmp_path)
    asked = []
    frame.on_change_permission_mode = lambda info=None: asked.append(info)
    info = frame.store.get("own-1").to_info()
    finished = []
    ClaudePermissions(frame, info).change_mode(lambda: finished.append(True))
    assert [i.cli_session_id for i in asked] == ["own-1"] and finished == [True]


def test_claude_host_follows_its_chat_when_claude_reports_a_new_id(tmp_path):
    from thechatplace.ui.permission_hosts import ClaudePermissions
    frame = Frame(tmp_path)
    host = ClaudePermissions(frame, frame.store.get("own-1").to_info())
    frame.store.rename_id("own-1", "own-1b")
    seen, done = _results()
    host.load(done)
    assert [r.rule_id for r in seen[-1][0]] == ["Bash(git push:*)", "WebSearch"]
    host.remove("WebSearch", done)
    assert _store(tmp_path).get("own-1b").allowed_tools == ["Bash(git push:*)"]


def test_remove_all_of_one_rule_names_it():
    row = cp.claude_rows(["WebSearch"])[0]
    assert cp.confirm_text(row, 1) == (
        "Remove this rule? Search the web. Claude will ask again from the next turn.")


def test_codex_host_keeps_nothing_and_reads_codexs_own_rule_files(tmp_path):
    frame = Frame(tmp_path)
    host, record = _codex(tmp_path, frame)
    seen, done = _results()
    host.load(done)
    assert seen[-1] == ([], None)
    # Nothing to remove, so no Remove; and no claim that nothing runs unasked.
    assert host.can_remove is False and not hasattr(host, "remove")
    assert host.empty_text == cp.CODEX_EMPTY != cp.EMPTY
    assert host.note() == cp.CODEX_ABOUT and "saved rule files" in host.note()
    assert host.mode_text() == "Can edit files in this folder; asks before anything else"
    counted = []
    frame._load_codex_rules = lambda folder, callback: callback(2, False)
    host.native(counted.append)
    assert counted == [cp.native_rule_files(2)]
    frame._load_codex_rules = lambda folder, callback: callback("Codex is busy.", True)
    host.native(counted.append)
    assert counted[-1] == "Saved user/system rule files: couldn't be checked. Codex is busy."


def test_kept_folders_are_listed_after_rules_and_can_be_removed(tmp_path):
    from thechatplace.ui.permission_hosts import ClaudePermissions
    folder = str(tmp_path / "work")
    frame = Frame(tmp_path)
    frame.store.update("own-1", allowed_dirs=[folder], allowed_dirs_at={folder: ALLOWED_MS})
    host = ClaudePermissions(frame, frame.store.get("own-1").to_info())
    seen, done = _results()
    host.load(done)
    rows = seen[-1][0]
    assert [r.rule_id for r in rows] == ["Bash(git push:*)", "WebSearch", cp.FOLDER + folder]
    assert rows[-1].text().startswith(f"Work in the folder {folder}. Allowed ")
    host.remove(cp.FOLDER + folder, done)
    assert [r.rule_id for r in seen[-1][0]] == ["Bash(git push:*)", "WebSearch"]
    kept = _store(tmp_path).get("own-1")
    assert kept.allowed_dirs == [] and kept.allowed_dirs_at == {}
    assert kept.allowed_tools == ["Bash(git push:*)", "WebSearch"]
    host.remove(cp.FOLDER + folder, done)
    assert seen[-1] == (None, "That rule is no longer kept.")
    frame.store.update("own-1", allowed_dirs=[folder])
    host.remove(None, done)
    kept = _store(tmp_path).get("own-1")
    assert seen[-1] == ([], None) and kept.allowed_tools == [] and kept.allowed_dirs == []


def test_kept_folders_survive_a_restart_and_bad_ones_are_dropped(tmp_path):
    from thechatplace.own_store import _session_from_dict
    folder = str(tmp_path)
    loaded = _session_from_dict({
        "cli_session_id": "own-1", "title": "Chat", "cwd": folder,
        "allowed_dirs": [folder, "-x", "relative", 3],
        "allowed_dirs_at": {folder: ALLOWED_MS, "relative": ALLOWED_MS, "gone": ALLOWED_MS}})
    assert loaded.allowed_dirs == [folder] and loaded.allowed_dirs_at == {folder: ALLOWED_MS}
    codex = _session_from_dict({"cli_session_id": "codex-1", "title": "Chat", "cwd": folder,
                                "provider": "codex", "allowed_dirs": [folder]})
    assert codex.allowed_dirs == []
    store = _store(tmp_path)
    store.add(OwnSession("own-2", "Chat", folder, allowed_dirs=[folder]))
    assert _store(tmp_path).get("own-2").allowed_dirs == [folder]
