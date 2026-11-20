"""What chats on API credits cost, where you read them (#692): each reply's
last line, kept in The Chat Place's own store, and the Session list's Cost
column. Windowless; ledgers and stores live in tmp_path."""
import json

import pytest

from thechatplace import reply_costs, spend_ledger
from thechatplace.claude_cli import StreamParser
from thechatplace.own_store import OwnSession, OwnSessionStore, _session_from_dict
from thechatplace.reply_costs import (ColumnCosts, claude_column_words, claude_reply_key,
                                      claude_words, codex_column_words, codex_words, cost_line,
                                      fill_api_costs, remember, with_costs)
from thechatplace.sessions import DEFAULT_FIELDS, FIELD_CHARGED, FIELD_COST, OWN, SessionInfo
from thechatplace.speech import SpeechSettings
from thechatplace.transcript import ASSISTANT, TOOL, USER, ChatMessage


# -- a reply's last line --------------------------------------------------------------


@pytest.mark.parametrize("words,line", [
    ("19 cents", "Cost: 19 cents"),
    ("$1.42", "Cost: $1.42"),
    ("at least 6 cents", "Cost: at least 6 cents"),
    ("nothing", "Cost: nothing"),
    ("cost not known", "Cost: not known"),
    ("estimated 3 cents", "Cost: an estimated 3 cents"),
    ("estimated at least 3 cents", "Cost: at least an estimated 3 cents"),
    ("estimated less than a cent", "Cost: less than a cent, estimated"),
    ("estimated, at most 5 cents", "Cost: at most an estimated 5 cents"),
    ("nothing was charged", "Cost: nothing"),
    ("used 12,400 tokens, cost unknown", "Cost: unknown, 12,400 tokens"),
    ("used at least 12,400 tokens; some tokens not reported, cost unknown",
     "Cost: unknown, at least 12,400 tokens"),
    ("tokens not reported, cost unknown", "Cost: unknown, tokens not reported"),
    ("used 12,400 tokens, cost unknown; allowance kept", "Cost: unknown, 12,400 tokens"),
    ("price expired during the reply; used 9 tokens, cost unknown",
     "Cost: unknown, 9 tokens (its price expired during the reply)"),
    ("", ""),
])
def test_the_line_is_said_as_the_cost_was(words, line):
    assert cost_line(words) == line


def test_a_price_unknown_reply_never_reads_as_dollars():
    line = cost_line("used 12,400 tokens, cost unknown")
    assert "$" not in line and "cent" not in line and "nothing" not in line


def test_claude_words_whatever_settings_says():
    data = {"billing": "api", "cost_usd": 0.19, "cost_complete": True, "sent": True}
    assert claude_words(data) == "19 cents"
    assert claude_words({**data, "cost_complete": False}) == "at least 19 cents"
    assert claude_words({**data, "cost_usd": None}) == "cost not known"
    # What came after an answer that already had its cost so far.
    assert claude_words({**data, "cost_usd": 0.25}, already=0.19) == "6 cents"
    assert claude_words({**data, "sent": False}) == ""
    assert claude_words({"cost_usd": 0.19}) == ""  # the subscription


def test_codex_words_are_its_own():
    data = {"billing": "api", "sent": True, "cost_words": "estimated 3 cents"}
    assert codex_words(data) == "estimated 3 cents"
    assert codex_words({**data, "sent": False}) == ""


def test_a_claude_reply_is_keyed_by_message_id_then_uuid():
    assert claude_reply_key({"reply_message_id": "msg_1", "reply_uuid": "u1"}) == "msg_1"
    assert claude_reply_key({"reply_message_id": "", "reply_uuid": "u1"}) == "u1"
    assert claude_reply_key({}) == ""
    assert claude_reply_key({"reply_message_id": "x" * 999}) == ""


