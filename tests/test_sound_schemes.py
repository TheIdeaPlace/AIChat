"""Sound schemes (#250, app-kit's sounds/SCHEMES.md): what's kept, what an event plays in each
scheme, moving a 0.5 preview's style over, export and import, and the gallery's view of what you
have. No windows."""
import io
import json
import wave
import zipfile
from pathlib import Path

import pytest

from thechatplace import platform_paths, sound_events as se, sound_gallery
from thechatplace.speech import SpeechSettings
from theideaplace_appkit import soundgallery as shared_gallery
from theideaplace_appkit import soundschemes as shared

USER = "user-" + "a" * 32
OTHER = "user-" + "b" * 32


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path)
    return tmp_path


def wav_bytes(seconds=0.05):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\x01\x00" * int(22050 * seconds))
    return buffer.getvalue()


def test_the_events_are_app_kits_for_this_app():
    assert [(e.key, e.label, e.sound) for e in se.EVENTS] == [
        (e.key, e.label, e.sound) for e in se.APP.events]
    assert list(se.BUILT_IN) == list(se.APP.built_in_sounds)
    assert list(se.PACKS) == ["glass", "softbell", "wood"]


def test_one_sound_one_meaning():
    """dev-notes/sound-design.md: every event has its own sound, its own file, and a name of its
    own in the Sound choice; none is QuickMail's."""
    from theideaplace_appkit import soundschemes
    sounds = [e.sound for e in se.EVENTS]
    assert len(sounds) == len(set(sounds)) == 20
    assert len(set(se.BUILT_IN.values())) == len(se.BUILT_IN)
    quickmail = set(soundschemes.catalog("quickmail").built_in_sounds)
    assert not quickmail & set(sounds)
    for pack in se.PACKS:
        for sound in sounds:
            path = se.built_in_path(sound, pack)
            assert path.is_file(), path
        assert sorted(p.stem for p in se.built_in_path("tool", pack).parent.glob("*.wav")) == sorted(sounds)


def test_a_preview_choice_keeps_its_meaning_under_the_new_names():
    loaded = se.load_schemes({"wood": {"events": {"ToolRan": "builtin:tap", "NeedsYou": "builtin:glass/attention",
                                                  "ReplyFinished": "builtin:Arrived"}},
                              USER: {"name": "Mine", "events": {"Compacted": "builtin:softbell/filed",
                                                                "PeerMessage": "builtin:matched"}}})
    wood = se.find(loaded, "wood")
    assert wood.events == {"ToolRan": "builtin:tool", "NeedsYou": "builtin:glass/needsyou",
                           "ReplyFinished": "builtin:reply"}
    mine = se.find(loaded, USER)
    assert mine.events["Compacted"] == "builtin:softbell/compacted"
    assert mine.events["PeerMessage"] == "builtin:peer"
    assert se.ref_for(mine, se.STEERED).kind == "none"  # a new event: silent in a scheme of your own


# -- what's kept ---------------------------------------------------------------------


def test_a_built_in_scheme_keeps_only_what_you_changed():
    glass = se.built_in_scheme("glass")
    glass.events = {"ToolRan": "none", "FileChanged": "builtin:edit"}
    assert se.save_schemes([glass]) == {"glass": {"events": {"ToolRan": "none"}}}
    assert se.save_schemes([se.built_in_scheme("wood")]) == {}


def test_your_own_scheme_keeps_every_event_and_where_it_came_from():
    mine = se.Scheme(USER, "Mine", False, {"ToolRan": "builtin:wood/tool"}, "Me", None, "Quiet.",
                     {"gallery": "rain", "sha256": "c" * 64})
    saved = se.save_schemes([mine])[USER]
    assert saved["name"] == "Mine" and saved["author"] == "Me" and saved["description"] == "Quiet."
    assert "license" not in saved and saved["source"] == {"gallery": "rain", "sha256": "c" * 64}
    assert list(saved["events"]) == [e.key for e in se.EVENTS]
    assert saved["events"]["ToolRan"] == "builtin:wood/tool" and saved["events"]["NeedsYou"] == "none"
    back = se.load_schemes(se.save_schemes([mine]))
    assert [s.id for s in back] == ["glass", "softbell", "wood", USER]
    assert back[3].events == saved["events"] and back[3].source == mine.source


@pytest.mark.parametrize("raw", [
    {"user-xyz": {"name": "Bad id", "events": {}}},
    {"user-" + "A" * 32: {"name": "Capital id", "events": {}}},
    {USER: {"name": "  ", "events": {}}},
    {USER: "not a dict"},
    {"glass": {"events": {"ToolRan": "custom:../../x.wav", "Made_up": "none", "NeedsYou": 5}}},
    {"wood": {"events": {"ToolRan": "builtin:removed"}}},
    "not a dict",
])
def test_saved_schemes_that_cant_be_used_are_left_out(raw):
    loaded = se.load_schemes(raw)
    assert [s.id for s in loaded] == ["glass", "softbell", "wood"]
    assert all(s.events == {} for s in loaded)


