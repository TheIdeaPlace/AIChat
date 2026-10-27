"""Messages as formatted pages (#190): structure kept, nothing unsafe let in."""
import pytest

from thechatplace.rendering import describe_code_block, markdown_to_html, message_page
from thechatplace.speech import SpeechSettings


def test_headings_lists_and_tables_keep_their_structure():
    out = markdown_to_html("## Result\n\n- one\n- two\n\n| Name | Value |\n|---|---|\n| a | 1 |")
    assert "<h2>Result</h2>" in out
    assert "<ul>\n<li>one</li>\n<li>two</li>\n</ul>" in out
    assert "<th>Name</th>" in out and "<td>1</td>" in out


def test_code_blocks_are_named_regions():
    out = markdown_to_html("```python\ndef f():\n    return 1\n```\n\n```\nplain\n```")
    assert '<section role="region" aria-label="Code block, Python, 2 lines">' in out
    assert '<section role="region" aria-label="Code block, 1 line">' in out
    assert "def f():" in out


@pytest.mark.parametrize("language,code,words", [
    ("py", "a\nb\nc\n", "Code block, Python, 3 lines"),
    ("", "x", "Code block, 1 line"),
    ("zig", "x\ny", "Code block, zig, 2 lines"),
    ("ps1", "", "Code block, PowerShell, 0 lines"),
])
def test_describe_code_block(language, code, words):
    assert describe_code_block(language, code) == words


def test_raw_html_and_images_are_text_and_bad_links_are_dropped():
    out = markdown_to_html("<script>alert(1)</script>\n\nhi <b>x</b> ![p](http://x/p.png) "
                           "[ok](https://example.com) [bad](javascript:alert(1)) "
                           "[file](file:///C:/x)")
    assert "<script" not in out and "&lt;script&gt;" in out
    assert "<b>" not in out and "&lt;b&gt;" in out
    assert "<img" not in out
    assert '<a href="https://example.com">ok</a>' in out
    assert "javascript:" not in out and "file:" not in out
    assert "<a>bad</a>" in out


def test_page_allows_nothing_remote():
    page = message_page('Claude "says"', "Hello")
    assert "default-src 'none'" in page
    assert "<title>Claude &quot;says&quot;</title>" in page
    assert "<body><p>Hello</p></body>" in page


def test_page_in_the_app_has_no_landmark():
    # A landmark around the whole window only added "main region" before
    # the message (#341); the published pages keep an unnamed one.
    from thechatplace.rendering import html_page
    page = message_page("Message: Claude", "Hello", heading="Claude")
    assert "<main" not in page and "aria-label=\"Message" not in page
    assert "<body><main><p>x</p></main></body>" in html_page("Guide", "<p>x</p>", landmark=True)


def test_message_page_starts_with_who_wrote_it():
    page = message_page("Message: Claude", "# Plan\n\nText\n\n## Step <one>\n\nSetext\n------\n\n###### Deep",
                        heading="Claude")
    body = page[page.index("<body>"):]
    assert body.startswith("<body><h1>Claude</h1>")
    # The message's own headings move down one level, under the speaker.
    assert "<h2>Plan</h2>" in body and "<h3>Step &lt;one&gt;</h3>" in body
    assert "<h3>Setext</h3>" in body and "<h6>Deep</h6>" in body
    assert body.count("<h1>") == 1


def test_message_page_heading_is_escaped_and_code_is_left_alone():
    page = message_page("t", "```html\n<h1>not a heading</h1>\n```\n\nSay <h2> here", heading="From <Codex>")
    assert "<h1>From &lt;Codex&gt;</h1>" in page
    assert "&lt;h1&gt;not a heading&lt;/h1&gt;" in page and "Say &lt;h2&gt; here" in page
    assert page.count("<h1>") == 1 and "<h2" not in page


def test_message_page_without_a_heading_is_unchanged():
    # The User Guide and Claude's plan bring their own headings.
    page = message_page("User Guide", "# The Chat Place")
    assert "<body><h1>The Chat Place</h1></body>" in page


