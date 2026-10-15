import dataclasses

import pytest

from thechatplace.providers.codex_events import CodexEvent, CodexTurnBuffer, decode_event


def test_delta_identity_is_immutable_and_backend_qualified_by_consumer():
    event = decode_event("item/agentMessage/delta", {
        "threadId": "chat", "turnId": "turn", "itemId": "reply", "delta": "A reply"})
    assert (event.kind, event.thread_id, event.turn_id, event.item_id, event.text) == (
        "text_delta", "chat", "turn", "reply", "A reply")
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.text = "changed"


@pytest.mark.parametrize("status", ["completed", "failed", "interrupted"])
def test_terminal_status_does_not_expose_private_error_payload(status):
    event = decode_event("turn/completed", {"threadId": "chat", "turn": {
        "id": "turn", "status": status, "error": {"message": "private path and credentials"}}})
    assert event.status == status and event.kind == "turn_completed"
    assert "private" not in repr(event)


@pytest.mark.parametrize("field,value", [("threadId", None), ("turnId", ""),
    ("itemId", " " ), ("itemId", 3), ("threadId", "x" * 257), ("delta", []),
    ("delta", "x" * (256 * 1024 + 1))],
    ids=["missing-thread", "empty-turn", "blank-item", "numeric-item",
         "oversized-thread", "non-text-delta", "oversized-delta"])
def test_malformed_known_delta_is_rejected(field, value):
    params = {"threadId": "chat", "turnId": "turn", "itemId": "reply", "delta": "text"}
    params[field] = value
    with pytest.raises(ValueError):
        decode_event("item/agentMessage/delta", params)


def test_final_text_replaces_delta_content_and_unknown_types_are_ignored():
    event = decode_event("item/completed", {"threadId": "chat", "turnId": "turn",
        "item": {"type": "agentMessage", "id": "reply", "text": "Final"}})
    assert event.kind == "text_completed" and event.text == "Final"
    assert decode_event("future/notification", None) is None
    assert decode_event("item/completed", {"threadId": "chat", "turnId": "turn",
        "item": {"type": "future"}}) is None


@pytest.mark.parametrize("method,status", [("turn/started", "completed"),
    ("turn/completed", "inProgress"), ("turn/completed", "unknown")])
def test_lifecycle_cannot_invent_success(method, status):
    with pytest.raises(ValueError):
        decode_event(method, {"threadId": "chat", "turn": {"id": "turn", "status": status}})


def test_turn_buffers_isolate_equal_item_ids_and_use_authoritative_completed_text():
    one, two = CodexTurnBuffer("one", "turn"), CodexTurnBuffer("two", "turn")
    events = [CodexEvent("text_delta", "one", "turn", "reply", "Draft"),
              CodexEvent("text_delta", "two", "turn", "reply", "Other"),
              CodexEvent("text_completed", "one", "turn", "reply", "Final")]
    for event in events:
        one.apply(event)
        two.apply(event)
    assert one.replies == (("reply", "Final"),) and two.replies == (("reply", "Other"),)
    assert not one.apply(events[-1])
    assert not one.apply(CodexEvent("text_delta", "one", "turn", "reply", "Late"))
    assert one.replies == (("reply", "Final"),)


@pytest.mark.parametrize("status", ["completed", "failed", "interrupted"])
def test_buffer_emits_terminal_transition_once_and_ignores_late_events(status):
    buffer = CodexTurnBuffer("chat", "turn")
    done = CodexEvent("turn_completed", "chat", "turn", status=status)
    assert buffer.apply(done) and not buffer.apply(done)
    assert buffer.status == status
    assert not buffer.apply(CodexEvent("text_delta", "chat", "turn", "reply", "Late"))
    buffer.disconnect()
    assert buffer.status == status


def test_disconnect_requires_reconciliation_and_never_guesses_completion():
    buffer = CodexTurnBuffer("chat", "turn")
    buffer.apply(CodexEvent("text_delta", "chat", "turn", "reply", "Partial"))
    buffer.disconnect()
    assert buffer.status == "unknown" and buffer.needs_reconciliation
    assert not buffer.apply(CodexEvent("turn_completed", "chat", "turn", status="completed"))


def test_success_without_final_item_requires_reconciliation():
    buffer = CodexTurnBuffer("chat", "turn")
    buffer.apply(CodexEvent("text_delta", "chat", "turn", "reply", "Partial"))
    with pytest.raises(ValueError, match="incomplete"):
        buffer.apply(CodexEvent("turn_completed", "chat", "turn", status="completed"))
    assert buffer.status == "unknown" and buffer.needs_reconciliation


@pytest.mark.parametrize("item,text", [("", "text"), ("item", None)])
def test_malformed_matching_text_cannot_be_followed_by_false_success(item, text):
    buffer = CodexTurnBuffer("chat", "turn")
    with pytest.raises(ValueError):
        buffer.apply(CodexEvent("text_delta", "chat", "turn", item, text))
    assert buffer.status == "unknown" and buffer.needs_reconciliation
    assert not buffer.apply(CodexEvent("turn_completed", "chat", "turn", status="completed"))


@pytest.mark.parametrize("limit", ["MAX_ITEMS", "MAX_TEXT"])
def test_buffer_limits_fail_without_partial_update_or_retry(limit):
    buffer = CodexTurnBuffer("chat", "turn")
    setattr(buffer, limit, 1)
    buffer.apply(CodexEvent("text_delta", "chat", "turn", "first", "A"))
    with pytest.raises(ValueError, match="reconcile"):
        buffer.apply(CodexEvent("text_delta", "chat", "turn", "second", "B"))
    assert buffer.replies == (("first", "A"),)
    assert buffer.status == "unknown" and buffer.needs_reconciliation
