import json
import os

import pytest

from thechatplace import desktop_groups, platform_paths
from thechatplace.own_store import OwnSession, OwnSessionStore
from thechatplace.sessions import NEEDS_YOU, load_desktop_sessions


@pytest.mark.parametrize("cwd,expected", [
    ("C:\\Users\\kelly\\GitHub\\QuickMail", "C--Users-kelly-GitHub-QuickMail"),
    ("C:\\Users\\kelly\\GitHub\\TheWorkBench\\.claude\\worktrees\\brave-cat-1",
     "C--Users-kelly-GitHub-TheWorkBench--claude-worktrees-brave-cat-1"),
    ("/Users/kelly/My Repo_2", "-Users-kelly-My-Repo-2"),
    ("C:\\Users\\kelly\\café", "C--Users-kelly-caf-"),
    ("", ""),
])
def test_encode_cwd(cwd, expected):
    assert platform_paths.encode_cwd(cwd) == expected


def test_transcript_path_direct_and_fallback(tmp_path):
    folder = tmp_path / "C--G-Repo"
    folder.mkdir()
    (folder / "abc-123.jsonl").write_text("")
    assert platform_paths.transcript_path("C:\\G\\Repo", "abc-123", tmp_path) == \
        folder / "abc-123.jsonl"
    # The cwd mapping misses (e.g. a shortened folder name): search by id.
    other = tmp_path / "shortened-name"
    other.mkdir()
    (other / "def-456.jsonl").write_text("")
    assert platform_paths.transcript_path("C:\\G\\Repo", "def-456", tmp_path) == \
        other / "def-456.jsonl"


def test_transcript_path_missing_and_unsafe(tmp_path):
    assert platform_paths.transcript_path("C:\\G", "nope", tmp_path) is None
    assert platform_paths.transcript_path("C:\\G", "", tmp_path) is None
    assert platform_paths.transcript_path("C:\\G", "../x", tmp_path) is None
    assert platform_paths.transcript_path("C:\\G", "*", tmp_path) is None


def test_claude_home_honours_config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert platform_paths.projects_dir() == tmp_path / "projects"
    assert platform_paths.live_sessions_dir() == tmp_path / "sessions"


def test_app_dirs_under_appdata(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_paths.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert platform_paths.app_data_dir() == tmp_path / "TheChatPlace"
    assert platform_paths.desktop_sessions_dir() == tmp_path / "Claude" / "claude-code-sessions"


def _store_layout(tmp_path, monkeypatch, classic=False):
    """A Microsoft Store install of the desktop app (#183), and optionally
    the classic folder too. Returns (classic dir, store dir)."""
    monkeypatch.setattr(platform_paths.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    store = (tmp_path / "Local" / "Packages" / "Claude_pzs8sxrjxfjjc" / "LocalCache"
             / "Roaming" / "Claude" / "claude-code-sessions")
    store.mkdir(parents=True)
    (tmp_path / "Local" / "Packages" / "OtherApp_123").mkdir()
    classic_dir = tmp_path / "Roaming" / "Claude" / "claude-code-sessions"
    if classic:
        classic_dir.mkdir(parents=True)
    return classic_dir, store


def _write_desktop(folder, local, title):
    path = folder / local / "org" / f"{local}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sessionId": local, "cliSessionId": "cli-" + local,
                                "cwd": "C:\\G\\Repo", "title": title,
                                "lastActivityAt": 1}), encoding="utf-8")
    return path


def test_store_app_sessions_found(monkeypatch, tmp_path):
    _classic, store = _store_layout(tmp_path, monkeypatch)
    assert platform_paths.desktop_sessions_dirs() == [store]
    _write_desktop(store, "local_s", "From the Store app")
    titles = [s.title for s in load_desktop_sessions().sessions]
    assert titles == ["From the Store app"]


