"""Layout checks that need no pictures (#155): every surface the visual
probe photographs, opened the same way on a hidden window, with no text
cut off, no controls on top of each other and nothing outside the window.

The surfaces and the measuring are the probe's (``tools/ui_probe.py``), so
what fails here is what the probe flags in its pictures. A failure names the
control and the problem; ``tools/ui_probe_vm.ps1`` in the test VM shows what
it looks like.

Problems already filed are listed in ``KNOWN`` with their issue. Each
surface's test expects exactly its known problems: a new one fails, and so
does a known one that has gone, so its entry is removed when the issue is
fixed. Windows only: what's cut off depends on the controls' and fonts'
sizes, and the probe's baseline is Windows.
"""
import re
import sys
from pathlib import Path

import pytest

wx = pytest.importorskip("wx")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import fake_env  # noqa: E402
import ui_probe  # noqa: E402
from markers import windows_layout  # noqa: E402

pytestmark = windows_layout

#: surface -> [(pattern matching one problem, issue)]. Each pattern names
#: the problem it excuses, so it can't hide another on the same control.
KNOWN = {}


def _problems(window) -> list:
    window.Layout()
    info = ui_probe.describe(window)
    return [f"{c['class']} {c['label'] or c['name']!r}: {p}"
            for c in info["controls"] for p in c["problems"]]


def _check(name, found):
    """``found`` is exactly the surface's known problems, each seen once or
    more (a pattern can match a control on several surfaces' windows)."""
    known = KNOWN.get(name, [])
    new = [p for p in found if not any(re.search(pattern, p) for pattern, _ in known)]
    gone = [f"{pattern} (#{issue})" for pattern, issue in known
            if not any(re.search(pattern, p) for p in found)]
    assert new == [], f"{name}: new layout problems"
    assert gone == [], f"{name}: fixed? remove these from KNOWN and close their issues"


@pytest.mark.parametrize("name", list(ui_probe.SURFACES))
def test_each_surface_lays_out_cleanly(name, app, tmp_path, monkeypatch):
    kind, function, _about = ui_probe.SURFACES[name]
    measured = []

    def measure(dialog):
        # A dialog's own background loading (From GitHub's list) lands first.
        fake_env.pump(lambda: False, timeout=0.3)
        measured.append(_problems(dialog))
        return wx.ID_CANCEL
    # Native common dialogs override ShowModal rather than inheriting the
    # patched wx.Dialog method. Intercept each one or CI opens a real modal.
    for cls in (wx.Dialog, wx.TextEntryDialog, wx.SingleChoiceDialog):
        monkeypatch.setattr(cls, "ShowModal", measure)
    empty = name in ui_probe.EMPTY_WORLD
    env = ui_probe.build_world(tmp_path, monkeypatch.setattr, empty=empty, formatted_view=False)
    frame = ui_probe.build_frame(env, empty=empty)
    frame.SetSize(ui_probe.DEFAULT_SIZE)
    try:
        if kind == "window":
            function(frame, env)
            measured.append(_problems(frame))
        else:
            function(frame, env)()
            assert fake_env.pump(lambda: measured, timeout=10), f"{name} opened no dialog"
    finally:
        ui_probe.close_frame(frame)
    _check(name, measured[0])


def test_a_long_session_title_fits_the_heading(app, tmp_path, monkeypatch):
    """Not one of the probe's pictures: the long-titled session loaded. Its
    heading and messages label used to be single lines that ran off the
    window (#176)."""
    env = ui_probe.build_world(tmp_path, monkeypatch.setattr, formatted_view=False)
    frame = ui_probe.build_frame(env)
    frame.SetSize(ui_probe.DEFAULT_SIZE)
    try:
        ui_probe._open(frame, "A session whose title")
        found = _problems(frame)
    finally:
        ui_probe.close_frame(frame)
    # The heading wraps and the messages label ends in "..." (#176), so
    # nothing here runs off the window.
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(KNOWN, "long-title", [])
        _check("long-title", found)


def test_status_bar_buttons_are_as_tall_as_their_text(app, tmp_path, monkeypatch):
    """#175: the buttons were 16 pixels in a bar whose text needs 23, which
    cut the bottoms off letters such as y ("needs vou")."""
    env = ui_probe.build_world(tmp_path, monkeypatch.setattr, formatted_view=False)
    frame = ui_probe.build_frame(env)
    frame.SetSize(ui_probe.DEFAULT_SIZE)
    try:
        frame.status_parts.set("context", "Context 42% full")
        frame.status_parts.set("update", "Update 0.2.0 ready")
        for key in ("context", "needs_you", "update"):
            button = frame.status_parts.get(key)
            assert button.IsShown()
            assert button.GetSize().height >= button.GetBestSize().height, key
    finally:
        ui_probe.close_frame(frame)


