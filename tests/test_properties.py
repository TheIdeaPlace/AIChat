"""View, Properties (#214): the lines a session's and a message's
properties are made of, from synthetic transcripts."""
from datetime import datetime, timedelta

import pytest

from thechatplace import properties
from thechatplace.own_store import OwnSession
from thechatplace.sessions import DESKTOP, OWN, TERMINAL, SessionInfo
from thechatplace.transcript import ASSISTANT, QUEUED, TOOL, USER, ChatMessage, parse_lines

from records import (assistant_block, lines, text_block, tool_result, tool_use_block,
                     user_text)

NOW_MS = int(datetime(2026, 10, 11, 15, 0).timestamp() * 1000)


def _info(**extra):
    values = dict(source=DESKTOP, key="local_a", title="Quiet one", cwd="C:\\G\\Repo",
                  cli_session_id="cli-a", desktop_session_id="local_a",
                  created_ms=NOW_MS - 2 * 86_400_000, last_activity_ms=NOW_MS - 3_600_000)
    values.update(extra)
    return SessionInfo(**values)


def _transcript():
    return parse_lines(lines(
        user_text("Please look at https://example.com/a"),
        assistant_block(text_block("Here:\n```python\nprint(1)\n```\nand "
                                   "[docs](https://example.com/docs)"), message_id="m1"),
        assistant_block(tool_use_block("Edit", {"file_path": "C:\\G\\Repo\\a.py",
                                                "old_string": "x", "new_string": "y"}),
                        message_id="m2"),
        tool_result("toolu_1", "done"),
        user_text("Thanks"),
        assistant_block(text_block("Welcome"), message_id="m3"),
    ))


def _value(lines_, name):
    for line in lines_:
        if line.startswith(f"{name}: "):
            return line[len(name) + 2:]
    raise AssertionError(f"no {name} line in {lines_}")


def test_session_properties_count_the_whole_transcript():
    rows = properties.session_properties(_info(), _transcript(), "C:\\t\\cli-a.jsonl", 2048,
                                         now_ms=NOW_MS)
    # Every line is "Property: value".
    assert all(": " in row for row in rows)
    assert rows[0] == "Title: Quiet one"
    assert _value(rows, "Kind") == "Desktop app session"
    assert _value(rows, "Folder") == "C:\\G\\Repo"
    assert _value(rows, "Status") == "Idle"
    assert _value(rows, "Started").endswith("(2 days ago)")
    assert _value(rows, "Last activity").endswith("(1 hour ago)")
    assert _value(rows, "Model of the latest reply") == "Opus 5.5"
    assert _value(rows, "Messages") == "4"
    assert _value(rows, "Your messages") == "2"
    assert _value(rows, "Claude's replies") == "2"
    assert _value(rows, "Tool calls") == "1"
    assert _value(rows, "Links") == "2"
    assert _value(rows, "Code blocks") == "1"
    assert _value(rows, "Files changed") == "1"
    assert _value(rows, "Groups") == "none"
    assert _value(rows, "Remote Control") == "off"
    assert _value(rows, "Session ID") == "cli-a"
    assert _value(rows, "Desktop app ID") == "local_a"
    assert _value(rows, "Transcript") == "C:\\t\\cli-a.jsonl"
    assert _value(rows, "Transcript size") == "2 KB"
    # Lines for what isn't so are left out, not said as "no".
    assert not any(r.startswith(("Archived", "Hidden", "New reply", "Times compacted",
                                 "Unreadable")) for r in rows)


def test_a_session_with_no_transcript_still_says_what_is_known():
    rows = properties.session_properties(
        _info(source=TERMINAL, key="terminal:cli-t", desktop_session_id="", created_ms=0,
              state="needs you", detail="Pick a name", archived=True, hidden=True,
              unread=True, remote=True, groups=("Work", "Play")),
        None, "", 0, now_ms=NOW_MS)
    assert _value(rows, "Kind") == "Terminal session"
    assert _value(rows, "Status") == "Needs you: Pick a name"
    assert _value(rows, "Started") == "not recorded"
    assert _value(rows, "Groups") == "Work, Play"
    assert _value(rows, "Remote Control") == "on"
    assert {"Archived: yes", "Hidden: yes", "New reply: yes"} <= set(rows)
    assert _value(rows, "Transcript") == "not on disk"
    assert not any(r.startswith(("Messages", "Links", "Desktop app ID", "Model")) for r in rows)


