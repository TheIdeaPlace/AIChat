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
from markers import windows_layout
from test_ui_hidden import add_desktop, pump
from thechatplace import platform_paths, sound_events as se
from thechatplace.claude_cli import PermissionRequest, TurnEvent
from thechatplace.own_store import OwnSession, OwnSessionStore
from thechatplace.providers.codex_runtime import CodexChatState
from thechatplace.speech import SpeechSettings
from thechatplace.ui import sound_host
from thechatplace.ui.sounds_dialog import SoundsDialog, SoundTutorialDialog, keep_custom_sound
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
    frame.speech.sound_schemes = {"glass": {"events": {"ToolRan": se.NONE_REF}}}
    turn(frame, TurnEvent("tool", text="Bash"))
    assert env["sounds"] == []


def test_volume_and_style_reach_the_player(frame, env):
    frame.speech.sound_volume, frame.speech.sound_scheme = 50, "wood"
    turn(frame, TurnEvent("tool", text="Bash"))
    last = env["players"][0].last
    assert last["volume"] == 0.5 and Path(last["path"]).parts[-2:] == ("wood", "tool.wav")
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


def test_another_chats_file_change_follows_other_sounds_not_tool_sounds(frame, env):
    """Tool sounds play for covers Tool ran only: a file changed in a chat in the background
    is heard as other sounds are, whatever the tool setting says."""
    turn(frame, TurnEvent("edited", data={"file_path": "a.py"}), "own-2", "Other")
    assert env["sounds"] == [se.FILE_CHANGED]  # tools: the open chat only; others: every chat
    frame.speech.sound_tools, frame.speech.sound_others = se.TOOLS_ALL, se.OTHERS_NONE
    turn(frame, TurnEvent("edited", data={"file_path": "a.py"}), "own-2", "Other")
    turn(frame, TurnEvent("tool", text="Bash"), "own-2", "Other")
    assert env["sounds"] == [se.FILE_CHANGED, se.TOOL]
    turn(frame, TurnEvent("edited", data={"file_path": "a.py"}))  # the open chat always sounds
    assert env["sounds"] == [se.FILE_CHANGED, se.TOOL, se.FILE_CHANGED]
    assert env["players"][0].last["priority"] == "normal"


# ---------------------------------------------------------------- Answer sent

def answering(frame, session="own-1", sent=True):
    """A Claude turn in ``session`` waiting on one permission request. Returns what it was sent."""
    running(frame, session)
    responses = []

    def respond(request_id, response):
        responses.append((request_id, response))
        return sent
    frame._runners[session].respond = respond
    request = PermissionRequest("r1", "Bash", {"command": "x"})
    frame._pending[session] = [request]
    return request, responses


@pytest.mark.parametrize("kind", ["allow", "allow for this chat", "refuse", "question",
                                  "decline questions", "plan", "keep planning", "form",
                                  "decline form"])
def test_each_claude_answer_sounds_once(frame, env, monkeypatch, kind):
    from thechatplace.claude_cli import allow_response, deny_response
    from thechatplace.claude_mcp import elicitation_response
    request, responses = answering(frame)
    decisions = {
        "allow": (allow_response(request), "Allowed.", {}),
        "allow for this chat": (allow_response(request, for_session=True),
                                "Allowed for the rest of this chat.", {}),
        "refuse": (deny_response("no"), "Refused.", {}),
        "question": ({"behavior": "allow", "updatedInput": {"answers": {"Q": "A"}}},
                     "Answer sent.", {}),
        "decline questions": (deny_response("not now"), "Declined to answer.", {}),
        "plan": (allow_response(request, mode="acceptEdits"), "Plan approved.", {}),
        "keep planning": (deny_response("Keep planning"), "Claude will keep planning.", {}),
        "form": (elicitation_response("accept", {"a": "b"}), "Sent to the tool.", {}),
        "decline form": (elicitation_response("decline"), "Declined.", {}),
    }
    monkeypatch.setattr(frame, "_ask", lambda title, req, own: decisions[kind])
    frame._answer_claude("own-1")
    assert len(responses) == 1
    assert env["sounds"] == [se.ANSWER_SENT]


def test_answer_later_or_a_turn_already_over_makes_no_answer_sound(frame, env, monkeypatch):
    from thechatplace.claude_cli import allow_response
    answering(frame)
    monkeypatch.setattr(frame, "_ask", lambda *a: None)  # Escape, or Answer Later
    frame._answer_claude("own-1")
    request, responses = answering(frame, sent=False)  # the turn ended meanwhile
    monkeypatch.setattr(frame, "_ask", lambda title, req, own: (allow_response(req), "Allowed.", {}))
    frame._answer_claude("own-1")
    assert len(responses) == 1 and env["sounds"] == []


def test_an_answer_sounds_for_a_chat_in_the_background_too(frame, env, monkeypatch):
    """It answers your key press, so Other sounds play for doesn't hold it back."""
    from thechatplace.claude_cli import allow_response
    frame.speech.sound_others = se.OTHERS_NONE
    answering(frame, "own-2")
    monkeypatch.setattr(frame, "_ask", lambda title, req, own: (allow_response(req), "Allowed.", {}))
    frame._answer_claude("own-2")
    assert env["sounds"] == [se.ANSWER_SENT]


def test_an_answer_sounds_during_a_presentation(frame, env, monkeypatch):
    from thechatplace.claude_cli import allow_response
    monkeypatch.setattr(sound_host, "quiet_now", lambda: True)
    answering(frame)
    monkeypatch.setattr(frame, "_ask", lambda title, req, own: (allow_response(req), "Allowed.", {}))
    frame._answer_claude("own-1")
    assert env["sounds"] == [se.ANSWER_SENT]


def codex_waiting(frame, request):
    from thechatplace.providers.codex_runtime import CodexChatState as State
    info = frame.store.get("cdx-1", provider="codex").to_info()
    state = State("cdx-1", "needs you", "turn", "inProgress", requests=(request,))
    frame._codex_states[info.key] = state
    return info, state


def codex_command():
    from thechatplace.providers.codex_requests import COMMAND, CodexRequest
    return CodexRequest(7, COMMAND, "cdx-1", "turn", "item", command="ls")


class FakeCodexDialog:
    """CodexPermissionDialog or QuestionDialog, answering as ``result`` says."""
    result = wx.ID_OK
    choice = "accept"
    declined = False

    def __init__(self, *args, **kwargs):
        pass

    def ShowModal(self):
        return type(self).result

    def answers(self):
        return {"q1": "Yes"}

    def Destroy(self):
        pass


def copilot_waiting(frame):
    """A Copilot chat waiting on a command it asks to run."""
    from thechatplace.providers.copilot_runtime import (AGENT_MODE, COMMAND, CopilotChatState,
                                                         CopilotRequest)
    frame.store.add(OwnSession("cp-1", "Copilot one", "C:\\G\\Scratch", provider="copilot",
                               last_activity_ms=fake_env.now_ms()))
    info = frame.store.get("cp-1", provider="copilot").to_info()
    request = CopilotRequest("perm-1", COMMAND, "call-1", title="Run a command", command="npm test",
                             choices=(("allow_once", "allow_once"), ("reject_once", "reject_once")))
    state = CopilotChatState("cp-1", "needs you", "1", "inProgress", mode=AGENT_MODE,
                             requests=(request,))
    frame._codex_states[info.key] = state
    return info, state


def waiting_for_permission(frame, provider):
    return (codex_waiting(frame, codex_command()) if provider == "codex"
            else copilot_waiting(frame))