@pytest.mark.parametrize("size", [(1000, 720), (800, 600), (640, 480)])
def test_the_split_stays_put_whatever_is_loaded(app, tmp_path, monkeypatch, size):
    """#179: the session list was 378 pixels wide with an own session and
    268 while a turn ran, because the turn status beside the buttons set the
    right column's width."""
    widths = {}
    for name in ("main-own", "main-own-working", "main-desktop", "main-activity",
                 "main-attachments"):
        env = ui_probe.build_world(tmp_path / name, monkeypatch.setattr, formatted_view=False)
        frame = ui_probe.build_frame(env)
        frame.SetSize(size)
        try:
            ui_probe.SURFACES[name][1](frame, env)
            frame.Layout()
            widths[name] = frame.session_list.GetSize().width
        finally:
            ui_probe.close_frame(frame)
    assert len(set(widths.values())) == 1, widths


def test_the_turn_status_fits_its_line_and_the_status_bar_has_it_whole(app, tmp_path, monkeypatch):
    """Not the regression test for #179 (the split test above is): this
    checks the status's own line is wide enough to read at the usual size."""
    env = ui_probe.build_world(tmp_path, monkeypatch.setattr, formatted_view=False)
    frame = ui_probe.build_frame(env)
    frame.SetSize(ui_probe.DEFAULT_SIZE)
    try:
        ui_probe.s_main_own_working(frame, env)
        frame.Layout()
        label = frame.turn_status.GetLabel()
        assert label.startswith("1 message queued. Claude is working (")
        # On its own line, wide enough to show it all at the usual size.
        assert frame.turn_status.GetSize().width >= frame.turn_status.GetTextExtent(label).width
        # The status bar's session part says it whole too.
        assert label in frame.status_session.latest()
    finally:
        ui_probe.close_frame(frame)


def _inspect_dialog(name, tmp_path, monkeypatch, inspect):
    """Open the probe's dialog surface ``name`` hidden and give it to
    ``inspect`` while it's up."""
    _kind, function, _about = ui_probe.SURFACES[name]
    done = []

    def show(dialog):
        fake_env.pump(lambda: False, timeout=0.3)
        dialog.Layout()
        inspect(dialog)
        done.append(True)
        return wx.ID_CANCEL
    monkeypatch.setattr(wx.Dialog, "ShowModal", show)
    env = ui_probe.build_world(tmp_path, monkeypatch.setattr, formatted_view=False)
    frame = ui_probe.build_frame(env)
    frame.SetSize(ui_probe.DEFAULT_SIZE)
    try:
        function(frame, env)()
        assert fake_env.pump(lambda: done, timeout=10), f"{name} opened no dialog"
    finally:
        ui_probe.close_frame(frame)


def test_every_column_name_fits_its_list_in_session_list_columns(app, tmp_path, monkeypatch):
    """#289: a list box has no sideways scroll bar, and "Kind (desktop app,
    Cowork, terminal or The Chat Place)" ran past the Shown list's edge."""
    from thechatplace.sessions import FIELD_NAMES

    def inspect(dialog):
        scroll = wx.SystemSettings.GetMetric(wx.SYS_VSCROLL_X, dialog)
        for box in (dialog.available, dialog.shown):
            room = box.GetClientSize().width - scroll
            for name in FIELD_NAMES.values():
                assert box.GetTextExtent(name).width < room, (box.GetName(), name)
    _inspect_dialog("session-columns", tmp_path, monkeypatch, inspect)


@pytest.mark.parametrize("name", ["links", "lists-commands"])
def test_the_lists_pages_line_up_with_kinds_and_close(name, app, tmp_path, monkeypatch):
    """#288: each page's buttons ended 14 pixels left of Close, and its
    controls sat 8 pixels further in than Kinds."""
    def inspect(dialog):
        def edges(window):
            rect = window.GetScreenRect()
            return rect.x, rect.x + rect.width
        close = dialog.FindWindowById(wx.ID_CANCEL)
        page = dialog.current()
        kinds_left, kinds_right = edges(dialog.kinds)
        assert edges(page.copy_btn if page.markdown_btn is None else page.markdown_btn)[1] \
            == edges(close)[1]
        assert edges(page.filter) == (kinds_left, kinds_right)
        assert edges(page.list) == (kinds_left, kinds_right)
    _inspect_dialog(name, tmp_path, monkeypatch, inspect)


def test_an_interrupted_codex_chat_says_its_problem_with_one_full_stop(app, tmp_path, monkeypatch):
    """#291: the heading read "reply and reconnect.. Codex chat." because
    the problem ends in a full stop and the heading adds its own."""
    env = ui_probe.build_world(tmp_path, monkeypatch.setattr, formatted_view=False)
    frame = ui_probe.build_frame(env)
    frame.SetSize(ui_probe.DEFAULT_SIZE)
    try:
        ui_probe.s_main_codex_interrupted(frame, env)
        frame._update_heading()
        heading = frame.session_heading.GetLabel()
        assert "reply and reconnect. Codex chat" in heading
        for text in (heading, frame.messages_label.GetLabel(), frame.status_session.latest(),
                     frame.session_list.GetString(frame.session_list.GetSelection())):
            assert ".." not in text and "., " not in text and ".," not in text, text
    finally:
        ui_probe.close_frame(frame)
