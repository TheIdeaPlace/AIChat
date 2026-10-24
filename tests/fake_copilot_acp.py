"""Stands in for ``copilot --acp`` in tests: NDJSON JSON-RPC on stdio.

Shaped like what Copilot CLI 1.0.103 sent (dev-notes/copilot-cli-probe.md),
with synthetic text only. Run as ``python -u fake_copilot_acp.py MODE``:

  answer      each prompt streams "Hello" " there" and ends the turn
  ask         each prompt asks permission to run a command first, then says
              "Allowed" or "Denied" depending on the answer
  hang        each prompt streams one chunk, then waits for session/cancel
  unsafe      sessions start in autopilot mode
  allowall    sessions start with allow_all on
  byok        the turn's usage record names another provider
  nolog       no usage record is written at all
  oldagent    initialize reports another agent
  flip        mid-turn, reports (config_option_update) a switch to autopilot
  curmode     mid-turn, reports (current_mode_update) a switch to autopilot
  byok2       the first turn is billed to Copilot, later ones to another provider

FAKE_COPILOT_SESSIONS names the folder session logs are written under, as
Copilot writes ~/.copilot/session-state/<id>/events.jsonl.
FAKE_COPILOT_LOG, if set, gets every message received, one per line.
"""
import json
import os
import sys
import threading
import uuid

MODE = sys.argv[1] if len(sys.argv) > 1 else "answer"
SESSIONS = os.environ.get("FAKE_COPILOT_SESSIONS", "")
LOG = os.environ.get("FAKE_COPILOT_LOG", "")
AGENT = "https://agentclientprotocol.com/protocol/session-modes#agent"
AUTOPILOT = "https://agentclientprotocol.com/protocol/session-modes#autopilot"
write_lock = threading.Lock()
answers = {}
turns = [0]
cancelled = threading.Event()


def send(message):
    with write_lock:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()


def update(session, body):
    send({"jsonrpc": "2.0", "method": "session/update",
          "params": {"sessionId": session, "update": body}})


def options():
    mode = AUTOPILOT if MODE == "unsafe" else AGENT
    allow = "on" if MODE == "allowall" else "off"
    return [{"type": "select", "id": "mode", "currentValue": mode},
            {"type": "select", "id": "model", "currentValue": "auto"},
            {"type": "select", "id": "allow_all", "currentValue": allow}]


def log_usage(session):
    if not SESSIONS or MODE == "nolog":
        return
    folder = os.path.join(SESSIONS, session)
    os.makedirs(folder, exist_ok=True)
    kind = "openai" if MODE == "byok" or (MODE == "byok2" and turns[0] > 1) else "copilot"
    with open(os.path.join(folder, "events.jsonl"), "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "session.usage_record", "data": {"usage": {
            "model": "synthetic", "provider": {"id": kind, "kind": kind}}}}) + "\n")


def prompt(message_id, session):
    turns[0] += 1
    if MODE == "flip":
        update(session, {"sessionUpdate": "config_option_update", "configOptions": [
            {"type": "select", "id": "mode", "currentValue": AUTOPILOT},
            {"type": "select", "id": "allow_all", "currentValue": "off"}]})
    if MODE == "curmode":
        update(session, {"sessionUpdate": "current_mode_update", "currentModeId": AUTOPILOT})
    if MODE == "hang":
        update(session, {"sessionUpdate": "agent_message_chunk",
                         "content": {"type": "text", "text": "Working"}})
        cancelled.wait(30)
        send({"jsonrpc": "2.0", "id": message_id, "result": {"stopReason": "cancelled"}})
        return
    if MODE == "ask":
        ask_id = "perm-" + uuid.uuid4().hex[:8]
        update(session, {"sessionUpdate": "tool_call", "toolCallId": "call-1",
                         "title": "Run a synthetic command", "kind": "execute", "status": "pending"})
        done = threading.Event()
        answers[ask_id] = [done, None]
        send({"jsonrpc": "2.0", "id": ask_id, "method": "session/request_permission", "params": {
            "sessionId": session,
            "toolCall": {"toolCallId": "call-1", "title": "Run a synthetic command",
                         "kind": "execute", "rawInput": {"command": "echo synthetic"}},
            "options": [{"optionId": "allow_once", "name": "Allow once", "kind": "allow_once"},
                        {"optionId": "allow_always", "name": "Always", "kind": "allow_always"},
                        {"optionId": "reject_once", "name": "Reject", "kind": "reject_once"}]}})
        done.wait(30)
        chosen = (answers[ask_id][1] or {}).get("outcome", {}).get("optionId")
        text = "Allowed" if chosen == "allow_once" else "Denied"
        update(session, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}})
    else:
        for piece in ("Hello", " there"):
            update(session, {"sessionUpdate": "agent_message_chunk",
                             "content": {"type": "text", "text": piece}})
    update(session, {"sessionUpdate": "usage_update", "used": 100, "size": 1000})
    log_usage(session)
    send({"jsonrpc": "2.0", "id": message_id, "result": {"stopReason": "end_turn"}})


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        message = json.loads(line)
        if LOG:
            with open(LOG, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        method, message_id = message.get("method"), message.get("id")
        params = message.get("params") or {}
        if method is None and message_id in answers:  # a permission answer
            answers[message_id][1] = message.get("result")
            answers[message_id][0].set()
        elif method == "initialize":
            name = "Other" if MODE == "oldagent" else "Copilot"
            send({"jsonrpc": "2.0", "id": message_id, "result": {
                "protocolVersion": 1, "agentCapabilities": {"loadSession": True},
                "agentInfo": {"name": name, "version": "0.0.0-synthetic"}}})
        elif method == "session/new":
            session = str(uuid.uuid4())
            if SESSIONS:  # as Copilot records a session an ACP client started
                os.makedirs(os.path.join(SESSIONS, session), exist_ok=True)
                with open(os.path.join(SESSIONS, session, "workspace.yaml"), "w",
                          encoding="utf-8") as handle:
                    handle.write(f"id: {session}\nclient_name: github/acp\n")
            send({"jsonrpc": "2.0", "id": message_id,
                  "result": {"sessionId": session, "configOptions": options()}})
        elif method == "session/load":
            session = params.get("sessionId")
            update(session, {"sessionUpdate": "user_message_chunk",
                             "content": {"type": "text", "text": "Replayed history"}})
            update(session, {"sessionUpdate": "agent_message_chunk",
                             "content": {"type": "text", "text": "Replayed reply"}})
            send({"jsonrpc": "2.0", "id": message_id, "result": {"configOptions": options()}})
        elif method == "session/prompt":
            threading.Thread(target=prompt, args=(message_id, params.get("sessionId")),
                             daemon=True).start()
        elif method == "session/cancel":
            cancelled.set()
        elif message_id is not None:
            send({"jsonrpc": "2.0", "id": message_id, "error": {"code": -32601, "message": "no"}})


if __name__ == "__main__":
    main()
