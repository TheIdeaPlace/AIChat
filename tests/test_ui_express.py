"""Express (#737) in the window: the pinned row, the list, its keys and R.
These tests construct windows: run them in the VM or CI only."""
import pytest

wx = pytest.importorskip("wx")

from test_ui_hidden import env, fake_runner, frame, select, settle  # noqa: F401,E402
from fake_env import add_transcript, pump, status_field  # noqa: E402
from records import assistant_block, text_block, user_text  # noqa: E402
from thechatplace import express as express_text  # noqa: E402
from thechatplace.express import EXPRESS_KEY  # noqa: E402
from thechatplace.sessions import SORT_VALUES  # noqa: E402
from thechatplace.ui import express as express_ui  # noqa: E402


def replies(env):
    """Something in each of the fixture's three chats: a reply, and for the
    quiet one a message of yours after it (Express shows the reply)."""
    add_transcript(env, "C:\\G\\Repo", "cli-a", [user_text("Tidy it"),
                                                 assistant_block(text_block("Tidied."), "a1"),
                                                 user_text("Thanks")])
    add_transcript(env, "C:\\G\\Repo", "cli-b", [user_text("Name it"),
                                                 assistant_block(text_block("Which name?"), "b1")])
    add_transcript(env, "C:\\G\\Scratch", "own-1", [user_text("Probe it"),
                                                    assistant_block(text_block("Probed."), "o1")])


def turn_on(frame):
    frame._express_set(True, say=False)
    settle(frame)
    assert pump(lambda: frame._list_keys[:1] == [EXPRESS_KEY])


def open_express(frame):
    frame.session_list.SetSelection(0)
    frame.on_open_session()
    assert frame._express_open


def row_of(frame, title):
    for i, key in enumerate(frame._express_keys):
        if frame._express_rows[key].title == title:
            frame.chat_list.SetSelection(i)
            return i
    raise AssertionError(f"{title} not in Express")


class Dialog:
    """ExpressReplyDialog, answered: sends ``text`` once, as Send would."""
    text = "Ship it"
    seen = []

    def __init__(self, parent, title, send, say=None, text=""):
        Dialog.seen.append(title)
        self.result = send(Dialog.text)

    def ShowModal(self):
        return wx.ID_OK if self.result else wx.ID_CANCEL

    def Destroy(self):
        pass


@pytest.fixture
def dialog(monkeypatch):
    Dialog.seen, Dialog.text = [], "Ship it"
    monkeypatch.setattr(express_ui, "ExpressReplyDialog", Dialog)
    return Dialog


def test_off_by_default(frame):
    assert not frame.speech.express_enabled
    assert EXPRESS_KEY not in frame._list_keys and frame.session_list.GetCount() == 3
    assert not frame.menu_items["express_toggle"].IsChecked()


def test_on_it_is_the_first_row_under_every_sort_and_filter(frame, env):
    replies(env)
    turn_on(frame)
    assert frame.menu_items["express_toggle"].IsChecked()
    assert frame.session_list.GetCount() == 4
    assert frame.session_list.GetString(0).startswith("Express, 3 chats")
    for order in SORT_VALUES:
        frame.on_sort(order)
        settle(frame)
        assert frame._list_keys[0] == EXPRESS_KEY
    frame._set_session_filter("Quiet")
    assert frame._list_keys[0] == EXPRESS_KEY
    assert frame.session_list.GetString(0) == "Express, 1 chat"
    assert frame.session_list.GetCount() == 2
    frame._set_session_filter("")
    frame._express_set(False, say=False)
    assert EXPRESS_KEY not in frame._list_keys


def test_the_express_row_is_never_a_chat(frame, env):
    turn_on(frame)
    frame.session_list.SetSelection(0)
    assert frame._current_info(EXPRESS_KEY) is None
    assert frame._selected_session() is None
    for handler in (frame.on_rename, frame.on_hide, frame.on_delete_permanently,
                    frame.on_export):
        env["feedback"].clear()
        handler()
        assert env["feedback"] and "selected" in env["feedback"][-1].lower(), handler
    assert frame._list_keys[0] == EXPRESS_KEY  # nothing hid or deleted it


def test_enter_opens_the_list_on_the_newest_chat(frame, env):
    replies(env)
    turn_on(frame)
    open_express(frame)
    assert frame.chat_list.GetCount() == 3 and frame.chat_list.GetSelection() == 0
    newest = frame._express_rows[frame._express_keys[0]]
    assert newest.last_activity_ms == max(r.last_activity_ms for r in frame._express_rows.values())
    quiet = frame._express_rows[next(k for k in frame._express_keys
                                     if frame._express_rows[k].title == "Quiet one")]
    assert quiet.last_reply == "Tidied."  # the reply, not "Thanks"
    assert not frame.own_reply.IsShown() and frame.desktop_reply.IsShown()
    assert frame.desktop_note.GetValue() == "Press R to reply to the chat you're on."
    assert frame.messages_label.GetLabel() == "&Express, 3 chats:"
    assert status_field(frame.GetStatusBar(), 1) == "Express, 3 chats"
    assert frame._open is None


def test_enter_on_a_row_opens_that_chat(frame, env):
    replies(env)
    turn_on(frame)
    open_express(frame)
    row_of(frame, "Blocked one")
    frame.express_open_chat()
    assert not frame._express_open and frame._open.title == "Blocked one"


