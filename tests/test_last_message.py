"""The session list's Last message column (#146), without wx: reading the end
of a transcript, remembering it, and the text the row says."""
import json
import os

import pytest

from records import (assistant_block, lines, other, text_block, thinking_block,
                     tool_result, tool_use_block, user_blocks, user_text)
from thechatplace import hub, platform_paths
from thechatplace.own_store import OwnSession
from thechatplace.sessions import DEFAULT_FIELDS, FIELD_LAST_MESSAGE, OWN, SessionInfo
from thechatplace.speech import SpeechSettings
from thechatplace.transcript import (ASSISTANT, USER, LastMessages,
                                     last_message_from_tail)
from thechatplace.ui_text import LAST_MESSAGE_LIMIT, last_message_line, one_line

SMALL = (512, 2048, 8192)  # tail steps small enough to test growing past them


def write(path, records, end="\n"):
    path.write_text("\n".join(lines(*records)) + end, encoding="utf-8")
    return path


def tool_run(count, size=200, start=0):
    """``count`` tool calls and their results: activity, never a message."""
    records = []
    for n in range(start, start + count):
        tool_id = f"toolu_{n}"
        records.append(assistant_block(tool_use_block("Bash", {"command": "ls"}, tool_id),
                                       f"msg_t{n}"))
        records.append(tool_result(tool_id, "x" * size))
    return records


# -- reading the end -----------------------------------------------------------------


def test_claudes_reply_last(tmp_path):
    path = write(tmp_path / "t.jsonl", [user_text("Can you fix it?"),
                                        assistant_block(text_block("Fixed it."), "m1")])
    message = last_message_from_tail(path)
    assert (message.kind, message.text, message.label) == (ASSISTANT, "Fixed it.", "Claude")


def test_your_message_last(tmp_path):
    path = write(tmp_path / "t.jsonl", [assistant_block(text_block("Done."), "m1"),
                                        user_text("Now the tests, please")])
    message = last_message_from_tail(path)
    assert (message.kind, message.text, message.label) == (USER, "Now the tests, please", "You")


def test_tool_calls_results_thinking_and_meta_are_skipped(tmp_path):
    path = write(tmp_path / "t.jsonl", [
        user_text("Look at the logs"),
        assistant_block(text_block("Looking now."), "m1"),
        assistant_block(thinking_block(), "m2"),
        *tool_run(3),
        user_text("<system-reminder>context</system-reminder>", isMeta=True),
        user_text("<task-notification>done</task-notification>"),
        other("system", subtype="compact_boundary"),
        other("custom-title", customTitle="x"),
        assistant_block(text_block("   "), "m3"),
    ])
    assert last_message_from_tail(path).text == "Looking now."


def test_a_reply_written_in_several_records_is_one_message(tmp_path):
    path = write(tmp_path / "t.jsonl", [user_text("Go"),
                                        assistant_block(text_block("First part."), "m1"),
                                        assistant_block(text_block("Second part."), "m1")])
    assert last_message_from_tail(path).text == "First part.\n\nSecond part."


def test_only_the_end_is_read_and_more_when_the_end_is_all_tools(tmp_path):
    path = write(tmp_path / "t.jsonl", [user_text("x" * 300) for _ in range(40)]
                 + [assistant_block(text_block("The answer."), "m1")] + tool_run(3, size=300))
    # The first step holds only tool output; a later one finds the reply.
    assert os.path.getsize(path) > SMALL[-1]
    assert last_message_from_tail(path, SMALL).text == "The answer."


def test_a_tail_of_nothing_but_tools_says_nothing(tmp_path):
    path = write(tmp_path / "t.jsonl", [user_text("Start")] + tool_run(60, size=300))
    assert os.path.getsize(path) > SMALL[-1]
    assert last_message_from_tail(path, SMALL) is None
    # Small enough to read whole: the start is found.
    assert last_message_from_tail(path).text == "Start"


def test_a_reply_cut_by_the_first_step_is_read_whole(tmp_path):
    """A reply in many records sharing an id, longer than the first step:
    it's read from its beginning, not from wherever the step began."""
    parts = [assistant_block(text_block(f"Part {n} " + "y" * 200), "m1") for n in range(10)]
    path = write(tmp_path / "t.jsonl", [user_text("Go")] + parts)
    message = last_message_from_tail(path, SMALL)
    assert message.text.startswith("Part 0 ")


def test_bad_lines_are_skipped_not_raised(tmp_path):
    path = tmp_path / "t.jsonl"
    good = lines(user_text("Hello"), assistant_block(text_block("Hi there."), "m1"))
    path.write_text("\n".join(["{not json", good[0], "[1, 2]", good[1], '{"type": "user"}',
                               "\x00\xff garbage"]) + "\n", encoding="utf-8")
    assert last_message_from_tail(path).text == "Hi there."


