"""Express (#737) without wx: which chats it lists and in what order, what
its rows and the session list's row say, each provider's latest reply (never
your own message), its settings and its menu command."""
import json

import pytest

from records import assistant_block, lines, text_block, user_text
from thechatplace import agents, express, hub
from thechatplace.commands import MENU_BAR, command, menu_path
from thechatplace.express import EXPRESS_KEY, ExpressRow
from thechatplace.sessions import (DEFAULT_EXPRESS_FIELDS, EXPRESS_FIELD_IDS, FIELD_ACTIVITY,
                                   FIELD_IDS, FIELD_LAST_REPLY, FIELD_PROVIDER, FIELD_STATUS,
                                   FIELD_TITLE, IDLE, OWN, WORKING, SessionInfo,
                                   clean_express_fields, in_view)
from thechatplace.speech import (EXPRESS_COUNT_DEFAULT, EXPRESS_COUNT_MAX, SpeechSettings)
from thechatplace.transcript import ASSISTANT, LastMessages
from thechatplace.ui_text import LAST_MESSAGE_LIMIT, REPLY_LIMIT


def chat(key, activity, reply="", message="", state=IDLE, provider="claude", **extra):
    extra.setdefault("title", key.title())
    return SessionInfo(source=OWN, key=key, cwd=f"C:\\work\\{key}",
                       cli_session_id=key, last_activity_ms=activity, state=state,
                       last_reply=reply, last_message=message or (f"Claude: {reply}" if reply else ""),
                       provider=provider, **extra)


# -- which chats, and in what order ---------------------------------------------------


def test_newest_first_capped_and_empty_chats_left_out():
    shown = [chat("old", 1, "Done."), chat("new", 9, "Ready."), chat("empty", 20),
             chat("middle", 5, "", message="You: Hi")]
    rows = express.chats(shown, 10)
    # "empty" has nothing in it yet; "middle" has your message, so it's in.
    assert [r.key for r in rows] == ["new", "middle", "old"]
    assert [r.key for r in express.chats(shown, 2)] == ["new", "middle"]
    assert express.chats(shown, 0) == []


def test_only_the_chats_the_session_list_shows():
    """Express takes what the list shows (view, groups, hidden, filter):
    the window passes ``_in_current_view``'s result. A chat out of the view
    is out of Express; the Express row itself never becomes a chat."""
    codex = chat("codex", 3, "Tests pass.", provider="codex")
    claude = chat("claude", 4, "Fixed.")
    shown = [s for s in (codex, claude) if in_view(s, "provider:codex")]
    assert [r.key for r in express.chats(shown, 10)] == ["codex"]
    pinned = ExpressRow("Express, 1 chat")
    assert express.chats([pinned, claude], 10) == [claude]


def test_a_working_chat_keeps_its_previous_reply():
    memo = {}
    idle = express.keep_replies([chat("a", 1, "The answer.")], memo)
    assert idle[0].last_reply == "The answer." and memo == {"a": "The answer."}
    working = chat("a", 2, "Let me check the tests", state=WORKING)
    kept = express.keep_replies([working], memo)
    assert kept[0].last_reply == "The answer."
    assert working.last_reply == "Let me check the tests"  # the snapshot's own isn't changed
    done = express.keep_replies([chat("a", 3, "All green.")], memo)
    assert done[0].last_reply == "All green." and memo["a"] == "All green."
    # A chat working before Express ever saw it idle shows what it has.
    fresh = express.keep_replies([chat("b", 1, "Starting", state=WORKING)], memo)
    assert fresh[0].last_reply == "Starting"


def test_a_chat_not_read_again_yet_keeps_its_reply_and_its_place():
    """Codex reads a few histories a pass and forgets a chat's line once it's
    active: until it's read again the chat keeps the reply it had, rather
    than drop out of Express (the window holds replies before choosing)."""
    memo = {}
    express.keep_replies([chat("codex", 1, "Tests pass.", provider="codex")], memo)
    blank = chat("codex", 2, provider="codex")  # active since, not read yet
    held = express.keep_replies([blank], memo)
    assert held[0].last_reply == "Tests pass."
    assert [r.key for r in express.chats(held, 10)] == ["codex"]


def test_frozen_order_keeps_your_row_and_adds_new_chats_at_the_end():
    before = ["b", "a", "c"]
    a, b, c, d = (chat(k, t, "x") for k, t in (("a", 10), ("b", 5), ("c", 1), ("d", 20)))
    listed = [d, a, b, c]
    rows = listed[:3]  # c has dropped out of the newest three
    frozen = express.ordered(before, rows, listed, frozen=True)
    assert [r.key for r in frozen] == ["b", "a", "c", "d"]
    unfrozen = express.ordered(before, rows, listed, frozen=False)
    assert [r.key for r in unfrozen] == ["d", "a", "b"]
    # A chat gone from the list goes from Express too, even frozen.
    assert [r.key for r in express.ordered(before, [a, b], [a, b], True)] == ["b", "a"]


