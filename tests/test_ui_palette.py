"""View, Command Palette (Ctrl+Shift+P, #637)."""
import pytest

wx = pytest.importorskip("wx")

from thechatplace.own_store import OwnSessionStore  # noqa: E402

import fake_env  # noqa: E402
from thechatplace.ui_text import key_name as K  # noqa: E402  (Cmd+ on a Mac)


@pytest.fixture
def env(tmp_path, monkeypatch, app):
    return fake_env.install(tmp_path, monkeypatch.setattr)


@pytest.fixture
def frame(env):
    from thechatplace.ui.main_frame import MainFrame
    window = MainFrame(store=OwnSessionStore(env["tmp"] / "own.json"),
                       check_updates_at_start=False)
    yield window
    window._runners.clear()
    window.stop_timers()
    window._pool.shutdown(wait=True)
    wx.GetApp().ProcessPendingEvents()
    window.Destroy()
    wx.GetApp().ProcessPendingEvents()


@pytest.fixture
def palette(frame):
    from thechatplace.ui.palette_dialog import CommandPaletteDialog
    said = []
    window = CommandPaletteDialog(frame, frame.keymap, ["sounds"],
                                  lambda i: frame.menu_items[i].IsEnabled(), said.append)
    window.said = said
    yield window
    window.Destroy()


def _key(code):
    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(code)
    return event


def test_ctrl_shift_p_is_the_palette_and_prompts_has_no_key(frame):
    items = frame.menu_items
    assert items["command_palette"].GetItemLabel() == "Command &Palette...\tCtrl+Shift+P"
    assert items["prompts"].GetItemLabel() == "&Prompts..."
    assert frame.keymap.command_for("Ctrl+Shift+P") == "command_palette"


def test_it_opens_on_recent_commands_then_the_rest(palette):
    rows = palette.list.GetStrings()
    assert rows[0] == "Sounds, Tools menu"
    assert rows[1] == f"New Session, {K('Ctrl+N')}, File menu"
    assert not any(row.startswith("Command Palette") for row in rows)
    assert palette.list.GetName() == f"Commands, {len(rows)}"
    assert "Stop Background Task, Chat menu, unavailable" in rows


def test_typing_narrows_and_arrows_move_from_the_search_box(palette, monkeypatch):
    palette.search.SetValue("perm mode")
    assert palette.list.GetStringSelection() == \
        f"Change Permission Mode, {K('Ctrl+Shift+O')}, Chat, Chat Settings"
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: palette.search))
    palette.search.SetValue("chat")
    # The first Down after typing says the top match, unheard so far (#641).
    palette._on_key(_key(wx.WXK_DOWN))
    assert palette.list.GetSelection() == 0
    assert palette.said[-1] == f"Chat Permissions, {K('Ctrl+Shift+H')}, Chat, Chat Settings"
    palette._on_key(_key(wx.WXK_DOWN))
    assert palette.said[-1] == palette.list.GetStringSelection() == "Chat Options, Chat, Chat Settings"
    palette._on_key(_key(wx.WXK_UP))
    palette._on_key(_key(wx.WXK_UP))  # stays on the first
    assert palette.list.GetSelection() == 0
    # A key, as written or as said, finds its command.
    palette.search.SetValue("ctrl+/")
    assert palette.selected().id == "insert_command"
    palette.search.SetValue("ctrl+slash")
    assert palette.selected().id == "insert_command"
    palette.search.SetValue("zzzz")
    assert palette.list.GetStrings() == ["No command matches."]
    palette._on_key(_key(wx.WXK_DOWN))
    assert palette.said[-1] == "No command matches."


def test_run_chooses_an_available_command_only(palette, monkeypatch):
    ended = []
    monkeypatch.setattr(palette, "EndModal", lambda code: ended.append(code))
    palette.search.SetValue("stop background")
    palette.on_run()
    assert palette.said[-1] == "Stop Background Task isn't available now." and ended == []
    palette.search.SetValue("sounds")
    palette.on_run()
    assert palette.chosen == "sounds" and ended == [wx.ID_OK]
    palette.search.SetValue("zzzz")
    palette.on_run()
    assert palette.said[-1] == "No command matches. Change the search."


def test_the_window_runs_the_choice_through_its_menu_item_and_remembers_it(frame, monkeypatch):
    ran = []
    monkeypatch.setattr(frame, "_run_command", lambda command_id: ran.append(command_id))

    class Chose:
        def __init__(self, parent, keys, recent, available, say):
            self.chosen = "find"
            self.recent = list(recent)

        def ShowModal(self):
            return wx.ID_OK

        def Destroy(self):
            pass

    from thechatplace.ui import main_frame
    monkeypatch.setattr(main_frame, "CommandPaletteDialog", Chose)
    frame.on_command_palette()
    wx.GetApp().ProcessPendingEvents()
    assert ran == ["find"] and frame._recent_commands == ["find"]
