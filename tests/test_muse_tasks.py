"""Stop Background Task for a Muse chat started here, shaped like installed
Muse Code 1.4.20's task/list and task/stop.

Natively: task/list answers {"tasks": []} with nothing running, and task/stop
for a task it doesn't have is -32030 commandRejected. Stopping a live task
couldn't be checked natively: on the checking computer Muse can't start a
command at all (meta-models/muse-code-sdk#149). Synthetic only.
"""
import copy
import uuid

import pytest

from test_muse_execution import NATIVE_ID, Execution, owned, record  # noqa: F401

from thechatplace.providers.muse_connection import MuseHistoryError, MuseRequestError

TASK = "019a84c2-a1f0-7e3b-9c1e-00000000ta51"


class TaskHost(Execution):
    def __init__(self):
        super().__init__()
        self.tasks = [
            {"taskId": TASK, "kind": "tool", "status": "inProgress", "tool": "powershell",
             "command": "synthetic-build --watch\n  --verbose " + "x" * 200,
             "executionMode": "background", "startedAt": "2026-11-15T10:00:00Z"},
            {"taskId": "future-1", "kind": "tool", "status": "someFutureState"},  # ignored: unknown status
        ]
        self.refuse_stop = False

    def request(self, method, params):
        if method == "task/list":
            self.messages.append((method, copy.deepcopy(params)))
            assert params == {"sessionId": NATIVE_ID}
            return {"tasks": copy.deepcopy(self.tasks)}
        if method == "task/stop":
            self.messages.append((method, copy.deepcopy(params)))
            assert set(params) == {"sessionId", "commandId", "taskId"} and params["sessionId"] == NATIVE_ID
            assert uuid.UUID(params["commandId"]).version == 7
            if self.refuse_stop or not any(t["taskId"] == params["taskId"] for t in self.tasks):
                raise MuseRequestError(-32030)
            self.tasks = [t for t in self.tasks if t["taskId"] != params["taskId"]]
            return {"commandId": params["commandId"], "status": "accepted", "taskId": params["taskId"]}
        return super().request(method, params)


@pytest.fixture
def host(owned):
    owned.execution = TaskHost()
    return owned


def test_tasks_lists_running_work_by_tool_and_short_command(host):
    own = record(host)
    (task,) = host.tasks(own)
    assert task["id"] == TASK
    assert task["label"].startswith("powershell: synthetic-build --watch --verbose ")
    assert "\n" not in task["label"] and len(task["label"]) <= 100 and task["label"].endswith("...")


def test_a_task_without_a_tool_or_command_is_named_by_its_kind(host):
    own = record(host)
    host.execution.tasks = [{"taskId": "r-1", "kind": "agent.run", "status": "inProgress"}]
    assert host.tasks(own)[0]["label"] == "agent.run"


def test_stop_task_stops_a_listed_task(host):
    own = record(host)
    assert host.stop_task(own, TASK) == "stopping"
    assert [p["taskId"] for m, p in host.execution.messages if m == "task/stop"] == [TASK]


def test_a_task_no_longer_listed_has_finished_and_nothing_is_sent(host):
    own = record(host)
    host.execution.tasks = []
    assert host.stop_task(own, TASK) == "finished"
    assert not any(m == "task/stop" for m, _ in host.execution.messages)


def test_a_task_muse_no_longer_has_has_finished(host):
    """task/stop for a task Muse doesn't have is -32030 (native)."""
    own = record(host)
    host.execution.refuse_stop = True
    assert host.stop_task(own, TASK) == "finished"


def test_stop_task_is_for_chats_started_here(host):
    from thechatplace.own_store import OwnSession
    with pytest.raises(MuseHistoryError):
        host.stop_task(OwnSession("other", "Other", "", provider="codex"), TASK)


def test_an_idle_chat_with_a_background_command_is_working_until_it_ends(host):
    """A turn can end with a command still running, a test suite say: Muse
    calls the chat idle, and lists the command with task/list. The chat is
    Working, and the window keeps watching, until the command ends."""
    from test_muse_provider import Connection
    from thechatplace.providers.muse import MuseProvider
    from thechatplace.sessions import IDLE, WORKING
    own = record(host)
    host.send(own, "Synthetic request")
    host.execution.complete("completed")
    host.execution.tasks = host.execution.tasks[:1]
    provider = MuseProvider(connection=Connection(), execution=host.execution)
    provider._owned = host
    polled = host.poll(own)
    assert polled["state"] == "idle" and polled["background"] is True
    assert provider.owned_state(own) == WORKING
    host.execution.tasks = []
    assert host.poll(own)["background"] is False
    assert provider.owned_state(own) == IDLE


def test_a_task_in_a_state_muse_may_add_later_is_not_background_work(host):
    own = record(host)
    host.send(own, "Synthetic request")
    host.execution.complete("completed")
    host.execution.tasks = host.execution.tasks[1:]  # only the unknown status
    assert host.poll(own)["background"] is False


def test_a_refused_task_list_leaves_the_chat_idle_not_unknown(owned):  # noqa: F811
    own = record(owned)
    owned.send(own, "Synthetic request")
    owned.execution.complete("completed")
    owned.execution.error = ("task/list", MuseRequestError(-32010))
    polled = owned.poll(own)
    assert polled["state"] == "idle" and polled["background"] is False


def test_a_running_turn_does_not_ask_for_background_tasks(host):
    own = record(host)
    host.send(own, "Synthetic request")
    host.execution.messages.clear()
    assert host.poll(own)["state"] == "running"
    assert not any(m == "task/list" for m, _ in host.execution.messages)
