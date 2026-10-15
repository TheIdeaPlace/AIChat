"""The desktop app's Cowork sessions (#91): listed read-only, beside its Code
sessions, with each one's transcript and live state read from its own Claude
Code home."""
import json

from thechatplace import hub, platform_paths
from thechatplace.sessions import (DESKTOP, IDLE, VIEW_ALL, VIEW_COWORK, VIEW_DESKTOP, WORKING,
                                   in_view, load_desktop_sessions)

from records import lines, user_text

NOW = 1_800_000_000_000


def write_cowork(root, local="local_cw1", cli="cli-cw1", title="Sort the receipts",
                 archived=False, account="acct", org="org"):
    """A Cowork session as the desktop app lays it out: the metadata file, and
    beside it a folder that is the session's own Claude Code home. Returns
    (metadata path, that home's .claude folder, the session's cwd)."""
    folder = root / account / org
    folder.mkdir(parents=True, exist_ok=True)
    cwd = str(folder / local / "outputs")
    data = {"sessionId": local, "cliSessionId": cli, "cwd": cwd, "title": title,
            "isArchived": archived, "lastActivityAt": NOW - 60_000,
            "processName": "happy-cat", "userSelectedFolders": []}
    path = folder / f"{local}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    home = folder / local / ".claude"
    (home / "projects").mkdir(parents=True)
    return path, home, cwd


