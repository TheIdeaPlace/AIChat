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
from thechatplace.contracts import (AskPermission, AskQuestion, Capability, ChatRef, Decision, Integration, Refused,
                                    ReplyText, TurnStatus)
from thechatplace.integrations.openai_chat import OpenAIChatIntegration


#: What every owned chat can do, wherever it comes from.
_CORE = {Capability.READ, Capability.SEND, Capability.STOP, Capability.RESUME, Capability.OPTIONS}


@dataclass
class Backend:
    name: str
    make: Callable
    needs_options: dict
    #: Exactly what it should claim. A claim gets its positive test below, and
    #: anything it doesn't claim gets its absence checked, never a skip.
    capabilities: frozenset


def _local(mode, data):
    server = FakeModelServer(mode)
    integration = OpenAIChatIntegration(id="fakelocal", name="Fake Local", base_url=server.url)
    integration._server = server  # closed with the test
    return integration


BACKENDS = [
    Backend("acp", lambda mode, data: acp(mode), {},
            frozenset(_CORE | {Capability.APPROVALS, Capability.USAGE, Capability.PLANS})),
    Backend("claude", lambda mode, data: claude(mode, data), {},
            frozenset(_CORE | {Capability.APPROVALS, Capability.QUESTIONS, Capability.USAGE})),
    Backend("codex", lambda mode, data: codex(mode, data), {},
            frozenset(_CORE | {Capability.APPROVALS, Capability.QUESTIONS, Capability.USAGE, Capability.PLANS})),
    Backend("plain", _local, {"model": "m1"}, frozenset(_CORE)),
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


def test_it_claims_exactly_what_it_should(backend, make_run):
    run = make_run()
    info = _chat(run, backend)
    assert info.owned and info.capabilities == backend.capabilities


def test_options_are_real_where_claimed(backend, make_run):
    run = make_run()
    info = _chat(run, backend)
    groups = run.service.chat_options(info.ref)
    assert groups, "it claims options but has none"
    group = groups[0]
    other = next(v.id for v in group.values if v.id != group.current)
    run.service.set_option(info.ref, group.id, other)
    assert run.service.store.get_chat(info.ref).options[group.id] == other


def test_a_turn_streams_and_ends(backend, make_run):
    run = make_run()
    info = _chat(run, backend)
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
    assert run.service.send(info.ref, "Second") == "queued"
    run.end_turn()
    run.end_turn()
    run.idle(info.ref)
    users = [t for t in run.text(info.ref) if t.startswith("user:")]
    assert users == ["user:First", "user:Second"]


def test_stop_ends_a_running_turn_and_gives_queued_messages_back(backend, make_run):
    run = make_run("hang")
    info = _chat(run, backend)
    run.service.send(info.ref, "First")
    assert run.service.send(info.ref, "Waiting") == "queued"
    run.wait(ReplyText, timeout=10)  # the turn has really started
    assert run.service.stop(info.ref) == ["Waiting"]
    assert run.end_turn().status is TurnStatus.STOPPED
    run.idle(info.ref)
    # Stopping leaves the chat usable, with nothing stuck.
    assert run.service.state(info.ref).busy is False


def test_a_stop_sent_right_after_the_message_still_lands(backend, make_run):
    """Before the turn has anything to stop. Asked once, it would be lost."""
    run = make_run("hang")
    info = _chat(run, backend)
    run.service.send(info.ref, "First")
    run.service.stop(info.ref)
    assert run.end_turn(timeout=15).status is TurnStatus.STOPPED
    run.idle(info.ref)


def test_a_late_stop_doesnt_reach_the_next_turn(backend, make_run):
    run = make_run()
    info = _chat(run, backend)
    run.service.send(info.ref, "First")
    run.end_turn()
    run.idle(info.ref)
    run.service.stop(info.ref)  # nothing to stop now
    run.service.send(info.ref, "Second")
    assert run.end_turn().status is TurnStatus.COMPLETED


def test_turning_it_off_mid_turn_keeps_a_waiting_message_readable(backend, make_run):
    run = make_run("hang")
    info = _chat(run, backend)
    run.service.send(info.ref, "First")
    assert run.service.send(info.ref, "Waiting") == "queued"
    run.wait(ReplyText, timeout=10)
    run.service.set_enabled(run.integration.id, False)
    run.service.stop(info.ref)  # gives it back; this is the other path
    run.end_turn(timeout=15)
    run.idle(info.ref)
    assert run.service.state(info.ref).queue == []


def test_stop_when_idle_is_harmless(backend, make_run):
    run = make_run()
    info = _chat(run, backend)
    assert run.service.stop(info.ref) == []
    assert run.service.state(info.ref).busy is False


def test_approvals_match_the_capability(backend, make_run):
    run = make_run("ask")
    info = _chat(run, backend)
    assert (Capability.APPROVALS in info.capabilities) == (Capability.APPROVALS in backend.capabilities)
    if Capability.APPROVALS not in backend.capabilities:
        # Its adapter has no way to ask at all, so no request can reach anyone.
        assert type(run.integration).answer_permission is Integration.answer_permission
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
    if Capability.APPROVALS not in backend.capabilities:
        return  # test_approvals_match_the_capability checks the absence
    run.service.send(info.ref, "First")
    request = run.wait(AskPermission).request
    run.service.answer_permission(info.ref, request, Decision.DENY)
    run.end_turn()
    run.idle(info.ref)
    assert any("Denied" in t for t in run.text(info.ref))


def test_stopping_during_a_request_never_allows_it(backend, make_run):
    run = make_run("ask")
    info = _chat(run, backend)
    if Capability.APPROVALS not in backend.capabilities:
        return  # test_approvals_match_the_capability checks the absence
    run.service.send(info.ref, "First")
    run.wait(AskPermission)
    run.service.stop(info.ref)
    run.end_turn()
    run.idle(info.ref)
    assert not any("Allowed" in t for t in run.text(info.ref))


def test_questions_match_the_capability(backend, make_run):
    run = make_run("question")
    info = _chat(run, backend)
    if Capability.QUESTIONS not in backend.capabilities:
        assert type(run.integration).answer_question is Integration.answer_question
        return
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
    if backend.name == "claude":
        # The fake doesn't write Claude's transcript, so one's written here.
        folder = run.data / "projects" / "here"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{info.ref.native_id}.jsonl").write_text(
            json.dumps({"type": "user", "message": {"content": "First"}}), encoding="utf-8")
    assert "First" in [m.text for m in again.service.load_history(info.ref) if m.role == "user"]
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
    # Usable means it takes another message and that turn ends too.
    run.service.send(info.ref, "Again")
    assert run.end_turn().status is TurnStatus.FAILED
    run.idle(info.ref)
    assert "user:Again" in run.text(info.ref)


