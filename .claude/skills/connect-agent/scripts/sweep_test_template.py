"""TEMPLATE: copy to tests/test_<agent>_chat_menu.py and fill in the TODOs.

Every Chat menu command against the new agent's chats, in four cases: a chat
started here that is idle, one running a turn, one waiting for an answer, and
a chat started in the agent itself. Copied from tests/test_muse_chat_menu.py,
which found five wrong behaviours on its first run and guarded every command
added after it. Read that file for a complete, working example.

What it checks, for every command and case:
- nothing raises; no Claude turn starts; a chat started elsewhere never
  reaches the agent's execution host;
- what's said doesn't name another provider (the Muse sweep found "Load one
  of your Claude chats..." this way);
- EXPECTED pins what each command does; a known bug is a strict xfail
  (``not_said``), so fixing it fails the test until the expectation changes;
- a new Chat menu command without a row fails
  ``test_every_chat_menu_command_has_an_expectation``.

Also test the menu bar itself (``frame.menu_items[id].IsEnabled()``): calling
handlers directly once missed a greyed-out item. See the last test here.

For a one-off survey of every menu (File, Session, View, Tools, Help), run the
same ``run()`` over ``commands.commands()`` from a temporary test file, print
the results, and delete the file: don't commit it.
"""
import dataclasses

import pytest

wx = pytest.importorskip("wx")
import fake_env

# TODO: the agent's fake execution host and history fake, shaped like the
# real agent's replies (probe it first). Muse:
# from test_muse_execution import NATIVE_ID, Execution, approval
# from test_muse_history_ui import _MuseHistoryConnection, _muse_wire_session

from thechatplace import commands

AGENT = "agent"                    # TODO: the provider id, as in PROVIDER_LABELS
LABEL = "Agent"                    # TODO: its label, as in the window ("Muse Code")
OTHER_PROVIDERS = ("Claude", "Codex", "Copilot")   # TODO: drop the agent's own name
NATIVE_ID = "native-1"             # TODO: from the fake host module
FOREIGN_ID = "agent-foreign-1"
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
    from thechatplace.session_controller import SessionController
    from thechatplace.ui.main_frame import MainFrame

    fake_env.install(tmp_path, monkeypatch.setattr)
    frame = MainFrame(store=OwnSessionStore(tmp_path / "own.json"), check_updates_at_start=False)
    frame._list_timer.Stop()
    # TODO: build the fake host and the provider. Muse:
    # execution = Execution()
    # provider = MuseProvider(connection=_MuseHistoryConnection(), execution=execution)
    # provider.set_owned_persistence(lambda record: frame.store.update(
    #     record.cli_session_id, provider="muse", provider_options=dict(record.provider_options)))
    execution = provider = None
    frame.sessions = SessionController([ClaudeConversations(), provider])
    record = OwnSession(NATIVE_ID, f"{LABEL} chat here", str(tmp_path), provider=AGENT, started=True)
    frame.store.add(record)
    owned = record.to_info()
    foreign = None  # TODO: a row as discovery makes it (Muse: session_from_muse(...))
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
    # A Claude turn starting for this agent's chat is the worst wrong route.
    monkeypatch.setattr(frame, "_start_turn", lambda *a, **k: w.claude_turns.append(a))
    # Background work runs at once; the agent's own monitor is tested elsewhere.
    monkeypatch.setattr(frame._pool, "submit", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    # TODO: stub the agent's background monitor, as Muse's was:
    # monkeypatch.setattr(frame, "_poll_muse_owned", lambda record: None)
    yield w
    frame.stop_timers()
    frame._pool.shutdown(wait=True)
    wx.GetApp().ProcessPendingEvents()
    frame.Destroy()
    wx.GetApp().ProcessPendingEvents()


def put_in_case(w, case):
    """Select and open the chat for ``case``, with the agent's state to match."""
    frame = w.frame
    info = w.foreign if case == "foreign" else w.owned
    # TODO: set the fake host's state for idle / running / waiting, and the
    # window's own record of it, as Muse's put_in_case does
    # (execution.session status, frame._muse_runtime, frame._muse_approvals).
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


NOT_YET = f"isn't available for {LABEL} chats yet"
READ_ONLY = f"was started in {LABEL}, so it's read-only here. Do that in {LABEL}."


def said(text):
    return ("said", text)


def dialog(name):
    return ("dialog", name)


def sent(method):
    return ("exec", method)


def not_said(text, reason):
    """Known wrong: ``text`` is said now and must not be. A strict xfail, so a
    fix fails the test until the expectation is updated to the new behaviour."""
    return ("not_said", text, reason)


def every(outcome):
    return {case: outcome for case in CASES}


def owned(outcome, foreign=None):
    return {"idle": outcome, "running": outcome, "waiting": outcome,
            "foreign": foreign or said(READ_ONLY)}


#: What each Chat menu command does for each case. Start from a report run
#: (print run()'s result for every pair), then pin it here and change it only
#: with the behaviour. TODO: one row per command in chat_menu_ids().
EXPECTED = {
    # "answer": {**every(said("No session is waiting for an answer.")),
    #            "waiting": dialog("...ApprovalDialog")},
    # "stop_running_turn": {"idle": said("Nothing is running in ..."),
    #                       "running": sent("..."), "waiting": sent("..."),
    #                       "foreign": said(READ_ONLY)},
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


def test_every_chat_menu_command_has_an_expectation():
    """A new Chat menu command needs a row here, as a new dialog needs a probe surface."""
    assert set(EXPECTED) == set(chat_menu_ids())
    assert all(set(row) == set(CASES) for row in EXPECTED.values())


@pytest.mark.parametrize("command_id,case", PAIRS)
def test_no_command_misroutes_this_agents_chat(world, command_id, case):
    result = run(world, command_id, case)
    assert result["error"] is None
    assert result["claude_turns"] == 0
    assert result["foreign_calls"] == []
    if case == "foreign":
        assert result["execution"] == []  # a chat started elsewhere never reaches execution
    named = [text for text in result["said"] for other in OTHER_PROVIDERS if other in text]
    assert named == [], "says another provider's name"


@pytest.mark.parametrize("command_id,case", [
    pytest.param(command_id, case, marks=known_wrong(EXPECTED[command_id][case]))
    for command_id, case in PAIRS if command_id in EXPECTED])
def test_chat_menu_command_does_what_is_expected(world, command_id, case):
    check(run(world, command_id, case), EXPECTED[command_id][case])


@pytest.mark.parametrize("case", ["idle", "running", "waiting"])
@pytest.mark.parametrize("command_id", chat_menu_ids())
def test_menu_items_are_enabled_where_they_should_be(world, command_id, case):
    """The menu bar itself: a command can work and still be greyed out by a
    separate enabling rule (_can_stop_background was one). TODO: list any
    command that is meant to be disabled for this agent and skip it here."""
    put_in_case(world, case)
    world.frame._update_send_state()
    item = world.frame.menu_items.get(command_id)
    assert item is None or item.IsEnabled(), command_id
