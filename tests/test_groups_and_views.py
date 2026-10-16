"""Groups (#31) and which sessions the list shows (#32)."""
import json

import pytest

from thechatplace.groups import GroupStore, clean_name
from thechatplace.sessions import (DESKTOP, IDLE, NEEDS_YOU, OWN, WORKING, SessionInfo,
                                   group_view, in_view, load_desktop_sessions, view_spoken)


def test_groups_round_trip_and_keep_their_order(tmp_path):
    path = tmp_path / "groups.json"
    groups = GroupStore(path)
    groups.create("  Work   stuff ")
    groups.create("Home")
    assert groups.add("Work stuff", "local_a") and groups.add("Home", "local_a")
    assert not groups.add("Home", "local_a")  # already there
    groups.add("Home", "own:x")
    again = GroupStore(path)
    assert again.names() == ["Work stuff", "Home"]
    assert again.groups_of("local_a") == ["Work stuff", "Home"]
    assert again.members("Home") == ["local_a", "own:x"]


def test_group_names_are_unique_ignoring_case_and_never_empty(tmp_path):
    groups = GroupStore(tmp_path / "g.json")
    groups.create("Work")
    with pytest.raises(ValueError):
        groups.create("work")
    with pytest.raises(ValueError):
        groups.create("   ")
    groups.create("Home")
    with pytest.raises(ValueError):
        groups.rename("Home", "WORK")
    assert groups.rename("Home", "home") == "home"  # its own name, new case
    assert clean_name("a\nb" + "x" * 100) == ("a b" + "x" * 100)[:60]


def test_rename_keeps_place_and_members_delete_keeps_sessions(tmp_path):
    groups = GroupStore(tmp_path / "g.json")
    for name in ("A", "B", "C"):
        groups.create(name)
    groups.add("B", "local_1")
    groups.rename("B", "Bee")
    assert groups.names() == ["A", "Bee", "C"] and groups.members("Bee") == ["local_1"]
    groups.delete("Bee")
    assert groups.names() == ["A", "C"] and groups.groups_of("local_1") == []
    assert groups.remove("A", "nobody") is False


def test_rename_key_follows_a_session_given_a_new_id(tmp_path):
    groups = GroupStore(tmp_path / "g.json")
    groups.create("Work")
    groups.add("Work", "own:old")
    groups.rename_key("own:old", "own:new")
    assert GroupStore(tmp_path / "g.json").members("Work") == ["own:new"]


def test_unreadable_groups_file_is_never_overwritten(tmp_path):
    path = tmp_path / "groups.json"
    path.write_text("{not json", encoding="utf-8")
    groups = GroupStore(path)
    assert groups.load_error and groups.names() == []
    with pytest.raises(OSError):
        groups.create("Work")
    assert path.read_text(encoding="utf-8") == "{not json"


def test_bad_entries_in_the_file_are_skipped(tmp_path):
    path = tmp_path / "groups.json"
    path.write_text(json.dumps({"groups": [{"name": "Ok", "sessions": ["a", 3, ""]},
                                           {"name": ""}, "junk", {"name": "Ok"}]}),
                    encoding="utf-8")
    groups = GroupStore(path)
    assert groups.names() == ["Ok"] and groups.members("Ok") == ["a"]


def _info(key, state=IDLE, source=DESKTOP, archived=False, remote=False, groups=(),
          hidden=False):
    return SessionInfo(source=source, key=key, title=key, cwd="C:\\r", cli_session_id=key,
                       state=state, archived=archived, remote=remote, groups=groups,
                       hidden=hidden)


SESSIONS = [
    _info("needs", NEEDS_YOU), _info("working", WORKING), _info("idle"),
    _info("own", source=OWN), _info("remote", remote=True),
    _info("old", archived=True), _info("old-grouped", archived=True, groups=("Work",)),
    _info("grouped", groups=("Work", "Home")),
    _info("hid", hidden=True),
]


