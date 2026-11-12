"""Tools, Links (#190): finding the links in a session's messages."""
import pytest

from thechatplace.message_links import FoundLink, find_links, links_in_text, matches
from thechatplace.transcript import ASSISTANT, USER, ChatMessage


def msg(kind, text):
    return ChatMessage(kind, text)


def test_markdown_angle_and_bare_links_in_order():
    text = ("See the [release notes](https://github.com/o/r/releases/tag/v1), "
            "<https://example.com/a> and https://docs.example.com/x.")
    assert links_in_text(text) == [
        ("https://github.com/o/r/releases/tag/v1", "release notes", False),
        ("https://example.com/a", "", False),
        ("https://docs.example.com/x", "", False),
    ]


@pytest.mark.parametrize("text, url", [
    ("(see https://x.org/a).", "https://x.org/a"),
    ("https://en.wikipedia.org/wiki/Foo_(bar), then", "https://en.wikipedia.org/wiki/Foo_(bar)"),
    ("**https://x.org/b**", "https://x.org/b"),
    ("Done: https://x.org/c?q=1&r=2!", "https://x.org/c?q=1&r=2"),
    ("[Foo](https://en.wikipedia.org/wiki/Foo_(bar))", "https://en.wikipedia.org/wiki/Foo_(bar)"),
    ('[t](https://x.org/d "Title")', "https://x.org/d"),
])
def test_addresses_end_where_they_should(text, url):
    assert links_in_text(text)[0][0] == url


def test_links_in_code_are_marked_and_markdown_in_code_is_not_a_link():
    text = ("Run:\n```bash\ncurl https://api.example.com/v1 [x](https://not.md/)\n```\n"
            "and `open https://inline.example.com` too.")
    found = links_in_text(text)
    assert ("https://api.example.com/v1", "", True) in found
    assert ("https://inline.example.com", "", True) in found
    # Inside a code block, [x](…) is code: only the bare address is found.
    assert all(words == "" for _url, words, _code in found)


def test_words_that_are_the_address_again_read_as_the_address():
    assert links_in_text("[https://x.org/a](https://x.org/a)") == [("https://x.org/a", "", False)]
    assert links_in_text("[x.org/a](https://x.org/a)") == [("https://x.org/a", "", False)]


def test_find_links_is_newest_first_once_each_with_who_and_when():
    messages = [msg(USER, "Look at https://a.org/one"),
                msg(ASSISTANT, "Sure: [the doc](https://b.org/doc) and https://a.org/one"),
                msg(USER, "thanks"),
                msg(ASSISTANT, "Also https://c.org/")]
    found = find_links(messages)
    # Newest message first; within a message, in the order they're written.
    assert [f.url for f in found] == ["https://c.org/", "https://b.org/doc", "https://a.org/one"]
    assert found[0].row() == "c.org/. Claude, the latest message"
    assert found[1].row() == "the doc, b.org/doc. Claude, 2 messages ago"
    assert found[2].row() == "a.org/one. Claude, 2 messages ago"
    assert found[2].ago == 2 and found[2].who == "Claude"


def test_a_repeated_address_keeps_words_from_an_older_mention():
    messages = [msg(ASSISTANT, "[release notes](https://x.org/r)"),
                msg(USER, "https://x.org/r")]
    (link,) = find_links(messages)
    assert link.words == "release notes" and link.who == "You" and link.ago == 0


def test_only_web_mail_and_session_links_open():
    web = FoundLink("https://x.org", "", "Claude", 0)
    mail = FoundLink("mailto:me@x.org", "", "Claude", 1)
    session = FoundLink("thechatplace://session/abc-1", "Build", "You", 2)
    file_link = FoundLink("file:///C:/secret.txt", "notes", "Claude", 0)
    script = FoundLink("javascript:alert(1)", "click", "Claude", 0)
    bad_app = FoundLink("thechatplace://elsewhere/x", "", "Claude", 0)
    assert web.can_open and mail.can_open and session.can_open
    assert not file_link.can_open and not script.can_open and not bad_app.can_open
    assert mail.address == "me@x.org"
    assert file_link.why_not().startswith("A file: link isn't opened from here")
    assert "copy only" in script.row()
    assert bad_app.why_not().startswith("That link isn't one The Chat Place knows.")
    assert web.why_not() == ""


def test_markdown_copy():
    assert FoundLink("https://x.org", "a [b]", "Claude", 0).markdown() == "[a \\[b\\]](https://x.org)"
    assert FoundLink("https://x.org", "", "Claude", 0).markdown() == "<https://x.org>"


def test_filter_matches_every_word():
    link = FoundLink("https://github.com/o/r", "release notes", "Claude", 0)
    assert matches(link, "release github")
    assert matches(link, "CLAUDE")
    assert not matches(link, "release gitlab")


def test_no_links_and_odd_text():
    assert find_links([msg(USER, ""), msg(ASSISTANT, "No links here. Not http:// either.")]) == []
    assert links_in_text("an email: someone@example.com") == []
    assert links_in_text("ftp://x.org isn't web") == []



def test_references_to_files_and_headings_are_not_links():
    text = "See [main.py](src/main.py#L12), [the top](#top) and [docs](https://x.org/d)."
    assert [url for url, _w, _c in links_in_text(text)] == ["https://x.org/d"]


def test_control_and_format_characters_never_open():
    nul = FoundLink("https:/\x00a", "", "Claude", 0)
    bidi = FoundLink("https://x.org/\u202egnp.exe", "", "Claude", 0)
    assert not nul.can_open and not bidi.can_open


@pytest.mark.parametrize("url", ["https:", "https://", "https:C:\\Windows\\calc.exe",
                                 "https:\\\\evil\\share", "mailto:"])
def test_incomplete_addresses_are_only_copied(url):
    link = FoundLink(url, "", "Claude", 0)
    assert not link.can_open
    assert link.why_not() == "That address isn't complete, so it can only be copied."


def test_a_path_isnt_called_a_scheme():
    assert FoundLink("C:\\Users\\x", "", "Claude", 0).why_not().startswith("This link isn't")


def test_long_addresses_are_read_short_and_copied_whole():
    url = "https://github.com/" + "a/" * 60 + "releases/tag/v0.1.5"
    link = FoundLink(url, "", "Claude", 0)
    assert link.address.startswith("github.com/…/") and link.address.endswith("v0.1.5")
    assert len(link.address) <= 80
    assert link.url == url and link.markdown() == f"<{url}>"


def test_the_same_address_in_another_case_is_listed_once():
    found = find_links([msg(USER, "HTTPS://Example.COM/Path"),
                        msg(ASSISTANT, "https://example.com/Path/")])
    assert len(found) == 1


def test_emphasis_is_dropped_from_link_words():
    assert links_in_text("[*the* _notes_ for snake_case](https://x.org)")[0][1] == \
        "the notes for snake_case"


@pytest.mark.parametrize("text", ["[" * 60000, "`https://a.b` " * 10000,
                                  "<https://a.b> " * 10000,
                                  "https://a.b/" + ")" * 20000],
                         ids=["brackets", "inline-code", "angle", "parentheses"])
def test_hostile_text_is_read_quickly(text):
    import time
    started = time.perf_counter()
    links_in_text(text)
    assert time.perf_counter() - started < 2.0
