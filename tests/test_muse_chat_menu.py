"""Every Chat menu command against Muse Code chats (dev-notes/muse-code-chat-menu.md).

Each command runs against four Muse cases: a chat started here that is idle,
one with a running turn, one waiting for an answer, and a chat started in
Muse Code itself. The invariants hold for every pair: nothing raises, no
Claude turn starts, a chat started elsewhere never reaches Muse's execution
host, and nothing said about a Muse chat names Claude or Codex as if it were
that chat's provider. EXPECTED then pins what each command does, so a change
in behaviour, or a new Chat menu command without an expectation, fails here.

Synthetic content only; no Muse, Claude or Codex process is started.
"""
import dataclasses

import pytest

wx = pytest.importorskip("wx")
import fake_env
from test_muse_execution import NATIVE_ID, Execution, approval
from test_muse_history_ui import _MuseHistoryConnection, _muse_wire_session

from thechatplace import commands

FOREIGN_ID = "muse-foreign-1"
CASES = ("idle", "running", "waiting", "foreign")


def chat_menu_ids():
    chat = next(menu for menu in commands.MENU_BAR if menu.label == "&Chat")
    return [item.id for item in commands.walk(chat.items) if isinstance(item, commands.Command)]


class World:
    """The window, its fakes, and what each command did."""

    def __init__(self, frame, execution, owned, foreign):
        self.frame, self.execution = frame, execution
        self.owned, self.foreign = owned, foreign
        self.said, self.dialogs, self.claude_turns = [], [], []

    def reset(self):
        self.said.clear()
        self.dialogs.clear()
        self.claude_turns.clear()
        self.execution.messages.clear()


