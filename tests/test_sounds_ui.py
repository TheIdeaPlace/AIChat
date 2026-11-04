"""Sounds in the window (#250): what plays when, for each provider, and the Sounds dialog.
These build hidden windows, so run them in the VM or CI. fake_env's recorder stands in for the
player, so nothing is ever heard."""
import json
import wave
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import wx

import fake_env
from test_ui_hidden import add_desktop, pump
from thechatplace import platform_paths, sound_events as se
from thechatplace.claude_cli import PermissionRequest, TurnEvent
from thechatplace.own_store import OwnSession, OwnSessionStore
from thechatplace.providers.codex_runtime import CodexChatState
from thechatplace.speech import SpeechSettings
from thechatplace.ui import sound_host
from thechatplace.ui.sounds_dialog import SoundsDialog, keep_custom_sound
from theideaplace_appkit.sounds import SoundProblem


def make_wav(path, seconds=0.05, width=2):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(width)
        w.setframerate(44100)
        w.writeframes(bytes(int(44100 * seconds) * width))
    return str(path)


@pytest.fixture
def env(tmp_path, monkeypatch, app):
    return fake_env.install(tmp_path, monkeypatch.setattr)


@pytest.fixture
def frame(env):
    from thechatplace.ui.main_frame import MainFrame
    store = OwnSessionStore(env["tmp"] / "own.json")
    store.add(OwnSession("own-1", "Loaded", "C:\\G\\Scratch", last_activity_ms=fake_env.now_ms()))
    store.add(OwnSession("own-2", "Other", "C:\\G\\Scratch", last_activity_ms=fake_env.now_ms()))
    store.add(OwnSession("cdx-1", "Codex one", "C:\\G\\Scratch", provider="codex",
                         last_activity_ms=fake_env.now_ms()))
    window = MainFrame(store=store, check_updates_at_start=False)
    window.speech.sounds_on = True
    assert pump(lambda: window.session_list.GetCount() >= 2)
    window._open = next(s for s in window._snapshot.sessions if s.key == "own:own-1")
    yield window
    window.stop_timers()
    window.Destroy()
    wx.YieldIfNeeded()


def turn(frame, event, session="own-1", title="Loaded"):
    frame._on_turn_event({"id": session}, title, event)


def running(frame, session="own-1"):
    frame._runners[session] = SimpleNamespace(cancelled=False, parser=None, prompt="hello",
                                              session_started=True, cwd="C:\\G\\Scratch",
                                              steering=(), waiting_on_background=False,
                                              elapsed=lambda: 1.0)


# ---------------------------------------------------------------- whether anything plays

def test_off_by_default_nothing_plays(frame, env):
    frame.speech.sounds_on = False
    turn(frame, TurnEvent("tool", text="Bash"))
    frame._sound(se.NEEDS_YOU)
    assert env["sounds"] == [] and env["players"] == []


def test_muted_nothing_plays(frame, env):
    frame.speech.sounds_muted = True
    turn(frame, TurnEvent("tool", text="Bash"))
    assert env["sounds"] == []


def test_an_event_set_to_none_is_silent(frame, env):
    frame.speech.sound_choices = {se.TOOL: se.NONE_REF}
    turn(frame, TurnEvent("tool", text="Bash"))
    assert env["sounds"] == []


def test_volume_and_style_reach_the_player(frame, env):
    frame.speech.sound_volume, frame.speech.sound_style = 50, "wood"
    turn(frame, TurnEvent("tool", text="Bash"))
    last = env["players"][0].last
    assert last["volume"] == 0.5 and Path(last["path"]).parts[-2:] == ("wood", "tap.wav")
    assert last["priority"] == "ambient"


def test_a_sound_problem_never_reaches_the_turn(frame, env, monkeypatch):
    monkeypatch.setattr(sound_host.SoundHost, "_sound_player", lambda self: 1 / 0)
    turn(frame, TurnEvent("tool", text="Bash"))  # no exception