# -- what it says -----------------------------------------------------------------------


@pytest.mark.parametrize("rows, said", [
    ([], "Express, no chats"),
    ([chat("a", 1, "x")], "Express, 1 chat"),
    ([chat("a", 1, "x", unread=True), chat("b", 2, "y")], "Express, 2 chats, 1 with a new reply"),
    ([chat("a", 1, "x", unread=True), chat("b", 2, "y", unread=True)],
     "Express, 2 chats, 2 with new replies"),
])
def test_the_session_lists_express_row(rows, said):
    assert express.summary(rows) == said


def test_heading_and_reply_title():
    assert express.heading(7) == "Express, 7 chats"
    assert express.heading(1) == "Express, 1 chat"
    info = chat("notes", 1, "x", title="Release & notes")
    assert express.reply_title(info) == "Reply to Release & notes (Claude)"
    assert express.reply_title(chat("c", 1, "x", provider="codex-api")) == "Reply to C (Codex)"
    conversation = chat("j", 1, "x", provider="agents", provider_label="Claude + Codex")
    assert express.reply_title(conversation) == "Reply to J (Claude + Codex)"


def test_rows_read_the_chosen_columns_and_the_reply_cut_short():
    long = " ".join(["word"] * 400)
    info = chat("build", 1, long)
    line = express.row_text(info, DEFAULT_EXPRESS_FIELDS, now_ms=60_001)
    assert line.startswith("Build, Claude, word word")
    assert line.endswith(", active 1 minute ago")
    reply_part = line.split(", ")[2]
    assert len(reply_part) <= LAST_MESSAGE_LIMIT + 1
    whole = express.row_text(info, DEFAULT_EXPRESS_FIELDS, now_ms=60_001, whole=True)
    assert long in whole
    assert express.row_text(info, [FIELD_STATUS, FIELD_TITLE]) == "idle, Build"
    # Never empty: with nothing in any shown column, the name.
    assert express.row_text(chat("quiet", 0), [FIELD_LAST_REPLY]) == "Quiet"


def test_express_row_stands_in_for_a_session_row():
    row = ExpressRow("Express, 3 chats")
    assert row.key == EXPRESS_KEY and row.list_line(0, ["title"]) == "Express, 3 chats"
    assert not isinstance(row, SessionInfo)


# -- each provider's latest reply ------------------------------------------------------


def write(path, records):
    path.write_text("\n".join(lines(*records)) + "\n", encoding="utf-8")
    return path


def test_claude_reply_is_never_your_own_message(tmp_path, monkeypatch):
    path = write(tmp_path / "t.jsonl", [user_text("Fix it"),
                                        assistant_block(text_block("**Fixed** it."), "m1"),
                                        user_text("Now the tests")])
    cache = LastMessages()
    message, reply = cache.get_both(path)
    assert message.text == "Now the tests"
    assert (reply.kind, reply.text) == (ASSISTANT, "**Fixed** it.")
    info = chat("a", 1)
    monkeypatch.setattr(SessionInfo, "transcript_path", lambda self: path)
    monkeypatch.setattr(hub, "_NO_TRANSCRIPT", {})
    hub.fill_last_messages([info], cache)
    assert info.last_message == "You: Now the tests"
    assert info.last_reply == "Fixed it."  # one plain line, no marks
    assert cache.reads == 1  # one read for both


def test_claude_reply_found_past_a_long_run_of_your_messages(tmp_path):
    from thechatplace.transcript import _tail_messages
    records = [assistant_block(text_block("The answer."), "m1")]
    records += [user_text("more " * 40) for _ in range(30)]
    path = write(tmp_path / "t.jsonl", records)
    with open(path, "rb") as handle:
        message, reply = _tail_messages(handle, path.stat().st_size, (512, 2048, 1 << 20))
    assert message.text.startswith("more") and reply.text == "The answer."


def test_claude_with_no_reply_yet(tmp_path):
    path = write(tmp_path / "t.jsonl", [user_text("Hello?")])
    message, reply = LastMessages().get_both(path)
    assert message.text == "Hello?" and reply is None


def test_a_long_reply_is_kept_to_its_limit(tmp_path, monkeypatch):
    path = write(tmp_path / "t.jsonl", [assistant_block(text_block("word " * 2000), "m1")])
    info = chat("a", 1)
    monkeypatch.setattr(SessionInfo, "transcript_path", lambda self: path)
    monkeypatch.setattr(hub, "_NO_TRANSCRIPT", {})
    hub.fill_last_messages([info], LastMessages())
    assert 0 < len(info.last_reply) <= REPLY_LIMIT + 1