@pytest.mark.parametrize("provider,choice", [
    ("codex", "accept"), ("codex", "decline"), ("codex", "tcp_chat_rule"),
    ("copilot", "allow_once"), ("copilot", "reject_once"), ("copilot", "tcp_chat_rule")])
def test_each_codex_permission_answer_sounds_once(frame, env, monkeypatch, provider, choice):
    from thechatplace.ui import codex_chats
    calls = []

    def owned_action(action, record, *args):
        calls.append(action)
        return None
    monkeypatch.setattr(frame.sessions, "owned_action", owned_action)
    monkeypatch.setattr(codex_chats, "CodexPermissionDialog",
                        type("Choosing", (FakeCodexDialog,), {"choice": choice}))
    info, state = waiting_for_permission(frame, provider)
    try:
        frame._answer_codex_owned(info, state)
        assert pump(lambda: env["sounds"] == [se.ANSWER_SENT])
        pump(lambda: False, timeout=0.2)
        assert env["sounds"] == [se.ANSWER_SENT]
        assert calls[-1] == ("allow_for_chat" if choice == "tcp_chat_rule" else "answer")
    finally:
        frame._codex_states.clear()


def test_a_codex_question_answered_sounds_and_declined_or_cancelled_doesnt(frame, env, monkeypatch):
    from thechatplace.providers.codex_requests import QUESTIONS, CodexQuestion, CodexRequest
    from thechatplace.ui import codex_chats
    monkeypatch.setattr(frame.sessions, "owned_action", lambda action, record, *args: None)
    stopped = []
    monkeypatch.setattr(frame, "_stop_codex_owned", lambda info: stopped.append(info.key))
    question = CodexRequest(8, QUESTIONS, "cdx-1", "turn", "item",
                            questions=(CodexQuestion("q1", "Go", "Go on?"),))
    info, state = codex_waiting(frame, question)
    try:
        monkeypatch.setattr(codex_chats, "QuestionDialog",
                            type("Cancelled", (FakeCodexDialog,), {"result": wx.ID_CANCEL}))
        frame._answer_codex_owned(info, state)
        monkeypatch.setattr(codex_chats, "QuestionDialog",
                            type("Declined", (FakeCodexDialog,), {"declined": True}))
        frame._answer_codex_owned(info, state)  # declining stops the turn: not an answer
        assert stopped == [info.key]
        monkeypatch.setattr(codex_chats, "QuestionDialog", FakeCodexDialog)
        frame._answer_codex_owned(info, state)
        assert pump(lambda: env["sounds"] == [se.ANSWER_SENT])
    finally:
        frame._codex_states.clear()


def test_a_codex_answer_that_couldnt_be_sent_makes_no_sound(frame, env, monkeypatch):
    from thechatplace.ui import codex_chats

    def owned_action(action, record, *args):
        if action == "answer":
            raise RuntimeError("no longer waiting")
        return None
    monkeypatch.setattr(frame.sessions, "owned_action", owned_action)
    monkeypatch.setattr(codex_chats, "CodexPermissionDialog", FakeCodexDialog)
    info, state = codex_waiting(frame, codex_command())
    try:
        frame._answer_codex_owned(info, state)
        assert pump(lambda: any("could not be answered" in text for text in env["feedback"]))
        assert env["sounds"] == []
    finally:
        frame._codex_states.clear()


@pytest.mark.parametrize("provider", ["codex", "copilot"])
def test_allow_for_this_chat_that_fails_makes_no_answer_sound(frame, env, monkeypatch, provider):
    from thechatplace.ui import codex_chats

    def owned_action(action, record, *args):
        if action == "allow_for_chat":
            raise RuntimeError("no longer waiting")
        return None
    monkeypatch.setattr(frame.sessions, "owned_action", owned_action)
    monkeypatch.setattr(codex_chats, "CodexPermissionDialog",
                        type("Keeping", (FakeCodexDialog,), {"choice": "tcp_chat_rule"}))
    info, state = waiting_for_permission(frame, provider)
    try:
        frame._answer_codex_owned(info, state)
        assert pump(lambda: any("could not be answered" in text for text in env["feedback"]))
        pump(lambda: False, timeout=0.2)
        assert env["sounds"] == []
    finally:
        frame._codex_states.clear()


def test_a_copilot_answer_sent_but_not_kept_sounds_once(frame, env, monkeypatch):
    """Allow for This Chat's answer went, though the command couldn't be kept: it's an answer."""
    from thechatplace.providers.copilot import NOT_KEPT, AllowedNotKept
    from thechatplace.ui import codex_chats

    def owned_action(action, record, *args):
        return AllowedNotKept(NOT_KEPT) if action == "allow_for_chat" else None
    monkeypatch.setattr(frame.sessions, "owned_action", owned_action)
    monkeypatch.setattr(codex_chats, "CodexPermissionDialog",
                        type("Keeping", (FakeCodexDialog,), {"choice": "tcp_chat_rule"}))
    info, state = copilot_waiting(frame)
    try:
        frame._answer_codex_owned(info, state)
        assert pump(lambda: env["feedback"] and env["feedback"][-1] == NOT_KEPT)
        pump(lambda: False, timeout=0.2)
        assert env["sounds"] == [se.ANSWER_SENT]
    finally:
        frame._codex_states.clear()


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


# ---------------------------------------------------------------- agent conversations (#248)

def codex_conversation(frame, mode="auto"):
    """Claude (own-1) and Codex (cdx-1) in a conversation that isn't loaded, waiting on
    Codex's reply to a message it sent."""
    from thechatplace import agents
    members = [agents.Member("claude", "own-1", "Claude", ""),
               agents.Member("codex", "cdx-1", "Codex", "")]
    conversation = agents.new_conversation("Pair", members, mode=mode, turn_limit=5)
    frame.agents.add(conversation)
    frame.speech.agent_auto_mode = True
    frame._agent_awaiting[("codex", "cdx-1")] = (conversation.conversation_id, True)
    return conversation


def fake_deliver(frame, monkeypatch, seen=None):
    """A send to a member that goes, as the real one does: the conversation now waits on it."""
    def deliver(conversation, index, text, relay=True):
        if seen is not None:
            seen.append((index, set(frame._sound_hushed_sends or ())))
        member = conversation.members[index]
        frame._agent_awaiting[member.identity] = (conversation.conversation_id, relay)
        return True
    monkeypatch.setattr(frame, "_agent_deliver_unhushed", deliver)


def test_a_codex_members_reply_handed_on_sounds_once_and_not_as_a_reply(frame, env, monkeypatch):
    codex_conversation(frame)
    seen = []
    fake_deliver(frame, monkeypatch, seen)
    frame._on_codex_update(codex("idle", "completed", replies=(("r1", "Reviewed: one bug."),)))
    frame._on_codex_update(codex("idle", "completed", replies=(("r1", "Reviewed: one bug."),)))
    assert seen == [(0, {"own:own-1"})]  # the send to Claude was hushed while it went
    assert env["sounds"] == [se.HANDED_ON]
    assert not frame._agent_sound_owed and not frame._sound_hushed_sends


def test_a_codex_members_reply_waiting_for_your_hand_off_sounds_as_a_reply(frame, env):
    codex_conversation(frame, mode="approve")
    frame._on_codex_update(codex("idle", "completed", replies=(("r1", "Reviewed: one bug."),)))
    assert env["sounds"] == [se.FINISHED_ELSEWHERE]


def test_a_codex_member_failing_in_a_conversation_sounds_failed_only(frame, env):
    codex_conversation(frame)
    frame._on_codex_update(codex("idle", "failed"))
    assert env["sounds"] == [se.FAILED]