def test_quiet_state_holds_back_what_happens_on_its_own_only(frame, env, monkeypatch):
    monkeypatch.setattr(sound_host, "quiet_now", lambda: True)
    frame._sound(se.FINISHED)
    frame._sound(se.NEEDS_YOU)
    frame._sound(se.SENT)
    frame._sound(se.STOPPED)
    assert env["sounds"] == [se.SENT, se.STOPPED]


# ---------------------------------------------------------------- Claude

def test_tool_taps_with_tool_activity_on_or_off(frame, env):
    for shown in (False, True):
        frame._show_activity = shown
        turn(frame, TurnEvent("tool", text="Bash", detail="Bash: ls"))
    assert env["sounds"] == [se.TOOL, se.TOOL]


def test_an_edit_sounds_when_it_has_worked_not_when_it_starts(frame, env):
    turn(frame, TurnEvent("tool", text="Edit", detail="Edit: a.py"))
    assert env["sounds"] == []
    turn(frame, TurnEvent("edited", data={"file_path": "a.py"}))
    assert env["sounds"] == [se.FILE_CHANGED]


def test_another_chats_tools_are_silent_unless_chosen(frame, env):
    turn(frame, TurnEvent("tool", text="Bash"), "own-2", "Other")
    assert env["sounds"] == []
    frame.speech.sound_tools = se.TOOLS_ALL
    turn(frame, TurnEvent("tool", text="Bash"), "own-2", "Other")
    assert env["sounds"] == [se.TOOL]


def test_a_question_needs_you_once_and_is_urgent(frame, env):
    running(frame)
    request = PermissionRequest("r1", "Bash", {"command": "x"})
    turn(frame, TurnEvent("permission", text="x", request=request))
    second = PermissionRequest("r2", "Bash", {"command": "y"})
    turn(frame, TurnEvent("permission", text="y", request=second))
    assert env["sounds"] == [se.NEEDS_YOU]
    assert env["players"][0].last["priority"] == "urgent"


def test_a_reply_finishing_here_and_elsewhere(frame, env):
    running(frame)
    running(frame, "own-2")
    turn(frame, TurnEvent("finished", text="done"))
    turn(frame, TurnEvent("finished", text="done"), "own-2", "Other")
    assert env["sounds"] == [se.FINISHED, se.FINISHED_ELSEWHERE]


def test_a_failed_turn(frame, env):
    running(frame)
    turn(frame, TurnEvent("failed", text="It broke", is_error=True))
    assert env["sounds"] == [se.FAILED]


def test_stopping_is_stopped_and_not_failed(frame, env):
    running(frame)
    frame._runners["own-1"].cancelled = True
    turn(frame, TurnEvent("failed", text="Stopped.", is_error=True))
    assert env["sounds"] == [se.STOPPED]


def test_a_turn_ending_with_refusals_needs_you(frame, env):
    running(frame)
    turn(frame, TurnEvent("finished", text="done", denials=["Bash was refused"]))
    assert env["sounds"] == [se.NEEDS_YOU]


def test_no_new_answer_after_background_work_is_silent(frame, env):
    running(frame)
    turn(frame, TurnEvent("finished", text="done", data={"after_background": True}))
    assert env["sounds"] == []


def test_compaction_sounds_once_from_both_its_reports(frame, env):
    frame._announce_compaction("own-1", "Loaded", "Compacted.", "stream", "k")
    frame._announce_compaction("own-1", "Loaded", "Compacted.", "reader", "k")
    assert env["sounds"] == [se.COMPACTED]


def test_other_chats_can_be_narrowed(frame, env):
    frame.speech.sound_others = se.OTHERS_NONE
    frame._sound_turn_end("own:own-2")
    frame.speech.sound_others = se.OTHERS_OWN
    frame._sound_turn_end("own:own-2", own=True)
    frame._sound_turn_end("local_x", own=False)
    frame.speech.sound_others = se.OTHERS_NONE
    frame._sound_turn_end("own:own-1")  # the loaded chat always sounds
    assert env["sounds"] == [se.FINISHED_ELSEWHERE, se.FINISHED]


# ---------------------------------------------------------------- Codex and Copilot

def codex(state="working", outcome="inProgress", **fields):
    return CodexChatState("cdx-1", state, "turn", outcome, **fields)