def test_a_huge_last_line(tmp_path):
    """A last line longer than every step is skipped over for the messages
    before it only if a step reaches past it; otherwise nothing is said."""
    path = tmp_path / "t.jsonl"
    head = lines(user_text("Before"), assistant_block(text_block("The reply."), "m1"))
    huge = lines(tool_result("toolu_1", "z" * 20_000))
    path.write_text("\n".join(head + huge) + "\n", encoding="utf-8")
    assert last_message_from_tail(path, SMALL) is None  # 20 KB line, 8 KB at most
    assert last_message_from_tail(path, SMALL + (64 * 1024,)).text == "The reply."


def test_a_huge_last_message_is_read(tmp_path):
    path = write(tmp_path / "t.jsonl", [user_text("Go"),
                                        assistant_block(text_block("w " * 50_000), "m1")])
    assert last_message_from_tail(path).text.startswith("w w")


def test_no_newline_at_the_end(tmp_path):
    path = write(tmp_path / "t.jsonl", [user_text("Go"),
                                        assistant_block(text_block("Finished."), "m1")], end="")
    assert last_message_from_tail(path).text == "Finished."


def test_a_line_still_being_written_is_left_for_later(tmp_path):
    path = tmp_path / "t.jsonl"
    done = lines(user_text("Go"), assistant_block(text_block("Done."), "m1"))
    partial = lines(user_text("Next one"))[0][:25]
    path.write_text("\n".join(done) + "\n" + partial, encoding="utf-8")
    assert last_message_from_tail(path).text == "Done."


def test_empty_missing_and_messageless_files_say_nothing(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"")
    assert last_message_from_tail(empty) is None
    assert last_message_from_tail(tmp_path / "missing.jsonl") is None
    assert last_message_from_tail(tmp_path) is None  # a folder, not a file
    blank = tmp_path / "blank.jsonl"
    blank.write_text("\n\n\n", encoding="utf-8")
    assert last_message_from_tail(blank) is None
    tools = write(tmp_path / "tools.jsonl", tool_run(2))
    assert last_message_from_tail(tools) is None


def test_the_file_is_never_written(tmp_path):
    path = write(tmp_path / "t.jsonl", [user_text("Go")])
    before = (path.read_bytes(), os.stat(path).st_mtime_ns)
    LastMessages().get(path)
    assert (path.read_bytes(), os.stat(path).st_mtime_ns) == before


# -- remembering it ------------------------------------------------------------------


def test_remembered_until_the_file_changes(tmp_path):
    cache = LastMessages()
    path = write(tmp_path / "t.jsonl", [user_text("Go"), assistant_block(text_block("One."), "m1")])
    assert cache.get(path).text == "One."
    assert cache.get(path).text == "One." and cache.reads == 1
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(lines(user_text("And two?"))[0] + "\n")
    assert cache.get(path).text == "And two?" and cache.reads == 2


def test_a_file_that_shrinks_or_is_replaced_is_read_again(tmp_path):
    cache = LastMessages()
    path = write(tmp_path / "t.jsonl", [user_text("Long one"),
                                        assistant_block(text_block("A long reply " * 20), "m1")])
    assert cache.get(path).text.startswith("A long reply")
    write(path, [user_text("Short")])  # rewritten, smaller
    assert cache.get(path).text == "Short"
    # Replaced by another file of the same size: its time differs.
    other_file = write(tmp_path / "other.jsonl", [user_text("Shorx")])
    stamp = os.stat(path)
    os.utime(other_file, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 5_000_000_000))
    os.replace(other_file, path)
    assert cache.get(path).text == "Shorx"


def test_a_step_that_starts_on_a_whole_line_keeps_it(tmp_path):
    """The part read can begin exactly where a line begins: that line is
    whole, and is read, not dropped as if it were cut."""
    path = tmp_path / "t.jsonl"
    head = ("\n".join(lines(*[user_text("x" * 300) for _ in range(10)])) + "\n").encode()
    tail = ("\n".join(lines(assistant_block(text_block("Found."), "m1"))) + "\n").encode()
    path.write_bytes(head + tail)
    message = last_message_from_tail(path, (len(tail),))  # one step, exactly the last line
    assert message is not None and message.text == "Found."


def test_a_file_busy_for_a_moment_keeps_its_message(tmp_path, monkeypatch):
    from thechatplace import transcript
    cache = LastMessages()
    path = write(tmp_path / "t.jsonl", [user_text("Go")])
    assert cache.get(path).text == "Go"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(lines(user_text("More"))[0] + "\n")

    def busy(*a, **k):
        raise PermissionError("in use")
    monkeypatch.setattr(transcript, "open", busy, raising=False)
    assert cache.get(path).text == "Go"  # what it said, not nothing
    monkeypatch.undo()
    assert cache.get(path).text == "More"