def test_the_parser_notes_the_last_reply_with_text():
    parser = StreamParser()
    feed = lambda **e: parser.feed(json.dumps(e))
    feed(type="assistant", uuid="u1", message={"id": "msg_1", "model": "claude-opus-5-5",
                                               "content": [{"type": "text", "text": "Looking"}]})
    assert (parser.reply_message_id, parser.reply_uuid) == ("msg_1", "u1")
    # A tool call alone, a subagent's message and Claude Code's own error
    # text aren't the reply.
    feed(type="assistant", uuid="u2", message={"id": "msg_2", "content": [
        {"type": "tool_use", "id": "t", "name": "Read", "input": {}}]})
    feed(type="assistant", uuid="u3", parent_tool_use_id="t",
         message={"id": "msg_3", "content": [{"type": "text", "text": "sub"}]})
    feed(type="assistant", uuid="u4", message={"id": "msg_4", "model": "<synthetic>",
                                               "content": [{"type": "text", "text": "error"}]})
    assert (parser.reply_message_id, parser.reply_uuid) == ("msg_1", "u1")
    feed(type="assistant", uuid="u5", message={"id": "msg_5", "content": [
        {"type": "text", "text": "Done"}]})
    assert (parser.reply_message_id, parser.reply_uuid) == ("msg_5", "u5")


# -- on the messages ---------------------------------------------------------------------


def _reply(text, key, uuid=""):
    return ChatMessage(ASSISTANT, text, key=key, uuid=uuid)


def test_claude_replies_match_by_key_or_uuid():
    messages = [ChatMessage(USER, "Hi", key="a"), _reply("Merged reply", "msg_1", uuid="u9"),
                _reply("Older form", "u2", uuid="u2"), ChatMessage(TOOL, "Read", key="msg_1:t")]
    shown = with_costs(messages, {"msg_1": "Cost: 19 cents", "u2": "Cost: 2 cents"})
    assert [m.cost for m in shown] == ["", "Cost: 19 cents", "Cost: 2 cents", ""]
    assert messages[1].cost == ""  # copies; the transcript's own are left alone
    by_uuid = with_costs(messages, {"u9": "Cost: 1 cent"})
    assert by_uuid[1].cost == "Cost: 1 cent"


def test_a_codex_turns_cost_goes_on_its_last_reply():
    messages = [ChatMessage(USER, "Hi", key="t1:i0"), _reply("Working", "t1:i1"),
                ChatMessage(TOOL, "ls", key="t1:i2"), _reply("Done", "t1:i3"),
                ChatMessage(USER, "More", key="t2:i0"), _reply("Sure", "t2:i1")]
    shown = with_costs(messages, {"turn:t1": "Cost: an estimated 3 cents"})
    assert [m.cost for m in shown] == ["", "", "", "Cost: an estimated 3 cents", "", ""]


def test_the_cost_is_the_replys_last_line_and_ends_its_row():
    message = ChatMessage(ASSISTANT, "First line\n\nMore text", key="k", cost="Cost: 19 cents")
    assert message.whole == "First line\n\nMore text\n\nCost: 19 cents"
    assert message.full_text().endswith("\n\nCost: 19 cents")
    assert message.list_line() == "Claude: First line. Cost: 19 cents"
    assert ChatMessage(ASSISTANT, "Done.", cost="Cost: 1 cent").list_line() == "Claude: Done. Cost: 1 cent"
    detailed = ChatMessage(ASSISTANT, "Reply", detail="Setup", cost="Cost: 1 cent")
    assert detailed.whole == "Reply\n\nSetup\n\nCost: 1 cent"
    assert ChatMessage(ASSISTANT, "Plain").list_line() == "Claude: Plain"


def test_kept_costs_are_bounded():
    costs = {}
    for number in range(reply_costs.MAX_REPLY_COSTS + 20):
        costs = remember(costs, f"msg_{number}", "Cost: 1 cent")
    assert len(costs) == reply_costs.MAX_REPLY_COSTS
    assert "msg_0" not in costs and f"msg_{reply_costs.MAX_REPLY_COSTS + 19}" in costs
    assert remember({}, "", "Cost: 1 cent") == {}
    assert remember({}, "k", "Cost: " + "x" * 999) == {}
    # Replacing one keeps it once, newest.
    again = remember({"a": "Cost: 1 cent", "b": "Cost: 2 cents"}, "a", "Cost: 3 cents")
    assert list(again.items()) == [("b", "Cost: 2 cents"), ("a", "Cost: 3 cents")]


