import json

import pytest

from thechatplace import announce
from thechatplace.sessions import IDLE, NEEDS_YOU
from thechatplace.speech import (ANNOUNCE_FULL, ANNOUNCE_SILENT, ANNOUNCE_SUMMARY,
                                 SpeechSettings, strip_for_speech)
from thechatplace.ui_text import shortcuts_text


def test_reply_levels():
    reply = "Done. The build passes now. Three files changed."
    assert announce.reply_text("Build", reply, ANNOUNCE_FULL) == f"Build replied. {reply}"
    assert announce.reply_text("Build", reply, ANNOUNCE_SUMMARY) == \
        "Build replied: Done. 8 words."
    assert announce.reply_text("Build", reply, ANNOUNCE_SILENT) is None
    assert announce.reply_text("Build", "  ", ANNOUNCE_FULL) is None


def test_turn_end_levels():
    assert announce.turn_end_text("QM", IDLE, "", "", ANNOUNCE_FULL) == "QM finished."
    assert announce.turn_end_text("QM", NEEDS_YOU, "Approve it", "Ready? Yes.",
                                  ANNOUNCE_SUMMARY) == "QM needs you: Approve it. Ready?"
    assert announce.turn_end_text("QM", IDLE, "", "All done.", ANNOUNCE_FULL) == \
        "QM finished. All done."
    assert announce.turn_end_text("QM", IDLE, "", "x", ANNOUNCE_SILENT) is None


def test_first_sentence_and_status_text():
    assert announce.first_sentence("No full stop here") == "No full stop here"
    assert announce.first_sentence("a" * 300).endswith("…")
    assert len(announce.status_text("x " * 200)) == 150


def test_own_message_read_back_follows_the_level_and_setting():
    message = "Fix the build.  Then run\nthe tests"
    # Which session comes first, so it's heard even if the rest is cut off.
    assert announce.sent_text("QM", message, ANNOUNCE_FULL, True) == \
        "Sent to QM: Fix the build. Then run the tests."
    assert announce.sent_text("QM", message, ANNOUNCE_SUMMARY, True) == \
        "Sent to QM: Fix the build."
    assert announce.sent_text("QM", message, ANNOUNCE_FULL, False) == "Sent. QM is working."
    assert announce.sent_text("QM", message, ANNOUNCE_SILENT, True) == "Sent. QM is working."
    assert announce.sent_text("QM", "Is it done?", ANNOUNCE_FULL, True) == \
        "Sent to QM: Is it done?"
    assert announce.sent_text("QM", "x", ANNOUNCE_FULL, True, queued=True) == \
        "Sent your queued message. QM is working."
    assert announce.queued_text("QM", "and the docs", ANNOUNCE_FULL, True) == \
        "Queued for QM: and the docs."
    assert announce.queued_text("QM", "more", ANNOUNCE_FULL, True, added=True) == \
        "Also queued for QM: more."
    assert announce.queued_text("QM", "more", ANNOUNCE_FULL, False) == \
        "Queued. It will be sent when QM finishes."


def spoken(text):
    """What the speaker actually says: the engine gets strip_for_speech's output."""
    return strip_for_speech(text)


def test_own_message_markdown_is_read_as_words():
    fenced = "Look at this:\n```python\nx = 1. y = 2\n```"
    assert spoken(announce.sent_text("QM", fenced, ANNOUNCE_SUMMARY, True)) == \
        "Sent to QM: Look at this."
    assert spoken(announce.sent_text("QM", fenced, ANNOUNCE_FULL, True)) == \
        "Sent to QM: Look at this. Code block omitted."
    plan = "Plan\n## Steps\n- do **this**\n- then that\n1. last"
    assert spoken(announce.sent_text("QM", plan, ANNOUNCE_FULL, True)) == \
        "Sent to QM: Plan. Steps. do this. then that. last."
    assert spoken(announce.sent_text("QM", "```\nonly code\n```", ANNOUNCE_FULL, True)) == \
        "Sent to QM: Code block omitted."
    assert announce.sent_text("QM", "```\n```", ANNOUNCE_FULL, True) == \
        "Sent to QM: Code block omitted."
    assert announce.sent_text("QM", "  \n ", ANNOUNCE_FULL, True) == "Sent. QM is working."


