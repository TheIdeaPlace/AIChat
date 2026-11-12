"""The menu bar is built from commands.MENU_BAR (#575)."""
import pytest

wx = pytest.importorskip("wx")

from thechatplace import commands  # noqa: E402
from thechatplace.commands import Menu, menu_label  # noqa: E402
from thechatplace.own_store import OwnSessionStore  # noqa: E402

import fake_env  # noqa: E402


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


def test_every_command_has_a_handler_and_every_handler_a_command(frame):
    assert set(frame._command_handlers()) == {c.id for c in commands.commands()}
    assert set(frame.menu_items) == {c.id for c in commands.commands()}


def test_the_menu_bar_is_the_list(frame):
    bar = frame.GetMenuBar()
    assert [bar.GetMenuLabelText(i) for i in range(bar.GetMenuCount())] == [
        m.label.replace("&", "") for m in commands.MENU_BAR]

    def check(menu, spec):
        items = list(menu.GetMenuItems())
        assert len(items) == len(spec.items), spec.label
        for item, entry in zip(items, spec.items):
            if entry is None:
                assert item.IsSeparator()
            elif isinstance(entry, Menu):
                assert item.GetItemLabel() == entry.label
                if not entry.id:
                    check(item.GetSubMenu(), entry)
            elif entry.id == "check_for_updates":
                assert item.GetItemLabel().startswith("Check for &Updates (running ")
            elif entry.stock:
                # A Mac names these itself ("Quit\tCtrl+Q") and puts them in
                # its app menu; what matters is the stock id.
                stock = {"preferences": wx.ID_PREFERENCES, "about": wx.ID_ABOUT,
                         "exit": wx.ID_EXIT}
                assert item.GetId() == stock[entry.stock]
            else:
                assert item.GetItemLabel() == menu_label(entry)
                assert item.IsCheckable() == entry.check

    for index, spec in enumerate(commands.MENU_BAR):
        check(bar.GetMenu(index), spec)


def test_the_window_filled_its_own_submenus(frame):
    assert frame.sort_items and frame.view_items
    assert sum(item.IsChecked() for item in frame.sort_items.values()) == 1


def test_each_item_reaches_its_handler(frame, monkeypatch):
    called = []
    handlers = {command_id: (lambda e, c=command_id: called.append(c))
                for command_id in frame._command_handlers()}
    monkeypatch.setattr(frame, "_command_handlers", lambda: handlers)
    frame._build_menu()
    for command_id, item in frame.menu_items.items():
        event = wx.CommandEvent(wx.wxEVT_MENU, item.GetId())
        event.SetEventObject(frame)
        frame.GetEventHandler().ProcessEvent(event)
    assert called == list(frame.menu_items)


def test_the_windows_own_access_keys_are_the_ones_the_menus_keep_clear_of(frame):
    # #575: a visible control's key wins over the menu bar's, so the
    # menus' letters avoid these. Every labelled control in the main window
    # and its two reply areas, so a new one has to be added to the list.
    from test_commands import WINDOW_ACCESS_KEYS

    def labelled(window):
        for child in window.GetChildren():
            if isinstance(child, (wx.Button, wx.CheckBox, wx.StaticText)):
                yield child
            yield from labelled(child)

    keys = {commands.access_key(control.GetLabel())
            for control in labelled(frame)} - {""}
    assert keys == WINDOW_ACCESS_KEYS
    assert frame.attach_btn.GetLabel() == "Attach F&iles..."
    assert frame.reply_claude_btn.GetLabel() == "Open &in Claude"
    assert frame.continue_btn.GetLabel() == "Continue H&ere..."
