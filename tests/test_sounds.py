"""Sounds (#250): the event table, what's kept, what the window plays for each thing that
happens, and the dialog. Nothing here makes a sound: fake_env puts a recorder in."""
import json
import os
import struct
import wave
from pathlib import Path

import pytest
import wx

import fake_env
from thechatplace import platform_paths, sound_events as se
from thechatplace.claude_cli import PermissionRequest, TurnEvent
from thechatplace.own_store import OwnSession, OwnSessionStore
from thechatplace.providers.codex_runtime import CodexChatState
from thechatplace.speech import SpeechSettings
from theideaplace_appkit.sounds import AMBIENT, NORMAL, URGENT, SoundProblem, read_wav


def make_wav(path, seconds=0.05, width=2):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(width)
        w.setframerate(44100)
        w.writeframes(bytes(int(44100 * seconds) * width))
    return str(path)


# ---------------------------------------------------------------- without a window

def test_every_event_has_a_sound_in_every_style():
    assert se.missing_built_ins() == []
    for event in se.EVENTS:
        assert event.sound in se.BUILT_IN
    assert len({e.id for e in se.EVENTS}) == len(se.EVENTS)


@pytest.mark.parametrize("style", list(se.STYLES))
@pytest.mark.parametrize("name", list(se.BUILT_IN))
def test_built_in_files_are_playable_and_short(style, name):
    params, _frames = read_wav(str(se.built_in_path(name, style)))
    assert params.nframes / params.framerate < 1.0


def test_what_must_be_heard_is_urgent_and_what_says_something_is_going_on_is_ambient():
    assert se.BY_ID[se.NEEDS_YOU].priority == URGENT and se.BY_ID[se.FAILED].priority == URGENT
    assert {e.id for e in se.EVENTS if e.priority == AMBIENT} == {se.TOOL, se.FILE_CHANGED, se.PULSE}


def test_a_sound_means_one_thing_by_default():
    sounds = [e.sound for e in se.EVENTS]
    assert len(sounds) == len(set(sounds))


def test_a_new_install_has_sounds_off_at_80_percent(tmp_path):
    settings = SpeechSettings.load(tmp_path / "none.json")
    assert (settings.sounds_on, settings.sounds_muted, settings.sound_volume,
            settings.sound_style, settings.sound_tools, settings.sound_others) == (
        False, False, 80, "glass", "loaded", "all")


def test_settings_round_trip(tmp_path):
    path = tmp_path / "s.json"
    settings = SpeechSettings()
    settings.sounds_on, settings.sounds_muted = True, True
    settings.sound_volume, settings.sound_style = 50, "wood"
    settings.sound_tools, settings.sound_others = "all", "own"
    settings.sound_choices = {se.TOOL: se.NONE_REF, se.FINISHED: se.custom_ref("mine.wav")}
    settings.save(path)
    loaded = SpeechSettings.load(path)
    assert (loaded.sounds_on, loaded.sounds_muted, loaded.sound_volume, loaded.sound_style,
            loaded.sound_tools, loaded.sound_others, loaded.sound_choices) == (
        True, True, 50, "wood", "all", "own", settings.sound_choices)


@pytest.mark.parametrize("raw", [
    {"sounds_on": "yes", "sound_volume": 55},
    {"sound_volume": True},
    {"sound_volume": 0},
    {"sound_volume": "80"},
    {"sound_style": "../../evil"},
    {"sound_style": ["glass"]},
    {"sound_tools": {}},
    {"sound_others": 3},
    {"sound_choices": "tap"},
    {"sound_choices": {se.TOOL: "custom:../../x.wav"}},
    {"sound_choices": {se.TOOL: "custom:C:\\x.wav"}},
    {"sound_choices": {se.TOOL: "custom:COM1.wav"}},
    {"sound_choices": {se.TOOL: "builtin:nothing"}},
    {"sound_choices": {se.TOOL: 5}},
    {"sound_choices": {"made-up": "none"}},
])
def test_odd_settings_fall_back_on_defaults(tmp_path, raw):
    path = tmp_path / "s.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = SpeechSettings.load(path)
    assert loaded.sounds_on is False and loaded.sound_volume == 80
    assert loaded.sound_style == "glass" and loaded.sound_tools == "loaded"
    assert loaded.sound_others == "all" and loaded.sound_choices == {}


def test_resolve(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path)
    path, fallback = se.resolve(se.TOOL, {}, "wood")
    assert Path(path).parts[-2:] == ("wood", "tap.wav") and fallback is None
    assert se.resolve(se.TOOL, {se.TOOL: "none"}, "wood") is None
    path, fallback = se.resolve(se.TOOL, {se.TOOL: se.built_in_ref("sent")}, "glass")
    assert Path(path).parts[-2:] == ("glass", "sent.wav")
    path, fallback = se.resolve(se.TOOL, {se.TOOL: se.custom_ref("m.wav")}, "glass")
    assert Path(path) == tmp_path / "sounds" / "m.wav"
    assert Path(fallback).parts[-2:] == ("glass", "tap.wav")


def test_a_bad_choice_or_style_never_leaves_the_sounds_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path)
    path, _ = se.resolve(se.TOOL, {se.TOOL: "custom:../../secret.wav"}, "../..")
    assert Path(path).parts[-2:] == ("glass", "tap.wav")


def test_row_labels():
    assert se.row_label(se.TOOL, {}) == "Tool ran: Tap"
    assert se.row_label(se.TOOL, {se.TOOL: "none"}) == "Tool ran: None"
    assert se.row_label(se.TOOL, {se.TOOL: se.custom_ref("m.wav")}) == "Tool ran: Your file, m.wav"


def test_custom_files_lists_only_usable_names(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path)
    assert se.custom_files() == []
    folder = tmp_path / "sounds"
    folder.mkdir()
    (folder / "b.wav").write_bytes(b"x")
    (folder / "A.WAV").write_bytes(b"x")
    (folder / "note.txt").write_bytes(b"x")
    (folder / "sub.wav").mkdir()
    assert se.custom_files() == ["A.WAV", "b.wav"]
