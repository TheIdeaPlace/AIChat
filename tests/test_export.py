"""Saving a session to a file (#33)."""
from datetime import datetime

from thechatplace import export
from thechatplace.transcript import ASSISTANT, USER, ChatMessage

WHEN = datetime(2026, 10, 7, 4, 5)
MESSAGES = [ChatMessage(USER, "Fix the build", "", "a"),
            ChatMessage(ASSISTANT, "# Plan\n```\n# not a heading\n```\n<b>bold?</b> Done", "", "b")]


def test_markdown_has_a_heading_per_message_and_shifts_theirs():
    text = export.to_markdown("Build fix", MESSAGES, "C:\\G\\Repo", WHEN)
    assert text.startswith("# Build fix\n\nExported from The Chat Place on 2026-10-07 at 04:05.")
    assert "Folder: C:\\G\\Repo" in text
    assert "## You\n\nFix the build\n" in text
    assert "## Claude\n\n### Plan\n```\n# not a heading\n```" in text  # code left alone


def test_text_is_plain_with_underlined_headings():
    text = export.to_text("Build fix", MESSAGES, "", WHEN)
    assert text.startswith("Build fix\n=========\nExported")
    assert "You\n---\nFix the build\n" in text
    assert "# Plan" in text  # as written


def test_web_page_is_inert_and_structured():
    page = export.to_html("Build <fix>", MESSAGES, "", WHEN)
    assert "<h1>Build &lt;fix&gt;</h1>" in page
    assert '<section aria-labelledby="m2"><h2 id="m2">Claude</h2><h3>Plan</h3>' in page
    assert "<b>" not in page and "&lt;b&gt;" in page  # HTML in a message is text
    assert "default-src 'none'" in page
    assert "<body><main><h1>" in page  # a file opened in a browser keeps its landmark (#341)


def test_message_times_and_heading_shift_limits():
    assert export.message_time("") == "" and export.message_time("garbage") == ""
    assert len(export.message_time("2026-10-07T03:42:00Z")) == 5
    assert export.shift_headings("###### deep\n##### five") == "###### deep\n###### five"
    assert export.shift_headings("#not a heading") == "#not a heading"


def test_default_filename_is_safe():
    assert export.default_filename("Fix: the/build?", WHEN) == "Fix the build 2026-10-07.md"
    assert export.default_filename("", WHEN, export.HTML) == "Session 2026-10-07.html"
    assert export.default_filename("..", WHEN) == "Session 2026-10-07.md"


def test_render_picks_the_format():
    assert export.render(export.TEXT, "T", MESSAGES, when=WHEN).startswith("T\n=")
    assert export.render(export.HTML, "T", MESSAGES, when=WHEN).startswith("<!DOCTYPE html>")
    assert export.render(export.MARKDOWN, "T", MESSAGES, when=WHEN).startswith("# T")


def test_fences_are_matched_by_marker_and_length():
    assert export.shift_headings("````md\n```\n# in\n````\n# out") == \
        "````md\n```\n# in\n````\n### out"
    assert export.shift_headings("```\n~~~\n# code\n```\n# real") == "```\n~~~\n# code\n```\n### real"
    assert export.shift_headings("   ## indented\n#") == "   #### indented\n###"


def test_the_date_is_said_when_the_day_changes():
    messages = [ChatMessage(USER, "a", "2026-10-06T15:00:00", "1"),
                ChatMessage(ASSISTANT, "b", "2026-10-06T15:05:00", "2"),
                ChatMessage(USER, "c", "2026-10-07T09:15:00", "3")]
    text = export.to_markdown("T", messages, when=WHEN)
    assert "## You, 6 October 2026, 15:00" in text
    assert "## Claude, 15:05" in text
    assert "## You, 7 October 2026, 09:15" in text


def test_a_cut_file_name_has_no_double_space():
    name = export.default_filename("x" * 79 + " y", WHEN)
    assert "  " not in name and name.endswith(" 2026-10-07.md")


