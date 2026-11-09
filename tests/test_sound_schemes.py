"""Sound schemes (#250, app-kit's sounds/SCHEMES.md): what's kept, what an event plays in each
scheme, moving a 0.5 preview's style over, export and import, and the gallery's view of what you
have. No windows."""
import io
import json
import wave
import zipfile
from pathlib import Path

import pytest

from markers import windows_junctions
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
    assert len(sounds) == len(set(sounds)) == 23
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


def test_a_preview_that_chose_double_tick_gets_write():
    """0.5 previews called File changed's sound "edit" (Double tick), and the first set's
    "changed" before that; both are now Write, in any tone and in a scheme of your own."""
    loaded = se.load_schemes({"glass": {"events": {"ToolRan": "builtin:edit"}},
                              USER: {"name": "Mine", "events": {"FileChanged": "builtin:wood/Edit",
                                                                "Compacted": "builtin:changed"}}})
    assert se.find(loaded, "glass").events == {"ToolRan": "builtin:write"}
    mine = se.find(loaded, USER)
    assert mine.events["FileChanged"] == "builtin:wood/write"
    assert mine.events["Compacted"] == "builtin:write"
    assert se.migrate("softbell", {se.FILE_CHANGED: "builtin:edit"}) == (
        "softbell", {"softbell": {"events": {"FileChanged": "builtin:write"}}})
    assert "edit" not in se.BUILT_IN and se.BUILT_IN["write"] == "Write"


def test_only_tool_ran_follows_the_tool_setting():
    """Kelly found one setting for Tick and Double tick too much: File changed follows "Other
    sounds play for" like every other event now."""
    assert se.TOOL_EVENTS == {se.TOOL}


def test_answer_sent_answers_you_and_comes_after_needs_you():
    keys = [e.key for e in se.EVENTS]
    assert keys[keys.index("NeedsYou") + 1] == "AnswerSent"
    event = se.BY_ID[se.ANSWER_SENT]
    assert (event.label, event.sound, event.answers_you, event.priority) == (
        "Answer sent", "answer", True, "normal")
    assert se.BUILT_IN["answer"] == "Answer"


# -- what's kept ---------------------------------------------------------------------


def test_a_built_in_scheme_keeps_only_what_you_changed():
    glass = se.built_in_scheme("glass")
    glass.events = {"ToolRan": "none", "FileChanged": "builtin:write"}
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


def put(data, scheme_id, name):
    """A sound file in a scheme's own folder."""
    folder = data / "sounds" / scheme_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(wav_bytes())
    return folder / name


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
    assert Path(path) == data / "sounds" / USER / "m.wav"  # from its own folder
    assert Path(fallback).parts[-2:] == ("glass", "needsyou.wav")


def test_a_built_in_schemes_own_files_are_in_its_folder(data):
    glass = se.built_in_scheme("glass")
    glass.events = {"NeedsYou": "custom:m.wav"}
    assert Path(se.resolve(se.NEEDS_YOU, glass)[0]) == data / "sounds" / "glass" / "m.wav"


def test_a_file_in_the_old_shared_folder_isnt_used(data):
    """0.5 previews kept every file in sounds itself. Nothing looks there now (Kelly's call)."""
    (data / "sounds").mkdir()
    (data / "sounds" / "m.wav").write_bytes(wav_bytes())
    glass = se.built_in_scheme("glass")
    glass.events = {"NeedsYou": "custom:m.wav"}
    assert se.custom_missing(se.ref_for(glass, se.NEEDS_YOU), glass)
    assert se.custom_files(glass) == []


def test_each_scheme_sees_only_its_own_files(data):
    mine, other = se.Scheme(USER, "Mine", False), se.Scheme(OTHER, "Other", False)
    (data / "sounds" / USER).mkdir(parents=True)
    (data / "sounds" / USER / "m.wav").write_bytes(wav_bytes())
    assert se.custom_files(mine) == ["m.wav"] and se.custom_files(other) == []
    assert "custom:m.wav" in se.offered(mine) and "custom:m.wav" not in se.offered(other)


@pytest.mark.parametrize("sid", ["..", ".", "../x", "user-" + "a" * 31, "Glass", "user-" + "A" * 32,
                                 "con", "", "a/b", "a\\b", None])
