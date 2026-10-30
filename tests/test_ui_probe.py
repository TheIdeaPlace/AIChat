"""The visual probe (#155) without taking any pictures: it still covers
every dialog, its plan names real surfaces, and its layout checks find
cut-off text and overlapping controls."""
import inspect
import json
import sys
from pathlib import Path

import pytest

wx = pytest.importorskip("wx")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import ui_probe  # noqa: E402


#: wx's own dialogs the probe photographs, which aren't classes of ours.
WX_DIALOGS = {"TextEntryDialog", "SingleChoiceDialog"}


def test_every_dialog_is_photographed_or_says_why_not():
    from thechatplace.ui import dialogs
    classes = {name for name, cls in inspect.getmembers(dialogs, inspect.isclass)
               if issubclass(cls, wx.Dialog) and cls.__module__ == dialogs.__name__}
    covered = set(ui_probe.DIALOG_CLASSES.values()) | set(ui_probe.NOT_PHOTOGRAPHED)
    assert classes - covered == set(), "add a surface to tools/ui_probe.py for these dialogs"
    assert covered - classes == WX_DIALOGS, "the probe names dialogs that no longer exist"


def test_each_dialog_surface_says_which_dialog_it_shows():
    dialog_surfaces = {name for name, (kind, _f, _a) in ui_probe.SURFACES.items()
                       if kind == "dialog"}
    assert dialog_surfaces == set(ui_probe.DIALOG_CLASSES)


@pytest.fixture
def opened(tmp_path, monkeypatch, app):
    """Each surface's setup run on a hidden window, with ShowModal recording
    which dialog would have opened instead of opening it."""
    shown = []

    def record(self):
        shown.append(type(self).__name__)
        return wx.ID_CANCEL
    for cls in (wx.Dialog, wx.TextEntryDialog, wx.SingleChoiceDialog):
        monkeypatch.setattr(cls, "ShowModal", record)
    return shown


@pytest.mark.parametrize("name", list(ui_probe.SURFACES))
def test_each_surface_opens_what_it_says_on_made_up_data(name, opened, tmp_path, monkeypatch):
    import fake_env
    kind, function, _about = ui_probe.SURFACES[name]
    empty = name in ui_probe.EMPTY_WORLD
    # No web view in the tests: the formatted surfaces expect their plain
    # stand-ins, as on a Mac.
    env = ui_probe.build_world(tmp_path, monkeypatch.setattr, empty=empty, formatted_view=False)
    frame = ui_probe.build_frame(env, empty=empty)
    try:
        if kind == "window":
            assert function(frame, env) is None
            return
        opener = function(frame, env)
        opener()
        assert fake_env.pump(lambda: opened, timeout=10), f"{name} opened no dialog"
        assert opened == [ui_probe.expected_class(name)]
    finally:
        ui_probe.close_frame(frame)


def test_surfaces_start_from_fresh_data(opened, tmp_path, monkeypatch):
    # The working surface leaves a turn running and messages queued; the
    # next surface's window and data are new, so none of that shows there.
    env = ui_probe.build_world(tmp_path / "one", monkeypatch.setattr, formatted_view=False)
    frame = ui_probe.build_frame(env)
    try:
        ui_probe.s_main_own_working(frame, env)
        assert frame._queued
    finally:
        ui_probe.close_frame(frame)
    env = ui_probe.build_world(tmp_path / "two", monkeypatch.setattr, formatted_view=False)
    frame = ui_probe.build_frame(env)
    try:
        assert not frame._queued and not frame._runners
    finally:
        ui_probe.close_frame(frame)


