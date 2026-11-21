"""The Status column for Muse Code chats.

A chat started here runs in The Chat Place's own Muse host, which knows its
state: Muse's session status there is notLoaded, idle, running or needsInput
(installed 1.4.20). A chat started in Muse Code runs in another process,
which this host can't see. Synthetic only.
"""
import pytest

from test_muse_execution import NATIVE_ID, Execution, approval, owned, question, record, workflow_item  # noqa: F401
from test_muse_provider import Connection, session

from thechatplace.providers.muse import MuseProvider
from thechatplace.providers.muse_connection import MuseHistoryError
from thechatplace.providers.base import Snapshot
from thechatplace.session_controller import SessionController
from thechatplace.sessions import IDLE, NEEDS_YOU, SORT_STATUS, STATUS_UNAVAILABLE, WORKING


@pytest.mark.parametrize("status,state", [
    ("running", WORKING), ("needsInput", NEEDS_YOU), ("idle", IDLE),
    # Not loaded in the app's host: nothing of this chat is running here.
    ("notLoaded", IDLE),
])
def test_a_chat_started_here_has_its_status_from_the_apps_own_host(owned, status, state):
    own = record(owned)
    owned.execution.session["status"] = status
    provider = MuseProvider(connection=Connection(), execution=owned.execution)
    assert provider.owned_state(own) == state


def test_reading_the_status_never_loads_or_resumes_the_chat(owned):
    own = record(owned)
    owned.execution.messages.clear()
    owned.execution.session["status"] = "notLoaded"
    MuseProvider(connection=Connection(), execution=owned.execution).owned_state(own)
    assert [m for m, _ in owned.execution.messages] == ["session/read"]
    assert owned.execution.messages[0][1] == {"sessionId": NATIVE_ID, "excludeItems": True}


def test_an_unreadable_status_stays_unavailable(owned):
    own = record(owned)
    owned.execution.error = ("session/read", MuseHistoryError("synthetic outage"))
    assert MuseProvider(connection=Connection(), execution=owned.execution).owned_state(own) == STATUS_UNAVAILABLE


def test_the_list_shows_a_chat_started_heres_status_and_not_another_processes(owned):
    own = record(owned)
    owned.execution.session["status"] = "running"
    history = Connection(pages=[{"sessions": [session(), session(sessionId="external-1")], "nextCursor": None}])
    controller = SessionController([MuseProvider(connection=history, execution=owned.execution)])
    snapshot = controller.collect(lambda: Snapshot(), SORT_STATUS, owned=[own])
    states = {info.cli_session_id: info.state for info in snapshot.sessions}
    assert states[NATIVE_ID] == WORKING
    assert states["external-1"] == STATUS_UNAVAILABLE  # another process: not seen from here


@pytest.mark.parametrize("waiting", ["approval", "question"])
def test_an_idle_chat_with_a_sub_agent_waiting_needs_you(owned, waiting):
    """Installed 1.4.24 calls a chat idle while its workflow's sub-agents wait
    for you, with their requests listed on the chat (subagentOrigin)."""
    own = record(owned)
    owned.execution.session["status"] = "idle"
    raw = approval() if waiting == "approval" else question()
    raw["subagentOrigin"] = {"subagentId": "sub-1", "childSessionId": "child-1", "childRunId": "run-1"}
    setattr(owned.execution, "approvals" if waiting == "approval" else "questions", [raw])
    assert MuseProvider(connection=Connection(), execution=owned.execution).owned_state(own) == NEEDS_YOU


def test_an_idle_chat_with_background_work_is_working_until_it_ends(owned):
    own = record(owned)
    owned.send(own, "Synthetic review request")
    owned.execution.items = [workflow_item("inProgress")]
    owned.execution.complete("completed")
    provider = MuseProvider(connection=Connection(), execution=owned.execution)
    provider._owned = owned
    owned.poll(own)
    assert provider.owned_state(own) == WORKING
    owned.execution.items = [workflow_item("completed", revision=5)]
    owned.poll(own)
    assert provider.owned_state(own) == IDLE


def test_a_chat_not_loaded_here_is_idle_without_asking_for_requests(owned):
    own = record(owned)
    owned.execution.messages.clear()
    owned.execution.session["status"] = "notLoaded"
    assert MuseProvider(connection=Connection(), execution=owned.execution).owned_state(own) == IDLE
    assert [m for m, _ in owned.execution.messages] == ["session/read"]