def test_codex_reply_is_never_your_own_message():
    from test_codex_provider import Connection, thread
    from thechatplace.providers.codex import CodexProvider, session_from_thread
    data = thread("one", turns=[{"id": "turn-1", "startedAt": 100, "items": [
        {"type": "userMessage", "id": "u1", "content": [{"type": "text", "text": "Hi"}]},
        {"type": "agentMessage", "id": "a", "text": "Ready when you are"},
        {"type": "userMessage", "id": "u2", "content": [{"type": "text", "text": "Go"}]},
    ]}])
    provider = CodexProvider(Connection([{"thread": data}]))
    rows = [session_from_thread(thread("one"), False)]
    provider.fill_last_messages(rows)
    assert rows[0].last_message == "You: Go"
    assert rows[0].last_reply == "Ready when you are"
    again = [session_from_thread(thread("one"), False)]
    provider.cached_last_messages(again)  # what discovery's outage keeps
    assert again[0].last_reply == "Ready when you are"
    again[0].last_activity_ms += 1
    provider.cached_last_messages(again)  # active since: not known
    assert again[0].last_reply == ""


def test_copilot_reply(tmp_path):
    from test_copilot_provider import ONE, answered_turn, write_session
    from thechatplace.providers.copilot import CopilotProvider
    write_session(tmp_path, ONE, answered_turn(answer="All done"))
    provider = CopilotProvider(tmp_path)
    sessions = provider.collect().sessions
    provider.fill_last_messages(sessions)
    assert sessions[0].last_reply == "All done"
    again = provider.collect().sessions
    provider.cached_last_messages(again)
    assert again[0].last_reply == "All done"


def test_an_agent_conversations_reply_is_its_latest_members():
    claude = chat("c1", 10, "Plan written.")
    codex = chat("x1", 20, "Tests pass.", provider="codex")
    members = [agents.Member("claude", "c1", "Claude"), agents.Member("codex", "x1", "Codex")]
    conversation = agents.new_conversation("Pair", members)
    rows = agents.rows([claude, codex], [conversation])
    row = next(r for r in rows if r.provider == agents.AGENTS)
    assert row.last_reply == "Codex: Tests pass."
    assert [r.key for r in express.chats(rows, 10)] == [row.key]


# -- settings and the menu ---------------------------------------------------------------


def test_settings_off_by_default_and_kept(tmp_path):
    settings = SpeechSettings()
    assert not settings.express_enabled
    assert settings.express_count == EXPRESS_COUNT_DEFAULT == 10
    assert settings.express_fields == DEFAULT_EXPRESS_FIELDS == [
        FIELD_TITLE, FIELD_PROVIDER, FIELD_LAST_REPLY, FIELD_ACTIVITY]
    path = tmp_path / "settings.json"
    settings.express_enabled, settings.express_count = True, 25
    settings.express_fields = [FIELD_STATUS, FIELD_TITLE]
    settings.save(path)
    loaded = SpeechSettings.load(path)
    assert (loaded.express_enabled, loaded.express_count, loaded.express_fields) == (
        True, 25, [FIELD_STATUS, FIELD_TITLE])


@pytest.mark.parametrize("count", [0, 51, -3, "10", True, 2.5, None])
def test_a_count_out_of_range_is_the_default(tmp_path, count):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"express_count": count, "express_enabled": "yes"}), encoding="utf-8")
    loaded = SpeechSettings.load(path)
    assert loaded.express_count == EXPRESS_COUNT_DEFAULT
    assert loaded.express_enabled is False  # only a real true turns it on
    path.write_text(json.dumps({"express_count": EXPRESS_COUNT_MAX}), encoding="utf-8")
    assert SpeechSettings.load(path).express_count == EXPRESS_COUNT_MAX


def test_express_columns_are_cleaned_and_separate_from_the_lists():
    assert clean_express_fields(["last_reply", "nonsense", "last_reply", "groups"]) == ["last_reply"]
    assert clean_express_fields("bad") == DEFAULT_EXPRESS_FIELDS
    assert clean_express_fields([]) == DEFAULT_EXPRESS_FIELDS
    # Last reply is Express's only: the session list never offers it.
    assert FIELD_LAST_REPLY in EXPRESS_FIELD_IDS and FIELD_LAST_REPLY not in FIELD_IDS


def test_view_express_is_a_check_item():
    item = command("express_toggle")
    assert item.check and not item.key
    assert menu_path("express_toggle") == "View, Express"
    assert any(m.label == "&View" for m in MENU_BAR)