def test_a_reply_handed_on_follows_other_sounds_play_for(frame, env, monkeypatch):
    codex_conversation(frame)
    frame.speech.sound_others = se.OTHERS_NONE
    fake_deliver(frame, monkeypatch)
    frame._on_codex_update(codex("idle", "completed", replies=(("r1", "Reviewed."),)))
    assert env["sounds"] == []


def test_a_hushed_send_is_silent_once_and_never_hushes_yours(frame, env):
    frame._sound_hush_send("codex:cdx-1")
    frame._sound(se.SENT, "codex:cdx-1")  # your key press: plays, and the hush stays
    frame._sound(se.SENT, "codex:cdx-1", on_its_own=True)  # the hand-off's: silent
    frame._sound(se.QUEUED, "codex:cdx-1", on_its_own=True)  # the hush is used up
    frame._sound(se.SENT, "own:own-1", on_its_own=True)  # another chat's
    assert env["sounds"] == [se.SENT, se.QUEUED, se.SENT]


def test_a_hushed_send_is_used_up_with_sounds_off_too(frame, env):
    frame.speech.sounds_on = False
    frame._sound_hush_send("own:own-1")
    frame._sound(se.SENT, "own:own-1", on_its_own=True)
    frame.speech.sounds_on = True
    frame._sound(se.SENT, "own:own-1", on_its_own=True)
    assert env["sounds"] == [se.SENT]


def own_scheme_without_pass(frame, explicit_none):
    """A scheme of your own with every sound but Agent reply handed on: one made before it
    existed has no entry for it; one saved since has None."""
    sid = "user-" + "c" * 32
    events = {e.key: se.built_in_ref(e.sound, "glass") for e in se.EVENTS if e.id != se.HANDED_ON}
    if explicit_none:
        events["ReplyHandedOn"] = "none"
    frame.speech.sound_schemes = {sid: {"name": "Mine from before", "events": events}}
    frame.speech.sound_scheme = sid


@pytest.mark.parametrize("explicit_none", [False, True])
def test_without_pass_in_the_scheme_a_codex_hand_off_sounds_as_before(frame, env, monkeypatch,
                                                                      explicit_none):
    own_scheme_without_pass(frame, explicit_none)
    assert not frame._sound_scheme_has(se.HANDED_ON)
    codex_conversation(frame)
    seen = []
    fake_deliver(frame, monkeypatch, seen)
    frame._on_codex_update(codex("idle", "completed", replies=(("r1", "Reviewed."),)))
    assert env["sounds"] == [se.FINISHED_ELSEWHERE]
    assert seen == [(0, set())]  # the send to Claude wasn't hushed


def test_a_codex_hand_off_refused_at_once_is_not_handed_on(frame, env, monkeypatch):
    """The send to the next member went nowhere (the real _agent_send_failed pops the wait
    on it): its reply sounds as one waiting for you, not as handed on."""
    codex_conversation(frame)
    monkeypatch.setattr(frame, "_agent_deliver_unhushed", lambda *a, **k: True)
    frame._on_codex_update(codex("idle", "completed", replies=(("r1", "Reviewed."),)))
    assert env["sounds"] == [se.FINISHED_ELSEWHERE] and not frame._sound_hushed_sends


def test_a_codex_members_turn_with_the_conversations_text_queued_is_silent(frame, env):
    codex_conversation(frame)
    frame._agent_sent_texts[("codex", "cdx-1")] = ["Review the lock too."]
    frame._on_codex_update(codex("idle", "completed", replies=(("r1", "Part one."),),
                                 queued=("Review the lock too.",)))
    assert env["sounds"] == [] and not frame._agent_sound_owed


def test_a_codex_members_turn_with_your_own_text_queued_keeps_its_sound(frame, env):
    codex_conversation(frame)
    frame._agent_sent_texts[("codex", "cdx-1")] = ["Review the lock too."]
    frame._on_codex_update(codex("idle", "completed", replies=(("r1", "Part one."),),
                                 queued=("Something I typed in the Codex chat.",)))
    assert env["sounds"] == [se.FINISHED_ELSEWHERE] and not frame._agent_sound_owed