def test_a_scheme_id_never_reaches_outside_the_sounds_folder(data, sid):
    scheme = se.Scheme(sid, "Bad", False, {"ToolRan": "custom:m.wav"})
    assert se.scheme_folder(scheme) is None and se.custom_path(scheme, "m.wav") is None
    assert se.custom_files(scheme) == []
    path, _ = se.resolve(se.TOOL, scheme)
    assert Path(path).parts[-2:] == ("glass", "tool.wav")  # the built-in sound, not a file
    se.remove_scheme_folder(sid)  # does nothing, and doesn't fail


@pytest.mark.parametrize("sid", ["..", "../x", "a/b", "a\\b", ".hidden", "", "x" * 65, None])
def test_platform_paths_refuses_a_folder_name_that_isnt_plain(sid):
    with pytest.raises(ValueError):
        platform_paths.scheme_sounds_dir(sid)


def test_platform_paths_scheme_folder(data):
    assert platform_paths.scheme_sounds_dir(USER) == data / "sounds" / USER
    assert platform_paths.scheme_sounds_dir("glass") == data / "sounds" / "glass"


def test_sound_tutorial_says_the_scheme_once():
    assert se.tutorial_intro(se.built_in_scheme("glass")) == (
        "Arrow to an event and press Space to hear it in the Glass scheme. "
        "Change sounds in File, Sounds.")
    assert "in the Soft bell scheme." in se.tutorial_intro(se.built_in_scheme("softbell"))
    assert "in the Calm scheme." in se.tutorial_intro(se.Scheme(USER, "Calm Scheme", False))
    assert "in the Quiet evenings scheme." in se.tutorial_intro(se.Scheme(USER, "Quiet evenings", False))
    # "Scheme" alone isn't a word followed by " scheme", so it stays.
    assert "in the Scheme scheme." in se.tutorial_intro(se.Scheme(USER, "Scheme", False))
    # Opened from File, Sounds (#596), it says it plays what Sounds shows, not where to go.
    assert se.tutorial_intro(se.built_in_scheme("wood"), from_sounds=True) == (
        "Arrow to an event and press Space to hear it in the Wood scheme. "
        "Changes you've made in Sounds play here, even before you choose OK.")


def test_sound_choices_say_started_in_the_chat_place():
    """#596: "started", not "running", as it covers chats started here that aren't working."""
    labels = list(se.TOOLS_LABELS.values()) + list(se.OTHERS_LABELS.values())
    assert "The open chat and chats started in The Chat Place" in labels
    assert not any("running" in label for label in labels)


def test_sound_tutorial_notes_only_what_cant_simply_play(data):
    glass = se.built_in_scheme("glass")
    glass.events = {"ToolRan": "none", "PeerMessage": "custom:m.wav"}
    assert se.tutorial_note(se.FINISHED, glass) is None
    assert se.tutorial_note(se.TOOL, glass) == "No sound."
    assert se.tutorial_note(se.PEER, glass) == "Its sound file is missing, so the built-in sound plays."
    put(data, "glass", "m.wav")
    assert se.tutorial_note(se.PEER, glass) is None
    # A scheme of your own leaves an event it doesn't mention silent.
    assert se.tutorial_note(se.FINISHED, se.Scheme(USER, "Mine", False)) == "No sound."


def test_a_bad_choice_never_leaves_the_sounds_folder(data):
    glass = se.built_in_scheme("glass")
    glass.events = {"ToolRan": "custom:../../secret.wav"}
    path, _ = se.resolve(se.TOOL, glass)
    assert Path(path).parts[-2:] == ("glass", "tool.wav")


