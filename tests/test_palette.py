"""The command palette's list, without wx (#637)."""
from thechatplace import commands, palette
from thechatplace.palette import entries, matches, row


def _ids(found):
    return [e.id for e in found]


def test_every_command_but_the_palette_is_offered():
    ids = [e.id for e in entries()]
    assert palette.SELF not in ids
    assert len(ids) == len([c for c in commands.commands()]) - 1
    model = next(e for e in entries() if e.id == "change_model")
    assert (model.name, model.place) == ("Change Model", "Chat, Chat Settings")
    find = next(e for e in entries() if e.id == "find")
    assert (find.name, find.place) == ("Find", "View menu")


def test_a_row_says_name_key_place_and_whether_it_can_run():
    find = next(e for e in entries() if e.id == "find")
    assert row(find, "Ctrl+F") == "Find, Ctrl+F, View menu"
    stop = next(e for e in entries() if e.id == "stop_background_task")
    assert row(stop, "", available=False) == "Stop Background Task, Chat menu, unavailable"


def test_words_in_any_order_each_starting_a_word():
    found = _ids(matches("mode perm", entries()))
    assert found[0] == "change_permission_mode"
    assert "change_model" not in found  # "mode" doesn't start a word of it
    assert _ids(matches("sign codex", entries())) == ["codex_sign_in"]


def test_a_name_starting_with_the_first_word_comes_first():
    found = _ids(matches("chat", entries()))
    # Chat Permissions and Chat Options start with it; the Chat menu's
    # commands only have it in their place.
    assert found[:2] == ["chat_permissions", "chat_options"]
    assert "answer" in found


def test_inside_a_word_still_matches_after_the_rest():
    # Names starting with it, then a later word starting with it, then
    # inside a word ("Subagents"), each in menu order.
    assert _ids(matches("agent", entries())) == [
        "agent_conversation_members", "agent_conversation_handoffs",
        "new_agent_conversation", "subagents"]
    assert _ids(matches("group", entries())) == [
        "add_to_group", "remove_from_group", "manage_groups"]


def test_a_key_finds_its_command():
    keys = {"find_in_all_sessions": "Ctrl+Shift+S"}
    found = _ids(matches("ctrl+shift+s", entries(), lambda i: keys.get(i, "")))
    assert found == ["find_in_all_sessions"]


def test_a_key_is_found_however_it_is_written():
    # As keymap names it, and as said on Windows; a Mac says Cmd (#641).
    keys = {"insert_command": "Cmd+Slash Ctrl+Slash", "settings": "Cmd+Comma Ctrl+Comma"}
    for typed in ("ctrl+/", "ctrl+slash", "cmd+slash", "Cmd+/", "cmd+sl"):
        assert _ids(matches(typed, entries(), lambda i: keys.get(i, ""))) == ["insert_command"], typed
    assert _ids(matches("cmd+comma", entries(), lambda i: keys.get(i, ""))) == ["settings"]
    # Part of a key is still words to match.
    assert "insert_command" in _ids(matches("ctrl+sl", entries(), lambda i: keys.get(i, "")))


def test_nothing_typed_lists_recent_first_then_menu_order():
    found = _ids(matches("", entries(), recent=["sounds", "find"]))
    assert found[:2] == ["sounds", "find"]
    assert found[2] == entries()[0].id
    assert len(found) == len(entries())


def test_no_match():
    assert matches("zzzz", entries()) == []
