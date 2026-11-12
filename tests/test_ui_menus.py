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


# -- your keys (#635) ------------------------------------------------------------------------

def _accel(item):
    """The item's accelerator in keymap's names: wx's own string orders the
    modifiers differently by version ("Alt+Ctrl+P" in wx 3.2)."""
    from thechatplace.keymap import normalize
    accel = item.GetAccel()
    return normalize(accel.ToString()) if accel is not None else ""


def test_the_menus_start_with_the_keys_in_keys_json(env):
    import json
    from thechatplace import platform_paths
    from thechatplace.ui.main_frame import MainFrame
    path = platform_paths.app_data_dir() / "keys.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "keys": {"prompts": "Ctrl+K", "sounds": "Ctrl+J",
                                                         "find": ""}}), encoding="utf-8")
    window = MainFrame(store=OwnSessionStore(env["tmp"] / "own.json"), check_updates_at_start=False)
    try:
        items = window.menu_items
        assert items["prompts"].GetItemLabel() == "&Prompts...\tCtrl+K"
        assert _accel(items["prompts"]) == "Ctrl+K"
        assert items["sounds"].GetItemLabel() == "S&ounds...\tCtrl+J"
        assert items["find"].GetItemLabel() == "F&ind..." and items["find"].GetAccel() is None
    finally:
        window.stop_timers()
        window._pool.shutdown(wait=True)
        wx.GetApp().ProcessPendingEvents()
        window.Destroy()
        wx.GetApp().ProcessPendingEvents()


def test_a_changed_key_shows_at_once_in_the_menus(frame):
    taken = frame.keymap.set("sounds", "Ctrl+Shift+P")
    assert taken == "prompts"
    frame.apply_keys()
    items = frame.menu_items
    assert items["sounds"].GetItemLabel() == "S&ounds...\tCtrl+Shift+P"
    assert _accel(items["sounds"]) == "Ctrl+Shift+P"
    assert items["prompts"].GetItemLabel() == "&Prompts..."
    assert items["prompts"].GetAccel() is None
    # The context menus follow the menu bar.
    frame.keymap.set("properties", "Ctrl+B")
    frame.apply_keys()
    menu = frame._message_menu()
    try:
        labels = [item.GetItemLabel() for item in menu.GetMenuItems()]
        assert "&Properties...\tCtrl+B" in labels
    finally:
        menu.Destroy()


def test_a_command_on_an_enter_key_runs_from_the_messages(frame, monkeypatch):
    # The window's key hook takes Enter keys first (Enter reads a message),
    # so a command you put on one runs from there, and Alt+Enter is no
    # longer Properties once you've moved Properties.
    ran = []
    handlers = frame._command_handlers()
    handlers["sounds"] = lambda e: ran.append("sounds")
    handlers["properties"] = lambda e: ran.append("properties")
    monkeypatch.setattr(frame, "_command_handlers", lambda: handlers)
    frame._build_menu()  # it runs through the menu item, as its accelerator would
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.chat_list))
    monkeypatch.setattr(frame, "on_read_message", lambda *a: ran.append("read"))

    def press(alt=False, shift=False):
        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        event.SetKeyCode(wx.WXK_RETURN)
        event.SetAltDown(alt)
        event.SetShiftDown(shift)
        frame._on_char_hook(event)

    press(alt=True)
    assert ran == ["properties"]
    frame.keymap.set("sounds", "Alt+Shift+Enter")
    frame.keymap.set("properties", "Ctrl+B")
    press(alt=True, shift=True)
    press(alt=True)
    assert ran == ["properties", "sounds", "read"]


def test_a_check_item_on_an_enter_key_toggles_and_an_unavailable_one_doesnt_run(frame, monkeypatch):
    # Through its menu item, as an accelerator would (#639): the check
    # changes, which is what Show Tool Activity's handler reads.
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: frame.chat_list))
    frame.keymap.set("show_tool_activity", "Alt+Shift+Enter")
    before = frame.activity_item.IsChecked()
    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(wx.WXK_RETURN)
    event.SetAltDown(True)
    event.SetShiftDown(True)
    frame._on_char_hook(event)
    assert frame.activity_item.IsChecked() is not before
    ran = []
    monkeypatch.setattr(frame, "on_stop_background_task", lambda: ran.append("stop"))
    frame.keymap.set("stop_background_task", "Alt+Shift+Enter")
    assert not frame.stop_background_item.IsEnabled()
    frame._run_command("stop_background_task")
    assert ran == []


def test_stop_is_named_by_your_key(frame):
    from thechatplace.ui_text import with_stop_key
    try:
        frame.keymap.set("stop_running_turn", "Ctrl+J")
        frame.apply_keys()
        assert "Press Stop (Ctrl+J)" in with_stop_key("Press Stop to end it.", "win32")
    finally:
        frame.keymap.reset_all()
        frame.apply_keys()
    assert "Press Stop (Ctrl+Period)" in with_stop_key("Press Stop to end it.", "win32")