def test_formatted_setting_round_trips(tmp_path):
    path = tmp_path / "speech.json"
    assert SpeechSettings.load(path).formatted_messages  # on by default
    SpeechSettings(formatted_messages=False).save(path)
    assert not SpeechSettings.load(path).formatted_messages


def test_shortcuts_page_has_a_heading_and_a_table_per_group():
    from thechatplace.rendering import html_page
    from thechatplace.ui_text import SHORTCUTS, shortcuts_html
    body = shortcuts_html()
    assert body.startswith("<h1>Keyboard shortcuts</h1>")
    assert body.count("<h2 ") == len(SHORTCUTS) == body.count("<table ")
    assert '<th scope="col">Keys</th><th scope="col">What it does</th>' in body
    assert '<tr><th scope="row">F1</th><td>This list of shortcuts</td></tr>' in body
    # Each table is named by the heading above it, not by a second label.
    assert '<h2 id="group2">Session list</h2>\n<table aria-labelledby="group2">' in body
    assert "aria-label=" not in body
    # Every shortcut is in it.
    rows = sum(len(items) for _group, items in SHORTCUTS)
    assert body.count('<th scope="row">') == rows
    assert "default-src 'none'" in html_page("Keyboard Shortcuts", body)


def test_shortcuts_page_escapes_everything(monkeypatch):
    from thechatplace import ui_text
    monkeypatch.setattr(ui_text, "SHORTCUTS", [("A<b>", [("x&y", '"q" <i>')])])
    monkeypatch.setattr(ui_text, "LAYOUT", "<script>")
    body = ui_text.shortcuts_html()
    assert "<b>" not in body and "<i>" not in body and "<script>" not in body
    assert "<h2 id=\"group1\">A&lt;b&gt;</h2>" in body
    assert '<th scope="row">x&amp;y</th><td>&quot;q&quot; &lt;i&gt;</td>' in body


def test_markdown_as_text_reads_without_marks():
    # #104: the user guide's text box, Read as Plain Text and on a Mac.
    from thechatplace.ui_text import markdown_as_text
    text = markdown_as_text("# Guide\n\n## Part two\n\nPress **Ctrl+N** and `claude`; see the "
                            "[releases page](https://example.com/r), or "
                            "[https://example.com](https://example.com).\n- __Bold__ item")
    assert text == ("Guide\n\nPart two\n\nPress Ctrl+N and claude; see the releases page "
                    "(https://example.com/r), or https://example.com.\n- Bold item")


def test_markdown_as_text_reads_a_table_row_by_row():
    # The guide's tables, in the text box: no pipes or rules, each row with
    # its column names, so it reads as sentences.
    from thechatplace.ui_text import markdown_as_text
    text = markdown_as_text("Intro.\n\n| Feature | Claude | Codex |\n|---|---|---|\n"
                            "| **Reply** | yes | no: `Stop` |\n| Delete | yes |  |\n\nAfter.")
    assert text == "Intro.\n\nReply: Claude yes; Codex no: Stop\nDelete: Claude yes\n\nAfter."


def test_markdown_as_text_leaves_pipes_that_arent_a_table():
    from thechatplace.ui_text import markdown_as_text
    assert markdown_as_text("| not a table |\nplain line") == "| not a table |\nplain line"
    assert markdown_as_text("| a | b |\n|:---|---:|") == ""  # a header and rule, no rows


def test_the_user_guide_as_text_has_no_tables_left():
    from thechatplace.platform_paths import user_guide_path
    from thechatplace.ui_text import markdown_as_text
    text = markdown_as_text(user_guide_path().read_text(encoding="utf-8"))
    assert not [line for line in text.splitlines() if line.lstrip().startswith("|")]
    assert "Copy Session Link: Claude yes; Codex no; GitHub Copilot CLI no" in text


def test_the_user_guide_as_text_has_no_markdown_left():
    from thechatplace.platform_paths import user_guide_path
    from thechatplace.ui_text import markdown_as_text
    text = markdown_as_text(user_guide_path().read_text(encoding="utf-8"))
    assert text.startswith("The Chat Place User Guide\n")
    for mark in ("**", "](", "`", "\n#"):
        assert mark not in text, mark