def test_codex_tools_and_edits_sound_by_count_and_merged_states_sound_once(frame, env):
    frame.speech.sound_tools = se.TOOLS_ALL
    frame._on_codex_update(codex(tools_started=0))
    assert env["sounds"] == []
    frame._on_codex_update(codex(tools_started=3))
    assert env["sounds"] == [se.TOOL]
    frame._on_codex_update(codex(tools_started=3))
    assert env["sounds"] == [se.TOOL]
    frame._on_codex_update(codex(tools_started=4, edits_made=1))
    assert env["sounds"] == [se.TOOL, se.FILE_CHANGED]


def test_a_new_turn_counts_from_nothing(frame, env):
    frame.speech.sound_tools = se.TOOLS_ALL
    frame._on_codex_update(codex(tools_started=5))
    frame._on_codex_update(CodexChatState("cdx-1", "working", "turn2", "inProgress", tools_started=1))
    assert env["sounds"] == [se.TOOL, se.TOOL]


@pytest.mark.parametrize("outcome,sound", [
    ("completed", se.FINISHED_ELSEWHERE), ("failed", se.FAILED), ("interrupted", se.TROUBLE)])
def test_codex_turn_endings(frame, env, outcome, sound):
    frame._on_codex_update(codex("idle", outcome))
    frame._on_codex_update(codex("idle", outcome))  # the same end again is no news
    assert env["sounds"] == [sound]


def test_an_interruption_you_asked_for_is_stopped_once(frame, env):
    frame._sound_note_stop("codex:cdx-1")
    frame._on_codex_update(codex("idle", "interrupted"))
    assert env["sounds"] == [se.STOPPED]
    frame._on_codex_update(CodexChatState("cdx-1", "idle", "turn2", "interrupted"))
    assert env["sounds"] == [se.STOPPED, se.TROUBLE]


def test_a_codex_send_failing_for_an_agent_conversation_sounds(frame, env, monkeypatch):
    record = frame.store.get("cdx-1", provider="codex")
    monkeypatch.setattr(frame, "_codex_work", lambda action, done: done(None, True))
    frame._send_codex_owned(record, "hi", announce_send=False, restore=False)
    assert env["sounds"] == [se.NOT_SENT]


def test_what_goes_on_its_own_holds_back_in_a_presentation(frame, env, monkeypatch):
    monkeypatch.setattr(sound_host, "quiet_now", lambda: True)
    frame._sound(se.SENT, "own:own-1", on_its_own=True)
    assert env["sounds"] == []
    frame._sound(se.SENT, "own:own-1")
    assert env["sounds"] == [se.SENT]


def test_an_answer_with_background_work_left_has_its_sound(frame, env):
    running(frame)
    frame._answered_with_background("own-1", "Loaded", TurnEvent("waiting", text="Answer"), True)
    assert env["sounds"] == [se.FINISHED]


def test_a_failed_answer_with_background_work_left_is_failed(frame, env):
    running(frame)
    frame._answered_with_background("own-1", "Loaded",
                                    TurnEvent("waiting", text="Bad", is_error=True), True)
    assert env["sounds"] == [se.FAILED]


def test_refusals_with_background_work_left_need_you(frame, env):
    running(frame)
    frame._answered_with_background("own-1", "Loaded",
                                    TurnEvent("waiting", text="Ok", denials=["Bash"]), True)
    assert env["sounds"] == [se.NEEDS_YOU]


def test_copilot_counts_an_edit_only_once_it_has_worked():
    from thechatplace.providers.copilot_runtime import _count_for_sounds
    chat = SimpleNamespace(tools_started=0, edits_made=0, edit_calls=set())
    _count_for_sounds(chat, "tool_call", {"toolCallId": "r", "kind": "read"})
    assert (chat.tools_started, chat.edits_made) == (1, 0)
    _count_for_sounds(chat, "tool_call_update", {"toolCallId": "r", "status": "completed"})
    _count_for_sounds(chat, "tool_call", {"toolCallId": "e", "kind": "edit", "status": "pending"})
    assert (chat.tools_started, chat.edits_made) == (1, 0)
    _count_for_sounds(chat, "tool_call_update", {"toolCallId": "e", "status": "completed"})
    _count_for_sounds(chat, "tool_call_update", {"toolCallId": "e", "status": "completed"})
    assert chat.edits_made == 1
    _count_for_sounds(chat, "tool_call", {"toolCallId": "d", "kind": "delete"})
    _count_for_sounds(chat, "tool_call_update", {"toolCallId": "d", "status": "failed"})
    _count_for_sounds(chat, "tool_call", {"toolCallId": "m", "kind": "move", "status": "completed"})
    assert chat.edits_made == 2