def test_an_option_changed_during_a_turn_survives_it(backend, make_run):
    run = make_run("hang")
    info = _chat(run, backend)
    run.service.send(info.ref, "First")
    run.wait(ReplyText, timeout=10)
    group = run.service.chat_options(info.ref)[0]
    other = next(v.id for v in group.values if v.id != group.current)
    run.service.set_option(info.ref, group.id, other)
    run.service.stop(info.ref)
    run.end_turn()
    run.idle(info.ref)
    assert run.service.store.get_chat(info.ref).options[group.id] == other


def test_a_store_failure_leaves_the_chat_usable(backend, make_run, monkeypatch):
    run = make_run()
    info = _chat(run, backend)
    real = run.service.store.save_chat

    def fails(chat):
        raise PermissionError("held by something else")
    monkeypatch.setattr(run.service.store, "save_chat", fails)
    run.service.send(info.ref, "First")
    run.end_turn()
    run.idle(info.ref)
    assert run.service.state(info.ref).busy is False
    monkeypatch.setattr(run.service.store, "save_chat", real)
    assert run.service.send(info.ref, "Second") == "sent"
    run.end_turn()
    run.idle(info.ref)


def test_a_turned_off_provider_takes_no_messages(backend, make_run):
    run = make_run()
    info = _chat(run, backend)
    run.service.set_enabled(run.integration.id, False)
    with pytest.raises(Refused, match="turned off"):
        run.service.send(info.ref, "Hello")