def write_transcript(home, cwd, cli, *records):
    # Like the real ones, these paths pass 260 characters: written in the
    # long form, which the app has to read them in too.
    folder = platform_paths.long_path(home) / "projects" / platform_paths.encode_cwd(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{cli}.jsonl"
    path.write_text("\n".join(lines(*records)) + "\n", encoding="utf-8")
    return path


def load(tmp_path, **kwargs):
    code = tmp_path / "code"
    code.mkdir(exist_ok=True)
    return load_desktop_sessions(code, {}, cowork_directory=tmp_path / "cowork", **kwargs)


def test_cowork_sessions_are_read_only_desktop_sessions(tmp_path):
    write_cowork(tmp_path / "cowork")
    result = load(tmp_path)
    [info] = result.sessions
    assert (info.source, info.key, info.title, info.cli_session_id) == \
        (DESKTOP, "local_cw1", "Sort the receipts", "cli-cw1")
    assert info.cowork and not info.is_own
    assert info.desktop_session_id == "local_cw1" and info.can_open_in_claude
    assert info.list_line(NOW).endswith("Cowork session, Claude")
    # The --resume guard knows its id, as it knows every desktop app id.
    assert result.desktop_cli_ids == {"cli-cw1"}
    # And knows it's a Cowork one, which can't even be copied here.
    assert result.cowork_cli_ids == {"cli-cw1"}


def test_transcript_is_found_in_the_sessions_own_home(tmp_path):
    _path, home, cwd = write_cowork(tmp_path / "cowork")
    transcript = write_transcript(home, cwd, "cli-cw1", user_text("Total these"))
    [info] = load(tmp_path).sessions
    assert info.claude_home == platform_paths.long_path(home)
    assert info.transcript_path() == transcript


def test_transcript_past_windows_path_limit_is_read(tmp_path):
    """Real Cowork transcripts sit at around 480 characters; Windows opens
    them only in the long form, so make one that long and read it back."""
    from thechatplace.transcript import read_transcript
    _path, home, _cwd = write_cowork(tmp_path / "cowork")
    # Encoded, the cwd is one folder name, which can't pass 255 characters.
    cwd = "C:\\" + "\\".join(["a-rather-long-folder-name-for-a-cowork-task"] * 5)
    transcript = write_transcript(home, cwd, "cli-cw1", user_text("Total these"))
    assert len(str(transcript)) > 300
    [info] = load(tmp_path).sessions
    assert len(read_transcript(info.transcript_path()).messages) == 1


def test_transcript_found_when_cwd_does_not_match_its_folder(tmp_path):
    # The Store app's cwd says %APPDATA%, but the files are in its package
    # folder: the search by id still finds it.
    _path, home, _cwd = write_cowork(tmp_path / "cowork")
    transcript = write_transcript(home, "C:\\Elsewhere\\outputs", "cli-cw1", user_text("Hi"))
    [info] = load(tmp_path).sessions
    assert info.transcript_path() == transcript


def test_missing_transcript_is_none_not_a_crash(tmp_path):
    write_cowork(tmp_path / "cowork")
    [info] = load(tmp_path).sessions
    assert info.transcript_path() is None


def test_files_inside_a_sessions_home_are_not_sessions(tmp_path):
    """A Cowork session's folder holds a whole Claude Code home (and its
    outputs), so a local_*.json deeper down isn't a session."""
    _path, home, _cwd = write_cowork(tmp_path / "cowork")
    stray = home / "projects" / "x"
    stray.mkdir(parents=True)
    (stray / "local_stray.json").write_text(json.dumps(
        {"sessionId": "local_stray", "cliSessionId": "cli-stray", "title": "Not one"}))
    assert [s.key for s in load(tmp_path).sessions] == ["local_cw1"]


def test_archived_and_unreadable_cowork_files(tmp_path):
    root = tmp_path / "cowork"
    write_cowork(root, local="local_old", cli="cli-old", archived=True)
    (root / "acct" / "org" / "local_bad.json").write_text("{nope", encoding="utf-8")
    result = load(tmp_path)
    assert result.sessions == [] and result.unreadable_files == 1
    assert result.desktop_cli_ids == {"cli-old"}
    [archived] = load(tmp_path, include_archived=True).sessions
    assert archived.archived and archived.cowork


def test_live_state_comes_from_the_sessions_own_home(tmp_path):
    _path, home, _cwd = write_cowork(tmp_path / "cowork")
    (home / "sessions").mkdir()
    (home / "sessions" / "4242.json").write_text(json.dumps(
        {"pid": 4242, "sessionId": "cli-cw1", "status": "busy"}), encoding="utf-8")
    [busy] = load(tmp_path, alive=lambda pid: pid == 4242, started=lambda pid: None).sessions
    assert busy.state == WORKING
    [idle] = load(tmp_path, alive=lambda pid: False, started=lambda pid: None).sessions
    assert idle.state == IDLE


def test_code_and_cowork_sessions_side_by_side_and_views(tmp_path):
    code = tmp_path / "code"
    folder = code / "x" / "org"
    folder.mkdir(parents=True)
    (folder / "local_code.json").write_text(json.dumps(
        {"sessionId": "local_code", "cliSessionId": "cli-code", "cwd": "C:\\G\\Repo",
         "title": "Fix the build", "lastActivityAt": NOW}), encoding="utf-8")
    write_cowork(tmp_path / "cowork")
    snap = hub.collect([], set(), desktop_dir=code, live_dir=tmp_path / "live",
                       alive=lambda pid: False)
    # A test's desktop_dir alone reads no Cowork folder (none of the real ones).
    assert [s.key for s in snap.sessions] == ["local_code"]
    sessions = {s.key: s for s in load(tmp_path).sessions}
    assert set(sessions) == {"local_code", "local_cw1"}
    assert not sessions["local_code"].cowork
    assert in_view(sessions["local_cw1"], VIEW_COWORK)
    assert not in_view(sessions["local_code"], VIEW_COWORK)
    assert in_view(sessions["local_cw1"], VIEW_DESKTOP)
    assert in_view(sessions["local_cw1"], VIEW_ALL)


def test_cowork_folders_both_installs(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_paths.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    # The long form is tested on its own; with sys.platform faked on a Mac
    # it would make a nonsense path of the tmp folder.
    monkeypatch.setattr(platform_paths, "long_path", lambda path: path)
    assert platform_paths.cowork_sessions_dirs() == []
    classic = tmp_path / "Roaming" / "Claude" / "local-agent-mode-sessions"
    store = (tmp_path / "Local" / "Packages" / "Claude_pzs8sxrjxfjjc" / "LocalCache"
             / "Roaming" / "Claude" / "local-agent-mode-sessions")
    classic.mkdir(parents=True)
    store.mkdir(parents=True)
    assert platform_paths.cowork_sessions_dirs() == [classic, store]
    write_cowork(store, title="From the Store app")
    titles = [s.title for s in load_desktop_sessions().sessions]
    assert titles == ["From the Store app"]


def test_folder_is_the_one_cowork_was_given_not_outputs(tmp_path):
    path, _home, _cwd = write_cowork(tmp_path / "cowork")
    [info] = load(tmp_path).sessions
    # With no folder it says so: the row ends "Cowork session" already.
    assert info.repo == "no folder"
    assert info.list_line(NOW).startswith("Sort the receipts, no folder, idle")
    assert info.list_line(NOW).count("Cowork") == 1
    data = json.loads(path.read_text(encoding="utf-8"))
    data["userSelectedFolders"] = ["C:\\Users\\k\\OneDrive\\Finance\\"]
    path.write_text(json.dumps(data), encoding="utf-8")
    [info] = load(tmp_path).sessions
    assert info.repo == "Finance"
    data["userSelectedFolders"] = [5, "C:\\x"]  # not what's expected: ignored
    path.write_text(json.dumps(data), encoding="utf-8")
    assert load(tmp_path).sessions[0].repo == "no folder"


def test_archived_cowork_live_state_is_not_read(tmp_path):
    _path, home, _cwd = write_cowork(tmp_path / "cowork", archived=True)
    (home / "sessions").mkdir()
    (home / "sessions" / "7.json").write_text(json.dumps(
        {"pid": 7, "sessionId": "cli-cw1", "status": "busy"}), encoding="utf-8")
    seen = []
    load(tmp_path, include_archived=True, alive=lambda pid: seen.append(pid) or True)
    assert seen == []


def test_cowork_claude_home_is_beside_the_metadata(tmp_path):
    meta = tmp_path / "a" / "o" / "local_x.json"
    assert platform_paths.cowork_claude_home(meta) == \
        platform_paths.long_path(tmp_path / "a" / "o" / "local_x" / ".claude")


def test_long_path_form_on_windows_only(monkeypatch):
    from pathlib import PureWindowsPath as P
    monkeypatch.setattr(platform_paths, "Path", P)
    monkeypatch.setattr(platform_paths.sys, "platform", "win32")
    long = platform_paths.long_path
    assert str(long(P(r"C:\A\b.jsonl"))) == r"\\?\C:\A\b.jsonl"
    assert str(long(P(r"\\srv\share\x"))) == r"\\?\UNC\srv\share\x"
    assert long(P(r"\\?\C:\A")) == P(r"\\?\C:\A")
    assert long(P("relative")) == P("relative")
    # A device path stays one, and ".." is resolved (the long form takes it literally).
    assert long(P(r"\\.\pipe\x")) == P(r"\\.\pipe\x")
    assert str(long(P(r"C:\a\..\b"))) == r"\\?\C:\b"
    monkeypatch.setattr(platform_paths.sys, "platform", "darwin")
    assert long(P(r"C:\A")) == P(r"C:\A")
