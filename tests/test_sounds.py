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


@pytest.mark.parametrize("pack", list(se.PACKS))
@pytest.mark.parametrize("name", list(se.BUILT_IN))
def test_built_in_files_are_playable_and_short(pack, name):
    params, _frames = read_wav(str(se.built_in_path(name, pack)))
    assert params.nframes / params.framerate < 1.0


def test_what_must_be_heard_is_urgent_and_what_says_something_is_going_on_is_ambient():
    assert se.BY_ID[se.NEEDS_YOU].priority == URGENT and se.BY_ID[se.FAILED].priority == URGENT
    assert {e.id for e in se.EVENTS if e.priority == AMBIENT} == {se.TOOL, se.FILE_CHANGED, se.PULSE}


def test_a_sound_means_one_thing_by_default():
    sounds = [e.sound for e in se.EVENTS]
    assert len(sounds) == len(set(sounds))


def test_a_new_install_has_sounds_off_at_80_percent(tmp_path):
    settings = SpeechSettings.load(tmp_path / "none.json")
    assert settings.sound_silent_notifications is False
    assert (settings.sounds_on, settings.sounds_muted, settings.sound_volume,
            settings.sound_scheme, settings.sound_tools, settings.sound_others,
            settings.sound_schemes) == (False, False, 80, "glass", "loaded", "all", {})


@pytest.mark.parametrize("raw", [
    {"sounds_on": "yes", "sound_volume": 55},
    {"sound_volume": True},
    {"sound_volume": 0},
    {"sound_volume": "80"},
    {"sound_tools": {}},
    {"sound_others": 3},
])
def test_odd_settings_fall_back_on_defaults(tmp_path, raw):
    path = tmp_path / "s.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = SpeechSettings.load(path)
    assert loaded.sounds_on is False and loaded.sound_volume == 80
    assert loaded.sound_scheme == "glass" and loaded.sound_tools == "loaded"
    assert loaded.sound_others == "all" and loaded.sound_schemes == {}


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