@pytest.fixture
def world(tmp_path, monkeypatch, app):
    from thechatplace.own_store import OwnSession, OwnSessionStore
    from thechatplace.providers.claude import ClaudeConversations
    from thechatplace.providers.muse import MuseProvider, session_from_muse
    from thechatplace.session_controller import SessionController
    from thechatplace.ui.main_frame import MainFrame

    fake_env.install(tmp_path, monkeypatch.setattr)
    frame = MainFrame(store=OwnSessionStore(tmp_path / "own.json"), check_updates_at_start=False)
    frame._list_timer.Stop()
    execution = Execution()
    # A chat started here reads its history through the execution host.
    answer_read = execution.request
    def request(method, params):
        result = answer_read(method, params)
        if method == "session/read" and not params.get("excludeItems"):
            result["history"] = {"mode": "inline", "snapshot": None, "items": [
                {"kind": "userMessage", "itemId": "u", "revision": 1, "status": "completed",
                 "text": "Synthetic question."},
                {"kind": "agentMessage", "itemId": "a", "revision": 1, "status": "completed",
                 "text": "Synthetic reply."}]}
        return result
    execution.request = request
    provider = MuseProvider(connection=_MuseHistoryConnection(), execution=execution)
    provider.set_owned_persistence(lambda record: frame.store.update(
        record.cli_session_id, provider="muse", provider_options=dict(record.provider_options)))
    frame.sessions = SessionController([ClaudeConversations(), provider])
    record = OwnSession(NATIVE_ID, "Muse chat here", str(tmp_path), provider="muse", started=True)
    frame.store.add(record)
    owned = record.to_info()
    foreign = session_from_muse(_muse_wire_session(FOREIGN_ID))
    snapshot = dataclasses.replace(frame._snapshot, sessions=[owned, foreign])
    frame._apply_snapshot(snapshot, [], {}, False)
    w = World(frame, execution, owned, foreign)

    # What is said, and which dialogs open (each closes itself unanswered).
    monkeypatch.setattr(frame, "_feedback", lambda text, *a, **k: w.said.append(str(text)))
    monkeypatch.setattr(frame, "_say", lambda text, *a, **k: w.said.append(str(text)))
    def modal(self, *a, **k):
        w.dialogs.append(type(self).__name__)
        return wx.ID_CANCEL
    for cls in (wx.Dialog, wx.MessageDialog, wx.SingleChoiceDialog, wx.TextEntryDialog,
                wx.FileDialog, wx.DirDialog):
        monkeypatch.setattr(cls, "ShowModal", modal)
    def box(message, *a, **k):
        w.dialogs.append("MessageBox")
        w.said.append(str(message))
        return wx.CANCEL
    monkeypatch.setattr(wx, "MessageBox", box)
    # A Claude turn starting for a Muse chat is the worst wrong route.
    monkeypatch.setattr(frame, "_start_turn", lambda *a, **k: w.claude_turns.append(a))
    # Background work runs at once; monitoring is tested in test_muse_ui.
    monkeypatch.setattr(frame._pool, "submit", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(frame, "_poll_muse_owned", lambda record: None)
    yield w
    frame.stop_timers()
    frame._pool.shutdown(wait=True)
    wx.GetApp().ProcessPendingEvents()
    frame.Destroy()
    wx.GetApp().ProcessPendingEvents()


def put_in_case(w, case):
    """Select and open the chat for ``case``, with Muse's state to match."""
    frame, execution = w.frame, w.execution
    info = w.foreign if case == "foreign" else w.owned
    execution.session.update(status="idle", activeTurnId=None)
    execution.approvals = []
    frame._muse_runtime.clear()
    frame._muse_approvals.clear()
    if case in ("running", "waiting"):
        turn = "019a84c2-a1f0-7e3b-9c1e-000000000001"
        execution.session.update(status="running" if case == "running" else "needsInput",
                                 activeTurnId=turn)
        record = frame.store.get(NATIVE_ID, provider="muse")
        record.provider_options["muse_command"] = {"command_id": turn, "turn_id": turn, "state": "running"}
        frame.sessions.capabilities(info)  # provider already registered
        pending = ()
        if case == "waiting":
            execution.approvals = [approval()]
            from thechatplace.providers.muse import approvals_from_muse
            pending = approvals_from_muse({"approvals": [approval()], "userInputs": []}, NATIVE_ID)
            for request in pending:
                frame._muse_approvals[(NATIVE_ID, request.request_id)] = request
        frame._muse_runtime[NATIVE_ID] = {"state": case, "pending": pending, "notice": ""}
    frame.open_session(info)
    assert fake_env.pump(lambda: frame._chat_loaded and not frame._reader_busy)
    frame._selected_session = lambda: info
    w.reset()
    return info


def run(w, command_id, case, text=""):
    info = put_in_case(w, case)
    w.frame.reply_text.SetValue(text)
    error = None
    try:
        w.frame._command_handlers()[command_id](wx.CommandEvent())
    except Exception as exc:  # recorded, then asserted on
        error = exc
    foreign_calls = [m for m, p in w.execution.messages if p.get("sessionId") == FOREIGN_ID]
    return {"error": error, "said": list(w.said), "dialogs": list(w.dialogs),
            "claude_turns": len(w.claude_turns), "execution": [m for m, _ in w.execution.messages],
            "foreign_calls": foreign_calls, "info": info}


#: The refusal a capability check gives a Muse chat.
NOT_YET = "isn't available for Muse Code chats yet"


def said(text):
    return ("said", text)


def dialog(name):
    return ("dialog", name)


def sent(method):
    return ("exec", method)


def not_said(text, reason):
    """Known wrong (dev-notes/muse-code-chat-menu.md step 2): ``text`` is said
    now and must not be. Run as a strict xfail, so a fix makes it fail until
    the expectation is updated to the new behaviour."""
    return ("not_said", text, reason)


def every(outcome):
    return {case: outcome for case in CASES}


#: What a chat started in Muse Code itself is told: true for every command,
#: where "not available yet" would suggest it might be one day.
READ_ONLY = "was started in Muse Code, so it's read-only here. Do that in Muse Code."


def owned(outcome, foreign=None):
    """``outcome`` for the three chats started here, the read-only refusal
    (or ``foreign``) for the chat started in Muse Code."""
    return {"idle": outcome, "running": outcome, "waiting": outcome,
            "foreign": foreign or said(READ_ONLY)}


#: What each Chat menu command does for each Muse case. Change it only with
#: the behaviour (dev-notes/muse-code-chat-menu.md).
EXPECTED = {
    "answer": {**every(said("No session is waiting for an answer.")),
               "waiting": dialog("MuseApprovalDialog")},
    "ask_aside": owned(said(NOT_YET)),
    "stop_running_turn": {
        "idle": said("Nothing is running in Muse chat here."),
        "running": sent("turn/cancel"), "waiting": sent("turn/cancel"),
        "foreign": said(READ_ONLY)},
    "stop_background_task": owned(said("Muse Code chats have no background tasks to stop.")),
    "turn_status": {
        "idle": said("Muse chat here: Ready."),
        "running": said("Muse chat here: Muse Code is working."),
        "waiting": said("Muse chat here: Muse Code is waiting for you."),
        "foreign": said(READ_ONLY)},
    "insert_command": owned(said("command and skill picker isn't available for Muse Code chats")),
    "attach_files": owned(said(NOT_YET)),
    "prompts": every(dialog("PromptsDialog")),
    "send_and_save_prompt": owned(said("Type a message first."),
                                  said("read-only here. Reply in Muse Code.")),
    # Steer goes into a running turn; waiting for you, answer first.
    "steer": {**owned(said("Type a message first.")),
              "waiting": said("answer it first")},
    "send_reply_to_another_chat": every(dialog("RelayDialog")),
    "retry_from_here": owned(said(NOT_YET)),
    "branch_from_here": owned(said(NOT_YET)),
    "change_model": owned(dialog("SingleChoiceDialog")),
    "change_effort": owned(said(NOT_YET)),
    "change_permission_mode": owned(said("Muse Code chats always use Muse Code's own mode")),
    "chat_permissions": owned(dialog("ChatPermissionsDialog"),
                              said("was started in Muse Code, so it's read-only here. "
                                   "Muse Code decides what it may do.")),
    "chat_options": owned(said(NOT_YET)),
    "other_machines": owned(said(NOT_YET)),
}

#: The same commands with text in the reply box, where it changes the outcome.
EXPECTED_WITH_TEXT = {
    ("steer", "idle"): sent("turn/start"),
    ("steer", "running"): sent("turn/steer"),
    ("steer", "waiting"): said("answer it first"),
}

PAIRS = [(command_id, case) for command_id in chat_menu_ids() for case in CASES]


def check(result, expected):
    kind = expected[0]
    if kind == "said":
        assert any(expected[1] in text for text in result["said"]), result["said"]
    elif kind == "dialog":
        assert expected[1] in result["dialogs"], result["dialogs"]
    elif kind == "exec":
        assert expected[1] in result["execution"], result["execution"]
    else:
        assert not any(expected[1] in text for text in result["said"]), result["said"]


def known_wrong(expected):
    return [pytest.mark.xfail(strict=True, reason=expected[2])] if expected[0] == "not_said" else []


def test_every_chat_menu_command_has_a_muse_expectation():
    """A new Chat menu command needs a row here, as a new dialog needs a probe surface."""
    assert set(EXPECTED) == set(chat_menu_ids())
    assert all(set(row) == set(CASES) for row in EXPECTED.values())


@pytest.mark.parametrize("command_id,case", PAIRS)
def test_no_command_misroutes_a_muse_chat(world, command_id, case):
    result = run(world, command_id, case)
    assert result["error"] is None
    assert result["claude_turns"] == 0
    assert result["foreign_calls"] == []
    if case == "foreign":
        assert result["execution"] == []  # a chat started elsewhere never reaches execution


@pytest.mark.parametrize("command_id,case", [
    pytest.param(command_id, case, marks=known_wrong(EXPECTED[command_id][case]))
    for command_id, case in PAIRS])
def test_chat_menu_command_does_what_is_expected(world, command_id, case):
    check(run(world, command_id, case), EXPECTED[command_id][case])


@pytest.mark.parametrize("command_id,case", [
    pytest.param(command_id, case, marks=known_wrong(expected))
    for (command_id, case), expected in EXPECTED_WITH_TEXT.items()])
def test_chat_menu_command_with_text_typed(world, command_id, case):
    check(run(world, command_id, case, text="Synthetic follow-up"),
          EXPECTED_WITH_TEXT[(command_id, case)])


def test_steer_clears_the_reply_box_once_muse_takes_it(world):
    run(world, "steer", "running", text="Synthetic change of plan")
    assert world.frame.reply_text.GetValue() == ""
    assert "turn/steer" in [m for m, _ in world.execution.messages]


def test_a_refused_steer_puts_the_text_back_with_why(world):
    from thechatplace.providers.muse_connection import MuseRequestError
    world.execution.error = ("turn/steer", MuseRequestError(-32030))
    result = run(world, "steer", "running", text="Synthetic change of plan")
    assert world.frame.reply_text.GetValue() == "Synthetic change of plan"
    assert any("already ended" in text and "back in the reply box" in text for text in result["said"])


def test_change_model_switches_to_the_chosen_muse_model(world, monkeypatch):
    put_in_case(world, "idle")
    chooser = []
    def choose(title, prompt, choices, selection=None):
        chooser.append((title, prompt, list(choices), selection))
        return 0  # the first model, not this chat's
    monkeypatch.setattr(world.frame, "_choose", choose)
    world.frame._command_handlers()["change_model"](wx.CommandEvent())
    title, prompt, choices, selection = chooser[0]
    assert title == "Change Model" and "now synthetic-model-b" in prompt
    assert choices == ["synthetic-model-a", "synthetic-model-b (now)"] and selection == 1
    assert world.execution.session["modelId"] == "synthetic-model-a"
    assert any("now uses synthetic-model-a" in text for text in world.said), world.said