def test_a_held_back_reply_sound_still_plays_if_the_conversation_raises(frame, env, monkeypatch):
    codex_conversation(frame)

    def broken(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(frame, "_agent_turn_ended", broken)
    with pytest.raises(RuntimeError):
        frame._on_codex_update(codex("idle", "completed", replies=(("r1", "Reviewed."),)))
    assert env["sounds"] == [se.FINISHED_ELSEWHERE] and not frame._agent_sound_owed


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

def test_windows_keeps_its_notification_sound_unless_you_choose_otherwise(frame, env, monkeypatch):
    silents = []
    monkeypatch.setattr(frame._notifier, "show",
                        lambda title, text, key, silent=False: silents.append(silent) or True)
    monkeypatch.setattr(type(frame), "_app_is_active", staticmethod(lambda: False))
    # Sounds on, muted and off all leave it as it is.
    frame._notify("k", "t", "m", needs_you=True)
    frame.speech.sounds_muted = True
    frame._notify("k", "t", "m", needs_you=True)
    frame.speech.sounds_on = False
    frame._notify("k", "t", "m", needs_you=True)
    assert silents == [False, False, False]
    frame.speech.sound_silent_notifications = True
    frame._notify("k", "t", "m", needs_you=True)
    assert silents[-1] is True


@windows_layout
def test_the_dialog_offers_it_off_and_keeps_your_choice(frame, dialog):
    assert dialog.silent_notifications.GetValue() is False
    assert dialog.get_settings().sound_silent_notifications is False
    dialog.silent_notifications.SetValue(True)
    assert dialog.get_settings().sound_silent_notifications is True


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
    assert dialog.scheme.GetStringSelection() == "Glass"
    assert dialog.events.GetString(0) == "Tool ran: Tick"
    assert dialog.sound.GetStringSelection() == "Tick"


def test_choosing_a_sound_changes_the_row_and_only_ok_keeps_it(frame, dialog):
    select_event(dialog, se.FINISHED)
    dialog.sound.SetStringSelection("None")
    dialog._sound_chosen()
    assert "Reply finished: None" in dialog.events.GetItems()
    assert frame.speech.sound_schemes == {}
    assert dialog.get_settings().sound_schemes == {"glass": {"events": {"ReplyFinished": se.NONE_REF}}}


def test_space_on_an_event_plays_it(frame, dialog, env):
    select_event(dialog, se.FINISHED)

    def key(code, shift=False):
        event = wx.KeyEvent(wx.wxEVT_KEY_DOWN)
        event.SetKeyCode(code)
        event.SetShiftDown(shift)
        return event
    dialog._on_events_key(key(wx.WXK_SPACE))
    path, _volume = env["previews"][-1]
    assert Path(path).name == "reply.wav"
    before = len(env["previews"])
    dialog._on_events_key(key(wx.WXK_SPACE, shift=True))  # not plain Space: left alone
    dialog._on_events_key(key(ord("A")))
    assert len(env["previews"]) == before


def test_preview_uses_what_is_showing_even_when_muted(frame, dialog, env):
    frame.speech.sounds_muted = True
    dialog.volume.SetStringSelection("30%")
    dialog.scheme.SetStringSelection("Wood")
    dialog._scheme_chosen()
    dialog._preview()
    path, volume = env["previews"][-1]
    assert volume == 0.3 and Path(path).parts[-2:] == ("wood", "tool.wav")


def test_preview_of_your_own_file_can_fall_back_as_real_playback_does(frame, dialog, env):
    dialog.current().events["ToolRan"] = se.custom_ref("gone.wav")
    dialog._preview()
    assert Path(env["players"][0].preview_fallback).name == "tool.wav"


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
    dialog.tools.SetStringSelection("The open chat and chats running in The Chat Place")
    dialog.others.SetStringSelection("The open chat only")
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

GLASS = se.built_in_scheme("glass")


def test_keep_custom_sound_copies_into_the_schemes_folder_and_reuses(env, tmp_path):
    source = make_wav(tmp_path / "mine.wav")
    assert keep_custom_sound(source, GLASS) == "mine.wav"
    assert (platform_paths.custom_sounds_dir() / "glass" / "mine.wav").is_file()
    assert not (platform_paths.custom_sounds_dir() / "mine.wav").exists()
    assert keep_custom_sound(source, GLASS) == "mine.wav"
    other = tmp_path / "o"
    other.mkdir()
    assert keep_custom_sound(make_wav(other / "mine.wav", seconds=0.1), GLASS) == "mine 2.wav"
    # Another scheme gets its own copy, under the same name.
    assert keep_custom_sound(source, se.built_in_scheme("wood")) == "mine.wav"
    assert (platform_paths.custom_sounds_dir() / "wood" / "mine.wav").is_file()


@pytest.mark.parametrize("build", [
    lambda p: make_wav(p / "x.wav", width=1),
    lambda p: make_wav(p / "x.wav", seconds=31),
    lambda p: ((p / "x.wav").write_bytes(b"junk"), str(p / "x.wav"))[1],
])
def test_keep_custom_sound_refuses_bad_files(env, tmp_path, build):
    with pytest.raises(SoundProblem):
        keep_custom_sound(build(tmp_path), GLASS)
    assert se.custom_files(GLASS) == []


def test_keep_custom_sound_refuses_another_extension(env, tmp_path):
    with pytest.raises(SoundProblem, match=r"\.wav"):
        keep_custom_sound(make_wav(tmp_path / "chime.bak"), GLASS)


def test_keep_custom_sound_refuses_a_device_name(env, tmp_path, monkeypatch):
    from thechatplace.ui import sounds_dialog
    monkeypatch.setattr(sounds_dialog.appkit_sounds, "read_wav", lambda path: None)
    with pytest.raises(SoundProblem):
        keep_custom_sound(str(tmp_path / "COM1.wav"), GLASS)


def test_keep_custom_sound_refuses_a_scheme_id_that_isnt_ours(env, tmp_path):
    with pytest.raises(SoundProblem):
        keep_custom_sound(make_wav(tmp_path / "x.wav"), se.Scheme("..", "Bad", False))
    assert not (platform_paths.app_data_dir() / "x.wav").exists()


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
    assert dialog.get_settings().sound_schemes == {}


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
    assert dialog.get_settings().sound_schemes == {"glass": {"events": {"ToolRan": se.custom_ref("mine.wav")}}}
    assert (platform_paths.custom_sounds_dir() / "glass" / "mine.wav").is_file()  # Glass's own folder
    assert dialog.sound.GetStringSelection() == "Your file, mine.wav"
    assert dialog.events.GetString(0) == "Tool ran: Your file, mine.wav"


# ---------------------------------------------------------------- sound schemes


class _Ask:
    """wx.TextEntryDialog answering with each of ``answers`` in turn (None: Cancel)."""
    answers: list = []
    asked: list = []

    def __init__(self, parent, message, title, value):
        _Ask.asked.append((title, value))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def SetMaxLength(self, n):
        pass

    def ShowModal(self):
        self._answer = _Ask.answers.pop(0)
        return wx.ID_CANCEL if self._answer is None else wx.ID_OK

    def GetValue(self):
        return self._answer


@pytest.fixture
def ask(monkeypatch):
    _Ask.answers, _Ask.asked = [], []
    monkeypatch.setattr(wx, "TextEntryDialog", _Ask)
    return _Ask


def _picker(monkeypatch, path):
    class Picker:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ShowModal(self):
            return wx.ID_OK if path else wx.ID_CANCEL

        def GetPath(self):
            return str(path)
    monkeypatch.setattr(wx, "FileDialog", Picker)


def test_each_built_in_scheme_keeps_its_own_changes(dialog):
    select_event(dialog, se.FINISHED)
    dialog.sound.SetStringSelection("None")
    dialog._sound_chosen()
    dialog.scheme.SetStringSelection("Wood")
    dialog._scheme_chosen()
    assert "Reply finished: Rise" in dialog.events.GetItems()
    assert dialog.events_label.GetLabelText() == "Sound events (23 of 23 events have a sound):"
    dialog.scheme.SetStringSelection("Glass")
    dialog._scheme_chosen()
    assert "Reply finished: None" in dialog.events.GetItems()
    assert dialog.events_label.GetLabelText() == "Sound events (22 of 23 events have a sound):"
    settings = dialog.get_settings()
    assert settings.sound_scheme == "glass"
    assert settings.sound_schemes == {"glass": {"events": {"ReplyFinished": "none"}}}


def test_a_new_scheme_starts_silent_and_offers_every_tone(dialog, ask, env):
    ask.answers = ["  ", "Glass", "Quiet"]  # empty and taken names are asked again
    dialog.on_new()
    assert [title for title, _ in ask.asked] == ["New Sound Scheme"] * 3
    assert any("Type a name" in str(b) for b in env["boxes"])
    assert any("already a scheme called Glass" in str(b) for b in env["boxes"])
    assert dialog.scheme.GetStringSelection() == "Quiet"
    assert dialog.events.GetString(0) == "Tool ran: None"
    assert "Rise (Soft bell)" in dialog.sound.GetItems()
    settings = dialog.get_settings()
    assert settings.sound_scheme.startswith("user-")
    assert settings.sound_schemes[settings.sound_scheme]["name"] == "Quiet"


def test_copy_rename_delete_and_reset(dialog, ask, env):
    dialog.scheme.SetStringSelection("Soft bell")
    dialog._scheme_chosen()
    ask.answers = [None]
    dialog.on_copy()  # cancelled
    assert [s.name for s in dialog._schemes] == ["Glass", "Soft bell", "Wood"]
    ask.answers = ["Bells"]
    dialog.on_copy()
    assert ask.asked[-1] == ("Copy Sound Scheme", "Soft bell copy")
    assert dialog.current().events["ToolRan"] == "builtin:softbell/tool"  # pinned to its tone
    ask.answers = ["Chimes"]
    dialog.on_rename()
    assert dialog.scheme.GetStringSelection() == "Chimes"
    dialog.on_delete()
    assert env["boxes"][-1] == ("Delete the sound scheme Chimes and its sound files? They're "
                                "deleted when you choose OK in Sounds.")
    assert dialog.scheme.GetStringSelection() == "Glass"
    assert [s.name for s in dialog._schemes] == ["Glass", "Soft bell", "Wood"]
    select_event(dialog, se.TOOL)
    dialog.sound.SetStringSelection("None")
    dialog._sound_chosen()
    dialog.on_reset()
    assert dialog.events.GetString(0) == "Tool ran: Tick"
    assert dialog.get_settings().sound_schemes == {}


def test_a_built_in_scheme_cant_be_renamed_or_deleted(dialog, ask):
    dialog.on_rename()
    dialog.on_delete()
    assert ask.asked == [] and [s.name for s in dialog._schemes] == ["Glass", "Soft bell", "Wood"]


def test_export_then_import_brings_the_scheme_back(dialog, env, tmp_path, monkeypatch):
    select_event(dialog, se.TOOL)
    dialog.sound.SetStringSelection("Stop")
    dialog._sound_chosen()
    target = tmp_path / "Glass"
    _picker(monkeypatch, target)
    said = []
    dialog._say = lambda text, **k: said.append(text)
    dialog.on_export()
    exported = tmp_path / "Glass.tcpsounds"
    assert exported.is_file() and said[-1] == "Exported Glass."
    _picker(monkeypatch, exported)
    dialog.on_import()
    assert dialog.scheme.GetStringSelection() == "Glass 2"
    assert said[-1] == "Added the sound scheme Glass 2. Choose OK to keep it."
    assert dialog.events.GetString(0) == "Tool ran: Stop (Glass)"


def test_importing_another_apps_scheme_says_whose_it_is(dialog, env, tmp_path, monkeypatch):
    import zipfile
    path = tmp_path / "mail.tcpsounds"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("scheme.json", json.dumps({"format": 1, "name": "Mail", "events": {}}))
    _picker(monkeypatch, path)
    dialog.on_import()
    assert "for QuickMail, so The Chat Place can't use it" in env["boxes"][-1]
    assert [s.name for s in dialog._schemes] == ["Glass", "Soft bell", "Wood"]


def _ok(frame, monkeypatch, act):
    """File, Sounds with ``act(dialog)`` done in it, then OK."""
    def show(self):
        act(self)
        return wx.ID_OK
    monkeypatch.setattr(SoundsDialog, "ShowModal", show)
    frame.on_sounds()


def _cancel(frame, monkeypatch, act):
    def show(self):
        act(self)
        return wx.ID_CANCEL
    monkeypatch.setattr(SoundsDialog, "ShowModal", show)
    frame.on_sounds()


def test_remove_file_takes_it_off_the_event_and_deletes_it_on_ok(frame, env, tmp_path, monkeypatch):
    good = make_wav(tmp_path / "mine.wav")
    _picker(monkeypatch, good)
    kept = platform_paths.custom_sounds_dir() / "glass" / "mine.wav"
    said = []

    def act(dialog):
        dialog._say = lambda text, **k: said.append(text)
        dialog._choose_file()
        assert kept.is_file() and dialog.remove_file.IsEnabled()
        dialog.on_remove_file()
        assert env["boxes"][-1] == ("Remove the sound file mine.wav? Tool ran goes back to its "
                                    "built-in sound, and the file is deleted when you choose OK in Sounds.")
        assert kept.is_file()  # not yet
        assert dialog.events.GetString(0) == "Tool ran: Tick"
        assert not dialog.remove_file.IsEnabled()
        assert "Your file, mine.wav" not in dialog.sound.GetItems()  # not offered again
        assert said[-1] == "Removed mine.wav."
    _ok(frame, monkeypatch, act)
    assert not kept.exists() and not kept.parent.exists()
    assert frame.speech.sound_schemes == {}


def test_remove_file_keeps_a_file_another_event_plays(frame, env, tmp_path, monkeypatch):
    good = make_wav(tmp_path / "mine.wav")
    _picker(monkeypatch, good)
    kept = platform_paths.custom_sounds_dir() / "glass" / "mine.wav"

    def act(dialog):
        dialog._choose_file()
        select_event(dialog, se.FINISHED)
        dialog.sound.SetStringSelection("Your file, mine.wav")
        dialog._sound_chosen()
        dialog.on_remove_file()
        assert env["boxes"][-1] == ("Remove the sound file mine.wav from Reply finished? It goes "
                                    "back to its built-in sound. Tool ran still uses the file, so "
                                    "it's kept.")
    _ok(frame, monkeypatch, act)
    assert kept.is_file()
    assert frame.speech.sound_schemes == {"glass": {"events": {"ToolRan": "custom:mine.wav"}}}


def test_remove_file_then_cancel_deletes_nothing(frame, env, tmp_path, monkeypatch):
    frame.speech.sound_schemes = {"glass": {"events": {"ToolRan": "custom:mine.wav"}}}
    kept = platform_paths.custom_sounds_dir() / "glass" / "mine.wav"
    kept.parent.mkdir(parents=True)
    make_wav(kept)
    _cancel(frame, monkeypatch, lambda dialog: dialog.on_remove_file())
    assert kept.is_file()


def test_deleting_a_scheme_deletes_its_folder_on_ok(frame, env, monkeypatch):
    sid = "user-" + "d" * 32
    frame.speech.sound_schemes = {sid: {"name": "Mine", "events": {"ToolRan": "custom:m.wav"}}}
    frame.speech.sound_scheme = sid
    folder = platform_paths.custom_sounds_dir() / sid
    folder.mkdir(parents=True)
    make_wav(folder / "m.wav")
    _cancel(frame, monkeypatch, lambda dialog: dialog.on_delete())
    assert (folder / "m.wav").is_file()  # Cancel takes the delete back
    _ok(frame, monkeypatch, lambda dialog: dialog.on_delete())
    assert not folder.exists() and sid not in frame.speech.sound_schemes
    assert frame.speech.sound_scheme == "glass"


def test_settings_that_cant_be_saved_are_taken_back_with_their_files(frame, env, tmp_path, monkeypatch):
    """A failed save is a Cancel: the settings in use and the files on disk keep agreeing."""
    sid = "user-" + "d" * 32
    frame.speech.sound_schemes = {sid: {"name": "Mine", "events": {"ToolRan": "custom:m.wav"}}}
    frame.speech.sound_scheme = sid
    before = frame.speech
    folder = platform_paths.custom_sounds_dir() / sid
    folder.mkdir(parents=True)
    make_wav(folder / "m.wav")
    _picker(monkeypatch, make_wav(tmp_path / "new.wav"))

    def fail(self, *a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(SpeechSettings, "save", fail)

    def act(dialog):
        dialog._choose_file()  # a new copy into the scheme
        dialog.on_delete()
    _ok(frame, monkeypatch, act)
    assert (folder / "m.wav").is_file() and not (folder / "new.wav").exists()
    assert frame.speech is before and sid in frame.speech.sound_schemes
    assert "your changes weren't kept" in env["boxes"][-1]


def test_cancel_deletes_a_file_choose_file_copied_into_an_existing_scheme(frame, env, tmp_path, monkeypatch):
    frame.speech.sound_schemes = {"glass": {"events": {"ToolRan": "custom:old.wav"}}}
    folder = platform_paths.custom_sounds_dir() / "glass"
    folder.mkdir(parents=True)
    make_wav(folder / "old.wav")
    make_wav(folder / "mine.wav")  # there before: choosing the same file again reuses it
    _picker(monkeypatch, make_wav(tmp_path / "new.wav"))
    _cancel(frame, monkeypatch, lambda dialog: dialog._choose_file())
    assert not (folder / "new.wav").exists() and (folder / "old.wav").is_file()
    _picker(monkeypatch, str(folder / "mine.wav"))
    _cancel(frame, monkeypatch, lambda dialog: dialog._choose_file())
    assert (folder / "mine.wav").is_file()  # not written by this dialog, so not deleted


def test_copy_scheme_that_cant_copy_its_files_isnt_made(frame, env, ask, monkeypatch):
    frame.speech.sound_schemes = {"wood": {"events": {"ToolRan": "custom:m.wav"}}}
    frame.speech.sound_scheme = "wood"
    folder = platform_paths.custom_sounds_dir() / "wood"
    folder.mkdir(parents=True)
    make_wav(folder / "m.wav")
    real_copy = se.shutil.copyfile

    def fail(src, dst, *a, **k):
        real_copy(src, dst)  # part-way: the file lands, then the copy fails
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(se.shutil, "copyfile", fail)
    ask.answers = ["Bells"]

    def act(dialog):
        dialog.on_copy()
        assert [s.name for s in dialog._schemes] == ["Glass", "Soft bell", "Wood"]
        assert "couldn't be copied" in env["boxes"][-1]
    _ok(frame, monkeypatch, act)
    assert sorted(p.name for p in platform_paths.custom_sounds_dir().iterdir()) == ["wood"]
    assert (folder / "m.wav").is_file()


@pytest.mark.parametrize("ok", [True, False])
def test_a_gallery_update_deletes_the_old_files_on_ok_and_the_new_on_cancel(frame, env, monkeypatch, ok):
    sid = "user-" + "c" * 32
    folder = platform_paths.custom_sounds_dir() / sid
    folder.mkdir(parents=True)
    make_wav(folder / "old.wav")
    make_wav(folder / "knock.wav", seconds=0.1)  # the new version's knock.wav has other bytes
    frame.speech.sound_schemes = {sid: {"name": "My bells", "events": {
        "ToolRan": "custom:old.wav", "NeedsYou": "custom:knock.wav"},
        "source": {"gallery": "bells", "sha256": "0" * 64}}}
    wood = platform_paths.custom_sounds_dir() / "wood"
    wood.mkdir()
    make_wav(wood / "knock.wav")
    new = se.built_in_scheme("wood")
    new.events = {"NeedsYou": "custom:knock.wav"}
    exported = se.export(new)

    def act(dialog):
        mine = next(s for s in dialog._schemes if s.id == sid)
        entry = _gallery_entry(exported, "bells", "Bells")
        _browse_and_add(dialog, monkeypatch, _FakeGallery([entry], exported))
        assert "sound files only the old version used are deleted when you choose OK in Sounds" in env["boxes"][-1]
        assert mine.events["NeedsYou"] == "custom:knock 2.wav"
    (_ok if ok else _cancel)(frame, monkeypatch, act)
    names = sorted(p.name for p in folder.iterdir())
    assert names == (["knock 2.wav"] if ok else ["knock.wav", "old.wav"])


def test_remove_file_counts_another_event_using_it_in_another_case(frame, env, monkeypatch):
    frame.speech.sound_schemes = {"glass": {"events": {"ToolRan": "custom:mine.wav",
                                                       "ReplyFinished": "custom:MINE.wav"}}}
    folder = platform_paths.custom_sounds_dir() / "glass"
    folder.mkdir(parents=True)
    make_wav(folder / "mine.wav")
    _ok(frame, monkeypatch, lambda dialog: dialog.on_remove_file())
    assert "Reply finished still uses the file, so it's kept." in env["boxes"][-1]
    assert (folder / "mine.wav").is_file()


def test_reset_deletes_a_built_in_schemes_files_on_ok(frame, env, monkeypatch):
    frame.speech.sound_schemes = {"wood": {"events": {"ToolRan": "custom:m.wav"}}}
    frame.speech.sound_scheme = "wood"
    folder = platform_paths.custom_sounds_dir() / "wood"
    folder.mkdir(parents=True)
    make_wav(folder / "m.wav")
    _ok(frame, monkeypatch, lambda dialog: dialog.on_reset())
    assert env["boxes"][-1] == ("Reset Wood to how it ships? Every event goes back to its built-in "
                                "sound. Your sound files for it are deleted when you choose OK in Sounds.")
    assert not folder.exists() and frame.speech.sound_schemes == {}


def test_copy_scheme_copies_its_files_into_the_copys_folder(frame, env, ask, monkeypatch):
    frame.speech.sound_schemes = {"wood": {"events": {"ToolRan": "custom:m.wav"}}}
    frame.speech.sound_scheme = "wood"
    folder = platform_paths.custom_sounds_dir() / "wood"
    folder.mkdir(parents=True)
    make_wav(folder / "m.wav")
    ask.answers = ["Bells"]
    _ok(frame, monkeypatch, lambda dialog: dialog.on_copy())
    sid = frame.speech.sound_scheme
    assert sid.startswith("user-") and frame.speech.sound_schemes[sid]["events"]["ToolRan"] == "custom:m.wav"
    assert (platform_paths.custom_sounds_dir() / sid / "m.wav").is_file()
    assert (folder / "m.wav").is_file()


def test_import_puts_its_files_in_the_new_schemes_folder(frame, env, tmp_path, monkeypatch):
    frame.speech.sound_schemes = {"wood": {"events": {"ToolRan": "custom:m.wav"}}}
    frame.speech.sound_scheme = "wood"
    folder = platform_paths.custom_sounds_dir() / "wood"
    folder.mkdir(parents=True)
    make_wav(folder / "m.wav")
    exported = tmp_path / "Wood.tcpsounds"
    exported.write_bytes(se.export(se.find(se.load_schemes(frame.speech.sound_schemes), "wood")))
    _picker(monkeypatch, exported)
    _ok(frame, monkeypatch, lambda dialog: dialog.on_import())
    sid = frame.speech.sound_scheme
    assert sid.startswith("user-")
    assert sorted(p.name for p in (platform_paths.custom_sounds_dir() / sid).iterdir()) == ["m.wav"]


def test_an_import_cancelled_takes_its_folder_with_it(frame, env, tmp_path, monkeypatch):
    frame.speech.sound_schemes = {"wood": {"events": {"ToolRan": "custom:m.wav"}}}
    folder = platform_paths.custom_sounds_dir() / "wood"
    folder.mkdir(parents=True)
    make_wav(folder / "m.wav")
    exported = tmp_path / "Wood.tcpsounds"
    exported.write_bytes(se.export(se.find(se.load_schemes(frame.speech.sound_schemes), "wood")))
    _picker(monkeypatch, exported)
    _cancel(frame, monkeypatch, lambda dialog: dialog.on_import())
    assert sorted(p.name for p in platform_paths.custom_sounds_dir().iterdir()) == ["wood"]


class _FakeGallery:
    def __init__(self, entries=None, data=b"", problem=None):
        self.entries, self.data, self.problem = entries or [], data, problem

    def schemes(self):
        if self.problem:
            raise self.problem
        return self.entries

    def download(self, entry):
        return self.data


def _gallery_entry(data, sid="rain", name="Rain"):
    import hashlib
    from theideaplace_appkit import soundgallery
    sha = hashlib.sha256(data).hexdigest()
    return soundgallery.Entry(sid, name, "Someone", "CC0-1.0", "Soft.", f"{sid}-{sha[:16]}.tcpsounds",
                              len(data), sha, 2)


def _gallery_dialog(frame, schemes, gallery):
    from thechatplace.ui.sound_gallery_dialog import SoundGalleryDialog
    said = []
    dialog = SoundGalleryDialog(frame, schemes, lambda: gallery, lambda text, **k: said.append(text))
    return dialog, said


def test_the_gallery_lists_and_says_what_you_have(frame):
    entry = _gallery_entry(b"x")
    mine = se.Scheme("user-" + "a" * 32, "Rain", False, source={"gallery": "rain", "sha256": "f" * 64})
    dialog, said = _gallery_dialog(frame, se.load_schemes({}) + [mine], _FakeGallery([entry]))
    try:
        assert pump(lambda: dialog.list.GetCount() == 1)
        assert dialog.list.GetString(0) == ("Rain, by Someone. 2 of 23 events have a sound. "
                                            "Update available.")
        assert dialog.details.GetValue() == "Soft. By Someone. Licence: CC0-1.0."
        assert dialog.add.GetLabel() == "&Update" and dialog.add.IsEnabled()
        assert said == ["Loading the sound scheme gallery…"]
    finally:
        dialog.Destroy()


def test_the_gallery_says_when_it_cant_be_reached(frame):
    from theideaplace_appkit import soundgallery
    dialog, said = _gallery_dialog(frame, se.load_schemes({}), _FakeGallery(problem=soundgallery.Unreachable()))
    try:
        assert pump(lambda: len(said) == 2)
        assert said[-1] == soundgallery.UNREACHABLE
        assert dialog.status.GetLabel() == soundgallery.UNREACHABLE
        assert dialog.list.GetCount() == 0 and not dialog.add.IsEnabled()
    finally:
        dialog.Destroy()


def test_adding_from_the_gallery_remembers_where_it_came_from(frame, dialog, monkeypatch):
    exported = se.export(se.built_in_scheme("wood"))
    entry = _gallery_entry(exported, "woody", "Woody")
    dialog._make_gallery = lambda: _FakeGallery([entry], exported)
    from thechatplace.ui import sound_gallery_dialog

    class Shown(sound_gallery_dialog.SoundGalleryDialog):
        def ShowModal(self):
            assert pump(lambda: self.list.GetCount() == 1)
            self.on_add()
            assert pump(lambda: self.result is not None)
            return wx.ID_OK
    monkeypatch.setattr(sound_gallery_dialog, "SoundGalleryDialog", Shown)
    dialog.on_browse()
    added = dialog.current()
    assert added.name == "Wood 2" and added.source == {"gallery": "woody", "sha256": entry.sha256}
    saved = dialog.get_settings().sound_schemes[added.id]
    assert saved["source"]["gallery"] == "woody"


def _browse_and_add(dialog, monkeypatch, gallery):
    from thechatplace.ui import sound_gallery_dialog
    dialog._make_gallery = lambda: gallery

    class Shown(sound_gallery_dialog.SoundGalleryDialog):
        def ShowModal(self):
            assert pump(lambda: self.list.GetCount() == len(gallery.entries))
            self.on_add()
            assert pump(lambda: self.result is not None)
            return wx.ID_OK
    monkeypatch.setattr(sound_gallery_dialog, "SoundGalleryDialog", Shown)
    dialog.on_browse()


@pytest.mark.parametrize("answer", [wx.YES, wx.NO])
def test_an_update_from_the_gallery_asks_and_keeps_the_scheme_itself(dialog, env, monkeypatch, answer):
    exported = se.export(se.built_in_scheme("softbell"))
    entry = _gallery_entry(exported, "bells", "Bells")
    mine = se.Scheme("user-" + "c" * 32, "My bells", False, {"ToolRan": "none"},
                     source={"gallery": "bells", "sha256": "0" * 64})
    dialog._schemes.append(mine)
    monkeypatch.setattr(wx, "MessageBox", lambda *a, **k: env["boxes"].append(a[0]) or answer)
    _browse_and_add(dialog, monkeypatch, _FakeGallery([entry], exported))
    assert "Replace your copy of My bells" in env["boxes"][-1]
    if answer == wx.YES:
        assert mine.id in [s.id for s in dialog._schemes] and mine.name == "My bells"
        assert mine.source["sha256"] == entry.sha256
        assert mine.events["ToolRan"] == "builtin:softbell/tool"
    else:
        assert mine.source["sha256"] == "0" * 64 and mine.events == {"ToolRan": "none"}
    assert len([s for s in dialog._schemes if not s.built_in]) == 1


def test_a_failed_download_says_to_try_add_again(frame):
    from theideaplace_appkit import soundgallery

    class Failing(_FakeGallery):
        def download(self, entry):
            raise soundgallery.Unreachable()
    entry = _gallery_entry(b"x")
    dialog, said = _gallery_dialog(frame, se.load_schemes({}), Failing([entry]))
    try:
        assert pump(lambda: dialog.list.GetCount() == 1)
        dialog.add.SetFocus()
        dialog.on_add()
        assert pump(lambda: said and "Add again" in said[-1])
        assert said[-1] == "Rain couldn't be downloaded. Check your connection, then choose Add again."
        assert dialog.add.IsEnabled() and dialog.result is None
    finally:
        dialog.Destroy()


def test_reload_waits_for_a_download(frame):
    import threading
    gate = threading.Event()

    class Slow(_FakeGallery):
        def download(self, entry):
            gate.wait(5)
            return b"x"
    entry = _gallery_entry(b"x")
    dialog, said = _gallery_dialog(frame, se.load_schemes({}), Slow([entry]))
    try:
        assert pump(lambda: dialog.list.GetCount() == 1)
        dialog.on_add()
        dialog.on_reload()
        assert said[-1] == "Wait for the download to finish."
        gate.set()
        assert pump(lambda: dialog.result is not None)
    finally:
        gate.set()
        dialog.Destroy()


def test_an_import_that_leaves_events_silent_is_shown_not_only_said(dialog, env, tmp_path, monkeypatch):
    import zipfile
    path = tmp_path / "gaps.tcpsounds"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("scheme.json", json.dumps({"format": 1, "app": "thechatplace", "name": "Gaps",
                                               "events": {"ToolRan": "custom:gone.wav"}}))
    _picker(monkeypatch, path)
    dialog.on_import()
    assert "1 event had a sound file that was missing" in env["boxes"][-1]
    assert dialog.scheme.GetStringSelection() == "Gaps"


def test_no_more_than_100_schemes_of_your_own(dialog, env, ask):
    dialog._schemes += [se.Scheme("user-" + f"{i:032x}", f"S{i}", False) for i in range(se.MAX_USER_SCHEMES)]
    dialog.on_new()
    assert ask.asked == [] and "the most there can be" in env["boxes"][-1]


def test_the_events_lists_name_says_how_many_have_a_sound(dialog):
    assert dialog._events_name() == "Sound events, 23 of 23 events have a sound"
    select_event(dialog, se.TOOL)
    dialog.sound.SetStringSelection("None")
    dialog._sound_chosen()
    assert dialog._events_name() == "Sound events, 22 of 23 events have a sound"
    assert dialog.events.GetName() == "Sound events, 22 of 23 events have a sound"


# ---------------------------------------------------------------- Help, Sound Tutorial

def tutorial_dialog(frame, said=None):
    return SoundTutorialDialog(frame, frame.speech, frame._sound_player,
                               say=(said.append if said is not None else None))


def key_event(code, kind=wx.wxEVT_KEY_DOWN, obj=None, shift=False):
    event = wx.KeyEvent(kind)
    event.SetKeyCode(code)
    event.SetShiftDown(shift)
    if obj is not None:
        event.SetEventObject(obj)
    return event


def pick(dialog, event_id):
    dialog.events.SetSelection([e.id for e in se.EVENTS].index(event_id))


def test_help_has_sound_tutorial_after_keyboard_shortcuts(frame):
    bar = frame.GetMenuBar()
    help_menu = bar.GetMenu(bar.FindMenu("Help"))
    labels = [item.GetItemLabel() for item in help_menu.GetMenuItems()]
    at = labels.index("Sound &Tutorial...")
    assert labels[at - 1].startswith("&Keyboard Shortcuts")
    assert help_menu.FindItemByPosition(at).GetAccel() is None  # no shortcut of its own


def test_sound_tutorial_lists_event_names_only_on_the_first(frame):
    frame.speech.sound_scheme = "wood"
    dialog = tutorial_dialog(frame)
    try:
        assert dialog.GetTitle() == "Sound Tutorial"
        assert dialog.events.GetItems() == [e.label for e in se.EVENTS]
        assert len(dialog.events.GetItems()) == 23
        assert dialog.events.GetSelection() == 0
        assert dialog.events.GetName() == "Sound events"
        assert "in the Wood scheme" in dialog.intro.GetLabel().replace("\n", " ")
        assert dialog.GetEscapeId() == wx.ID_CANCEL and dialog.close.GetId() == wx.ID_CANCEL
    finally:
        dialog.Destroy()


def test_sound_tutorial_space_plays_the_event_in_the_active_scheme(frame, env):
    frame.speech.sound_scheme = "wood"
    frame.speech.sound_volume = 60
    said = []
    dialog = tutorial_dialog(frame, said)
    try:
        pick(dialog, se.FINISHED)
        dialog._on_events_key(key_event(wx.WXK_SPACE))
        path, volume = env["previews"][-1]
        assert Path(path).parts[-2:] == ("wood", "reply.wav") and volume == 0.6
        assert said == []  # a sound that plays needs no words
        before = len(env["previews"])
        dialog._on_events_key(key_event(wx.WXK_SPACE, shift=True))
        dialog._on_events_key(key_event(ord("A")))
        assert len(env["previews"]) == before
    finally:
        dialog.Destroy()


def test_sound_tutorial_enter_plays_and_stays_open(frame, env):
    dialog = tutorial_dialog(frame)
    try:
        pick(dialog, se.TOOL)
        enter = key_event(wx.WXK_RETURN, wx.wxEVT_CHAR_HOOK, obj=dialog.events)
        dialog._on_char_hook(enter)
        assert Path(env["previews"][-1][0]).parts[-2:] == ("glass", "tool.wav")
        assert not enter.GetSkipped()  # taken here, so no button gets it
    finally:
        dialog.Destroy()


def test_sound_tutorial_enter_on_close_is_left_to_the_dialog(frame, env, monkeypatch):
    dialog = tutorial_dialog(frame)
    try:
        # Hidden windows can't be relied on to take focus, so say where it is.
        monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: dialog.close))
        other = key_event(wx.WXK_RETURN, wx.wxEVT_CHAR_HOOK, obj=dialog.close)
        dialog._on_char_hook(other)
        assert env["previews"] == []
        # A Mac presses a focused button only with Space, so there the hook presses Close
        # itself (press_focused_button_on_a_mac); elsewhere the key goes on to the button.
        assert other.GetSkipped() is (wx.Platform != "__WXMAC__")
    finally:
        dialog.Destroy()