def test_saved_names_are_cleaned_and_kept_apart():
    raw = {USER: {"name": "Glass", "events": {}}, OTHER: {"name": "Mine\u0007", "events": {},
                                                          "source": {"gallery": "../x", "sha256": "1"}}}
    names = {s.id: s.name for s in se.load_schemes(raw)}
    assert names[USER] == "Glass 2" and names[OTHER] == "Mine"
    assert se.load_schemes(raw)[-1].source is None


def test_at_most_100_schemes_of_your_own_are_loaded():
    raw = {"user-" + f"{i:032x}": {"name": f"S{i}", "events": {}} for i in range(150)}
    assert len(se.load_schemes(raw)) == 3 + se.MAX_USER_SCHEMES


def test_settings_round_trip(tmp_path):
    path = tmp_path / "s.json"
    settings = SpeechSettings()
    settings.sound_scheme = USER
    settings.sound_schemes = {USER: {"name": "Mine", "events": {"ToolRan": "builtin:softbell/tool"}},
                              "wood": {"events": {"NeedsYou": "none"}}}
    settings.save(path)
    loaded = SpeechSettings.load(path)
    assert loaded.sound_scheme == USER
    assert loaded.sound_schemes["wood"] == {"events": {"NeedsYou": "none"}}
    assert loaded.sound_schemes[USER]["events"]["ToolRan"] == "builtin:softbell/tool"
    assert loaded.sound_schemes[USER]["events"]["NeedsYou"] == "none"


def test_a_scheme_that_isnt_there_plays_as_glass(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"sound_scheme": OTHER, "sound_schemes": {}}), encoding="utf-8")
    assert SpeechSettings.load(path).sound_scheme == "glass"


def test_a_preview_style_and_choices_become_that_schemes(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"sound_style": "wood", "sound_choices": {
        se.TOOL: "none", se.FINISHED: "custom:mine.wav", "made-up": "none",
        se.NEEDS_YOU: "custom:../x.wav"}}), encoding="utf-8")
    loaded = SpeechSettings.load(path)
    assert loaded.sound_scheme == "wood"
    assert loaded.sound_schemes == {"wood": {"events": {"ToolRan": "none",
                                                        "ReplyFinished": "custom:mine.wav"}}}
    saved = json.loads(_saved(loaded, tmp_path))
    assert "sound_style" not in saved and "sound_choices" not in saved


def test_an_unknown_preview_style_is_glass(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"sound_style": "../../evil", "sound_choices": "x"}), encoding="utf-8")
    loaded = SpeechSettings.load(path)
    assert loaded.sound_scheme == "glass" and loaded.sound_schemes == {}


def _saved(settings, tmp_path):
    out = tmp_path / "out.json"
    settings.save(out)
    return out.read_text(encoding="utf-8")


# -- what an event plays -------------------------------------------------------------


def test_resolve_in_a_built_in_scheme(data):
    wood = se.built_in_scheme("wood")
    path, fallback = se.resolve(se.TOOL, wood)
    assert Path(path).parts[-2:] == ("wood", "tool.wav") and fallback is None
    wood.events = {"ToolRan": "none", "NeedsYou": "builtin:glass/send", "ReplyFinished": "builtin:send"}
    assert se.resolve(se.TOOL, wood) is None
    assert Path(se.resolve(se.NEEDS_YOU, wood)[0]).parts[-2:] == ("glass", "send.wav")
    assert Path(se.resolve(se.FINISHED, wood)[0]).parts[-2:] == ("wood", "send.wav")


def test_resolve_in_your_own_scheme(data):
    mine = se.Scheme(USER, "Mine", False, {"ToolRan": "builtin:tool", "NeedsYou": "custom:m.wav"})
    assert se.resolve(se.FINISHED, mine) is None  # blank means silent
    assert Path(se.resolve(se.TOOL, mine)[0]).parts[-2:] == ("glass", "tool.wav")
    path, fallback = se.resolve(se.NEEDS_YOU, mine)
    assert Path(path) == data / "sounds" / "m.wav"
    assert Path(fallback).parts[-2:] == ("glass", "needsyou.wav")


def test_a_bad_choice_never_leaves_the_sounds_folder(data):
    glass = se.built_in_scheme("glass")
    glass.events = {"ToolRan": "custom:../../secret.wav"}
    path, _ = se.resolve(se.TOOL, glass)
    assert Path(path).parts[-2:] == ("glass", "tool.wav")


def test_labels(data):
    glass, wood = se.built_in_scheme("glass"), se.built_in_scheme("wood")
    assert se.row_label(se.TOOL, glass) == "Tool ran: Tick"
    glass.events = {"ToolRan": "none", "FileChanged": "builtin:wood/edit",
                    "PeerMessage": "custom:m.wav", "NeedsYou": "builtin:glass/needsyou"}
    assert se.row_label(se.TOOL, glass) == "Tool ran: None"
    assert se.row_label(se.FILE_CHANGED, glass) == "File changed: Double tick (Wood)"
    assert se.row_label(se.NEEDS_YOU, glass) == "Needs you: Call"
    assert se.row_label(se.PEER, glass) == "Message from another session: Your file, m.wav (missing)"
    (data / "sounds").mkdir()
    (data / "sounds" / "m.wav").write_bytes(wav_bytes())
    assert se.row_label(se.PEER, glass) == "Message from another session: Your file, m.wav"
    assert se.count_label(glass) == "19 of 20 events have a sound"
    assert se.count_label(se.Scheme(USER, "Mine", False)) == "0 of 20 events have a sound"
    assert se.count_label(wood) == "20 of 20 events have a sound"