def test_context_warning_sounds_once(frame, env, monkeypatch):
    monkeypatch.setattr(frame, "_context", lambda info: (900_000, 1_000_000))
    frame._check_context()
    frame._check_context()
    assert env["sounds"] == [se.LIMIT]


def test_an_own_chat_seen_by_the_list_counts_as_own(frame, env):
    frame.speech.sound_others = se.OTHERS_OWN
    frame._sound_turn_end("codex:cdx-1", own=True)
    assert env["sounds"] == [se.FINISHED_ELSEWHERE]


def test_codex_counts_reach_the_state_the_window_reads():
    """Through the real turn executor: the window only sees what its snapshot copies."""
    from test_codex_execution import Connection
    from thechatplace.providers.codex_events import CodexEvent, decode_event
    from thechatplace.providers.codex_execution import CodexTurnExecutor
    connection = Connection()
    proof = connection.proof("one")
    executor = CodexTurnExecutor(connection)
    turn = executor.start(proof, "A").turn_id

    def started(kind, item):
        return decode_event("item/started", {"threadId": "one", "turnId": turn, "item": {
            "type": kind, "id": item, "command": "ls"}})
    connection.events.extend([started("commandExecution", "c"), started("webSearch", "w"),
                              started("mcpToolCall", "m"), started("reasoning", "r")])
    executor.drain()
    assert executor.snapshot(proof).tools_started == 3
    for paths in ((), ("a.py",), ("a.py",)):  # started, made, and made again
        connection.events.append(CodexEvent("file_changes", "one", turn, "f", "x", paths=paths))
        executor.drain()
    assert executor.snapshot(proof).edits_made == 1


def test_a_stop_that_came_too_late_does_not_label_a_later_turn(frame, env):
    frame._sound_note_stop("codex:cdx-1")
    frame._on_codex_update(codex("idle", "completed"))
    frame._on_codex_update(CodexChatState("cdx-1", "idle", "turn2", "interrupted"))
    assert env["sounds"] == [se.FINISHED_ELSEWHERE, se.TROUBLE]


def test_a_stop_pressed_before_the_turn_is_reported_as_started_still_counts(frame, env):
    frame._on_codex_update(CodexChatState("cdx-1", "idle", "turn0", "completed"))
    frame._sound_note_stop("codex:cdx-1")
    frame._on_codex_update(CodexChatState("cdx-1", "working", "turn1", "inProgress"))
    frame._on_codex_update(CodexChatState("cdx-1", "idle", "turn1", "interrupted"))
    assert env["sounds"][-1] == se.STOPPED


def test_an_old_stop_does_not_label_a_much_later_turn(frame, env):
    frame._sound_note_stop("codex:cdx-1")
    frame._sound_stops_asked["codex:cdx-1"] -= sound_host.STOP_COUNTS_SECONDS + 1
    frame._on_codex_update(codex("idle", "interrupted"))
    assert env["sounds"] == [se.TROUBLE]


def test_a_blank_state_in_between_replays_no_tool_sounds(frame, env):
    frame.speech.sound_tools = se.TOOLS_ALL
    frame._on_codex_update(codex(tools_started=2))
    frame._on_codex_update(CodexChatState("cdx-1", "unavailable"))
    frame._on_codex_update(codex(tools_started=2))
    assert env["sounds"] == [se.TOOL]