def _moved_store_layout(tmp_path, monkeypatch, name="claude-code-sessions"):
    """The Store app after it moved its data to Claude-Data (#542): the
    package folder keeps only the marker file. Returns the new folder."""
    monkeypatch.setattr(platform_paths.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setattr(platform_paths, "long_path", lambda path: path)
    old = tmp_path / "Local" / "Packages" / "Claude_pzs8sxrjxfjjc" / "LocalCache" / "Roaming" / "Claude"
    old.mkdir(parents=True)
    (old / ".user-data-left.claude_pzs8sxrjxfjjc").write_text("", encoding="utf-8")
    data = tmp_path / "Local" / "Claude-Data"
    data.mkdir(parents=True)
    (data / "README.txt").write_text("Do not delete.", encoding="utf-8")
    moved = data / "claude_pzs8sxrjxfjjc" / name
    moved.mkdir(parents=True)
    return moved


def test_store_app_sessions_found_after_the_move_to_claude_data(monkeypatch, tmp_path):
    moved = _moved_store_layout(tmp_path, monkeypatch)
    assert platform_paths.desktop_sessions_dirs() == [moved]
    assert platform_paths.cowork_sessions_dirs() == []
    _write_desktop(moved, "local_m", "After the move")
    result = load_desktop_sessions()
    assert [s.title for s in result.sessions] == ["After the move"]
    # The guard against sending into a desktop app session needs these ids.
    assert result.desktop_cli_ids == {"cli-local_m"}


def test_cowork_found_after_the_move_to_claude_data(monkeypatch, tmp_path):
    moved = _moved_store_layout(tmp_path, monkeypatch, platform_paths.COWORK_DIR_NAME)
    assert platform_paths.cowork_sessions_dirs() == [moved]
    assert platform_paths.desktop_sessions_dirs() == []


def test_groups_found_after_the_move_to_claude_data(monkeypatch, tmp_path):
    moved = _moved_store_layout(tmp_path, monkeypatch)
    (moved / "account").mkdir()
    scope = {"groups": [{"id": "g1", "name": "Work"}], "assignments": {}}
    (moved.parent / desktop_groups.CONFIG_NAME).write_text(json.dumps(
        {"preferences": {"epitaxyPrefs": {"dframe-group-scopes": {"account": scope}}}}),
        encoding="utf-8")
    groups = desktop_groups.load_desktop_groups(platform_paths.desktop_sessions_dirs())
    assert groups.names == ["Work"]


def test_a_session_left_behind_by_the_move_is_read_once(monkeypatch, tmp_path):
    moved = _moved_store_layout(tmp_path, monkeypatch)
    old = (tmp_path / "Local" / "Packages" / "Claude_pzs8sxrjxfjjc" / "LocalCache"
           / "Roaming" / "Claude" / "claude-code-sessions")
    old.mkdir()
    stale = _write_desktop(old, "local_both", "Old copy")
    fresh = _write_desktop(moved, "local_both", "New copy")
    os.utime(stale, (1, 1))
    os.utime(fresh, (2, 2))
    assert platform_paths.desktop_sessions_dirs() == [moved, old]
    assert [s.title for s in load_desktop_sessions().sessions] == ["New copy"]


def test_a_claude_data_folder_of_another_shape_is_ignored(monkeypatch, tmp_path):
    moved = _moved_store_layout(tmp_path, monkeypatch)
    other = tmp_path / "Local" / "Claude-Data" / "something_else" / "claude-code-sessions"
    other.mkdir(parents=True)
    assert platform_paths.desktop_sessions_dirs() == [moved]


def test_no_claude_data_folder_is_fine(monkeypatch, tmp_path):
    _classic, store = _store_layout(tmp_path, monkeypatch)
    assert platform_paths.desktop_sessions_dirs() == [store]


def test_both_installs_read_once_each_newest_copy_wins(monkeypatch, tmp_path):
    import os
    classic, store = _store_layout(tmp_path, monkeypatch, classic=True)
    assert platform_paths.desktop_sessions_dirs() == [classic, store]
    old = _write_desktop(classic, "local_both", "Old title")
    _write_desktop(store, "local_both", "New title")
    os.utime(old, (1, 1))
    _write_desktop(classic, "local_only_classic", "Classic only")
    titles = sorted(s.title for s in load_desktop_sessions().sessions)
    assert titles == ["Classic only", "New title"]


def test_no_desktop_app_means_no_folders(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_paths.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert platform_paths.desktop_sessions_dirs() == []
    assert load_desktop_sessions().sessions == []


# -- own store ------------------------------------------------------------------


def test_store_round_trip(tmp_path):
    path = tmp_path / "hub" / "sessions.json"
    store = OwnSessionStore(path)
    store.add(OwnSession("id-1", "First", "C:\\a", permission_mode="plan"))
    store.update("id-1", state=NEEDS_YOU, detail="1 tool was refused", unread=True,
                 not_a_field="ignored")
    again = OwnSessionStore(path)
    session = again.get("id-1")
    assert (session.title, session.permission_mode, session.state, session.unread) == \
        ("First", "plan", NEEDS_YOU, True)
    assert session.created_ms > 0
    info = session.to_info()
    assert info.key == "own:id-1" and info.is_own and not info.can_open_in_claude


def test_store_rename_and_remove(tmp_path):
    store = OwnSessionStore(tmp_path / "s.json")
    store.add(OwnSession("old", "T", "C:\\a"))
    store.rename_id("old", "new")
    assert not store.contains("old") and store.contains("new")
    store.remove("new")
    assert OwnSessionStore(tmp_path / "s.json").all() == []


def test_store_rejects_unsafe_id(tmp_path):
    store = OwnSessionStore(tmp_path / "s.json")
    with pytest.raises(ValueError):
        store.add(OwnSession("../x", "T", "C:\\a"))


def test_store_survives_bad_files(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"sessions": [
        {"cli_session_id": "ok", "title": "T", "cwd": "C:\\", "future_field": 1},
        {"cli_session_id": "../bad", "title": "T", "cwd": "C:\\"},
        {"title": "no id"}, "junk",
        {"cli_session_id": "missing-title"},
        {"cli_session_id": "bad-title", "title": 5, "cwd": "C:\\"},
        {"cli_session_id": "odd-types", "title": "T", "cwd": "C:\\", "unread": "yes",
         "created_ms": "soon", "state": None, "started": 1, "last_activity_ms": True},
    ]}), encoding="utf-8")
    store = OwnSessionStore(path)
    assert sorted(s.cli_session_id for s in store.all()) == ["odd-types", "ok"]
    odd = store.get("odd-types")
    assert (odd.unread, odd.created_ms, odd.state, odd.started, odd.last_activity_ms) == \
        (False, 0, "idle", True, 0)
    assert store.load_error == ""