def test_long_own_message_is_capped_at_the_full_level():
    text = announce.sent_text("QM", "word " * 120, ANNOUNCE_FULL, True)
    assert len(text) < announce.OWN_LIMIT + 60
    assert text.endswith("word… and 60 more words.")
    # 301 characters: everything but the last word fits.
    assert announce.sent_text("QM", "a " * 150 + "b", ANNOUNCE_FULL, True).endswith(
        "… and 1 more word.")
    short = "x" * announce.OWN_LIMIT
    assert announce.sent_text("QM", short, ANNOUNCE_FULL, True) == f"Sent to QM: {short}."
    # One word longer than the limit is still cut.
    url = "https://example.com/" + "x" * 400
    assert announce.sent_text("QM", url, ANNOUNCE_FULL, True) == \
        f"Sent to QM: {url[:announce.OWN_LIMIT]}…"


def test_speech_settings_defaults_and_round_trip(tmp_path):
    path = tmp_path / "speech.json"
    settings = SpeechSettings.load(path)
    assert settings.announce == ANNOUNCE_FULL and settings.enabled
    assert settings.announce_all_sessions
    assert settings.announce_own  # on unless Kelly turns it off
    settings.announce = ANNOUNCE_SILENT
    settings.announce_all_sessions = False
    settings.announce_own = False
    settings.save(path)
    again = SpeechSettings.load(path)
    assert again.announce == ANNOUNCE_SILENT and not again.enabled
    assert not again.announce_all_sessions
    assert not again.announce_own


def test_the_ungrouped_view_is_remembered(tmp_path):
    path = tmp_path / "speech.json"
    settings = SpeechSettings.load(path)
    settings.session_view = "ungrouped"
    settings.save(path)
    assert SpeechSettings.load(path).session_view == "ungrouped"
    assert "enabled" not in json.loads(path.read_text())


def test_speech_settings_bad_values(tmp_path):
    path = tmp_path / "speech.json"
    path.write_text(json.dumps({"announce": "shout", "rate_preset": "warp"}))
    settings = SpeechSettings.load(path)
    assert settings.announce == ANNOUNCE_FULL and settings.rate_preset == "default"
    path.write_text("garbage")
    assert SpeechSettings.load(path).announce == ANNOUNCE_FULL


def test_shortcut_list_has_the_essentials():
    text = shortcuts_text(platform="win32")
    for needle in ("Enter", "Ctrl+O", "Ctrl+N", "F5", "Escape", "Ctrl+Enter", "F1", "Ctrl+T"):
        assert needle in text


@pytest.mark.parametrize("message, level, heard", [
    # Abbreviations and initials don't end the first sentence.
    ("Use e.g. the docs. Then go", ANNOUNCE_SUMMARY, "Use e.g. the docs."),
    ("Dr. Smith said hi. Then left", ANNOUNCE_SUMMARY, "Dr. Smith said hi."),
    ("J. Smith wrote it. Read it", ANNOUNCE_SUMMARY, "J. Smith wrote it."),
    # Wrapped prose reads on, whatever the next line starts with.
    ("When the build is done\nI want you to run the tests\nQuickMail first", ANNOUNCE_FULL,
     "When the build is done I want you to run the tests QuickMail first."),
    ("Call me at\n555-1234", ANNOUNCE_FULL, "Call me at 555-1234."),
    ("Mr. Smith\nwent home", ANNOUNCE_SUMMARY, "Mr. Smith went home."),
    # Blank lines, headings, list items, quotes and rules end a sentence.
    ("First paragraph here\n\nSecond paragraph", ANNOUNCE_FULL,
     "First paragraph here. Second paragraph."),
    ("> I said this\nI disagree", ANNOUNCE_FULL, "I said this. I disagree."),
    ("> line one\n> line two\nMy answer", ANNOUNCE_FULL, "line one line two. My answer."),
    ("text\n---\nmore", ANNOUNCE_FULL, "text. more."),
    ("- [ ] task one\n- [x] task two", ANNOUNCE_FULL, "task one. task two."),
    ("1. first\n2. second", ANNOUNCE_FULL, "first. second."),
    # A year or a big number isn't a list marker.
    ("2026. That was the year.", ANNOUNCE_FULL, "2026. That was the year."),
    ("100) things", ANNOUNCE_FULL, "100) things."),
    # An unclosed fence is still a code block.
    ("Unclosed fence\n```\ncode here\nmore code", ANNOUNCE_FULL,
     "Unclosed fence. Code block omitted."),
    # Emphasis across a line break, and Windows line endings.
    ("**a\nb**", ANNOUNCE_FULL, "a b."),
    ("Fix it.\r\nThen test.", ANNOUNCE_FULL, "Fix it. Then test."),
])
def test_own_message_reads_as_written(message, level, heard):
    assert spoken(announce.sent_text("QM", message, level, True)) == f"Sent to QM: {heard}"