def test_labels(data):
    glass, wood = se.built_in_scheme("glass"), se.built_in_scheme("wood")
    assert se.row_label(se.TOOL, glass) == "Tool ran: Tick"
    glass.events = {"ToolRan": "none", "FileChanged": "builtin:wood/write",
                    "PeerMessage": "custom:m.wav", "NeedsYou": "builtin:glass/needsyou"}
    assert se.row_label(se.TOOL, glass) == "Tool ran: None"
    assert se.row_label(se.FILE_CHANGED, glass) == "File changed: Write (Wood)"
    assert se.row_label(se.NEEDS_YOU, glass) == "Needs you: Call"
    assert se.row_label(se.PEER, glass) == "Message from another session: Your file, m.wav (missing)"
    put(data, "wood", "m.wav")  # another scheme's file isn't this one's
    assert se.row_label(se.PEER, glass) == "Message from another session: Your file, m.wav (missing)"
    put(data, "glass", "m.wav")
    assert se.row_label(se.PEER, glass) == "Message from another session: Your file, m.wav"
    assert se.count_label(glass) == "22 of 23 events have a sound"
    assert se.count_label(se.Scheme(USER, "Mine", False)) == "0 of 23 events have a sound"
    assert se.count_label(wood) == "23 of 23 events have a sound"


def test_what_the_sound_choice_offers(data):
    put(data, "softbell", "m.wav")
    put(data, USER, "n.wav")
    built_in = se.offered(se.built_in_scheme("softbell"))
    assert built_in[0] == "none" and built_in[1] == "builtin:tool" and built_in[-1] == "custom:m.wav"
    assert len(built_in) == 1 + 23 + 1
    own = se.offered(se.Scheme(USER, "Mine", False))
    assert "builtin:softbell/reply" in own and len(own) == 1 + 3 * 23 + 1
    assert own[-1] == "custom:n.wav"


def test_custom_files_lists_only_usable_names(data):
    glass = se.built_in_scheme("glass")
    assert se.custom_files(glass) == []
    folder = data / "sounds" / "glass"
    folder.mkdir(parents=True)
    (folder / "b.wav").write_bytes(b"x")
    (folder / "A.WAV").write_bytes(b"x")
    (folder / "note.txt").write_bytes(b"x")
    (folder / "sub.wav").mkdir()
    assert se.custom_files(glass) == ["A.WAV", "b.wav"]


def test_events_using_a_file_in_one_scheme():
    a = se.Scheme(USER, "A", False, {"ToolRan": "custom:m.wav", "NeedsYou": "custom:m.wav",
                                     "PeerMessage": "custom:n.wav"})
    assert se.events_using(a, "m.wav") == ["Tool ran", "Needs you"]
    assert se.events_using(a, "m.wav", leaving_out=se.TOOL) == ["Needs you"]
    assert se.events_using(se.built_in_scheme("glass"), "m.wav") == []


def test_without_file_goes_back_to_the_built_in_sound():
    glass = se.built_in_scheme("glass")
    glass.events = {"ToolRan": "custom:m.wav"}
    se.without_file(glass, se.TOOL)
    assert glass.events == {} and se.row_label(se.TOOL, glass) == "Tool ran: Tick"
    mine = se.Scheme(USER, "Mine", False, {"ToolRan": "custom:m.wav"})
    se.without_file(mine, se.TOOL)
    assert mine.events["ToolRan"] == "builtin:glass/tool"


# -- keeping each scheme's folder tidy ---------------------------------------------------


def test_copy_files_brings_what_the_scheme_plays_into_the_copys_folder(data):
    put(data, "wood", "m.wav")
    put(data, "wood", "spare.wav")  # in the folder but not played: not copied
    wood = se.built_in_scheme("wood")
    wood.events = {"ToolRan": "custom:m.wav", "NeedsYou": "custom:gone.wav"}
    copy = se.Scheme(USER, "Copy", False, dict(wood.events))
    assert se.copy_files(wood, copy) == 1
    assert sorted(p.name for p in (data / "sounds" / USER).iterdir()) == ["m.wav"]
    assert (data / "sounds" / "wood" / "m.wav").is_file()  # the original keeps its own


def test_copy_files_with_no_files_makes_no_folder(data):
    assert se.copy_files(se.built_in_scheme("glass"), se.Scheme(USER, "C", False)) == 0
    assert not (data / "sounds" / USER).exists()


def test_a_deleted_scheme_takes_its_folder_after_ok_only(data):
    put(data, USER, "m.wav")
    nested = data / "sounds" / USER / "inner"
    nested.mkdir()
    (nested / "x.wav").write_bytes(b"x")
    changes = se.FileChanges(deleted={USER})
    se.apply_file_changes(changes, [se.Scheme(USER, "Mine", False)], ok=False)  # Cancel: kept
    assert (data / "sounds" / USER / "m.wav").is_file()
    se.apply_file_changes(changes, [], ok=True)
    assert not (data / "sounds" / USER).exists()
    assert (data / "sounds").is_dir()  # only that scheme's folder goes