def test_compaction_in_a_loaded_desktop_chat_counts_as_the_loaded_chat(frame, env):
    frame.speech.sound_others = se.OTHERS_NONE
    frame._open = SimpleNamespace(key="local_x", cli_session_id="cli-x", is_own=False,
                                  provider="claude", title="Desktop", state="idle")
    frame._announce_compaction("cli-x", "Desktop", "Compacted.", "reader", "k")
    assert env["sounds"] == [se.COMPACTED]


def test_a_command_named_like_compact_has_the_finished_sound(frame, env):
    frame._sound_claude_turn_end("own-1", TurnEvent("finished", text="done"), [], None,
                                 "/compact-notes", True)
    frame._sound_claude_turn_end("own-1", TurnEvent("finished", text="Nothing to do."), [], None,
                                 "/compact", True)
    assert env["sounds"] == [se.FINISHED, se.FINISHED]
    env["sounds"].clear()
    frame._sound_claude_turn_end("own-1", TurnEvent("finished", text="Compacted."), [], None,
                                 "/compact", True)
    assert env["sounds"] == []
    frame._sound_claude_turn_end("own-1", TurnEvent("finished", text="Compacted."), [], None,
                                 "/compact", True, quiet=True)
    assert env["sounds"] == [se.COMPACTED]


def test_nothing_starts_the_player_again_once_closed(frame, env):
    frame._sound(se.SENT)
    frame._sound_close()
    frame._sound(se.SENT)
    assert env["sounds"] == [se.SENT] and len(env["players"]) == 1


def test_codex_compaction_is_not_a_reply(frame, env):
    frame._on_codex_update(codex("idle", "completed", compaction=True))
    assert env["sounds"] == [se.COMPACTED]


def test_codex_request_needs_you(frame, env):
    request = SimpleNamespace(summary=lambda: "Run x")
    frame._on_codex_update(codex(requests=(request,)))
    assert se.NEEDS_YOU in env["sounds"]


# ---------------------------------------------------------------- still working

def test_pulse_after_twenty_silent_seconds_and_not_before(frame, env):
    running(frame)
    frame._sound_tick(1000.0)
    frame._sound_tick(1019.0)
    assert env["sounds"] == []
    frame._sound_tick(1020.0)
    assert env["sounds"] == [se.PULSE]
    frame._sound_tick(1039.0)
    assert env["sounds"] == [se.PULSE]
    frame._sound_tick(1040.0)
    assert env["sounds"] == [se.PULSE, se.PULSE]


def test_no_pulse_while_it_waits_for_you_or_with_nothing_running(frame, env):
    frame._sound_tick(1000.0)
    frame._sound_tick(1100.0)
    running(frame)
    frame._pending["own-1"] = [object()]
    frame._sound_tick(1200.0)
    frame._sound_tick(1300.0)
    assert env["sounds"] == []


def test_no_pulse_while_only_background_work_is_left(frame, env):
    running(frame)
    frame._runners["own-1"].waiting_on_background = True
    frame._sound_tick(1000.0)
    frame._sound_tick(1100.0)
    assert env["sounds"] == []


def test_moving_to_another_working_chat_starts_the_wait_again(frame, env):
    running(frame)
    running(frame, "own-2")
    frame._sound_tick(1000.0)
    frame._sound_tick(1019.0)
    frame._open = next(s for s in frame._snapshot.sessions if s.key == "own:own-2")
    frame._sound_tick(1020.0)
    assert env["sounds"] == []
    frame._sound_tick(1040.0)
    assert env["sounds"] == [se.PULSE]


def test_a_problem_in_the_tick_leaves_the_timer_alone(frame, monkeypatch):
    monkeypatch.setattr(type(frame), "_sound_loaded_working", lambda self: 1 / 0)
    frame._sound_tick(1000.0)  # no exception


def test_no_pulse_for_a_chat_that_is_not_loaded(frame, env):
    running(frame, "own-2")
    frame._sound_tick(1000.0)
    frame._sound_tick(1100.0)
    assert env["sounds"] == []


# ---------------------------------------------------------------- mute