@pytest.mark.parametrize("content,why", [("{broken", "valid JSON"),
                                         (json.dumps({"sessions": "nope"}), "expected format")])
def test_corrupt_store_is_set_aside_not_overwritten(tmp_path, content, why):
    path = tmp_path / "sessions.json"
    path.write_text(content, encoding="utf-8")
    store = OwnSessionStore(path)
    assert store.all() == []
    assert why in store.load_error and "moved to sessions.json.bad-" in store.load_error
    backups = list(tmp_path.glob("sessions.json.bad-*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == content
    store.add(OwnSession("new", "T", "C:\\"))  # saving now can't destroy the old file
    assert backups[0].read_text(encoding="utf-8") == content


def test_store_that_cannot_be_set_aside_refuses_to_save(tmp_path, monkeypatch):
    import thechatplace.own_store as own_store
    path = tmp_path / "sessions.json"
    path.write_text("{broken", encoding="utf-8")

    def refuse(*a):
        raise OSError("locked")
    monkeypatch.setattr(own_store.os, "replace", refuse)
    store = OwnSessionStore(path)
    assert "couldn't be moved aside" in store.load_error
    with pytest.raises(OSError):
        store.add(OwnSession("new", "T", "C:\\"))
    assert path.read_text(encoding="utf-8") == "{broken"


def test_store_missing_file_is_empty_without_error(tmp_path):
    store = OwnSessionStore(tmp_path / "none.json")
    assert store.all() == [] and store.load_error == ""


def test_store_locked_at_startup_is_never_overwritten(tmp_path, monkeypatch):
    path = tmp_path / "sessions.json"
    good = json.dumps({"sessions": [{"cli_session_id": "keep-me", "title": "T", "cwd": "C:/"}]})
    path.write_text(good, encoding="utf-8")
    real_read = type(path).read_text

    def locked(self, *a, **k):
        if self == path:
            raise PermissionError("in use by another process")
        return real_read(self, *a, **k)
    monkeypatch.setattr(type(path), "read_text", locked)
    store = OwnSessionStore(path)
    assert store.all() == [] and "in use" in store.load_error
    with pytest.raises(OSError):
        store.add(OwnSession("new", "T", "C:/"))
    monkeypatch.undo()
    assert path.read_text(encoding="utf-8") == good


def test_store_keeps_the_model(tmp_path):
    path = tmp_path / "sessions.json"
    store = OwnSessionStore(path)
    store.add(OwnSession("id-1", "First", "C:\a", model="opus"))
    assert OwnSessionStore(path).get("id-1").model == "opus"


@pytest.mark.parametrize("stored", [None, 5, "fable", "sonnet[1m]", "--help", "made-up"])
def test_store_loads_an_unoffered_model_as_the_default(tmp_path, stored):
    # Older stores have no model; anything the picker doesn't offer (a hand
    # edit, or a model that bills usage credits) is never passed on.
    entry = {"cli_session_id": "id-1", "title": "Old", "cwd": "C:\a"}
    if stored is not None:
        entry["model"] = stored
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({"sessions": [entry]}), encoding="utf-8")
    assert OwnSessionStore(path).get("id-1").model == ""



def test_store_keeps_the_effort(tmp_path):
    path = tmp_path / "sessions.json"
    store = OwnSessionStore(path)
    store.add(OwnSession("id-1", "First", "C:\a", effort="high"))
    assert OwnSessionStore(path).get("id-1").effort == "high"


@pytest.mark.parametrize("stored", [None, 5, "huge", "--help", "HIGH"])
def test_store_loads_an_unknown_effort_as_the_default(tmp_path, stored):
    entry = {"cli_session_id": "id-1", "title": "Old", "cwd": "C:\a"}
    if stored is not None:
        entry["effort"] = stored
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({"sessions": [entry]}), encoding="utf-8")
    assert OwnSessionStore(path).get("id-1").effort == ""


