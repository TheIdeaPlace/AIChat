"""Read-only boxes of text (#763): one kind, a plain multi-line box, whose
name ends with the caret's line while JAWS runs, since JAWS 2027 reads no
text of a multi-line box as it gets focus."""
import re
from pathlib import Path

import pytest

from markers import msaa

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "thechatplace" / "ui"


def test_no_read_only_box_is_a_rich_one():
    """NVDA didn't call a rich one read only, and JAWS named it from the
    label before it, so the line for JAWS never reached it (#763). The rich
    style is for boxes you type in."""
    rich_read_only = re.compile(r"TE_READONLY[^)\n]*TE_RICH|TE_RICH[^)\n]*TE_READONLY")
    found = [f"{path.name}:{n}" for path in UI.glob("*.py")
             for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
             if rich_read_only.search(line)]
    assert found == []


def test_no_one_line_read_only_box_holds_text_to_read():
    """wx leaves a one-line read-only box out of the Tab order: Tab and
    Shift+Tab went past the Address box under JAWS and NVDA (#763). The only
    ones left are one value each, Chat Permissions' Permission mode (Alt+M)
    and What AI Knows About You's Location (Alt+N), reached by their keys."""
    one_line = re.compile(r"wx\.TextCtrl\([^)]*style=wx\.TE_READONLY\)")
    found = sorted(f"{path.name}:{n}" for path in UI.glob("*.py")
                   for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                   if one_line.search(line))
    assert [f.split(":")[0] for f in found] == ["dialogs.py", "dialogs.py"]


@pytest.fixture
def box(app):
    wx = pytest.importorskip("wx")
    from thechatplace.ui.dialogs import READ_ONLY_BOX_STYLE, _read_only_text
    frame = wx.Frame(None)
    text = _read_only_text(frame, "First line here.\nSecond line, more.\n\nLast.", "Details")
    assert text.GetWindowStyleFlag() & READ_ONLY_BOX_STYLE == READ_ONLY_BOX_STYLE
    yield text
    frame.Destroy()


def test_a_read_only_box_is_plain_multi_line_and_in_the_tab_order(box):
    wx = pytest.importorskip("wx")
    assert box.IsMultiLine() and not box.IsEditable()
    assert not box.GetWindowStyleFlag() & (wx.TE_RICH | wx.TE_RICH2)
    assert box.AcceptsFocusFromKeyboard()


def test_the_current_line_is_the_carets(box):
    from thechatplace.ui.a11y import current_line
    box.SetInsertionPoint(0)
    assert current_line(box) == "First line here."
    box.SetInsertionPoint(box.XYToPosition(0, 1))
    assert current_line(box) == "Second line, more."
    box.SetInsertionPoint(box.XYToPosition(0, 2))
    assert current_line(box) == ""


@msaa
def test_jaws_hears_the_line_in_the_name_and_nvda_doesnt(box, monkeypatch):
    from thechatplace import platform_paths
    accessible = box._hub_accessible
    box.SetInsertionPoint(0)
    monkeypatch.setattr(platform_paths, "jaws_running", lambda: False)
    assert accessible.GetName(0)[1] == "Details"
    monkeypatch.setattr(platform_paths, "jaws_running", lambda: True)
    assert accessible.GetName(0)[1] == "Details, First line here."
    box.SetInsertionPoint(box.XYToPosition(0, 1))
    assert accessible.GetName(0)[1] == "Details, Second line, more."
    box.SetInsertionPoint(box.XYToPosition(0, 2))
    assert accessible.GetName(0)[1] == "Details"  # a blank line adds nothing
    # A child (there are none) is left to wx.
    import wx
    assert accessible.GetName(1)[0] == wx.ACC_NOT_IMPLEMENTED
    # A long line is cut at a word.
    box.ChangeValue("word " * 200)
    box.SetInsertionPoint(0)
    name = accessible.GetName(0)[1]
    assert name.startswith("Details, word word") and name.endswith("word…")
    assert len(name) <= len("Details, ") + accessible.LINE_MOST + 1
    # The label before the box gets the label alone, not the line (#763's review).
    from thechatplace.ui.a11y import _msaa_name
    assert _msaa_name(box) == "Details"


def test_jaws_running_is_false_off_windows(monkeypatch):
    from thechatplace import platform_paths
    real = platform_paths.jaws_running.__wrapped__  # conftest's _no_jaws keeps it
    monkeypatch.setattr(platform_paths.sys, "platform", "linux")
    assert real() is False


def test_no_test_hears_whether_jaws_runs_here():
    from thechatplace import platform_paths
    assert platform_paths.jaws_running() is False


def test_the_line_in_the_name_is_cut_at_a_word_when_long():
    from thechatplace.ui.a11y import _ReadOnlyBoxAccessible
    assert _ReadOnlyBoxAccessible.LINE_MOST <= 300


@pytest.mark.parametrize("text, position, last, index", [
    ("one\ntwo", 5, 8, 4),     # a plain Windows box: the break counts two
    ("one\ntwo", 4, 7, 4),     # a box that counts it one
    ("one", 2, 3, 2),
    ("a\nb\nc", 6, 7, 4),      # the start of "c"
    ("", 0, 0, 0)])
def test_a_box_position_is_found_in_its_text(text, position, last, index):
    from thechatplace.ui.a11y import _text_index
    assert _text_index(text, position, last) == index
