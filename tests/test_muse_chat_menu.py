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
    "ask_aside": owned(dialog("AskAsideDialog")),
    "stop_running_turn": {
        "idle": said("Nothing is running in Muse chat here."),
        "running": sent("turn/cancel"), "waiting": sent("turn/cancel"),
        "foreign": said(READ_ONLY)},
    "stop_background_task": owned(said("No background work is running in Muse chat here.")),
    "turn_status": {
        "idle": said("Muse chat here: Ready."),
        "running": said("Muse chat here: Muse Code is working."),
        "waiting": said("Muse chat here: Muse Code is waiting for you."),
        "foreign": said(READ_ONLY)},
    "insert_command": owned(dialog("CommandPickerDialog")),
    "attach_files": owned(dialog("FileDialog")),
    "prompts": every(dialog("PromptsDialog")),
    "send_and_save_prompt": owned(said("Type a message first."),
                                  said("read-only here. Reply in Muse Code.")),
    # Steer goes into a running turn; waiting for you, answer first.
    "steer": {**owned(said("Type a message first.")),
              "waiting": said("answer it first")},
    "send_reply_to_another_chat": every(dialog("RelayDialog")),
    "retry_from_here": owned(said("Choose one of your own messages")),
    # The reply is selected when a chat opens, so Branch asks before copying.
    "branch_from_here": owned(said("Branch here?")),
    "change_model": owned(dialog("SingleChoiceDialog")),
    "change_effort": owned(dialog("SingleChoiceDialog")),
    "change_permission_mode": owned(dialog("SingleChoiceDialog")),
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


def test_change_effort_sets_the_chosen_muse_tier(world, monkeypatch):
    put_in_case(world, "idle")
    chooser = []
    def choose(title, prompt, choices, selection=None):
        chooser.append((title, prompt, list(choices), selection))
        return 0
    monkeypatch.setattr(world.frame, "_choose", choose)
    world.frame._command_handlers()["change_effort"](wx.CommandEvent())
    title, prompt, choices, selection = chooser[0]
    assert title == "Change Effort" and "now maximum" in prompt
    assert choices == ["low", "medium", "high", "maximum (now)"] and selection == 3
    assert any("effort is now low" in text for text in world.said), world.said


def test_insert_skill_puts_it_before_your_draft(world, monkeypatch):
    from thechatplace.ui import dialogs
    put_in_case(world, "idle")
    world.frame.reply_text.SetValue("Outline it briefly.")
    shown = []
    def pick(self):
        shown.append([row["name"] for row in self._all])
        self.chosen = next(row for row in self._all if row["name"] == "acme:deploy")
        return wx.ID_OK
    monkeypatch.setattr(dialogs.CommandPickerDialog, "ShowModal", pick)
    world.frame._command_handlers()["insert_command"](wx.CommandEvent())
    assert shown == [["synthetic-plan", "acme:deploy"]]
    assert world.frame.reply_text.GetValue() == "/acme:deploy Outline it briefly."


def test_a_side_chat_ask_aside_made_is_hidden_here(world):
    """Muse keeps each side chat as an ordinary chat; the window hides it."""
    put_in_case(world, "idle")
    world.frame._hide_muse_aside("019a84c2-a1f0-7e3b-9c1e-0000000000aa")
    assert "muse:019a84c2-a1f0-7e3b-9c1e-0000000000aa" in world.frame.hidden


def test_attached_files_go_with_the_next_muse_message(world, tmp_path, monkeypatch):
    from test_muse_input import png
    from thechatplace.providers.muse_input import MuseInput
    put_in_case(world, "idle")
    picture = png(tmp_path / "synthetic.png")
    monkeypatch.setattr(wx.FileDialog, "ShowModal", lambda self: wx.ID_OK)
    monkeypatch.setattr(wx.FileDialog, "GetPaths", lambda self: [str(picture)])
    world.frame._command_handlers()["attach_files"](wx.CommandEvent())
    sent = []
    real = world.frame.sessions.owned_action
    def spy(action, record, *args, **kwargs):
        if action == "send":
            sent.append(args[0])
        return real(action, record, *args, **kwargs)
    monkeypatch.setattr(world.frame.sessions, "owned_action", spy)
    world.frame.reply_text.SetValue("What is this?")
    world.frame.on_send()
    assert isinstance(sent[0], MuseInput) and len(sent[0].images) == 1
    assert "Attached image: synthetic.png" in str(sent[0])
    assert not world.frame._attachments.get(world.frame._attachment_key(world.owned))