def test_what_the_sound_choice_offers(data):
    (data / "sounds").mkdir()
    (data / "sounds" / "m.wav").write_bytes(wav_bytes())
    built_in = se.offered(se.built_in_scheme("softbell"))
    assert built_in[0] == "none" and built_in[1] == "builtin:tool" and built_in[-1] == "custom:m.wav"
    assert len(built_in) == 1 + 20 + 1
    own = se.offered(se.Scheme(USER, "Mine", False))
    assert "builtin:softbell/reply" in own and len(own) == 1 + 60 + 1


def test_schemes_using_a_file():
    a = se.Scheme(USER, "A", False, {"ToolRan": "custom:m.wav"})
    b = se.built_in_scheme("wood")
    b.events = {"NeedsYou": "custom:m.wav"}
    assert se.schemes_using([se.built_in_scheme("glass"), a, b], "m.wav") == ["A", "Wood"]


# -- scheme files ----------------------------------------------------------------------


def test_export_and_import_bring_a_scheme_and_its_file_across(data):
    folder = data / "sounds"
    folder.mkdir()
    (folder / "knock.wav").write_bytes(wav_bytes())
    softbell = se.built_in_scheme("softbell")
    softbell.events = {"NeedsYou": "custom:knock.wav", "ToolRan": "none"}
    exported = se.export(softbell)
    with zipfile.ZipFile(io.BytesIO(exported)) as zf:
        meta = json.loads(zf.read("scheme.json"))
        assert zf.namelist() == ["scheme.json", "sounds/knock.wav"]
    assert meta["app"] == "thechatplace" and meta["name"] == "Soft bell"
    assert meta["events"]["FileChanged"] == "builtin:softbell/edit"  # pinned to its tone
    (folder / "knock.wav").unlink()
    imported = se.import_scheme(exported, ["Glass", "Soft bell", "Wood"])
    scheme = imported.scheme
    assert scheme.name == "Soft bell 2" and not scheme.built_in and scheme.id.startswith("user-")
    assert scheme.events["NeedsYou"] == "custom:knock.wav" and (folder / "knock.wav").is_file()
    assert scheme.events["FileChanged"] == "builtin:softbell/edit"
    assert imported.message() == "Added the sound scheme Soft bell 2, with 1 sound file."
    assert Path(se.resolve(se.FILE_CHANGED, scheme)[0]).parts[-2:] == ("softbell", "edit.wav")


def test_import_says_what_was_left_silent(data):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("scheme.json", json.dumps({"format": 1, "app": "thechatplace", "name": "X",
                                               "events": {"ToolRan": "custom:gone.wav",
                                                          "NeedsYou": "custom:also.wav"}}))
    imported = se.import_scheme(buffer.getvalue(), [])
    assert imported.message() == ("Added the sound scheme X. 2 events had a sound file that was "
                                  "missing or could not be played, and now have no sound.")


def test_a_quickmail_scheme_is_refused_by_name(data):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("scheme.json", json.dumps({"format": 1, "name": "Mail", "events": {}}))
    with pytest.raises(shared.SchemeFileProblem, match="for QuickMail, so The Chat Place can't use it"):
        se.import_scheme(buffer.getvalue(), [])


# -- the gallery's view ----------------------------------------------------------------


def _entry(sid="rain", sha="d" * 64, events=8, description="Soft."):
    return shared_gallery.Entry(sid, "Rain", "Someone", "CC0-1.0", description,
                                f"{sid}-{sha[:16]}.tcpsounds", 10, sha, events)


def test_gallery_rows_say_what_you_have():
    entry = _entry()
    assert sound_gallery.row_label(entry, []) == "Rain, by Someone. 8 of 20 events have a sound."
    mine = se.Scheme(USER, "Rain", False, source={"gallery": "rain", "sha256": "d" * 64})
    assert sound_gallery.state(entry, [mine]) == sound_gallery.ADDED
    assert sound_gallery.row_label(entry, [mine]).endswith(" Added.")
    mine.source["sha256"] = "e" * 64
    assert sound_gallery.state(entry, [mine]) == sound_gallery.UPDATE
    assert sound_gallery.row_label(entry, [mine]).endswith(" Update available.")
    assert sound_gallery.added_as(entry, [mine]) is mine
    assert sound_gallery.row_label(_entry(events=None), []) == "Rain, by Someone."
    assert sound_gallery.details(entry) == "Soft. By Someone. Licence: CC0-1.0."
    assert sound_gallery.details(_entry(description="")) == "By Someone. Licence: CC0-1.0."


def test_the_gallery_is_this_apps_and_checks_its_key():
    assert sound_gallery.gallery().base == "https://theideaplace.github.io/soundgallery/thechatplace/"
    assert sound_gallery.check() is True
