"""The suite every backend passes (#489 part 4). It's capability-aware: an optional
feature gets its positive test where it's implemented, and its absence is checked
where it isn't. Plain chat needn't grow permissions to pass.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

from harness import FakeModelServer, Run, acp, claude, codex
from thechatplace.contracts import (Activity, AskPermission, AskQuestion, Capability, ChatRef, Decision, Notice, Refused,
                                    TurnStatus)
from thechatplace.integrations.openai_chat import OpenAIChatIntegration


@dataclass
class Backend:
    name: str
    make: Callable
    needs_options: dict


def _local(mode, data):
    server = FakeModelServer(mode)
    integration = OpenAIChatIntegration(id="fakelocal", name="Fake Local", base_url=server.url)
    integration._server = server  # closed with the test
    return integration


BACKENDS = [
    Backend("acp", lambda mode, data: acp(mode), {}),
    Backend("claude", lambda mode, data: claude(mode, data), {}),
    Backend("codex", lambda mode, data: codex(mode, data), {}),
    Backend("plain", _local, {"model": "m1"}),
]


@pytest.fixture(params=BACKENDS, ids=lambda b: b.name)
def backend(request):
    return request.param


@pytest.fixture
def make_run(backend, tmp_path):
    runs = []
    folder = tmp_path / "work"
    folder.mkdir()
    data = tmp_path / "data"
    data.mkdir()

    def make(mode="answer"):
        run = Run(backend.make(mode, data), data, folder)
        runs.append(run)
        return run

    yield make
    for run in runs:
        run.close()
        server = getattr(run.integration, "_server", None)
        if server is not None:
            server.close()


def _chat(run, backend):
    return run.start(**backend.needs_options)


def test_a_turn_streams_and_ends(backend, make_run):
    run = make_run()
    info = _chat(run, backend)
    assert Capability.SEND in info.capabilities and info.owned
    assert run.service.send(info.ref, "First") == "sent"
    assert run.end_turn().status is TurnStatus.COMPLETED
    run.idle(info.ref)
    texts = run.text(info.ref)
    assert texts[0] == "user:First"
    assert any(t.startswith("assistant:") and "Hello" in t for t in texts[1:])


def test_a_second_message_waits_its_turn(backend, make_run):
    run = make_run()
    info = _chat(run, backend)
    assert run.service.send(info.ref, "First") == "sent"
    outcome = run.service.send(info.ref, "Second")
    assert outcome in ("queued", "sent")
    run.end_turn()
    if outcome == "queued":
        run.end_turn()
    run.idle(info.ref)
    assert "user:Second" in run.text(info.ref)


def test_stop_ends_a_running_turn_and_gives_queued_messages_back(backend, make_run):
    run = make_run("hang")
    info = _chat(run, backend)
    run.service.send(info.ref, "First")
    assert run.service.send(info.ref, "Waiting") == "queued"
    run.wait(object, timeout=10)  # the turn has really started
    assert run.service.stop(info.ref) == ["Waiting"]
    assert run.end_turn().status is TurnStatus.STOPPED
    run.idle(info.ref)
    # Stopping leaves the chat usable, with nothing stuck.
    assert run.service.state(info.ref).busy is False


def test_stop_when_idle_is_harmless(backend, make_run):
    run = make_run()
    info = _chat(run, backend)
    assert run.service.stop(info.ref) == []
    assert run.service.state(info.ref).busy is False


def test_approvals_match_the_capability(backend, make_run):
    run = make_run("ask")
    info = _chat(run, backend)
    if Capability.APPROVALS not in info.capabilities:
        # Nothing to approve: it just answers. No request can reach the user.
        run.service.send(info.ref, "First")
        run.end_turn()
        assert run.service.state(info.ref).pending_permission is None
        return
    run.service.send(info.ref, "First")
    request = run.wait(AskPermission).request
    assert Decision.DENY in request.decisions and Decision.ALLOW_ONCE in request.decisions
    assert request.detail
    run.service.answer_permission(info.ref, request, Decision.ALLOW_ONCE)
    assert run.end_turn().status is TurnStatus.COMPLETED
    run.idle(info.ref)
    assert any("Allowed" in t for t in run.text(info.ref))


def test_denying_is_heard(backend, make_run):
    run = make_run("ask")
    info = _chat(run, backend)
    if Capability.APPROVALS not in info.capabilities:
        pytest.skip("no approvals here; test_approvals_match_the_capability covers that")
    run.service.send(info.ref, "First")
    request = run.wait(AskPermission).request
    run.service.answer_permission(info.ref, request, Decision.DENY)
    run.end_turn()
    run.idle(info.ref)
    assert any("Denied" in t for t in run.text(info.ref))


def test_stopping_during_a_request_never_allows_it(backend, make_run):
    run = make_run("ask")
    info = _chat(run, backend)
    if Capability.APPROVALS not in info.capabilities:
        pytest.skip("no approvals here")
    run.service.send(info.ref, "First")
    run.wait(AskPermission)
    run.service.stop(info.ref)
    run.end_turn()
    run.idle(info.ref)
    assert not any("Allowed" in t for t in run.text(info.ref))


def test_questions_match_the_capability(backend, make_run):
    run = make_run("question")
    info = _chat(run, backend)
    if Capability.QUESTIONS not in info.capabilities:
        pytest.skip("this integration can't ask questions")
    run.service.send(info.ref, "First")
    request = run.wait(AskQuestion).request
    item = request.items[0]
    assert item.prompt == "Which colour?" and [c.label for c in item.choices] == ["Red", "Blue"]
    run.service.answer_question(info.ref, request, {item.id: "Blue"})
    run.end_turn()
    run.idle(info.ref)
    assert any("Blue" in t for t in run.text(info.ref))


def test_a_chat_not_started_here_can_never_be_sent_to(backend, make_run):
    run = make_run()
    with pytest.raises(Refused):
        run.service.send(ChatRef(run.integration.id, "someone-elses-chat"), "Hello")


def test_something_unknown_is_told_once_counted_and_stores_no_content(backend, make_run):
    run = make_run("unknown")
    info = _chat(run, backend)
    run.service.send(info.ref, "First")
    assert run.end_turn().status is TurnStatus.COMPLETED
    run.idle(info.ref)
    report = run.integration.drift.snapshot()
    assert report["unknown"], "the unknown update wasn't counted"
    assert "must never be stored" not in json.dumps(report)
    notes = [t for t in run.text(info.ref) if t.startswith("note:") and "can't show yet" in t]
    if backend.name == "plain":
        return  # a stream's extra fields have no chat-level meaning
    assert len(notes) == 1
    assert run.service.state(info.ref).pending_permission is None
    assert "must never be stored" not in " ".join(run.text(info.ref))


def test_the_chat_survives_a_restart(backend, make_run):
    run = make_run()
    info = _chat(run, backend)
    run.service.send(info.ref, "First")
    run.end_turn()
    run.idle(info.ref)
    run.close()

    again = make_run()
    listed = {chat.ref.key: chat for chat in again.service.store.chats([again.integration.id])}
    assert info.ref.key in listed
    reopened = again.service.info(info.ref)
    assert Capability.SEND in reopened.capabilities
    if backend.name != "claude":  # the fake doesn't write Claude's transcript
        assert any("user:First" == f"{m.role}:{m.text}" for m in again.service.load_history(info.ref))
    else:
        again.service.state(info.ref).loaded = True
    again.service.send(info.ref, "Second")
    assert again.end_turn().status is TurnStatus.COMPLETED


def test_two_chats_stay_apart(backend, make_run):
    run = make_run()
    one, two = _chat(run, backend), _chat(run, backend)
    assert one.ref != two.ref
    run.service.send(one.ref, "For one")
    run.end_turn()
    run.idle(one.ref)
    run.service.send(two.ref, "For two")
    run.end_turn()
    run.idle(two.ref)
    assert not any("For two" in t for t in run.text(one.ref))
    assert not any("For one" in t for t in run.text(two.ref))


def test_a_turn_that_fails_says_so_and_the_chat_stays_usable(backend, make_run):
    mode = {"acp": "die", "claude": "error", "codex": "fail", "plain": "error"}[backend.name]
    run = make_run(mode)
    info = _chat(run, backend)
    run.service.send(info.ref, "First")
    ended = run.end_turn()
    assert ended.status is TurnStatus.FAILED and ended.message
    run.idle(info.ref)
    assert run.service.state(info.ref).busy is False