def _choose_mode(world, monkeypatch, profile_label, answer=wx.YES):
    put_in_case(world, "idle")
    asked = []
    def choose(title, prompt, choices, selection=None):
        asked.append(("choose", title, prompt, list(choices), selection))
        return [c.replace(" (now)", "") for c in choices].index(profile_label)
    def box(message, caption="", style=0, *a, **k):
        asked.append(("box", caption, message, style))
        return answer
    monkeypatch.setattr(world.frame, "_choose", choose)
    monkeypatch.setattr(wx, "MessageBox", box)
    world.frame._command_handlers()["change_permission_mode"](wx.CommandEvent())
    return asked


def test_change_mode_offers_muses_profiles_and_switches(world, monkeypatch):
    asked = _choose_mode(world, monkeypatch, "Read only")
    _kind, title, prompt, choices, selection = asked[0]
    assert title == "Change Permission Mode" and "now Ask me" in prompt
    assert choices == ["Read only", "Ask me (now)", "Auto-review", "Unrestricted"] and selection == 1
    assert len(asked) == 1  # no warning for a profile you still review
    assert world.execution.session["permissionState"]["profileId"] == ":read-only"
    assert any("Read only" in text for text in world.said), world.said


@pytest.mark.parametrize("label,profile,warns", [
    ("Auto-review", ":auto-review", "an AI reviewer"),
    ("Unrestricted", ":unrestricted", "nothing asks you"),
])
def test_a_mode_without_your_review_warns_first_and_no_keeps_it(world, monkeypatch, label, profile, warns):
    asked = _choose_mode(world, monkeypatch, label, answer=wx.NO)
    kind, caption, message, style = asked[1]
    assert kind == "box" and warns in message and style & wx.NO_DEFAULT
    assert world.execution.session["permissionState"]["profileId"] == ":ask-me"
    asked = _choose_mode(world, monkeypatch, label, answer=wx.YES)
    assert world.execution.session["permissionState"]["profileId"] == profile


def test_chat_permissions_names_the_chats_profile_and_can_change_it(world):
    from thechatplace.ui.permission_hosts import MusePermissions
    put_in_case(world, "idle")
    record = world.frame.store.get(world.owned.cli_session_id, provider="muse")
    host = MusePermissions(world.frame, world.owned, record)
    assert host.mode_text() == "Muse Code: Ask me"
    assert host.can_change_mode()[0] is True


@pytest.mark.parametrize("answer,expected", [(wx.NO, False), (wx.YES, True)])
def test_confirm_muse_mode_says_the_chat_keeps_its_profile_on_no(world, monkeypatch, answer, expected):
    put_in_case(world, "idle")
    shown = []
    def box(message, caption="", style=0, *a, **k):
        shown.append((message, caption, style))
        return answer
    monkeypatch.setattr(wx, "MessageBox", box)
    assert world.frame._confirm_muse_mode("Muse chat here", ":unrestricted", "Ask me") is expected
    message, caption, style = shown[0]
    assert caption == "Change Permission Mode" and style & wx.NO_DEFAULT
    assert ("Muse chat here keeps Ask me." in world.said) is (not expected)
    assert world.frame._confirm_muse_mode("Muse chat here", ":read-only", "Ask me") is True
    assert len(shown) == 1  # a profile you still review asks nothing


def test_retry_from_here_opens_the_copy_with_your_message_to_change(world, monkeypatch):
    from thechatplace.transcript import USER
    from thechatplace.ui import dialogs
    put_in_case(world, "idle")
    message = next(m for m in world.frame._chat_messages if m.kind == USER)
    monkeypatch.setattr(world.frame, "_selected_message", lambda: message)
    made = []
    real = world.frame.sessions.owned_action
    def spy(action, record, *args, **kwargs):
        if action == "retry":
            made.append(args)
            from thechatplace.own_store import OwnSession
            return OwnSession("019a84c2-a1f0-7e3b-9c1e-0000000000f0", args[1], record.cwd,
                              provider="muse", started=True)
        return real(action, record, *args, **kwargs)
    monkeypatch.setattr(world.frame.sessions, "owned_action", spy)
    monkeypatch.setattr(dialogs.RewindDialog, "ShowModal", lambda self: wx.ID_OK)
    world.frame._command_handlers()["retry_from_here"](wx.CommandEvent())
    assert made == [(message.key, "Muse chat here (retry)")]
    copy_ = world.frame.store.get("019a84c2-a1f0-7e3b-9c1e-0000000000f0", provider="muse")
    assert copy_ is not None and copy_.forked_from == "Muse chat here"
    assert world.frame._codex_drafts[copy_.to_info().key] == message.text
    assert any("The original chat is kept" in text for text in world.said), world.said