@pytest.mark.parametrize("message", ["- ", "- \n- \n- ", "- -", "*", "***", "_", "1. ",
                                     "1.\n2.\n3.", "", "  \n\t "])
def test_marks_alone_are_not_read_back(message):
    assert announce.sent_text("QM", message, ANNOUNCE_FULL, True) == "Sent. QM is working."


def test_cap_counts_words_and_code_blocks_separately():
    text = announce.sent_text("QM", "word " * 60 + "\n```\nx\n```", ANNOUNCE_FULL, True)
    assert text.endswith("word… and 1 more word and a code block.")
    two = "word " * 70 + "\n```\nx\n```\n```\ny\n```"
    assert announce.sent_text("QM", two, ANNOUNCE_FULL, True).endswith(
        "… and 10 more words and 2 code blocks.")


def test_summary_cut_is_on_a_word_boundary():
    text = announce.first_sentence("word, " * 60)
    assert text.endswith("word…") and len(text) <= 201


def test_replies_lose_quote_markers_rules_and_unclosed_fences():
    assert strip_for_speech("> quoted\n\n---\n\nAfter") == "quoted\n\n\n\nAfter"
    assert strip_for_speech("Here:\n```\nhalf a snippet") == "Here:\n Code block omitted."
    assert strip_for_speech("***bold***") == "bold"  # emphasis, not a rule


@pytest.mark.parametrize("items,level,said", [
    ([("text", "Let me check the **build**."), ("tool", "Bash: git status"),
      ("tool", "Read: main.py")], "full",
     "Let me check the build. Using Bash: git status; Read: main.py."),
    ([("tool", "Read: a"), ("tool", "Read: b"), ("tool", "Read: c"), ("tool", "Read: d"),
      ("tool", "Bash: x")], "full", "Using Read 4 times, then Bash."),
    ([("tool", "Bash: git status")], "summary", "Using Bash."),
    ([("text", "One. Two."), ("tool", "Edit: a.py")], "summary", "One. Using Edit."),
    ([("tool", "Bash: x")], "silent", None),
    ([], "full", None),
    ([("text", "```\ncode only\n```")], "full", "Code block omitted."),
])
def test_activity_text(items, level, said):
    from thechatplace.announce import activity_text
    assert activity_text(items, level) == said


def test_activity_text_cuts_long_details():
    from thechatplace.announce import activity_text
    said = activity_text([("tool", "Bash: " + "word " * 60)], "full")
    assert said.endswith("…") and len(said) < 140


@pytest.mark.parametrize("items,level,said", [
    ([("tool", "Read: a"), ("tool", "Bash: b"), ("tool", "Read: c"), ("tool", "Bash: d")],
     "full", "Using Read twice and Bash twice."),
    ([("tool", "Read: a"), ("tool", "Read: b")], "summary", "Using Read twice."),
    ([("tool", "mcp__github__create_issue: Fix it")], "full", "Using github create issue: Fix it."),
    ([("tool", "mcp__github__create_issue: Fix it")], "summary", "Using github create issue."),
])
def test_activity_text_counts_and_names(items, level, said):
    from thechatplace.announce import activity_text
    assert activity_text(items, level) == said