def test_own_and_cowork_sessions():
    own = OwnSession("own-1", "Hub probe", "C:\\G\\Scratch", permission_mode="plan",
                     model="claude-sonnet-5-5", effort="high", forked_from="Quiet one")
    rows = properties.session_properties(
        _info(source=OWN, key="own:own-1", cli_session_id="own-1", desktop_session_id="",
              permission_mode="auto"), None, own=own, now_ms=NOW_MS)
    assert _value(rows, "Kind") == "Chat Place session"
    assert _value(rows, "Effort") == "High"
    # The store's mode is the one its turns use.
    assert _value(rows, "Permission mode") == "plan"
    assert _value(rows, "Continued from") == "Quiet one"
    assert "Model chosen: " in "\n".join(rows)
    cowork = properties.session_properties(
        _info(cowork=True, cowork_folder="", cwd="C:\\x\\outputs"), None, now_ms=NOW_MS)
    assert _value(cowork, "Kind") == "Cowork session"
    assert _value(cowork, "Folder") == "none"
    assert _value(cowork, "Working folder") == "C:\\x\\outputs"


def test_a_transcript_that_could_not_be_read_is_not_called_missing():
    rows = properties.session_properties(_info(), None, "C:\\t\\cli-a.jsonl", 5 * 1024,
                                         now_ms=NOW_MS, read_error="locked")
    assert _value(rows, "Transcript") == "C:\\t\\cli-a.jsonl"
    assert _value(rows, "Transcript size") == "5 KB"
    assert _value(rows, "Transcript read") == "failed (locked)"
    assert not any(r.startswith("Messages") for r in rows)


def test_context_compactions_and_unreadable_lines():
    transcript = _transcript()
    transcript.context_tokens = 124_000
    transcript.compactions = 2
    transcript.unreadable_lines = 3
    rows = properties.session_properties(_info(), transcript, "t.jsonl", 10,
                                         reported_window=200_000, now_ms=NOW_MS)
    assert _value(rows, "Context") == "62% full, 124,000 of 200,000 tokens"
    assert _value(rows, "Times compacted") == "2"
    assert _value(rows, "Unreadable transcript lines") == "3"
    unknown = properties.session_properties(_info(), transcript, "t.jsonl", 10, now_ms=NOW_MS)
    assert _value(unknown, "Context").startswith("124,000 tokens used")


def test_message_properties():
    moment = datetime(2026, 10, 11, 14, 5).astimezone()
    text = "Two lines\nwith a link https://example.com and\n```\ncode\n```"
    message = ChatMessage(ASSISTANT, text, moment.isoformat(), "uuid-1")
    rows = properties.message_properties(message, 3, 10, now=moment + timedelta(hours=2))
    assert rows == [
        "From: Claude",
        "Time: 11 October 2026 at 14:05 (2 hours ago)",
        "Position: 3 of 10",
        "Lines: 5",
        f"Words: {len(text.split())}",
        f"Characters: {len(text)}",
        "Links: 1",
        "Code blocks: 1",
        "Message ID: uuid-1",
    ]
    bare = properties.message_properties(ChatMessage(USER, ""), 1, 1)
    assert "Time: not recorded" in bare and "Lines: 0" in bare
    assert not any(r.startswith("Message ID") for r in bare)
    # A queued row's key is the app's own: say it's queued instead.
    queued = properties.message_properties(
        ChatMessage(QUEUED, "Later", key="queued:own-1:0"), 3, 3)
    assert queued[-1] == "Queued: yes, sent when Claude's turn ends"
    assert not any("queued:own-1" in r for r in queued)
    assert properties.message_properties(ChatMessage(TOOL, "Read a.py"), 1, 1)[0] == "From: Tool"