def test_mute_key_silences_cuts_off_and_says_so_with_announcements_silent(frame, env):
    frame.speech.announce = "silent"
    frame._sound(se.SENT)
    frame.on_toggle_mute_sounds()
    assert frame.speech.sounds_muted and frame.mute_item.IsChecked()
    assert env["sound_stops"] == ["stop"]
    assert "Sounds muted." in env["feedback"]
    frame.on_toggle_mute_sounds()
    assert not frame.speech.sounds_muted and not frame.mute_item.IsChecked()
    assert "Sounds on." in env["feedback"]
    assert SpeechSettings.load().sounds_muted is False


def test_unmuting_with_sounds_off_says_how_to_turn_them_on(frame, env):
    frame.speech.sounds_on = False
    frame.speech.sounds_muted = True
    frame.on_toggle_mute_sounds()
    assert any("File, Sounds turns them on" in text for text in env["feedback"])


def test_mute_is_on_the_menu_and_in_the_shortcuts():
    from thechatplace.ui_text import shortcuts_text
    assert "Ctrl+Shift+X" in shortcuts_text() or "Cmd+Shift+X" in shortcuts_text()


# ---------------------------------------------------------------- balloons

def test_a_balloon_is_silent_with_sounds_on_or_muted(frame, env, monkeypatch):
    silents = []
    monkeypatch.setattr(frame._notifier, "show",
                        lambda title, text, key, silent=False: silents.append(silent) or True)
    monkeypatch.setattr(type(frame), "_app_is_active", staticmethod(lambda: False))
    frame._notify("k", "t", "m", needs_you=True)
    frame.speech.sounds_on, frame.speech.sounds_muted = False, True
    frame._notify("k", "t", "m", needs_you=True)
    frame.speech.sounds_muted = False
    frame._notify("k", "t", "m", needs_you=True)
    assert silents == [True, True, False]


# ---------------------------------------------------------------- the dialog

@pytest.fixture
def dialog(frame, env):
    made = SoundsDialog(frame, frame.speech, frame._sound_player)
    yield made
    made.Destroy()


def select_event(dialog, event_id):
    dialog.events.SetSelection([e.id for e in se.EVENTS].index(event_id))
    dialog._show_event()


def test_dialog_shows_the_settings_and_its_rows(frame, dialog):
    assert dialog.play_sounds.GetValue() is True
    assert dialog.volume.GetStringSelection() == "80%"
    assert dialog.style.GetStringSelection() == "Glass"
    assert dialog.events.GetString(0) == "Tool ran: Tap"
    assert dialog.sound.GetStringSelection() == "Tap"


def test_choosing_a_sound_changes_the_row_and_only_ok_keeps_it(frame, dialog):
    select_event(dialog, se.FINISHED)
    dialog.sound.SetStringSelection("None")
    dialog._sound_chosen()
    assert "Reply finished: None" in dialog.events.GetItems()
    assert frame.speech.sound_choices == {}
    assert dialog.get_settings().sound_choices == {se.FINISHED: se.NONE_REF}


def test_preview_uses_what_is_showing_even_when_muted(frame, dialog, env):
    frame.speech.sounds_muted = True
    dialog.volume.SetStringSelection("30%")
    dialog.style.SetStringSelection("Wood")
    dialog._preview()
    path, volume = env["previews"][-1]
    assert volume == 0.3 and Path(path).parts[-2:] == ("wood", "tap.wav")


def test_preview_of_your_own_file_can_fall_back_as_real_playback_does(frame, dialog, env):
    dialog._choices[se.TOOL] = se.custom_ref("gone.wav")
    dialog._preview()
    assert Path(env["players"][0].preview_fallback).name == "tap.wav"


def test_closing_without_a_preview_makes_no_player(frame, env, monkeypatch):
    monkeypatch.setattr(SoundsDialog, "ShowModal", lambda self: wx.ID_CANCEL)
    frame.on_sounds()
    assert env["players"] == []


def test_preview_of_none_stops_what_was_previewing(dialog, env):
    dialog._preview()
    env["previews"].clear()
    dialog.sound.SetStringSelection("None")
    dialog._sound_chosen()
    dialog._preview()
    assert env["previews"] == [] and "stop_preview" in env["sound_stops"]


