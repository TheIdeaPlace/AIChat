"""Hidden sessions (Session, Hide Session): the store."""
import pytest

from thechatplace.hidden import HiddenStore


def test_hide_show_rename_and_kept_across_loads(tmp_path):
    path = tmp_path / "hidden.json"
    store = HiddenStore(path)
    store.hide("own:a")
    store.hide("local_b")
    store.rename_key("own:a", "own:c")
    assert HiddenStore(path).keys() == ["local_b", "own:c"]
    store.show("local_b")
    assert "local_b" not in HiddenStore(path)
    assert not list(tmp_path.glob("hidden-*.tmp"))


def test_a_bad_file_is_reported_and_never_saved_over(tmp_path):
    path = tmp_path / "hidden.json"
    path.write_text("[not ours", encoding="utf-8")
    store = HiddenStore(path)
    assert store.load_error and store.keys() == []
    with pytest.raises(OSError):
        store.hide("own:a")
    assert path.read_text(encoding="utf-8") == "[not ours"


def test_tool_activity_store_is_its_own_file(tmp_path):
    """#162: the sessions Show Tool Activity is on for, kept apart from hidden ones."""
    from thechatplace.hidden import ToolActivityStore
    path = tmp_path / "tool_activity.json"
    store = ToolActivityStore(path)
    store.add("own:a")
    store.rename_key("own:a", "own:b")
    assert ToolActivityStore(path).keys() == ["own:b"]
    assert '"show_activity"' in path.read_text(encoding="utf-8")
    store.discard("own:b")
    assert "own:b" not in ToolActivityStore(path)



def test_an_unreadable_tool_activity_file_is_reported_and_never_saved_over(tmp_path):
    from thechatplace.hidden import ToolActivityStore
    path = tmp_path / "tool_activity.json"
    path.write_text('{"version": 1, "show_activity": "own:a"}', encoding="utf-8")
    store = ToolActivityStore(path)
    assert store.load_error.startswith("Your Show Tool Activity file (")
    store.discard("own:a")  # off is already what's kept: nothing to save, no error
    with pytest.raises(OSError):
        store.add("own:a")
    assert "show_activity" in path.read_text(encoding="utf-8")


def test_the_hidden_file_messages_are_unchanged(tmp_path):
    path = tmp_path / "hidden.json"
    path.write_text("[]", encoding="utf-8")
    assert HiddenStore(path).load_error.startswith("Your hidden sessions file (")
    path.write_text("{", encoding="utf-8")
    assert HiddenStore(path).load_error.startswith("Couldn't read your hidden sessions (")