def test_sizes_and_times():
    assert properties.describe_size(1) == "1 byte"
    assert properties.describe_size(512) == "512 bytes"
    assert properties.describe_size(14 * 1024) == "14 KB"
    assert properties.describe_size(int(2.3 * 1024 * 1024)) == "2.3 MB"
    assert properties.describe_size(1024 * 1024 - 1) == "1.0 MB"  # not "1,024 KB"
    assert properties.describe_ms(0) == "not recorded"
    assert properties.describe_ms(10 ** 20) == "not recorded"  # nonsense from a bad file
    assert properties.describe_ms(NOW_MS, NOW_MS).endswith("(just now)")


def test_transcript_facts(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text("abc", encoding="utf-8")
    assert properties.transcript_facts(path) == (str(path), 3)
    assert properties.transcript_facts(tmp_path / "missing.jsonl") == ("", 0)


def test_codex_properties_do_not_invent_claude_models_policies_or_usage():
    from thechatplace.transcript import Transcript

    info = _info(source=OWN, provider="codex", key="codex:cli-a", desktop_session_id="")
    own = OwnSession("cli-a", "Codex", info.cwd, provider="codex")
    transcript = Transcript(messages=[ChatMessage(ASSISTANT, "Reply", assistant_name="Codex")],
                            model="synthetic-codex-model", context_tokens=123)
    result = properties.session_properties(info, transcript, "", 0, own, 200000, now_ms=NOW_MS)
    assert "Kind: Chat Place session" in result
    assert "Codex's replies: 1" in result
    assert "Model of the latest reply: synthetic-codex-model" in result
    assert "Model chosen: configured Codex model" in result
    assert "Sandbox: read-only" in result
    assert "History: read from Codex" in result
    assert not any(line.startswith(("Permission mode:", "Remote Control:",
                                    "Files changed:")) for line in result)
    assert not any("% full" in line or "Claude" in line for line in result)


def test_rule_check_refusal_is_distinct_from_not_checked_and_has_no_count():
    from thechatplace.properties import saved_rule_lines
    assert saved_rule_lines(pending=True) == ['Saved command rules: checking...']
    rows = saved_rule_lines(7, error='Project rules were refused. Rules folder: /synthetic/.codex/rules')
    assert 'refused' in rows[0] and '/synthetic/.codex/rules' in rows[0]
    assert not any('in effect: 7' in row or 'not checked' in row for row in rows)


def test_a_codex_chat_whose_state_is_not_known_says_unavailable_once():
    """#290: the state "status unavailable" after "Status: " said the word twice."""
    from thechatplace.sessions import STATUS_UNAVAILABLE
    info = _info(source=OWN, provider="codex", key="codex:cli-a", desktop_session_id="",
                 state=STATUS_UNAVAILABLE)
    result = properties.session_properties(info, None, "", 0, None, 0, now_ms=NOW_MS)
    assert "Status: Unavailable" in result
    assert not any("Status unavailable" in line for line in result)
    info.detail = "Codex connection interrupted: the stream closed. Use Stop to check this chat's reply and reconnect."
    result = properties.session_properties(info, None, "", 0, None, 0, now_ms=NOW_MS)
    assert ("Status: Unavailable: Codex connection interrupted: the stream closed. "
            "Use Stop to check this chat's reply and reconnect") in result


def test_other_states_still_read_as_before():
    from thechatplace.sessions import NEEDS_YOU
    result = properties.session_properties(_info(state=NEEDS_YOU, detail="Approve Bash"), None,
                                           "", 0, None, 0, now_ms=NOW_MS)
    assert "Status: Needs you: Approve Bash" in result



@pytest.mark.parametrize('provider', ['claude', 'codex'])
def test_a_task_waiting_for_permission_is_still_in_progress(provider):
    from thechatplace.sessions import NEEDS_YOU
    from thechatplace.tasklist import TaskStep
    from thechatplace.transcript import Transcript
    transcript = Transcript(tasks=[TaskStep('tests', 'Run the tests', status='in_progress')])
    info = _info(source=OWN, provider=provider, key=f'{provider}:cli-a', state=NEEDS_YOU)
    rows = properties.session_properties(info, transcript, '', 0, None, 0, now_ms=NOW_MS)
    assert 'Tasks: Task 1 of 1: Run the tests. 0 done.' in rows