@pytest.mark.parametrize("ref", ["builtin:wood/reply", "custom:gone.wav"])
def test_sound_tutorial_plays_what_the_event_plays(frame, env, ref):
    """The same file, and the same fallback, as the event itself in a scheme of your own."""
    frame.speech.sound_scheme = "user-" + "c" * 32
    frame.speech.sound_schemes = {frame.speech.sound_scheme: {
        "name": "Mine", "events": {"ReplyFinished": ref}}}
    frame._sound(se.FINISHED)
    player = env["players"][0]
    assert env["sounds"] == [se.FINISHED]
    dialog = tutorial_dialog(frame)
    try:
        pick(dialog, se.FINISHED)
        dialog.play()
        assert Path(env["previews"][-1][0]) == Path(player.last["path"])
        assert player.preview_fallback == player.last["fallback"]
        if ref.startswith("builtin"):
            assert Path(player.last["path"]).parts[-2:] == ("wood", "reply.wav")
        else:
            assert Path(player.last["fallback"]).name == "reply.wav"
    finally:
        dialog.Destroy()


def test_the_tutorials_words_come_from_its_own_window(frame, env, monkeypatch):
    """VoiceOver drops what's posted from a window behind the one you're in, so the note is
    posted from the dialog's. Elsewhere queue_announcement declines, as here, and speech goes
    as before."""
    from thechatplace.ui import main_frame
    handles = []
    monkeypatch.setattr(main_frame.mac_a11y, "queue_announcement",
                        lambda handle, text: handles.append(handle) or False)
    frame.speech.engine = "auto"
    frame.speech.sound_schemes = {"glass": {"events": {"ToolRan": se.NONE_REF}}}

    # A hidden window has no native handle on Linux, so the dialog's is made up.
    monkeypatch.setattr(SoundTutorialDialog, "GetHandle", lambda self: 4242)

    def press_space(self):
        self._on_events_key(key_event(wx.WXK_SPACE))
        return wx.ID_CANCEL
    monkeypatch.setattr(SoundTutorialDialog, "ShowModal", press_space)
    frame.on_sound_tutorial()
    assert handles == [4242]
    assert "No sound." in env["feedback"]  # declined, so spoken as before