# -- kept with the chat ----------------------------------------------------------------


def _store(tmp_path):
    store = OwnSessionStore(tmp_path / "sessions.json")
    store.load()
    return store


def test_a_replys_cost_survives_a_restart_in_the_apps_own_store(tmp_path):
    store = _store(tmp_path)
    store.add(OwnSession("chat-api", "Budget", str(tmp_path), billing="api"))
    store.add(OwnSession("chat-sub", "Plan", str(tmp_path)))
    store.add(OwnSession("thread-1", "Codex", str(tmp_path), provider="codex-api", billing="api"))
    assert store.add_reply_cost("chat-api", "msg_1", "Cost: 19 cents")
    assert store.add_reply_cost("thread-1", "turn:t1", "Cost: an estimated 3 cents",
                                provider="codex-api")
    # A subscription chat has none, and none is kept for it.
    assert not store.add_reply_cost("chat-sub", "msg_1", "Cost: 19 cents")
    assert not store.add_reply_cost("nobody", "msg_1", "Cost: 19 cents")
    again = _store(tmp_path)
    assert again.get("chat-api").reply_costs == {"msg_1": "Cost: 19 cents"}
    assert again.get("thread-1", provider="codex-api").reply_costs == {
        "turn:t1": "Cost: an estimated 3 cents"}
    assert again.get("chat-sub").reply_costs == {}
    # The subscription's file, the one older releases read, doesn't have them.
    legacy = json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))
    assert "Cost: 19 cents" not in json.dumps(legacy)


def test_a_hand_edited_record_is_made_safe():
    base = {"cli_session_id": "chat-api", "title": "T", "cwd": "C:\\x", "billing": "api"}
    session = _session_from_dict({**base, "reply_costs": {
        "msg_1": "Cost: 1 cent", "msg_2": "not ours", "msg_3": 5, "": "Cost: 2 cents",
        "msg_4": "Cost: two\nlines"}})
    assert session.reply_costs == {"msg_1": "Cost: 1 cent"}
    assert _session_from_dict({**base, "reply_costs": ["Cost: 1 cent"]}).reply_costs == {}
    sub = _session_from_dict({**base, "billing": "subscription",
                              "reply_costs": {"msg_1": "Cost: 1 cent"}})
    assert sub.reply_costs == {}
    many = {f"m{n}": "Cost: 1 cent" for n in range(reply_costs.MAX_REPLY_COSTS + 5)}
    assert len(_session_from_dict({**base, "reply_costs": many}).reply_costs) == \
        reply_costs.MAX_REPLY_COSTS


# -- the Cost column ----------------------------------------------------------------------


def _totals(**values):
    return spend_ledger.Totals(**values)


def test_claude_column_is_what_properties_says():
    totals = _totals(chat_all_time={"chat-1": 1.42, "chat-2": 0.0})
    assert claude_column_words(totals, "chat-1") == "$1.42"
    assert claude_column_words(totals, "chat-2") == ""
    assert claude_column_words(totals, "chat-3") == ""


def test_codex_column_is_estimated_and_never_zero_dollars():
    key = "codex-api:t"
    assert codex_column_words(_totals(chat_all_time={key: 0.03}), key) == "an estimated 3 cents"
    assert codex_column_words(_totals(), key) == ""
    assert codex_column_words(_totals(priced_unknown_chats={key}), key) == "cost not known"
    assert codex_column_words(_totals(chat_all_time={key: 0.03}, priced_unknown_chats={key}),
                              key) == "at least an estimated 3 cents"
    tokens = {"inputTokens": 12_000, "outputTokens": 400}
    unpriced = codex_column_words(_totals(unpriced_all_time={key: tokens}), key)
    assert unpriced == "cost unknown for 12,400 tokens" and "$" not in unpriced
    assert codex_column_words(_totals(unpriced_all_time={key: tokens},
                                      unpriced_missing_all_time={key}), key) == (
        "cost unknown for at least 12,400 tokens")
    assert codex_column_words(_totals(unpriced_all_time={key: {}},
                                      unpriced_missing_all_time={key}), key) == (
        "cost unknown and tokens not reported")
    assert codex_column_words(_totals(chat_all_time={key: 0.05}, unpriced_all_time={key: tokens}),
                              key) == "an estimated 5 cents plus 12,400 tokens at an unknown price"
    assert codex_column_words(_totals(problem="damaged"), key) == "cost not known"


