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
    ("itemId", " " ), ("itemId", 3), ("threadId", "x" * 257), ("delta", [])],
    ids=["missing-thread", "empty-turn", "blank-item", "numeric-item",
         "oversized-thread", "non-text-delta"])
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
    buffer.apply(CodexEvent("turn_completed", "chat", "turn", status="completed"))
    assert buffer.status == "completed" and buffer.truncated
    assert "truncated" in buffer.replies[-1][1]


@pytest.mark.parametrize("item,text", [("", "text"), ("item", None)])
def test_malformed_matching_text_cannot_be_followed_by_false_success(item, text):
    buffer = CodexTurnBuffer("chat", "turn")
    with pytest.raises(ValueError):
        buffer.apply(CodexEvent("text_delta", "chat", "turn", item, text))
    assert buffer.status == "unknown" and buffer.needs_reconciliation
    assert not buffer.apply(CodexEvent("turn_completed", "chat", "turn", status="completed"))


@pytest.mark.parametrize("limit", ["MAX_ITEMS", "MAX_TEXT"])
def test_display_limits_truncate_without_interrupting_the_turn(limit):
    buffer = CodexTurnBuffer("chat", "turn")
    setattr(buffer, limit, 1)
    buffer.apply(CodexEvent("text_delta", "chat", "turn", "first", "A"))
    buffer.apply(CodexEvent("text_delta", "chat", "turn", "second", "B"))
    assert buffer.status == 'inProgress' and buffer.truncated
    buffer.apply(CodexEvent('turn_completed', 'chat', 'turn', status='completed'))
    assert buffer.status == 'completed' and not buffer.needs_reconciliation
    assert 'truncated' in buffer.replies[-1][1]


@pytest.mark.parametrize('method', ['item/started', 'item/completed'])
def test_file_approval_details_are_bounded_and_scoped_to_turn_and_item(method):
    event = decode_event(method, {'threadId': 'chat', 'turnId': 'turn', 'item': {
        'type': 'fileChange', 'id': 'files', 'changes': [
            {'path': 'old.py', 'kind': {'type': 'update', 'move_path': 'new.py'}, 'diff': '+new'}]}})
    buffer = CodexTurnBuffer('chat', 'turn')
    assert buffer.apply(event)
    assert buffer.file_changes == (('files', 'Update: old.py\nMove to: new.py\n+new'),)
    assert not CodexTurnBuffer('other', 'turn').apply(event)
    assert not CodexTurnBuffer('chat', 'other').apply(event)
    buffer.MAX_TEXT = 1
    assert buffer.apply(event)
    assert buffer.truncated and buffer.status == 'inProgress'
    assert len(buffer.file_changes[0][1]) == 1


@pytest.mark.parametrize('changes', [None, [{'path': 'file', 'kind': {'type': 'unknown'}, 'diff': ''}],
                                    [{'path': '', 'kind': {'type': 'add'}, 'diff': ''}]])
def test_missing_or_unknown_file_details_fail_closed(changes):
    with pytest.raises(ValueError):
        decode_event('item/started', {'threadId': 'chat', 'turnId': 'turn', 'item': {
            'type': 'fileChange', 'id': 'files', 'changes': changes}})


def test_command_activity_is_turn_scoped_bounded_and_has_elapsed_origin():
    event = decode_event('item/started', {'threadId': 'chat', 'turnId': 'turn', 'item': {
        'type': 'commandExecution', 'id': 'command', 'command': 'git status\n--short'}})
    buffer = CodexTurnBuffer('chat', 'turn')
    assert buffer.started_at > 0 and buffer.apply(event)
    assert buffer.activity == 'Running command: git status --short'
    assert not CodexTurnBuffer('other', 'turn').apply(event)
    done = decode_event('item/completed', {'threadId': 'chat', 'turnId': 'turn', 'item': {
        'type': 'commandExecution', 'id': 'command', 'command': 'git status'}})
    assert buffer.apply(done) and buffer.activity == 'Codex is working.'


def test_malformed_activity_requires_reconciliation():
    buffer = CodexTurnBuffer('chat', 'turn')
    with pytest.raises(ValueError):
        buffer.apply(CodexEvent('activity', 'chat', 'turn', 'item', None))
    assert buffer.needs_reconciliation


def test_large_diffs_cannot_consume_the_final_spoken_answer():
    buffer = CodexTurnBuffer('chat', 'turn')
    for index in range(5):
        buffer.apply(CodexEvent('file_changes', 'chat', 'turn', f'diff-{index}', 'x' * 250000))
    buffer.apply(CodexEvent('text_completed', 'chat', 'turn', 'answer', 'FINAL ANSWER: done.'))
    buffer.apply(CodexEvent('turn_completed', 'chat', 'turn', status='completed'))
    assert ('answer', 'FINAL ANSWER: done.') in buffer.replies
    assert buffer._file_characters <= buffer.MAX_TEXT
    assert buffer._characters == len('FINAL ANSWER: done.')
    assert buffer.truncated and buffer.status == 'completed'


@pytest.mark.parametrize('limit', ['MAX_TEXT', 'MAX_ITEMS'])
def test_finished_answer_displaces_old_commentary_at_display_limit(limit):
    buffer = CodexTurnBuffer('chat', 'turn')
    setattr(buffer, limit, 2)
    buffer.apply(CodexEvent('text_completed', 'chat', 'turn', 'first', 'A'))
    buffer.apply(CodexEvent('text_completed', 'chat', 'turn', 'second', 'B'))
    buffer.apply(CodexEvent('text_completed', 'chat', 'turn', 'answer', 'OK'))
    assert ('answer', 'OK') in buffer.replies
    assert buffer._characters <= buffer.MAX_TEXT
    assert len(buffer._texts) <= buffer.MAX_ITEMS