def test_sound_tutorial_plays_with_sounds_off_and_muted(frame, env):
    frame.speech.sounds_on = False
    frame.speech.sounds_muted = True
    dialog = tutorial_dialog(frame)
    try:
        dialog.play()
        assert Path(env["previews"][-1][0]).parts[-2:] == ("glass", "tool.wav")
        assert env["sounds"] == []  # a preview, not an event sound
    finally:
        dialog.Destroy()


def test_sound_tutorial_says_no_sound_and_stops_the_last(frame, env):
    frame.speech.sound_schemes = {"glass": {"events": {"ReplyFinished": se.NONE_REF}}}
    said = []
    dialog = tutorial_dialog(frame, said)
    try:
        dialog.play()  # Tool ran plays
        env["previews"].clear()
        pick(dialog, se.FINISHED)
        dialog.play()
        assert env["previews"] == [] and said == ["No sound."]
        assert "stop_preview" in env["sound_stops"]
    finally:
        dialog.Destroy()


def test_sound_tutorial_says_a_missing_file_and_plays_the_built_in(frame, env):
    frame.speech.sound_schemes = {"glass": {"events": {"ToolRan": se.custom_ref("gone.wav")}}}
    said = []
    dialog = tutorial_dialog(frame, said)
    try:
        dialog.play()
        assert Path(env["previews"][-1][0]).name == "gone.wav"
        assert Path(env["players"][0].preview_fallback).name == "tool.wav"
        assert said == ["Its sound file is missing, so the built-in sound plays."]
    finally:
        dialog.Destroy()