def test_get_settings_carries_everything_else_through(frame, dialog):
    frame.speech.session_order = "title"
    dialog._original = frame.speech
    dialog.tools.SetStringSelection("Every chat run here")
    dialog.others.SetStringSelection("None")
    dialog.mute.SetValue(True)
    got = dialog.get_settings()
    assert (got.sound_tools, got.sound_others, got.sounds_muted, got.session_order) == (
        se.TOOLS_ALL, se.OTHERS_NONE, True, "title")


def test_on_sounds_saves_on_ok_and_stops_when_turned_off(frame, env, monkeypatch):
    def ok(self):
        self.play_sounds.SetValue(False)
        return wx.ID_OK
    monkeypatch.setattr(SoundsDialog, "ShowModal", ok)
    frame._sound(se.SENT)
    frame.on_sounds()
    assert frame.speech.sounds_on is False and SpeechSettings.load().sounds_on is False
    assert "stop" in env["sound_stops"]


def test_on_sounds_cancel_changes_nothing(frame, env, monkeypatch):
    monkeypatch.setattr(SoundsDialog, "ShowModal", lambda self: wx.ID_CANCEL)
    frame.on_sounds()
    assert frame.speech.sounds_on is True


# ---------------------------------------------------------------- your own files

def test_keep_custom_sound_copies_and_reuses(env, tmp_path):
    source = make_wav(tmp_path / "mine.wav")
    assert keep_custom_sound(source) == "mine.wav"
    assert (platform_paths.custom_sounds_dir() / "mine.wav").is_file()
    assert keep_custom_sound(source) == "mine.wav"
    other = tmp_path / "o"
    other.mkdir()
    assert keep_custom_sound(make_wav(other / "mine.wav", seconds=0.1)) == "mine 2.wav"


@pytest.mark.parametrize("build", [
    lambda p: make_wav(p / "x.wav", width=1),
    lambda p: make_wav(p / "x.wav", seconds=31),
    lambda p: ((p / "x.wav").write_bytes(b"junk"), str(p / "x.wav"))[1],
])
def test_keep_custom_sound_refuses_bad_files(env, tmp_path, build):
    with pytest.raises(SoundProblem):
        keep_custom_sound(build(tmp_path))
    assert se.custom_files() == []


def test_keep_custom_sound_refuses_another_extension(env, tmp_path):
    with pytest.raises(SoundProblem, match=r"\.wav"):
        keep_custom_sound(make_wav(tmp_path / "chime.bak"))


def test_keep_custom_sound_refuses_a_device_name(env, tmp_path, monkeypatch):
    from thechatplace.ui import sounds_dialog
    monkeypatch.setattr(sounds_dialog.appkit_sounds, "read_wav", lambda path: None)
    with pytest.raises(SoundProblem):
        keep_custom_sound(str(tmp_path / "COM1.wav"))


def test_choose_file_failure_says_why_and_keeps_the_choice(frame, dialog, env, tmp_path, monkeypatch):
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"junk")

    class Picker:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ShowModal(self):
            return wx.ID_OK

        def GetPath(self):
            return str(bad)
    monkeypatch.setattr(wx, "FileDialog", Picker)
    dialog._choose_file()
    assert any("can't be used" in str(box) for box in env["boxes"])
    assert dialog.get_settings().sound_choices == {}


def test_choose_file_uses_it_and_lists_it(frame, dialog, env, tmp_path, monkeypatch):
    good = make_wav(tmp_path / "mine.wav")

    class Picker:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ShowModal(self):
            return wx.ID_OK

        def GetPath(self):
            return good
    monkeypatch.setattr(wx, "FileDialog", Picker)
    focused = []
    monkeypatch.setattr(dialog.sound, "SetFocus", lambda: focused.append(True))
    dialog._choose_file()
    assert focused, "focus goes where it's read that it worked"
    assert dialog.get_settings().sound_choices == {se.TOOL: se.custom_ref("mine.wav")}
    assert dialog.sound.GetStringSelection() == "Your file, mine.wav"
    assert dialog.events.GetString(0) == "Tool ran: Your file, mine.wav"
