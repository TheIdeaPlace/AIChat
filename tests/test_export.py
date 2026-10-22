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