def test_surfaces_show_their_made_up_content(opened, tmp_path, monkeypatch):
    # Opening the right dialog isn't enough: in 0.1.7's first run the Codex
    # screens showed "Couldn't load this Codex chat" and Lists only its empty
    # lines, and every other test still passed (#283).
    from thechatplace import lists

    def world(name):
        env = ui_probe.build_world(tmp_path / name, monkeypatch.setattr, formatted_view=False)
        return env, ui_probe.build_frame(env)

    env, frame = world("codex")
    try:
        ui_probe.s_main_codex(frame, env)
        assert [m.text for m in frame._chat_messages][:1] == ["Explain the upload test."]
        assert len(frame._chat_messages) == 2
        row = frame.session_list.GetString(frame.session_list.GetSelection())
        assert "2 hours ago" in row
    finally:
        ui_probe.close_frame(frame)
    env, frame = world("codex-files")
    try:
        ui_probe.d_lists_codex_files(frame, env)
        files = lists.file_items(frame._chat_edits, None,
                                 lambda changes: changes.counts())
        assert [item.row for item in files] == ["2 lines added, 1 removed", "created, 1 line", "deleted", "renamed"]
        assert "Added:     wait_until_ready()" in files[0].detail
        assert "Lines 1 to 2:\nRemoved: Old notes.\nRemoved: More old notes." in files[2].detail
        assert "Renamed from: old_test.py" in files[3].detail
    finally:
        ui_probe.close_frame(frame)
    env, frame = world("owned")
    try:
        ui_probe.s_main_codex_owned(frame, env)
        assert len(frame._chat_messages) == 2
        row = next(frame.session_list.GetString(i) for i in range(frame.session_list.GetCount())
                   if frame.session_list.GetString(i).startswith("Codex history preview"))
        assert "working" in row and "Chat Place session" in row
    finally:
        ui_probe.close_frame(frame)
    env, frame = world("copilot-copy")
    try:
        ui_probe.s_main_copilot_copy(frame, env)
        assert [m.label for m in frame._chat_messages] == ["Earlier conversation", "You",
                                                           "Copilot"]
        assert frame._chat_messages[0].text == '2 messages from "Copilot history preview".'
        assert frame._open.is_own
        assert frame._chat_messages[1].text == "Now make the upload test faster."
    finally:
        ui_probe.close_frame(frame)
    env, frame = world("commands")
    try:
        ui_probe.d_lists_commands(frame, env)
        assert frame._chat_commands
    finally:
        ui_probe.close_frame(frame)
    env, frame = world("decisions")
    try:
        ui_probe.d_lists_decisions(frame, env)
        assert len(lists.decision_items(frame._chat_messages, None)) == 2
    finally:
        ui_probe.close_frame(frame)


def test_the_plan_names_real_surfaces_and_well_formed_variants():
    plan = json.loads((ROOT / "tools" / "ui_probe_plan.json").read_text(encoding="utf-8"))
    tags = [v["tag"] for v in plan["windows"] + plan["macos"]]
    assert len(tags) == len(set(tags))
    for variant in plan["windows"]:
        assert variant["theme"] in ("light", "dark", "high-contrast")
        assert variant["scale"] in (100, 125, 150, 175, 200)
        assert set(variant.get("surfaces", list(ui_probe.SURFACES))) <= set(list(ui_probe.SURFACES))
    for variant in plan["macos"]:
        assert variant["theme"] in ("light", "dark")


def _control(entries, label):
    return next(e for e in entries["controls"] if e["label"] == label)


def test_describe_flags_cut_off_text_and_overlaps(app):
    dialog = wx.Dialog(None, title="Probe check", size=(400, 300))
    try:
        # A Mac sizes a label to its text unless told not to.
        cramped = wx.StaticText(dialog, label="A label far too long for the room it was given",
                                pos=(10, 10), size=(60, 20), style=wx.ST_NO_AUTORESIZE)
        cramped.SetSize((60, 20))
        roomy = wx.StaticText(dialog, label="Fits", pos=(10, 60))
        wx.Button(dialog, label="First", pos=(10, 120), size=(100, 30))
        wx.Button(dialog, label="Second", pos=(50, 125), size=(100, 30))
        roomy.SetSize(roomy.GetBestSize())
        assert cramped.GetSize().width == 60
        info = ui_probe.describe(dialog)
        assert info["title"] == "Probe check"
        assert any("text cut off" in p for p in _control(info, cramped.GetLabel())["problems"])
        assert _control(info, "Fits")["problems"] == []
        assert any("overlaps" in p for p in _control(info, "First")["problems"])
    finally:
        dialog.Destroy()


def test_describe_lists_list_items_and_text(app):
    dialog = wx.Dialog(None, title="Lists", size=(400, 300))
    try:
        wx.ListBox(dialog, choices=["one", "two"], pos=(0, 0), size=(100, 100),
                   name="sessions").SetSelection(1)
        wx.TextCtrl(dialog, value="typed", pos=(0, 150), size=(100, 25))
        info = ui_probe.describe(dialog)
        listbox = next(c for c in info["controls"] if c["class"] == "ListBox")
        assert listbox["items"] == ["one", "two"] and listbox["selection"] == 1
        assert next(c for c in info["controls"] if c["class"] == "TextCtrl")["value"] == "typed"
    finally:
        dialog.Destroy()


def test_a_blank_capture_is_recognised(app):
    blank = wx.Bitmap(50, 40)
    dc = wx.MemoryDC(blank)
    dc.SetBackground(wx.WHITE_BRUSH)
    dc.Clear()
    dc.SelectObject(wx.NullBitmap)
    assert ui_probe._one_colour(blank)
    drawn = wx.Bitmap(50, 40)
    dc = wx.MemoryDC(drawn)
    dc.SetBackground(wx.WHITE_BRUSH)
    dc.Clear()
    dc.SetBrush(wx.BLACK_BRUSH)
    dc.DrawRectangle(5, 5, 30, 20)
    dc.SelectObject(wx.NullBitmap)
    assert not ui_probe._one_colour(drawn)