def test_git_names_diff_program_reads_what_git_config_lists(monkeypatch, tmp_path):
    """#426: git diff is answered in Auto only when no git config names a
    diff program; Git for Windows' own astextplain setting doesn't count."""
    import subprocess
    from types import SimpleNamespace
    monkeypatch.setattr(platform_paths, "find_tool", lambda name: "git")
    answers = {}

    def run(argv, **kwargs):
        code, out = answers["now"]
        return SimpleNamespace(returncode=code, stdout=out)
    monkeypatch.setattr(subprocess, "run", run)
    cases = [((1, b""), False),
             ((0, b"diff.astextplain.textconv astextplain\n"), False),
             ((0, b"diff.astextplain.textconv astextplain\ndiff.x.command evil\n"), True),
             ((0, b"diff.external evil\n"), True),
             ((128, b""), None)]
    for index, (reply, expected) in enumerate(cases):
        answers["now"] = reply
        folder = str(tmp_path / str(index))  # a new folder each time: nothing cached
        assert platform_paths.git_names_diff_program(folder) is expected, reply
    monkeypatch.setattr(platform_paths, "find_tool", lambda name: None)
    assert platform_paths.git_names_diff_program(str(tmp_path / "none")) is None


def test_an_unreadable_claude_data_folder_doesnt_hide_the_package_folder(monkeypatch, tmp_path):
    _classic, store = _store_layout(tmp_path, monkeypatch)
    real_glob = platform_paths.Path.glob

    def glob(self, pattern, **kwargs):
        if self.name == "Claude-Data":
            raise OSError("no access")
        return real_glob(self, pattern, **kwargs)

    monkeypatch.setattr(platform_paths.Path, "glob", glob)
    assert platform_paths.desktop_sessions_dirs() == [store]


def test_claude_data_is_found_without_localappdata(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_paths.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(platform_paths.Path, "home", lambda: tmp_path)
    moved = (tmp_path / "AppData" / "Local" / "Claude-Data" / "claude_pkg"
             / "claude-code-sessions")
    moved.mkdir(parents=True)
    assert platform_paths.desktop_sessions_dirs() == [moved]


def test_plain_path_takes_off_only_the_long_form():
    plain = platform_paths.plain_path
    assert str(plain("\\\\?\\C:\\a")).endswith("a")
    assert "?" not in str(plain("\\\\?\\C:\\a"))
    assert "?" not in str(plain("\\\\?\\UNC\\server\\share"))
    ordinary = platform_paths.Path("ordinary")
    assert plain(ordinary) == ordinary