# -- Many chats at once (#461) -----------------------------------------------------------

def msg(role, text):
    return ChatMessage(role, text, "", "x")


def test_export_many_writes_one_file_each_and_never_replaces(tmp_path):
    when = datetime(2026, 10, 31, 9, 0)
    (tmp_path / "Plan 2026-10-31.md").write_text("mine", encoding="utf-8")
    chats = [("Plan", "/work/a", "a"), ("Plan", "/work/b", "b"), ("Other: one", "", "c")]
    messages = {"a": [msg(USER, "Hello a")], "b": [msg(ASSISTANT, "Hello b")], "c": [msg(USER, "C")]}
    seen = []
    result = export.export_many(chats, str(tmp_path), export.MARKDOWN, messages.get,
                                progress=lambda done, total: seen.append((done, total)), when=when)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["Other  one 2026-10-31.md".replace("  ", " "), "Plan 2026-10-31 (2).md",
                     "Plan 2026-10-31 (3).md", "Plan 2026-10-31.md"]
    assert (tmp_path / "Plan 2026-10-31.md").read_text(encoding="utf-8") == "mine"
    assert "Hello a" in (tmp_path / "Plan 2026-10-31 (2).md").read_text(encoding="utf-8")
    assert [(t, n) for t, _p, n in result.written] == [("Plan", 1), ("Plan", 1), ("Other: one", 1)]
    assert not result.failed and not result.stopped
    assert seen[0] == (0, 3) and seen[-1] == (3, 3)


def test_export_many_goes_on_past_a_chat_that_fails(tmp_path):
    def read(source):
        if source == "bad":
            raise OSError("its history is gone")
        return [] if source == "empty" else [msg(USER, "Fine")]
    chats = [("Bad", "", "bad"), ("Empty", "", "empty"), ("Good", "", "good")]
    result = export.export_many(chats, str(tmp_path), export.TEXT, read)
    assert [t for t, _p, _n in result.written] == ["Good"]
    assert result.failed == [("Bad", "its history is gone"), ("Empty", "it has no messages")]
    text = export.many_summary(result, str(tmp_path))
    assert text.startswith(f"Exported 1 chat to {tmp_path}.")
    assert "2 couldn't be exported:\n- Bad: its history is gone\n- Empty: it has no messages" in text


def test_export_many_stops_when_asked(tmp_path):
    calls = []
    def read(source):
        calls.append(source)
        return [msg(USER, source)]
    chats = [(f"Chat {n}", "", str(n)) for n in range(5)]
    result = export.export_many(chats, str(tmp_path), export.HTML, read,
                                cancelled=lambda: len(calls) >= 2)
    assert result.stopped and len(result.written) == 2 and calls == ["0", "1"]
    assert export.many_summary(result, "F").startswith("Export stopped. 2 chats were exported to F")
    assert all(p.suffix == ".html" for p in tmp_path.iterdir())


def test_export_many_leaves_no_partial_file_when_a_write_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(export, "render", lambda *a, **k: 12345)   # not text: write fails
    result = export.export_many([("Chat", "", "a")], str(tmp_path), export.MARKDOWN,
                                lambda source: [msg(USER, "Hi")])
    assert not result.written and result.failed[0][0] == "Chat"
    assert list(tmp_path.iterdir()) == []


def test_export_many_summary_when_nothing_went(tmp_path):
    result = export.ManyResult()
    result.failed = [(f"T{n}", "no") for n in range(12)]
    text = export.many_summary(result, "F")
    assert text.startswith("No chats were exported.\n12 couldn't be exported:")
    assert "- T9: no" in text and "- T10" not in text and text.endswith("and 2 more.")


def test_chat_row_names_the_ai_folder_and_day():
    row = export.chat_row("Fix it", "Codex", r"C:\work\proj" + "\\", 1_790_000_000_000)
    assert row.startswith("Fix it, Codex, proj, last active ")
    assert export.chat_row("New", "Claude", "", 0) == "New, Claude, no folder"