def test_a_scheme_made_and_cancelled_takes_its_folder(data):
    put(data, USER, "m.wav")
    put(data, OTHER, "m.wav")
    se.apply_file_changes(se.FileChanges(made={USER}), [se.Scheme(OTHER, "Old", False)], ok=False)
    assert not (data / "sounds" / USER).exists() and (data / "sounds" / OTHER / "m.wav").is_file()


def test_a_scheme_made_and_kept_keeps_its_folder(data):
    put(data, USER, "m.wav")
    mine = se.Scheme(USER, "Mine", False, {"ToolRan": "custom:m.wav"})
    se.apply_file_changes(se.FileChanges(made={USER}), [mine], ok=True)
    assert (data / "sounds" / USER / "m.wav").is_file()


def test_a_reset_built_in_scheme_loses_its_files_and_folder_on_ok(data):
    put(data, "glass", "m.wav")
    put(data, "glass", "spare.wav")
    glass = se.built_in_scheme("glass")
    changes = se.FileChanges(reset={"glass"})
    se.apply_file_changes(changes, [], ok=False)
    assert (data / "sounds" / "glass" / "m.wav").is_file()  # Cancel: nothing goes
    se.apply_file_changes(changes, [glass], ok=True)
    assert not (data / "sounds" / "glass").exists()


def test_a_reset_scheme_keeps_a_file_chosen_again_after_the_reset(data):
    put(data, "glass", "m.wav")
    put(data, "glass", "old.wav")
    glass = se.built_in_scheme("glass")
    glass.events = {"ToolRan": "custom:M.wav"}  # matched as Windows matches names
    se.apply_file_changes(se.FileChanges(reset={"glass"}), [glass], ok=True)
    assert sorted(p.name for p in (data / "sounds" / "glass").iterdir()) == ["m.wav"]


def test_a_removed_file_goes_on_ok_unless_another_event_plays_it(data):
    put(data, USER, "m.wav")
    put(data, USER, "n.wav")
    put(data, USER, "spare.wav")
    mine = se.Scheme(USER, "Mine", False, {"NeedsYou": "custom:n.wav"})
    changes = se.FileChanges(removed={(USER, "m.wav"), (USER, "n.wav"), (USER, "../escape.wav")})
    se.apply_file_changes(changes, [mine], ok=False)
    assert (data / "sounds" / USER / "m.wav").is_file()
    se.apply_file_changes(changes, [mine], ok=True)
    assert sorted(p.name for p in (data / "sounds" / USER).iterdir()) == ["n.wav", "spare.wav"]