def test_a_file_that_goes_away_is_forgotten(tmp_path):
    cache = LastMessages()
    path = write(tmp_path / "t.jsonl", [user_text("Go")])
    assert cache.get(path).text == "Go"
    path.unlink()
    assert cache.get(path) is None and cache.get(None) is None
    write(path, [user_text("Back")])
    assert cache.get(path).text == "Back"


def test_keep_only_forgets_sessions_no_longer_listed(tmp_path):
    cache = LastMessages()
    first = write(tmp_path / "a.jsonl", [user_text("A")])
    second = write(tmp_path / "b.jsonl", [user_text("B")])
    cache.get(first), cache.get(second)
    cache.keep_only([second])
    cache.get(first), cache.get(second)
    assert cache.reads == 3  # only the forgotten one again


# -- the text the row says -----------------------------------------------------------


@pytest.mark.parametrize("markdown,plain", [
    ("# Done\n\nThe **build** passes and `pytest` is green.",
     "Done The build passes and pytest is green."),
    ("- first\n- second\n1. third\n> quoted", "first second third quoted"),
    ("Run this:\n\n```bash\nls -la\n```\n\nThen *wait*.", "Run this: ls -la Then wait."),
    ("| a | b |\n|---|---|\n| 1 | 2 |", "a b 1 2"),
    ("See [the docs](https://example.com/x).", "See the docs."),
    ("Keep snake_case_names and 2*3*4 as they are.",
     "Keep snake_case_names and 2*3*4 as they are."),
    ("  lots\t of\n\n\n   space  ", "lots of space"),
    ("```\n```", ""),
    ("See [the PR](https://github.com/x/y/pull/1) now.", "See the PR now."),
    ("Docs: https://www.example.com/a/b?c=1. And <https://docs.python.org/3/>",
     "Docs: example.com. And docs.python.org"),
])
def test_one_plain_line(markdown, plain):
    assert one_line(markdown) == plain


def test_a_long_message_is_cut_at_a_word():
    text = one_line("word " * 100)
    assert text.endswith("…") and len(text) <= LAST_MESSAGE_LIMIT + 1
    assert not text[:-1].endswith(" ") and text[:-1].split(" ")[-1] == "word"
    short = "a" * LAST_MESSAGE_LIMIT
    assert one_line(short) == short
    # One very long word: cut in it rather than say nothing.
    assert one_line("x" * 400) == "x" * LAST_MESSAGE_LIMIT + "…"


def test_the_column_says_who():
    assert last_message_line("Claude", "**Done.**") == "Claude: Done."
    assert last_message_line("You", "Thanks") == "You: Thanks"
    assert last_message_line("You", "  \n ") == ""


# -- the column in the list ----------------------------------------------------------


def test_the_column_is_there_but_off_by_default(tmp_path):
    assert FIELD_LAST_MESSAGE not in DEFAULT_FIELDS
    assert FIELD_LAST_MESSAGE not in SpeechSettings().session_fields
    # An older order gains Provider without gaining the optional Last message.
    path = tmp_path / "settings.json"
    saved = ["status", "title", "folder", "activity"]
    path.write_text(json.dumps({"session_fields": saved}), encoding="utf-8")
    assert SpeechSettings.load(path).session_fields == saved + ["provider"]
    # Chosen, it's kept where it was put.
    path.write_text(json.dumps({"session_fields": ["title", "last_message", "status"]}),
                    encoding="utf-8")
    assert SpeechSettings.load(path).session_fields == ["title", "last_message", "status", "provider"]


def test_the_row_reads_the_last_message_where_it_was_put():
    info = SessionInfo(source=OWN, key="own:x", title="Fix", cwd="C:\\G\\Repo",
                       cli_session_id="x", last_message="Claude: All done.")
    assert info.list_line(0, ["title", "last_message", "status"]) == \
        "Fix, Claude: All done., idle"
    assert "All done" not in info.list_line(0)  # the default doesn't read it
    info.last_message = ""
    assert info.list_line(0, ["title", "last_message"]) == "Fix"