def _row(**extra):
    values = dict(source=OWN, key="own:chat-1", title="Budget", cwd="C:\\G\\Repo",
                  cli_session_id="chat-1", api_credits=True, api_cost="$1.42")
    values.update(extra)
    return SessionInfo(**values)


def test_the_row_says_the_cost_just_after_charged_to():
    assert DEFAULT_FIELDS.index(FIELD_COST) == DEFAULT_FIELDS.index(FIELD_CHARGED) + 1
    line = _row().list_line(0, ["title", "charged", "cost", "status"])
    assert line == "Budget, Claude API credits, $1.42, idle"
    # Nothing to say, nothing said: nothing spent, or not on API credits.
    assert _row(api_cost="").list_line(0, ["title", "charged", "cost"]) == "Budget, Claude API credits"
    assert _row(api_credits=False).list_line(0, ["title", "cost"]) == "Budget"


def test_older_columns_gain_cost_once_and_a_removal_is_kept(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"session_fields": ["title", "provider", "charged", "status"],
                                "provider_column_added": True, "charged_column_added": True}))
    settings = SpeechSettings.load(path)
    assert settings.session_fields == ["title", "provider", "charged", "cost", "status"]
    settings.session_fields.remove("cost")
    settings.save(path)
    assert json.loads(path.read_text())["cost_column_added"] is True
    assert "cost" not in SpeechSettings.load(path).session_fields
    # Without Charged to, it goes where Charged to would.
    path.write_text(json.dumps({"session_fields": ["title", "provider", "status"],
                                "provider_column_added": True, "charged_column_added": True}))
    assert SpeechSettings.load(path).session_fields == ["title", "provider", "cost", "status"]


def _ledgers(tmp_path):
    books = {"claude": spend_ledger.SpendLedger(tmp_path / "claude"),
             "openai": spend_ledger.SpendLedger(tmp_path / "openai")}
    return books, ColumnCosts(lambda which: books[which])


def _spend(book, chat, cost):
    reservation = book.reserve(chat)
    book.settle(reservation, cost)
    book.close(reservation, complete=True)


def test_the_column_is_filled_from_the_ledgers(tmp_path):
    books, costs = _ledgers(tmp_path)
    _spend(books["claude"], "chat-1", 0.19)
    _spend(books["openai"], "codex-api:t", 0.03)
    rows = [_row(api_cost=""), _row(key="codex-api:t", cli_session_id="t", provider="codex-api",
                                    api_cost=""),
            _row(key="own:chat-2", cli_session_id="chat-2", api_credits=False, api_cost="")]
    fill_api_costs(rows, costs)
    assert [r.api_cost for r in rows] == ["19 cents", "an estimated 3 cents", ""]


def test_the_ledger_is_replayed_only_when_it_changed(tmp_path, monkeypatch):
    books, costs = _ledgers(tmp_path)
    _spend(books["claude"], "chat-1", 0.19)
    replays = []
    original = spend_ledger.SpendLedger.totals

    def counted(self, now=None):
        replays.append(self.path)
        return original(self, now)
    monkeypatch.setattr(spend_ledger.SpendLedger, "totals", counted)
    rows = [_row(api_cost="")]
    for _ in range(5):
        fill_api_costs(rows, costs)
    assert len(replays) == 1 and rows[0].api_cost == "19 cents"
    _spend(books["claude"], "chat-1", 0.06)
    fill_api_costs(rows, costs)
    assert len(replays) == 2 and rows[0].api_cost == "25 cents"
    # A list with no chat on API credits never reads a ledger.
    fill_api_costs([_row(api_credits=False)], costs)
    assert len(replays) == 2