@pytest.mark.parametrize("kind,retry,branch", [("user", True, True), ("assistant", False, True)])
def test_the_messages_menu_offers_retry_and_branch_for_a_muse_chat(world, kind, retry, branch):
    from thechatplace.transcript import ASSISTANT, USER
    put_in_case(world, "idle")
    wanted = USER if kind == "user" else ASSISTANT
    message = next(m for m in world.frame._chat_messages if m.kind == wanted)
    world.frame._selected_message = lambda: message
    menu = world.frame._message_menu()
    try:
        labels = {item.GetItemLabelText(): item.IsEnabled() for item in menu.GetMenuItems()}
    finally:
        menu.Destroy()
    assert labels.get("Retry from Here...") is retry
    assert labels.get("Branch from Here") is branch


@pytest.mark.parametrize("answer", [wx.YES, wx.NO])
def test_branch_from_here_asks_then_opens_the_copy(world, monkeypatch, answer):
    from thechatplace.own_store import OwnSession
    from thechatplace.transcript import ASSISTANT
    put_in_case(world, "idle")
    message = next(m for m in world.frame._chat_messages if m.kind == ASSISTANT)
    monkeypatch.setattr(world.frame, "_selected_message", lambda: message)
    asked, made = [], []
    def box(text, caption="", style=0, *a, **k):
        asked.append((text, caption, style))
        return answer
    monkeypatch.setattr(wx, "MessageBox", box)
    real = world.frame.sessions.owned_action
    def spy(action, record, *args, **kwargs):
        if action == "branch":
            made.append(args)
            return OwnSession("019a84c2-a1f0-7e3b-9c1e-0000000000f1", args[1], record.cwd,
                              provider="muse", started=True)
        return real(action, record, *args, **kwargs)
    monkeypatch.setattr(world.frame.sessions, "owned_action", spy)
    world.frame._command_handlers()["branch_from_here"](wx.CommandEvent())
    text, caption, style = asked[0]
    assert caption == "Branch from Here" and style & wx.NO_DEFAULT and "this chat is kept" in text
    copy_ = world.frame.store.get("019a84c2-a1f0-7e3b-9c1e-0000000000f1", provider="muse")
    if answer == wx.NO:
        assert made == [] and copy_ is None
        return
    assert made == [(message.key, "Muse chat here (branch)")]
    assert copy_.forked_from == "Muse chat here"
    assert world.frame._codex_drafts.get(copy_.to_info().key) is None  # nothing to change: a new message
    assert any("The original chat is kept" in t for t in world.said), world.said


@pytest.mark.parametrize("answer", [wx.YES, wx.NO])
def test_stop_background_task_chooses_confirms_and_stops(world, monkeypatch, answer):
    put_in_case(world, "running")
    tasks = [{"id": "task-1", "label": "powershell: synthetic-build --watch"}]
    calls = []
    real = world.frame.sessions.owned_action
    def spy(action, record, *args, **kwargs):
        if action == "tasks":
            return tasks
        if action == "stop_task":
            calls.append(args)
            return "stopping"
        return real(action, record, *args, **kwargs)
    monkeypatch.setattr(world.frame.sessions, "owned_action", spy)
    chosen = []
    monkeypatch.setattr(world.frame, "_choose",
                        lambda title, prompt, choices, selection=None: chosen.append((title, list(choices))) or 0)
    asked = []
    def box(text, caption="", style=0, *a, **k):
        asked.append((text, caption, style))
        return answer
    monkeypatch.setattr(wx, "MessageBox", box)
    world.frame._command_handlers()["stop_background_task"](wx.CommandEvent())
    assert chosen == [("Stop Background Task", ["powershell: synthetic-build --watch"])]
    text, caption, style = asked[0]
    assert caption == "Stop Background Task" and style & wx.NO_DEFAULT and "carry on" in text
    if answer == wx.NO:
        assert calls == []
        return
    assert calls == [("task-1",)]
    assert any("Stopping powershell: synthetic-build --watch" in t for t in world.said), world.said


@pytest.mark.parametrize("case", ["idle", "running", "waiting"])
@pytest.mark.parametrize("command_id", ["stop_running_turn", "stop_background_task"])
def test_the_stop_menu_items_are_on_for_a_muse_chat_started_here(world, case, command_id):
    """The sweep calls handlers directly; this checks the menu bar itself, as
    you reach it. Stop Background Task was greyed out for Muse chats."""
    put_in_case(world, case)
    world.frame._update_send_state()
    item = world.frame.menu_items[command_id]
    assert item is not None and item.IsEnabled(), command_id