def test_escape_goes_back_to_the_express_row_and_enter_comes_back(frame, env):
    replies(env)
    turn_on(frame)
    open_express(frame)
    frame.session_list.SetSelection(2)
    frame._express_back_to_list()
    assert frame.session_list.GetSelection() == 0
    assert frame._express_open  # still shown, as a chat stays loaded
    frame.on_open_session()
    assert env["feedback"][-1] == "Back in Express, 3 chats."


def test_r_on_an_own_claude_chat_sends_without_leaving_express(frame, env, fake_runner, dialog):
    replies(env)
    turn_on(frame)
    open_express(frame)
    row_of(frame, "Hub probe")
    frame.express_reply()
    assert dialog.seen == ["Reply to Hub probe (Claude)"]
    assert fake_runner.instances and fake_runner.instances[-1].prompt == "Ship it"
    assert frame._express_open and frame._open is None
    assert frame._express_rows[frame._express_keys[frame.chat_list.GetSelection()]].title \
        == "Hub probe"


def test_r_on_a_desktop_chat_says_why_and_offers_continue_here(frame, env, dialog, monkeypatch):
    replies(env)
    turn_on(frame)
    open_express(frame)
    row_of(frame, "Quiet one")
    asked = []

    class Question:
        def __init__(self, parent, text, title, style):
            asked.append((title, text))

        def SetYesNoLabels(self, yes, no):
            asked.append((yes, no))

        def ShowModal(self):
            return wx.ID_NO

        def Destroy(self):
            pass
    monkeypatch.setattr(express_ui.wx, "MessageDialog", Question)
    frame.express_reply()
    assert dialog.seen == []  # no reply box for it
    assert asked[0][0] == "Reply to Quiet one (Claude)"
    assert "Claude desktop app" in asked[0][1] or "desktop" in asked[0][1].lower()
    assert asked[1] == ("&Continue Here...", "Cancel")
    assert frame._express_open


def test_a_failed_send_keeps_the_text(frame, env, dialog, monkeypatch):
    from thechatplace import platform_paths
    replies(env)
    turn_on(frame)
    open_express(frame)
    row_of(frame, "Hub probe")
    boxes = []
    monkeypatch.setattr(platform_paths, "find_claude",
                        lambda: platform_paths.ClaudeLookup(None, "Claude Code isn't installed."))
    monkeypatch.setattr(wx, "MessageBox", lambda text, *a, **k: boxes.append(text))
    sent = []
    real = frame._express_send
    monkeypatch.setattr(frame, "_express_send", lambda key, text: sent.append(real(key, text))
                        or sent[-1])
    frame.express_reply()
    # The send said why and returned False: the dialog keeps the text.
    assert sent == [False] and boxes
    assert frame._express_open


def test_the_order_holds_while_you_are_in_the_list(frame, env):
    replies(env)
    turn_on(frame)
    open_express(frame)
    before = list(frame._express_keys)
    last = frame._express_rows[before[-1]]
    for info in frame._snapshot.sessions:
        if info.key == last.key:
            info.last_activity_ms += 10_000_000  # now the newest
    frame._express_fill(frozen=True)
    assert frame._express_keys == before
    frame._express_fill(frozen=False)
    assert frame._express_keys[0] == last.key


def test_turning_it_off_while_open_empties_the_messages(frame, env):
    replies(env)
    turn_on(frame)
    open_express(frame)
    frame._express_set(False, say=False)
    assert not frame._express_open
    assert frame.chat_list.GetString(0).startswith("No session loaded")
    assert EXPRESS_KEY not in frame._list_keys


def test_screen_readers_read_more_of_the_reply_than_the_row_shows(frame, env):
    long = " ".join(["word"] * 300)
    add_transcript(env, "C:\\G\\Scratch", "own-1", [assistant_block(text_block(long), "o1")])
    turn_on(frame)
    open_express(frame)
    index = row_of(frame, "Hub probe")
    shown = frame.chat_list.GetString(index)
    read = frame._message_item_text(index)
    assert len(read) > len(shown) and long[:900] in read


def test_shift_enter_reads_the_whole_reply(frame, env, monkeypatch):
    replies(env)
    turn_on(frame)
    open_express(frame)
    row_of(frame, "Blocked one")
    read = []
    monkeypatch.setattr(frame, "_read_whole_message", lambda message: read.append(message))
    frame.express_read_reply()
    assert pump(lambda: read)
    assert read[0].text == "Which name?" and read[0].label == "Latest reply in Blocked one"


def test_columns_dialog_opens_expresss_own(frame, env, monkeypatch):
    from thechatplace.ui import dialogs
    replies(env)
    turn_on(frame)
    open_express(frame)
    seen = {}

    def show(self):
        seen["title"] = self.GetTitle()
        seen["available"] = list(self.available.GetStrings())
        self._shown = ["status", "title"]
        return wx.ID_OK
    monkeypatch.setattr(dialogs.SessionColumnsDialog, "ShowModal", show)
    frame.on_session_columns()
    assert seen["title"] == "Express Columns"
    assert "Status (needs you, working or idle)" in seen["available"]
    assert frame.speech.express_fields == ["status", "title"]
    first = frame._express_rows[frame._express_keys[0]]
    assert frame.chat_list.GetString(0) == express_text.row_text(first, ["status", "title"])


def test_text_used_in_the_list():
    assert express_text.REPLY_NOTE == "Press R to reply to the chat you're on."