@pytest.mark.parametrize("view,expected", [
    ("all", ["needs", "working", "idle", "own", "remote", "grouped"]),
    ("active", ["needs", "working"]),
    ("needs", ["needs"]),
    ("desktop", ["needs", "working", "idle", "remote", "grouped"]),
    ("own", ["own"]),
    ("remote", ["remote"]),
    # In no group; archived ones stay in Archived, as everywhere but a group.
    ("ungrouped", ["needs", "working", "idle", "own", "remote"]),
    ("hidden", ["hid"]),  # a hidden session, ungrouped or not, is only in Hidden
    ("archived", ["old", "old-grouped"]),
    ("group:Work", ["old-grouped", "grouped"]),  # a group shows what you put in it
    ("group:Nobody", []),
])
def test_views(view, expected):
    assert [s.key for s in SESSIONS if in_view(s, view)] == expected


def test_view_names_and_row_text():
    assert view_spoken("active") == "needs you or working"
    assert view_spoken("ungrouped") == "ungrouped sessions"
    assert view_spoken(group_view("Work")) == "group Work"
    assert view_spoken("bogus") == "all sessions"
    line = _info("x", archived=True, groups=("Work", "Home")).list_line(0)
    assert line.endswith(", archived, groups Work, Home")
    assert _info("y", groups=("Work",)).list_line(0).endswith(", group Work")


def _write(folder, local, **extra):
    path = folder / local / "org" / f"{local}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"sessionId": local, "cliSessionId": "cli-" + local, "cwd": "C:\\G\\Repo",
            "title": local, "lastActivityAt": 1}
    data.update(extra)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_archived_and_remote_come_from_the_desktop_files(tmp_path):
    _write(tmp_path, "local_a", isArchived=True)
    _write(tmp_path, "local_r", bridgeSessionIds=["session_1"])
    _write(tmp_path, "local_n", bridgeSessionIds=[])
    everything = {s.key: s for s in load_desktop_sessions(tmp_path, include_archived=True).sessions}
    assert everything["local_a"].archived and not everything["local_r"].archived
    assert everything["local_r"].remote and not everything["local_n"].remote
    assert "local_a" not in {s.key for s in load_desktop_sessions(tmp_path).sessions}


def test_a_change_that_cannot_be_saved_is_undone(tmp_path, monkeypatch):
    groups = GroupStore(tmp_path / "g.json")
    groups.create("Work")

    def fail():
        raise OSError("disk full")
    monkeypatch.setattr(groups, "save", fail)
    for change in (lambda: groups.create("Home"), lambda: groups.add("Work", "k"),
                   lambda: groups.rename("Work", "Jobs"), lambda: groups.delete("Work")):
        with pytest.raises(OSError):
            change()
        assert groups.names() == ["Work"] and groups.members("Work") == []


def test_a_wrong_shaped_file_is_reported_and_never_overwritten(tmp_path):
    path = tmp_path / "groups.json"
    for content in ('["a list"]', '{"groups": "nope"}'):
        path.write_text(content, encoding="utf-8")
        groups = GroupStore(path)
        assert "expected format" in groups.load_error
        with pytest.raises(OSError):
            groups.create("Work")
        assert path.read_text(encoding="utf-8") == content


def test_forget_removes_a_session_everywhere(tmp_path):
    groups = GroupStore(tmp_path / "g.json")
    groups.create("A")
    groups.create("B")
    groups.add("A", "k")
    groups.add("B", "k")
    groups.forget("k")
    assert GroupStore(tmp_path / "g.json").groups_of("k") == []


def test_archived_sessions_do_not_announce_turn_ends():
    from thechatplace.hub import finished_turns
    done = _info("done", archived=True)
    assert finished_turns({"done": "working"}, [done]) == []
    assert finished_turns({"done": "working"}, [_info("done")])[0].key == "done"
