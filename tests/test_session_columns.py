"""The session list's columns and its status views (#134), without wx."""
import json

import pytest

from thechatplace.sessions import (DEFAULT_FIELDS, DESKTOP, FIELD_IDS, FIELDS,
                                   NEEDS_YOU, OWN, VIEWS, WORKING, SessionInfo, clean_fields,
                                   field_short_name, in_view, view_spoken)
from thechatplace.speech import SpeechSettings

NOW = 1_800_000_000_000


def _info(key="x", **extra):
    values = dict(source=DESKTOP, key=key, title="Fix the build", cwd="C:\\G\\Repo",
                  cli_session_id=key, last_activity_ms=NOW - 5 * 60_000)
    values.update(extra)
    return SessionInfo(**values)


# -- the row text --------------------------------------------------------------------


def test_the_default_row_reads_as_it_always_has():
    info = _info(source=OWN, state=NEEDS_YOU, detail="Pick a name", unread=True, remote=True,
                 groups=("Work",))
    line = ("Fix the build, Repo, needs you: Pick a name, new reply, active 5 minutes ago, "
            "Chat Place session, Remote Control, group Work")
    assert info.list_line(NOW) == line
    assert info.list_line(NOW, DEFAULT_FIELDS) == line


def test_the_row_follows_the_chosen_order_and_leaves_out_the_rest():
    info = _info(state=WORKING, unread=True)
    assert info.list_line(NOW, ["status", "new_reply", "title"]) == \
        "working, new reply, Fix the build"
    assert info.list_line(NOW, ["activity", "folder"]) == "active 5 minutes ago, Repo"


def test_a_column_with_nothing_to_say_is_skipped():
    info = _info()  # not unread, not remote, no groups
    assert info.list_line(NOW, ["new_reply", "status", "remote", "groups"]) == "idle"


def test_a_row_with_nothing_to_say_reads_the_title_rather_than_nothing():
    assert _info().list_line(NOW, ["new_reply", "archived"]) == "Fix the build"
    assert _info(title="").list_line(NOW, ["hidden"]) == "Untitled session"


def test_cowork_and_chat_place_kind_is_one_column():
    assert _info(cowork=True, cowork_folder="Taxes").list_line(NOW, ["kind", "folder"]) == \
        "Cowork session, Taxes"


def test_every_column_has_a_name_and_a_short_name():
    # Every column is shown by default but Started (#209) and Last message (#146), added last.
    assert FIELD_IDS == DEFAULT_FIELDS + ["started", "last_message"]
    assert len(set(FIELD_IDS)) == len(FIELDS)
    assert field_short_name("last_message") == "Last message"
    assert field_short_name("status") == "Status"
    assert field_short_name("kind") == "Kind"
    assert field_short_name("new_reply") == "New reply"


# -- the saved order -----------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    (["status", "title"], ["status", "title"]),
    (["status", "from_a_newer_version", "title"], ["status", "title"]),  # unknown: dropped
    (["title", "title", "status"], ["title", "status"]),  # each once
    (["title", 3, None, {"a": 1}], ["title"]),
    ([], DEFAULT_FIELDS),  # nothing left: the default, never an empty row
    (["only_unknown"], DEFAULT_FIELDS),
    ("status,title", DEFAULT_FIELDS),
    (None, DEFAULT_FIELDS),
])
def test_clean_fields(raw, expected):
    assert clean_fields(raw) == expected


def test_the_order_is_saved_and_loaded(tmp_path):
    path = tmp_path / "settings.json"
    settings = SpeechSettings()
    assert settings.session_fields == DEFAULT_FIELDS
    settings.session_fields = ["status", "title", "folder"]
    settings.save(path)
    assert SpeechSettings.load(path).session_fields == ["status", "title", "folder"]


def test_a_missing_or_corrupt_settings_file_gives_the_default_order(tmp_path):
    assert SpeechSettings.load(tmp_path / "nothing.json").session_fields == DEFAULT_FIELDS
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    assert SpeechSettings.load(path).session_fields == DEFAULT_FIELDS
    path.write_text(json.dumps({"session_fields": "status"}), encoding="utf-8")
    assert SpeechSettings.load(path).session_fields == DEFAULT_FIELDS
    # An older settings file, from before columns, keeps everything else.
    path.write_text(json.dumps({"session_view": "needs"}), encoding="utf-8")
    loaded = SpeechSettings.load(path)
    assert loaded.session_fields == DEFAULT_FIELDS and loaded.session_view == "needs"


def test_a_newer_versions_columns_are_ignored_on_load(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"session_fields": ["model", "status", "title"]}),
                    encoding="utf-8")
    assert SpeechSettings.load(path).session_fields == ["status", "title"]


def test_default_settings_do_not_share_one_list():
    first, second = SpeechSettings(), SpeechSettings()
    first.session_fields.remove("title")
    assert second.session_fields == DEFAULT_FIELDS


# -- the status views ----------------------------------------------------------------


SESSIONS = [
    _info("needs", state=NEEDS_YOU), _info("working", state=WORKING), _info("idle"),
    _info("replied", source=OWN, unread=True),
    _info("replied-working", source=OWN, state=WORKING, unread=True),
    _info("old", archived=True, unread=True), _info("hid", hidden=True, state=WORKING),
]


@pytest.mark.parametrize("view,expected", [
    ("working", ["working", "replied-working"]),
    ("new_reply", ["replied", "replied-working"]),
    ("idle", ["idle", "replied"]),
    ("needs", ["needs"]),
])
def test_status_views(view, expected):
    assert [s.key for s in SESSIONS if in_view(s, view)] == expected


def test_status_views_are_in_the_menu_and_remembered(tmp_path):
    values = [v for v, _label in VIEWS]
    assert {"working", "new_reply", "idle"} <= set(values)
    assert view_spoken("working") == "working sessions"
    assert view_spoken("new_reply") == "sessions with a new reply"
    assert view_spoken("idle") == "idle sessions"
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"session_view": "new_reply"}), encoding="utf-8")
    assert SpeechSettings.load(path).session_view == "new_reply"


def test_show_sessions_access_keys_are_all_different():
    keys = [label[label.index("&") + 1].lower() for _v, label in VIEWS]
    assert len(keys) == len(set(keys))