def test_sound_tutorial_and_real_playback_use_the_active_schemes_folder(frame, env):
    sid = "user-" + "e" * 32
    frame.speech.sound_scheme = sid
    frame.speech.sound_schemes = {sid: {"name": "Mine", "events": {
        "ToolRan": se.custom_ref("m.wav"), "ReplyFinished": se.custom_ref("m.wav")}}}
    folder = platform_paths.custom_sounds_dir() / sid
    folder.mkdir(parents=True)
    make_wav(folder / "m.wav")
    make_wav(platform_paths.custom_sounds_dir() / "other.wav")  # the old shared folder: unused
    said = []
    dialog = tutorial_dialog(frame, said)
    try:
        dialog.play()
        assert Path(env["previews"][-1][0]) == folder / "m.wav" and said == []
    finally:
        dialog.Destroy()
    frame._sound(se.FINISHED)
    assert Path(env["players"][0].last["path"]) == folder / "m.wav"


def test_on_sound_tutorial_says_even_with_announcements_silent(frame, env, monkeypatch):
    frame.speech.sound_schemes = {"glass": {"events": {"ToolRan": se.NONE_REF}}}
    frame.speech.announce = "silent"

    def press_space(self):
        self._on_events_key(key_event(wx.WXK_SPACE))
        return wx.ID_CANCEL
    monkeypatch.setattr(SoundTutorialDialog, "ShowModal", press_space)
    frame.on_sound_tutorial()
    assert "No sound." in env["feedback"]
    assert frame.speech.sound_schemes == {"glass": {"events": {"ToolRan": se.NONE_REF}}}


def test_closing_the_tutorial_without_playing_makes_no_player(frame, env, monkeypatch):
    monkeypatch.setattr(SoundTutorialDialog, "ShowModal", lambda self: wx.ID_CANCEL)
    frame.on_sound_tutorial()
    assert env["players"] == []