def _own_with_transcript(tmp_path, monkeypatch, cli, records, cwd="C:\\G\\Repo"):
    projects = tmp_path / "projects"
    monkeypatch.setattr(platform_paths, "projects_dir", lambda: projects)
    folder = projects / platform_paths.encode_cwd(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    write(folder / f"{cli}.jsonl", records)
    return OwnSession(cli, f"Session {cli}", cwd, last_activity_ms=1)


def test_collect_reads_last_messages_only_when_asked(tmp_path, monkeypatch):
    own = [_own_with_transcript(tmp_path, monkeypatch, "own-1",
                                [user_text("Go"), assistant_block(text_block("**Done.**"), "m")]),
           OwnSession("own-2", "No transcript", "C:\\G\\Elsewhere", last_activity_ms=1)]
    cache = LastMessages()
    monkeypatch.setattr(hub, "LAST_MESSAGES", cache)
    snap = hub.collect(own, set(), desktop_dir=tmp_path / "d", live_dir=tmp_path / "l")
    assert [s.last_message for s in snap.sessions] == ["", ""] and cache.reads == 0
    snap = hub.collect(own, set(), desktop_dir=tmp_path / "d", live_dir=tmp_path / "l",
                       last_messages=True)
    by_key = {s.key: s.last_message for s in snap.sessions}
    assert by_key == {"own:own-1": "Claude: Done.", "own:own-2": ""}
    hub.collect(own, set(), desktop_dir=tmp_path / "d", live_dir=tmp_path / "l",
                last_messages=True)
    assert cache.reads == 1  # the second pass read nothing


def test_a_missing_transcript_is_not_searched_for_every_refresh(tmp_path, monkeypatch):
    calls = []
    real = SessionInfo.transcript_path

    def counted(self):
        calls.append(self.key)
        return real(self)
    monkeypatch.setattr(SessionInfo, "transcript_path", counted)
    monkeypatch.setattr(platform_paths, "projects_dir", lambda: tmp_path / "projects")
    monkeypatch.setattr(hub, "_NO_TRANSCRIPT", {})
    info = SessionInfo(source=OWN, key="own:gone", title="Gone", cwd="C:\\G\\Gone",
                       cli_session_id="gone", last_activity_ms=5)
    cache = LastMessages()
    for _ in range(3):
        hub.fill_last_messages([info], cache)
    assert calls == ["own:gone"] and info.last_message == ""
    info.last_activity_ms = 6  # active since: looked for again
    hub.fill_last_messages([info], cache)
    assert len(calls) == 2
    monkeypatch.setattr(hub, "NO_TRANSCRIPT_RETRY", 0.0)  # and after a while
    hub.fill_last_messages([info], cache)
    assert len(calls) == 3
    hub.forget_last_messages()
    assert hub._NO_TRANSCRIPT == {}


def test_a_cowork_session_reads_its_own_claude_home(tmp_path):
    home = tmp_path / "cowork-home"
    folder = home / "projects" / platform_paths.encode_cwd("C:\\outputs")
    folder.mkdir(parents=True)
    write(folder / "cw-1.jsonl", [user_text("Sort my receipts")])
    info = SessionInfo(source="desktop", key="local_cw", title="Receipts", cwd="C:\\outputs",
                       cli_session_id="cw-1", cowork=True, claude_home=home)
    assert hub.fill_last_message(info, cache=LastMessages()) == "You: Sort my receipts"


def test_timing_with_sixty_sessions(tmp_path, monkeypatch):
    """~60 sessions, some with multi-megabyte transcripts: the first pass
    reads only their ends, and a pass with nothing changed reads nothing.
    Generous bounds, so a slow CI machine doesn't fail it; the numbers are
    printed with -s."""
    import time
    own = []
    big_body = [user_text("q " * 400) if n % 2 else
                assistant_block(text_block("a " * 400), f"m{n}") for n in range(6000)]
    for n in range(60):
        records = big_body if n % 6 == 0 else big_body[:40]
        records = records + [user_text(f"Question {n}"),
                             assistant_block(text_block(f"Answer {n}"), "end")]
        if n % 4 == 0:
            records = records + tool_run(30, size=2000)  # a tail of tool output
        own.append(_own_with_transcript(tmp_path, monkeypatch, f"own-{n}", records,
                                        cwd=f"C:\\G\\Repo{n}"))
    sizes = [os.path.getsize(platform_paths.transcript_path(o.cwd, o.cli_session_id))
             for o in own]
    assert max(sizes) > 4 * 1024 * 1024
    cache = LastMessages()
    monkeypatch.setattr(hub, "LAST_MESSAGES", cache)

    def timed(**kw):
        start = time.perf_counter()
        snap = hub.collect(own, set(), desktop_dir=tmp_path / "d", live_dir=tmp_path / "l", **kw)
        return snap, time.perf_counter() - start
    _snap, off = timed()
    snap, first = timed(last_messages=True)
    _snap, again = timed(last_messages=True)
    print(f"\n60 sessions ({sum(sizes) / 1e6:.0f} MB): column off {off * 1000:.0f} ms, "
          f"first pass {first * 1000:.0f} ms, unchanged {again * 1000:.0f} ms")
    assert all(s.last_message == f"Claude: Answer {s.cli_session_id[4:]}"
               for s in snap.sessions)
    assert cache.reads == 60
    # Generous: a slow CI machine is far inside these.
    assert first < 15 and again < 5
