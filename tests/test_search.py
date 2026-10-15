"""Find in All Sessions (#109): searching each session's transcript."""
from datetime import datetime, timezone

from records import assistant_block, lines, text_block, tool_use_block, user_text

from thechatplace import search
from thechatplace.search import Target, describe_moment, search_sessions, snippet
from thechatplace.transcript import ASSISTANT, TOOL, ChatMessage, Transcript


def test_backend_history_search_needs_no_claude_file_and_keeps_speaker():
    history = Transcript(messages=[
        ChatMessage(ASSISTANT, "Migration ready", key="item-1", assistant_name="Codex"),
        ChatMessage(TOOL, "Migration tool", key="tool-1"),
    ])

    def wrong_path():
        raise AssertionError("Backend history must not look for a Claude file")

    target = Target("codex:one", "Build", wrong_path, read_history=lambda: history)
    results = search_sessions([target], "migration")
    assert [(m.message_key, m.who) for m in results.matches] == [("item-1", "Codex")]
    assert results.searched == 1 and results.unreadable == 0
    target.with_activity = True
    assert len(search_sessions([target], "migration").matches) == 2


def test_unavailable_backend_history_does_not_stop_other_searches(tmp_path):
    def broken():
        raise OSError("Backend disconnected")

    good = _transcript(tmp_path, "good", user_text("hello"))
    targets = [Target("codex:missing", "Missing", lambda: None, read_history=lambda: None),
               Target("codex:broken", "Broken", lambda: None, read_history=broken),
               _target("claude:good", "Good", good)]
    results = search_sessions(targets, "hello")
    assert results.unreadable == 2 and results.searched == 1
    assert results.matches[0].title == "Good"


def _transcript(tmp_path, name, *records):
    path = tmp_path / f"{name}.jsonl"
    path.write_text("\n".join(lines(*records)) + "\n", encoding="utf-8")
    return path


def _target(key, title, path, activity=False):
    return Target(key, title, lambda: path, activity)


def test_finds_messages_in_each_session_newest_first(tmp_path):
    one = _transcript(tmp_path, "one", user_text("Plan the database migration"),
                      assistant_block(text_block("The **database migration** is ready."), "m1"),
                      user_text("thanks"))
    two = _transcript(tmp_path, "two", user_text("Nothing about it here"))
    results = search_sessions([_target("own:a", "Build", one), _target("own:b", "Docs", two)],
                              "Database Migration")
    assert [(m.title, m.who) for m in results.matches] == [("Build", "Claude"), ("Build", "You")]
    assert results.matches[0].snippet == "The database migration is ready."
    assert results.matches[0].index == 1 and results.matches[1].index == 0
    assert results.searched == 2 and results.unreadable == 0 and results.sessions == 1
    assert results.summary() == ('2 messages in 1 session contain "Database Migration". '
                                 "Searched 2 sessions.")


def test_missing_and_unreadable_transcripts_are_counted(tmp_path):
    good = _transcript(tmp_path, "good", user_text("hello there"))
    folder = tmp_path / "a-folder.jsonl"
    folder.mkdir()  # can't be read as a file

    def broken():
        raise OSError("drive gone")
    targets = [_target("k1", "Good", good), Target("k2", "Gone", lambda: None),
               Target("k3", "Broken", broken), _target("k4", "Folder", folder)]
    results = search_sessions(targets, "nowhere")
    assert results.searched + results.unreadable == 4
    assert results.unreadable >= 2
    assert results.summary().startswith('No messages contain "nowhere". Searched ')
    assert "had no transcript to read." in results.summary()


def test_tool_calls_count_only_with_tool_activity(tmp_path):
    path = _transcript(tmp_path, "t", user_text("go"),
                       assistant_block(tool_use_block("Bash", {"command": "pytest -q"}), "m1"))
    assert search_sessions([_target("k", "T", path)], "pytest").matches == []
    assert len(search_sessions([_target("k", "T", path, activity=True)], "pytest").matches) == 1


def test_empty_text_finds_nothing_and_cancel_stops(tmp_path):
    path = _transcript(tmp_path, "t", user_text("anything"))
    assert search_sessions([_target("k", "T", path)], "  ").matches == []
    results = search_sessions([_target("k", "T", path)], "any", cancelled=lambda: True)
    assert results.searched == 0 and results.matches == []


