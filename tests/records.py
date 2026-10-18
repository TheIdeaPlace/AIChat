"""Synthetic transcript records, shaped like Claude Code 2.1's.

Every record here is invented. The shapes (field names, nesting, one content
block per assistant record sharing a message id) are modelled on real
transcripts; no real conversation content is copied.
"""
from __future__ import annotations

import itertools
import json

_counter = itertools.count(1)


def _uuid() -> str:
    return f"00000000-0000-4000-8000-{next(_counter):012d}"


def _base(kind: str, **extra) -> dict:
    record = {
        "parentUuid": None,
        "isSidechain": False,
        "type": kind,
        "uuid": _uuid(),
        "timestamp": "2026-10-06T12:00:00.000Z",
        "userType": "external",
        "entrypoint": "claude-desktop",
        "cwd": "C:\\Users\\someone\\GitHub\\Example",
        "sessionId": "11111111-2222-4333-8444-555555555555",
        "version": "2.1.289",
        "gitBranch": "main",
    }
    record.update(extra)
    return record


def user_text(text, **extra) -> dict:
    return _base("user", message={"role": "user", "content": text}, **extra)


def user_blocks(blocks, **extra) -> dict:
    return _base("user", message={"role": "user", "content": blocks}, **extra)


def assistant_block(block, message_id="msg_A", **extra) -> dict:
    return _base("assistant", message={
        "id": message_id, "type": "message", "role": "assistant",
        "model": "claude-opus-5-5", "content": [block],
        "stop_reason": None, "usage": {"input_tokens": 1, "output_tokens": 1},
    }, requestId="req_x", **extra)


def text_block(text) -> dict:
    return {"type": "text", "text": text}


def thinking_block() -> dict:
    return {"type": "thinking", "thinking": "", "signature": "abc"}


def tool_use_block(name, tool_input, tool_id="toolu_1") -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input,
            "caller": {"type": "direct"}}


def tool_result(tool_id, content, is_error=False, **extra) -> dict:
    block = {"type": "tool_result", "tool_use_id": tool_id, "content": content}
    if is_error:
        block["is_error"] = True
    return user_blocks([block], sourceToolAssistantUUID=_uuid(), **extra)


def other(kind, **extra) -> dict:
    return {"type": kind, "sessionId": "11111111-2222-4333-8444-555555555555", **extra}


def lines(*records) -> list:
    return [json.dumps(r) for r in records]


def compact_boundary(trigger='manual', pre_tokens=968_000, post_tokens=9_000):
    return other('system', subtype='compact_boundary', uuid=_uuid(),
                 compactMetadata={'trigger': trigger, 'preTokens': pre_tokens, 'postTokens': post_tokens})


def command_stdout(text):
    return user_text('<local-command-stdout>' + text + '</local-command-stdout>')


def task_create(task_id, subject, tool_id, description="", active_form=""):
    """A TaskCreate call and its result (#231), shaped like Claude Code 2.1.296's."""
    tool_input = {"subject": subject, "description": description}
    if active_form:
        tool_input["activeForm"] = active_form
    return [assistant_block(tool_use_block("TaskCreate", tool_input, tool_id),
                            message_id=f"msg_{tool_id}"),
            tool_result(tool_id, f"Task #{task_id} created successfully: {subject}",
                        toolUseResult={"task": {"id": str(task_id), "subject": subject}})]


def task_update(task_id, tool_id, **changes):
    """A TaskUpdate call and its result (#231)."""
    tool_input = {"taskId": str(task_id), **changes}
    result = {"success": True, "taskId": str(task_id), "updatedFields": sorted(changes)}
    if "status" in changes:
        result["statusChange"] = {"from": "pending", "to": changes["status"]}
    return [assistant_block(tool_use_block("TaskUpdate", tool_input, tool_id),
                            message_id=f"msg_{tool_id}"),
            tool_result(tool_id, f"Updated task #{task_id} status", toolUseResult=result)]