class _View:
    def __init__(self, rect):
        self._rect = rect

    def GetScreenRect(self):
        return wx.Rect(*self._rect)


class _Dialog:
    def GetHandle(self):
        return 1


def _page(app, colour_box=None):
    """A 300 by 200 dialog picture whose page area (50, 40, 200, 120 on a
    screen where the dialog is at 100, 100) is flat white, or has a box."""
    image = wx.Image(300, 200)
    image.SetRGB(wx.Rect(0, 0, 300, 200), 240, 240, 240)
    image.SetRGB(wx.Rect(50, 40, 200, 120), 255, 255, 255)
    if colour_box:
        image.SetRGB(wx.Rect(*colour_box), 0, 0, 0)
    return image.ConvertToBitmap()


@pytest.mark.parametrize("box, drawn", [(None, False), ((80, 70, 60, 20), True),
                                        # Only outside the page (the frame): still blank.
                                        ((0, 0, 30, 30), False)])
def test_a_page_is_drawn_only_when_its_area_has_more_than_one_colour(app, monkeypatch, box, drawn):
    monkeypatch.setattr(ui_probe, "IS_WINDOWS", True)
    monkeypatch.setattr(ui_probe, "_print_window", lambda window: _page(app, box))
    monkeypatch.setattr(ui_probe, "_window_rect", lambda hwnd: (100, 100, 400, 300))
    view = _View((150, 140, 200, 120))  # the page, in screen coordinates
    assert ui_probe._pages_drawn(_Dialog(), [view]) is drawn


def test_a_page_windows_could_not_print_is_not_drawn(app, monkeypatch):
    monkeypatch.setattr(ui_probe, "IS_WINDOWS", True)
    monkeypatch.setattr(ui_probe, "_print_window", lambda window: None)
    assert ui_probe._pages_drawn(_Dialog(), [_View((0, 0, 50, 50))]) is False


def test_the_working_surface_says_working(opened, tmp_path, monkeypatch):
    env = ui_probe.build_world(tmp_path, monkeypatch.setattr, formatted_view=False)
    frame = ui_probe.build_frame(env)
    try:
        ui_probe.s_main_own_working(frame, env)
        assert "working" in frame.session_heading.GetLabel()
        row = next(s for s in frame.session_list.GetStrings() if s.startswith("Visual probe"))
        assert "working" in row
    finally:
        ui_probe.close_frame(frame)


LONG_STATUS = "1 message queued. Claude is working (1 minute 15 seconds)."


# Widths from the label's own text, so the cases hold with any font and
# scaling (a Mac's system font is larger than Windows' at 100%).
@pytest.mark.parametrize("label, width, hidden", [
    (LONG_STATUS, lambda text: 1, True),
    (LONG_STATUS, lambda text: text.GetTextExtent("W...").width // 2, True),
    (LONG_STATUS, lambda text: text.GetTextExtent(LONG_STATUS).width // 2, False),
    (LONG_STATUS, lambda text: text.GetTextExtent(LONG_STATUS).width + 2, False),
    # A short label a pixel too narrow for itself still shows nearly all of it.
    ("OK", lambda text: text.GetTextExtent("OK").width - 1, False),
])
def test_an_ellipsized_label_too_narrow_to_show_anything_is_flagged(app, label, width, hidden):
    dialog = wx.Dialog(None, size=(1000, 200))
    try:
        text = wx.StaticText(dialog, label=label, pos=(10, 10),
                             style=wx.ST_ELLIPSIZE_END | wx.ST_NO_AUTORESIZE)
        text.SetSize((width(text), 20))
        info = ui_probe.describe(dialog)
        problems = next(c for c in info["controls"] if c["label"] == label)["problems"]
        assert any("too narrow" in p for p in problems) is hidden
        assert not any("cut off" in p for p in problems)  # shortened on purpose
    finally:
        dialog.Destroy()


def test_status_bar_text_is_shortened_with_an_ellipsis(app):
    from thechatplace.ui.statusbar import shown_text
    frame = wx.Frame(None)
    try:
        bitmap = wx.Bitmap(10, 10)  # a memory DC: the frame is never shown
        dc = wx.MemoryDC(bitmap)
        dc.SetFont(frame.GetFont())
        text = "Fix the flaky upload test: needs you, Pick a name for the release branch"
        assert shown_text(dc, text, 2000) == text
        short = shown_text(dc, text, 120)
        assert short.endswith("...") and len(short) < len(text)
        assert dc.GetTextExtent(short).width <= 120
        assert shown_text(dc, text, 0) == ""
        assert shown_text(dc, "Tom & Jerry", 2000) == "Tom & Jerry"  # "&" drawn as written
    finally:
        frame.Destroy()