def test_spoken_markdown_reads_a_whole_message():
    from thechatplace.announce import spoken_markdown
    assert spoken_markdown("## Summary\nThe build **passes**.\n\n- one\n- two\n\n```py\nx=1\n```\nDone") == (
        "Summary. The build passes. one. two. Code block omitted. Done.")
    assert spoken_markdown("") == "" and spoken_markdown("```\nonly code\n```") == "Code block omitted."


def test_peer_message_announcement():
    """#123: a message from another session names it; silent says nothing."""
    assert announce.peer_text("Hub", "coordinate", "PR is up. CI running.", ANNOUNCE_FULL) ==         "Hub: message from coordinate. PR is up. CI running."
    assert announce.peer_text("Hub", "coordinate", "PR is up. CI running.",
                              ANNOUNCE_SUMMARY) == "Hub: message from coordinate: PR is up."
    assert announce.peer_text("Hub", "", "hi", ANNOUNCE_FULL) ==         "Hub: message from another session. hi"
    assert announce.peer_text("Hub", "x", "hi", ANNOUNCE_SILENT) is None
    assert announce.peer_text("Hub", "x", "  ", ANNOUNCE_FULL) is None


def test_nvda_is_offered_whenever_it_is_installed():
    """The probe calls NVDA "available" only if it finds a controller client
    of its own, so NVDA vanished from the list on a normal install (#98). The
    app ships the client now, so installed or running is enough."""
    from thechatplace.speech import _parse_windows_probe
    probe = {"screenReaders": [
        {"engine": "jaws", "name": "JAWS", "available": True, "running": True},
        {"engine": "nvda", "name": "NVDA", "available": False, "installed": True,
         "running": False},
    ], "systemVoices": {"engine": "onecore", "displayName": "Microsoft Zira",
                        "match": "MSTTS_V110_enUS_ZiraM"}}
    labels = [o.label for o in _parse_windows_probe(json.dumps(probe))]
    assert labels[1:] == ["JAWS screen reader",
                          "NVDA screen reader (not running right now)",
                          "Microsoft Zira — Windows voice (OneCore)"]
    probe["screenReaders"] = [{"engine": "nvda", "available": False, "installed": False,
                               "running": False}]
    assert [o.engine for o in _parse_windows_probe(json.dumps(probe))] == ["auto", "onecore"]


def test_update_notice_settings_round_trip_and_default(tmp_path):
    # #141: the notice is on unless turned off; the last version run is kept.
    path = tmp_path / "speech.json"
    settings = SpeechSettings.load(path)
    assert settings.update_installed_notice is True
    assert settings.last_run_version == ""
    settings.update_installed_notice = False
    settings.last_run_version = "0.1.4"
    settings.save(path)
    again = SpeechSettings.load(path)
    assert again.update_installed_notice is False
    assert again.last_run_version == "0.1.4"
    path.write_text('{"update_installed_notice": "no", "last_run_version": 7}', encoding="utf-8")
    odd = SpeechSettings.load(path)
    assert odd.update_installed_notice is True
    assert odd.last_run_version == ""


def test_what_is_still_running_in_the_background():
    from thechatplace.announce import background_text
    assert background_text(["Windows build"]) == \
        "Still running in the background: Windows build."
    assert background_text(["Build", " Watch\nCI "]) == \
        "Still running in the background: Build; Watch CI."
    assert background_text([]) == "Still running in the background."
    assert background_text(["", " "]) == "Still running in the background."


@pytest.mark.parametrize("value", [True, False, "true", 1, None])
def test_codex_discovery_is_strictly_opt_in_and_persists(tmp_path, value):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"codex_enabled": value}))
    settings = SpeechSettings.load(path)
    assert settings.codex_enabled is (value is True)
    settings.save(path)
    assert SpeechSettings.load(path).codex_enabled is (value is True)