def test_a_link_in_place_of_a_schemes_folder_is_removed_not_followed(data, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.wav").write_bytes(b"x")
    (data / "sounds").mkdir(exist_ok=True)
    try:
        (data / "sounds" / USER).symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("can't make a link here")
    se.apply_file_changes(se.FileChanges(deleted={USER}), [], ok=True)
    assert (outside / "keep.wav").is_file() and not (data / "sounds" / USER).exists()
    (data / "sounds" / OTHER).symlink_to(outside, target_is_directory=True)
    mine = se.Scheme(OTHER, "Mine", False)
    se.apply_file_changes(se.FileChanges(reset={OTHER}), [mine], ok=True)
    assert (outside / "keep.wav").is_file()


def _junction(link, target):
    import _winapi
    _winapi.CreateJunction(str(target), str(link))


@windows_junctions
def test_a_junction_in_place_of_a_schemes_folder_is_removed_not_followed(data, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.wav").write_bytes(b"x")
    (data / "sounds").mkdir(exist_ok=True)
    _junction(data / "sounds" / USER, outside)
    se.apply_file_changes(se.FileChanges(deleted={USER}), [], ok=True)
    assert (outside / "keep.wav").is_file() and not (data / "sounds" / USER).exists()
    _junction(data / "sounds" / OTHER, outside)
    se.apply_file_changes(se.FileChanges(reset={OTHER}), [se.Scheme(OTHER, "Mine", False)], ok=True)
    assert (outside / "keep.wav").is_file()


@windows_junctions
def test_a_junction_inside_a_schemes_folder_isnt_followed(data, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.wav").write_bytes(b"x")
    put(data, USER, "m.wav")
    _junction(data / "sounds" / USER / "inner", outside)
    se.apply_file_changes(se.FileChanges(deleted={USER}), [], ok=True)
    assert (outside / "keep.wav").is_file() and not (data / "sounds" / USER).exists()


def test_without_isjunction_as_on_python_3_11_tidying_still_works(data, monkeypatch):
    """Python 3.11 (Debian 12, from source) has no os.path.isjunction."""
    monkeypatch.delattr(se.os.path, "isjunction", raising=False)
    put(data, USER, "m.wav")
    put(data, "glass", "spare.wav")
    se.apply_file_changes(se.FileChanges(deleted={USER}, reset={"glass"}), [se.built_in_scheme("glass")],
                          ok=True)
    assert not (data / "sounds" / USER).exists() and not (data / "sounds" / "glass").exists()
    put(data, OTHER, "m.wav")
    se.remove_scheme_folder(OTHER)
    assert not (data / "sounds" / OTHER).exists()


def test_cancel_deletes_only_the_files_this_dialog_wrote_that_the_scheme_doesnt_play(data):
    put(data, "glass", "old.wav")
    put(data, "glass", "new.wav")
    put(data, "glass", "Played.wav")
    put(data, "glass", "untouched.wav")
    glass = se.built_in_scheme("glass")
    glass.events = {"ToolRan": "custom:old.wav", "NeedsYou": "custom:played.wav"}
    changes = se.FileChanges(written={("glass", "new.wav"), ("glass", "Played.wav")})
    se.apply_file_changes(changes, [glass], ok=False)
    assert sorted(p.name for p in (data / "sounds" / "glass").iterdir()) == [
        "Played.wav", "old.wav", "untouched.wav"]


def test_ok_keeps_files_this_dialog_wrote(data):
    put(data, "glass", "new.wav")
    se.apply_file_changes(se.FileChanges(written={("glass", "new.wav")}), [se.built_in_scheme("glass")],
                          ok=True)
    assert (data / "sounds" / "glass" / "new.wav").is_file()


def test_events_using_matches_names_whatever_their_case():
    a = se.Scheme(USER, "A", False, {"ToolRan": "custom:mine.wav", "NeedsYou": "custom:MINE.wav"})
    assert se.events_using(a, "Mine.wav") == ["Tool ran", "Needs you"]
    assert se.events_using(a, "mine.wav", leaving_out=se.TOOL) == ["Needs you"]


def test_an_update_reports_the_files_it_wrote(data):
    put(data, USER, "knock.wav")
    (data / "sounds" / USER / "knock.wav").write_bytes(wav_bytes(0.1))
    put(data, "softbell", "knock.wav")
    softbell = se.built_in_scheme("softbell")
    softbell.events = {"NeedsYou": "custom:knock.wav"}
    assert se.import_scheme(se.export(softbell), [], scheme_id=USER).added == ["knock 2.wav"]


# -- scheme files ----------------------------------------------------------------------


def test_export_and_import_bring_a_scheme_and_its_file_across(data):
    put(data, "softbell", "knock.wav")
    softbell = se.built_in_scheme("softbell")
    softbell.events = {"NeedsYou": "custom:knock.wav", "ToolRan": "none"}
    exported = se.export(softbell)
    with zipfile.ZipFile(io.BytesIO(exported)) as zf:
        meta = json.loads(zf.read("scheme.json"))
        assert zf.namelist() == ["scheme.json", "sounds/knock.wav"]
    assert meta["app"] == "thechatplace" and meta["name"] == "Soft bell"
    assert meta["events"]["FileChanged"] == "builtin:softbell/write"  # pinned to its tone
    imported = se.import_scheme(exported, ["Glass", "Soft bell", "Wood"])
    scheme = imported.scheme
    assert scheme.name == "Soft bell 2" and not scheme.built_in and scheme.id.startswith("user-")
    # In the new scheme's own folder, not Soft bell's and not sounds itself.
    assert scheme.events["NeedsYou"] == "custom:knock.wav"
    assert sorted(p.name for p in (data / "sounds" / scheme.id).iterdir()) == ["knock.wav"]
    assert sorted(p.name for p in (data / "sounds").iterdir()) == sorted(["softbell", scheme.id])
    assert scheme.events["FileChanged"] == "builtin:softbell/write"
    assert imported.message() == "Added the sound scheme Soft bell 2, with 1 sound file."
    assert Path(se.resolve(se.FILE_CHANGED, scheme)[0]).parts[-2:] == ("softbell", "write.wav")
    assert se.export(scheme)  # and exports again from its own folder
    with zipfile.ZipFile(io.BytesIO(se.export(scheme))) as zf:
        assert "sounds/knock.wav" in zf.namelist()


def test_export_reads_only_the_schemes_own_folder(data):
    put(data, "wood", "knock.wav")
    (data / "sounds" / "knock.wav").write_bytes(wav_bytes())  # the old shared folder
    glass = se.built_in_scheme("glass")
    glass.events = {"NeedsYou": "custom:knock.wav"}
    assert se.missing_files(glass) == 1
    with zipfile.ZipFile(io.BytesIO(se.export(glass))) as zf:
        assert zf.namelist() == ["scheme.json"]


def test_importing_twice_makes_two_folders(data):
    put(data, "softbell", "knock.wav")
    softbell = se.built_in_scheme("softbell")
    softbell.events = {"NeedsYou": "custom:knock.wav"}
    exported = se.export(softbell)
    a = se.import_scheme(exported, []).scheme
    b = se.import_scheme(exported, [a.name]).scheme
    assert a.id != b.id
    assert (data / "sounds" / a.id / "knock.wav").is_file() and (data / "sounds" / b.id / "knock.wav").is_file()


def test_an_update_goes_in_the_schemes_own_folder(data):
    put(data, USER, "knock.wav")
    (data / "sounds" / USER / "knock.wav").write_bytes(wav_bytes(0.1))  # different bytes
    softbell = se.built_in_scheme("softbell")
    softbell.events = {"NeedsYou": "custom:knock.wav"}
    put(data, "softbell", "knock.wav")
    imported = se.import_scheme(se.export(softbell), [], scheme_id=USER)
    assert imported.scheme.id == USER
    # Never overwrites: the new version's file takes a new name, and the event follows it.
    assert imported.scheme.events["NeedsYou"] == "custom:knock 2.wav"
    assert (data / "sounds" / USER / "knock 2.wav").is_file()


def test_an_update_into_a_built_in_or_bad_id_is_refused(data):
    with pytest.raises(ValueError):
        se.import_scheme(b"", [], scheme_id="glass")
    with pytest.raises(ValueError):
        se.import_scheme(b"", [], scheme_id="../x")


def test_a_scheme_file_naming_a_file_it_doesnt_carry_gets_nothing_from_elsewhere(data):
    """A file left out of a scheme file used to be taken from the shared folder if it was there.
    A new scheme's folder is empty, so the event is silent."""
    (data / "sounds").mkdir()
    (data / "sounds" / "gone.wav").write_bytes(wav_bytes())
    put(data, "glass", "gone.wav")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("scheme.json", json.dumps({"format": 1, "app": "thechatplace", "name": "X",
                                               "events": {"ToolRan": "custom:gone.wav"}}))
    imported = se.import_scheme(buffer.getvalue(), [])
    assert imported.dropped == 1 and imported.scheme.events["ToolRan"] == "none"


def test_a_failed_import_leaves_no_folder(data, monkeypatch):
    put(data, "softbell", "knock.wav")
    softbell = se.built_in_scheme("softbell")
    softbell.events = {"NeedsYou": "custom:knock.wav"}
    exported = se.export(softbell)

    def fail(scheme, folder):
        Path(folder).mkdir(parents=True)
        (Path(folder) / "half.wav").write_bytes(b"x")
        raise OSError("disk full")
    monkeypatch.setattr(shared, "install_sounds", fail)
    with pytest.raises(OSError):
        se.import_scheme(exported, [])
    assert sorted(p.name for p in (data / "sounds").iterdir()) == ["softbell"]


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
    assert sound_gallery.row_label(entry, []) == "Rain, by Someone. 8 of 23 events have a sound."
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