def test_results_are_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(search, "MAX_RESULTS", 3)
    path = _transcript(tmp_path, "t", *[user_text(f"word {i}") for i in range(6)])
    results = search_sessions([_target("k", "T", path)], "word")
    assert len(results.matches) == 3 and results.more
    assert results.summary().startswith('The first 3 messages in 1 session contain "word"')


def test_snippet_is_around_the_words_and_cut_at_spaces():
    text = ("alpha " * 30) + "the needle is here " + ("omega " * 40)
    at = text.find("needle")
    piece = snippet(text, at, len("needle"))
    assert piece.startswith("…alpha") and piece.endswith("…")
    assert "the needle is here" in piece
    assert len(piece) <= search.SNIPPET_LENGTH + 2
    assert snippet("short needle", 6, 6) == "short needle"


def test_moments_read_naturally():
    def local(*parts):
        return datetime(*parts).astimezone()  # naive is local time, summer or winter
    now = local(2026, 10, 10, 15, 0)
    assert describe_moment(local(2026, 10, 10, 9, 5).isoformat(), now) == "09:05"
    assert describe_moment(local(2026, 10, 9, 9, 5).isoformat(), now) == "yesterday 09:05"
    utc = local(2026, 3, 3, 9, 5).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    assert describe_moment(utc, now) == "3 March 09:05"  # as transcripts write it
    assert describe_moment(local(2025, 10, 10, 9, 5).isoformat(), now) == "10 October 2025"
    assert describe_moment("", now) == "" and describe_moment("not a time", now) == ""


def test_row():
    match = search.Match("k", "Build", "m", "Claude", "", "the migration ran")
    assert match.row() == "Build, Claude: the migration ran"



def test_the_cap_stops_the_whole_search(tmp_path, monkeypatch):
    monkeypatch.setattr(search, "MAX_RESULTS", 3)
    paths = [_transcript(tmp_path, f"s{n}", *[user_text(f"word {i}") for i in range(4)])
             for n in range(5)]
    done = []
    results = search_sessions([_target(f"k{n}", f"S{n}", p) for n, p in enumerate(paths)],
                              "word", progress=done.append)
    assert len(results.matches) == 3 and results.more
    assert results.searched == 1 and results.total == 5
    assert results.summary() == ('The first 3 messages in 1 session contain "word". '
                                 "Searched 1 of 5 sessions before stopping.")


def test_text_whose_case_changes_length_still_shows_the_match(tmp_path):
    path = _transcript(tmp_path, "t", user_text("İ" * 100 + " needle here"),
                       user_text("Straße " * 30 + "the needle"))
    results = search_sessions([_target("k", "T", path)], "NEEDLE")
    assert len(results.matches) == 2
    assert all("needle" in m.snippet for m in results.matches)


def test_snippets_cut_at_line_breaks_and_keep_addresses():
    text = "\n".join(["before"] * 40) + "\nthe needle\n" + "\n".join(["after"] * 40)
    piece = snippet(text, text.find("needle"), 6)
    assert "needle after after" in piece and piece.startswith("…before")
    url = "see https://example.com/path/needle/here for more"
    assert "example.com/path/needle/here" in snippet(url, url.find("needle"), 6)


def test_progress_is_reported_per_session(tmp_path):
    a = _transcript(tmp_path, "a", user_text("x"))
    done = []
    search_sessions([_target("a", "A", a), Target("b", "B", lambda: None)], "x",
                    progress=done.append)
    assert done == [1, 2]



def test_the_window_title_says_how_many_and_for_what_first(tmp_path, monkeypatch):
    path = _transcript(tmp_path, "t", user_text("a needle"), user_text("another needle"))
    results = search_sessions([_target("k", "T", path)], "needle")
    assert results.title("The Chat Place") == ('The Chat Place: 2 results for "needle", '
                                               "Find in All Sessions")
    one = search_sessions([_target("k", "T", path)], "another")
    assert one.title("App") == 'App: 1 result for "another", Find in All Sessions'
    monkeypatch.setattr(search, "MAX_RESULTS", 1)
    capped = search_sessions([_target("k", "T", path)], "needle")
    assert capped.title("App") == 'App: the first 1 results for "needle", Find in All Sessions'
