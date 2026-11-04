"""The Claude desktop app's own session groups (#51)."""
import json
import os

from thechatplace.desktop_groups import CONFIG_NAME, load_desktop_groups


def _config(tmp_path, scopes):
    sessions = tmp_path / "claude-code-sessions"
    for scope in scopes:
        (sessions / scope).mkdir(parents=True)  # the account/org folders
    prefs = {"preferences": {"epitaxyPrefs": {"dframe-group-scopes": scopes}}}
    (tmp_path / CONFIG_NAME).write_text(json.dumps(prefs), encoding="utf-8")
    return sessions


def test_groups_and_their_sessions_in_the_apps_order(tmp_path):
    folder = _config(tmp_path, {"acct/org": {
        "groups": [{"id": "cg-1", "name": "IDT"}, {"id": "cg-2", "name": "Random"},
                   {"id": "cg-3", "name": "  "}],
        "assignments": {"code:local_a": "cg-1", "code:local_b": "cg-2",
                        "code:local_c": "cg-gone", "chat:x": "cg-1"},
        "order": {}}})
    groups = load_desktop_groups([folder])
    assert groups.names == ["IDT", "Random"]
    assert groups.by_session == {"local_a": "IDT", "local_b": "Random"}


def test_anything_unexpected_means_no_groups(tmp_path):
    assert load_desktop_groups([tmp_path / "missing" / "claude-code-sessions"]).names == []
    folder = tmp_path / "claude-code-sessions"
    folder.mkdir()
    for raw in ("not json", json.dumps({"preferences": {}}),
                json.dumps({"preferences": {"epitaxyPrefs": {"dframe-group-scopes": []}}}),
                json.dumps({"preferences": {"epitaxyPrefs": {"dframe-group-scopes": {
                    "s": {"groups": "nope", "assignments": []}}}}})):
        (tmp_path / CONFIG_NAME).write_text(raw, encoding="utf-8")
        groups = load_desktop_groups([folder])
        assert (groups.names, groups.by_session) == ([], {})


def test_a_bad_read_is_flagged_and_other_accounts_groups_are_left_out(tmp_path):
    folder = _config(tmp_path, {
        "acct/org": {"groups": [{"id": "cg-1", "name": "IDT"}],
                     "assignments": {"code:local_a": "cg-1"}},
        "old/gone": {"groups": [{"id": "cg-2", "name": "Stale"}],
                     "assignments": {"code:local_z": "cg-2"}}})
    (folder / "old" / "gone").rmdir()  # an old sign-in's: no sessions here now
    groups = load_desktop_groups([folder])
    assert groups.names == ["IDT"] and groups.read_ok
    (tmp_path / CONFIG_NAME).write_text('{"preferences": {"epit', encoding="utf-8")
    assert not load_desktop_groups([folder]).read_ok  # caught mid-rewrite


def test_names_are_tidied_and_case_only_twins_kept_once(tmp_path):
    folder = _config(tmp_path, {"acct/org": {
        "groups": [{"id": "cg-1", "name": "  Quill   app "}, {"id": "cg-2", "name": "QUILL APP"}],
        "assignments": {"code:local_a": "cg-1", "code:local_b": "cg-2"}}})
    groups = load_desktop_groups([folder])
    assert groups.names == ["Quill app"]
    assert groups.by_session == {"local_a": "Quill app", "local_b": "QUILL APP"}


def _copy_of_settings(root, written, name):
    sessions = root / "claude-code-sessions"
    (sessions / "acct/org").mkdir(parents=True)
    prefs = {"preferences": {"epitaxyPrefs": {"dframe-group-scopes": {"acct/org": {
        "groups": [{"id": "cg-1", "name": name}],
        "assignments": {"code:local_a": "cg-1"}}}}}}
    config = root / CONFIG_NAME
    config.write_text(json.dumps(prefs), encoding="utf-8")
    os.utime(config, (written, written))
    return sessions


def test_the_newest_copy_of_the_settings_wins_in_either_order(tmp_path):
    # #542: a stale copy must not bring back a deleted group.
    old = _copy_of_settings(tmp_path / "old", 1000, "Deleted group")
    new = _copy_of_settings(tmp_path / "new", 2000, "Current group")
    for folders in ([old, new], [new, old]):
        groups = load_desktop_groups(folders)
        assert groups.names == ["Current group"]
        assert groups.by_session == {"local_a": "Current group"}
