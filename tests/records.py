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


# -- subagents (#468) --------------------------------------------------------------------------


def agent_call(tool_id, agent_type, description, prompt, background=False, **extra):
    """Claude handing work to a subagent: an Agent tool call."""
    tool_input = {"description": description, "prompt": prompt, "subagent_type": agent_type}
    if background:
        tool_input["run_in_background"] = True
    return assistant_block(tool_use_block("Agent", tool_input, tool_id),
                           message_id=f"msg_{tool_id}", **extra)


def agent_completed(tool_id, agent_id, report, agent_type="general-purpose", **extra):
    """The result of an Agent call Claude waited for, as Claude Code 2.1.296
    writes it: the report as content blocks, then the id and usage lines."""
    text = f"{report}\nagentId: {agent_id} (use SendMessage to continue)\n<usage>total_tokens: 10</usage>"
    return tool_result(tool_id, [text_block(text)], toolUseResult={
        "status": "completed", "agentId": agent_id, "agentType": agent_type,
        "content": [text_block(report)], "totalDurationMs": 1000, "totalToolUseCount": 2},
        **extra)


def agent_launched(tool_id, agent_id):
    """The result of an Agent call sent to the background."""
    return tool_result(tool_id, [text_block(
        f"Async agent launched successfully.\nagentId: {agent_id} (internal ID)")],
        toolUseResult={"status": "async_launched", "agentId": agent_id, "isAsync": True})


def task_notification(task_id, status, result="", tool_id="", summary="Agent finished", **extra):
    """A background task ending, as the parent's transcript records it."""
    parts = [f"<task-id>{task_id}</task-id>"]
    if tool_id:
        parts.append(f"<tool-use-id>{tool_id}</tool-use-id>")
    parts += [f"<status>{status}</status>", f"<summary>{summary}</summary>"]
    if result:
        parts.append(f"<result>{result}</result>")
    return user_text("<task-notification>\n" + "\n".join(parts) + "\n</task-notification>", **extra)


def handback_attachment(agent_id, name="code-reviewer"):
    """A subagent's report reaching its session while Claude is working."""
    return _base("attachment", attachment={
        "type": "queued_command", "commandMode": "prompt",
        "prompt": f'<agent-message from="{agent_id}">\n[Subagent hand-back] Report.',
        "origin": {"kind": "peer", "from": agent_id, "senderTaskId": agent_id,
                   "name": name, "body": "[Subagent hand-back] Report.", "handback": True}})


def sidechain(record, agent_id):
    """``record`` as a subagent's own transcript has it."""
    return {**record, "isSidechain": True, "agentId": agent_id}


def handback(message, tool_id="toolu_hb", **extra):
    """A background subagent's last call: its final report."""
    return assistant_block(tool_use_block("SubagentHandback", {"message": message}, tool_id),
                           message_id=f"msg_{tool_id}", **extra)
