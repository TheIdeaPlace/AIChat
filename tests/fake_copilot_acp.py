"""Stands in for ``copilot --acp`` in tests: NDJSON JSON-RPC on stdio.

Shaped like what Copilot CLI 1.0.103 sent (dev-notes/copilot-cli-probe.md),
with synthetic text only. Run as ``python -u fake_copilot_acp.py MODE``:

  answer      each prompt streams "Hello" " there" and ends the turn
  ask         each prompt asks permission to run a command first (echo
              synthetic, or FAKE_COPILOT_COMMAND), then says
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
  edit        each prompt asks to create a file, shaped like Copilot 1.0.103's
              real request (rawInput.fileName and rawInput.diff); allowed, the
              log gets the tool's fileEdits, as Copilot logs them
  editcontent the same, with the diff in ACP's toolCall.content instead
  clientreq   each prompt first asks the client to read a file and to make a
              terminal, then answers; FAKE_COPILOT_LOG gets the replies
  strayperm   each prompt first sends a permission request for another session
  die         each prompt streams one chunk, then the process exits
  deaf        each prompt streams one chunk and ignores session/cancel
  garbage     writes bad JSON lines before every message
  mute        never answers session/new
  flipback    mid-turn, reports allow_all on, waits for session/cancel,
              reports allow_all off, waits two seconds, then ends the turn
              normally anyway (end_turn), as Copilot may despite a cancel
  byokhang    the first prompt waits for session/cancel; later prompts are
              billed to another provider
  slow        as answer, after a second and a half, so a message can wait its turn;
              the reply ends with the message it answers ("Hello there: First")
  hookran     each prompt's log shows a preToolUse hook running
  twousers    each prompt's log shows a second user.message, as another
              Copilot sending to the same chat at the same time leaves
  inuse       session/load logs a session.resume saying the chat is already
              in use elsewhere, at once
  inuselater  the same, logged when the first prompt after session/load
              arrives (as 1.0.103 logs every session.resume)
  signedout   never signed in: session/new, session/load and prompts all fail with
              ACP's -32000 "Authentication required", as on a clean machine (#391)
  expired     chats open, but each prompt is logged, then fails a second later
              with -32000, as when a sign-in stops working mid-chat
  toplan      mid-turn, reports (current_mode_update) a switch to plan mode
  modeflip    as hang, and session/set_config_option's reply for the mode says
              allow_all is on
  nomode      session/set_config_option for the mode always fails
  keepagent   session/set_config_option for the mode says yes, but its reply
              still names agent mode
  modeless    session/set_config_option for the mode says yes, but its reply
              leaves the mode out
  forgetmode  session/load reopens a chat in agent mode, whatever it was in
  startplan   chats start in plan mode, as if that were Copilot's own default
  nomodel     session/set_config_option always fails, as for a model the plan
              no longer allows
  modelflip   as hang, and session/set_config_option's reply says allow_all is
              on: Copilot no longer asking before it acts
  refused     chats open, but each prompt fails at once with -32000 before
              anything is logged, not even its user.message (#405)
  strict      as hang, but a session/cancel that arrives while no prompt is
              running is ignored, as real Copilot ignores one that comes
              before the prompt it was meant for

Every prompt logs a user.message first, as Copilot does (except in refused).

FAKE_COPILOT_SESSIONS names the folder session logs are written under, as
Copilot writes ~/.copilot/session-state/<id>/events.jsonl.
FAKE_COPILOT_LOG, if set, gets every message received, one per line.
"""
import json
import os
import sys
import threading
import time
import uuid

MODE = sys.argv[1] if len(sys.argv) > 1 else "answer"
SESSIONS = os.environ.get("FAKE_COPILOT_SESSIONS", "")
LOG = os.environ.get("FAKE_COPILOT_LOG", "")
AGENT = "https://agentclientprotocol.com/protocol/session-modes#agent"
AUTOPILOT = "https://agentclientprotocol.com/protocol/session-modes#autopilot"
PLAN = "https://agentclientprotocol.com/protocol/session-modes#plan"
#: Each session's mode, as session/set_config_option left it.
mode_of = {}
write_lock = threading.Lock()
answers = {}
#: Sessions open in this process: like Copilot 1.0.103, it won't load one
#: that's already open until it's closed (session/close).
open_sessions = set()
#: Reopened sessions whose session.resume is still to be logged: Copilot
#: writes it when the first prompt after session/load arrives.
resume_pending = {}
turns = [0]
client = ["github/acp"]  # what Copilot records unless clientInfo names one
cancelled = threading.Event()
prompting = threading.Event()  # strict: set as a prompt is read, before its thread


#: The command "ask" asks to run (FAKE_COPILOT_COMMAND): Auto, rules and
#: Allow for This Chat are tested with ones they do and don't cover (#426).
ASK_COMMAND = os.environ.get("FAKE_COPILOT_COMMAND", "echo synthetic")

def send(message):
    with write_lock:
        if MODE == "garbage":
            sys.stdout.write("{not json\n[\"also\", \"not\", \"a\", \"message\"]\n")
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()


def update(session, body):
    send({"jsonrpc": "2.0", "method": "session/update",
          "params": {"sessionId": session, "update": body}})


#: The models offered, shaped like Copilot 1.0.105's: Auto twice (once
#: with its cost), then models with theirs. Synthetic names.
MODELS = [{"value": "auto", "name": "Auto", "description": "Let Copilot pick", "_meta": None},
          {"value": "auto", "name": "Auto", "description": "Auto",
           "_meta": {"copilotUsage": "1x", "copilotEnablement": "enabled"}},
          {"value": "gpt-test", "name": "GPT Test", "_meta": {"copilotUsage": "1x"}},
          {"value": "gpt-mini", "name": "GPT Mini", "_meta": {"copilotUsage": "0.33x"}}]
#: Each session's model, as session/set_config_option left it.
model_of = {}


def options(session=None):
    mode = AUTOPILOT if MODE == "unsafe" else mode_of.get(session, PLAN if MODE == "startplan" else AGENT)
    allow = "on" if MODE == "allowall" else "off"
    return [{"type": "select", "id": "mode", "currentValue": mode},
            {"type": "select", "id": "model", "currentValue": model_of.get(session, "auto"),
             "options": MODELS},
            {"type": "select", "id": "allow_all", "currentValue": allow}]


def hold_lock(session):
    """As Copilot 1.0.103 does with a chat it has open: inuse.<its pid>.lock
    in the session folder, left behind when it exits."""
    if not SESSIONS:
        return
    folder = os.path.join(SESSIONS, session)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f"inuse.{os.getpid()}.lock"), "w", encoding="utf-8"):
        pass


def log_event(session, kind, data):
    if not SESSIONS:
        return
    folder = os.path.join(SESSIONS, session)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "events.jsonl"), "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": kind, "data": data, "id": uuid.uuid4().hex}) + "\n")


def log_usage(session):
    if not SESSIONS or MODE == "nolog":
        return
    folder = os.path.join(SESSIONS, session)
    os.makedirs(folder, exist_ok=True)
    later = MODE in ("byok2", "byokhang") and turns[0] > 1
    kind = "openai" if MODE == "byok" or later else "copilot"
    with open(os.path.join(folder, "events.jsonl"), "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "session.usage_record", "data": {"usage": {
            "model": "synthetic", "provider": {"id": kind, "kind": kind}}}}) + "\n")


def ask(session, tool_call):
    """Send a permission request and wait for its answer; the chosen option."""
    ask_id = "perm-" + uuid.uuid4().hex[:8]
    done = threading.Event()
    answers[ask_id] = [done, None]
    send({"jsonrpc": "2.0", "id": ask_id, "method": "session/request_permission", "params": {
        "sessionId": session, "toolCall": tool_call,
        "options": [{"optionId": "allow_once", "name": "Allow once", "kind": "allow_once"},
                    {"optionId": "allow_always", "name": "Always", "kind": "allow_always"},
                    {"optionId": "reject_once", "name": "Reject", "kind": "reject_once"}]}})
    done.wait(30)
    return ((answers[ask_id][1] or {}).get("outcome") or {}).get("optionId")


def prompt(message_id, session, text=""):
    turns[0] += 1
    if session in resume_pending:
        log_event(session, "session.resume", {"alreadyInUse": resume_pending.pop(session)})
    if MODE == "refused":
        send({"jsonrpc": "2.0", "id": message_id,
              "error": {"code": -32000, "message": "Authentication required"}})
        return
    log_event(session, "user.message", {"content": "synthetic"})
    if MODE == "expired":
        time.sleep(1.0)  # long enough for a second message to be queued
        send({"jsonrpc": "2.0", "id": message_id,
              "error": {"code": -32000, "message": "Authentication required"}})
        return
    if MODE == "twousers":
        log_event(session, "user.message", {"content": "from elsewhere"})
    if MODE == "hookran":
        log_event(session, "hook.start", {"hookType": "preToolUse", "input": {}})
    if MODE in ("edit", "editcontent"):
        call = {"toolCallId": "call-e", "title": "Create file", "kind": "edit",
                "locations": [{"path": "C:/synthetic/a.txt"}]}
        if MODE == "edit":
            call["rawInput"] = {"fileName": "C:/synthetic/a.txt",
                                "diff": "--- a/dev/null\n+++ b/C:/synthetic/a.txt\n+hello\n"}
        else:
            call["rawInput"] = {}
            call["content"] = [{"type": "diff", "path": "C:/synthetic/a.txt",
                                "oldText": "", "newText": "hello"}]
        chosen = ask(session, call)
        if chosen == "allow_once":
            log_event(session, "tool.execution_complete", {
                "toolCallId": "call-e", "success": True,
                "fileEdits": [{"path": "C:/synthetic/a.txt", "kind": "create"}]})
        update(session, {"sessionUpdate": "agent_message_chunk", "content": {
            "type": "text", "text": "Written" if chosen == "allow_once" else "Not written"}})
        log_usage(session)
        send({"jsonrpc": "2.0", "id": message_id, "result": {"stopReason": "end_turn"}})
        return
    if MODE == "clientreq":
        send({"jsonrpc": "2.0", "id": "fs-1", "method": "fs/read_text_file",
              "params": {"sessionId": session, "path": "C:/synthetic/secret.txt"}})
        send({"jsonrpc": "2.0", "id": "term-1", "method": "terminal/create",
              "params": {"sessionId": session, "command": "echo"}})
    if MODE == "strayperm":
        send({"jsonrpc": "2.0", "id": "stray-1", "method": "session/request_permission",
              "params": {"sessionId": "not-a-session-of-yours", "toolCall": {"kind": "execute"},
                         "options": [{"optionId": "reject_once", "kind": "reject_once"}]}})
    if MODE in ("die", "deaf"):
        update(session, {"sessionUpdate": "agent_message_chunk",
                         "content": {"type": "text", "text": "Working"}})
        if MODE == "die":
            os._exit(1)
        threading.Event().wait(60)  # deaf: never ends, ignores cancel
        return
    if MODE == "slow":
        time.sleep(1.5)
    if MODE in ("flip", "curmode", "toplan"):
        # Long enough for a second message to be queued first, even on the
        # macOS CI runner, where send() reads hooks and the log before queuing.
        time.sleep(1.0)
    if MODE == "flip":
        update(session, {"sessionUpdate": "config_option_update", "configOptions": [
            {"type": "select", "id": "mode", "currentValue": AUTOPILOT},
            {"type": "select", "id": "allow_all", "currentValue": "off"}]})
    if MODE == "curmode":
        update(session, {"sessionUpdate": "current_mode_update", "currentModeId": AUTOPILOT})
    if MODE == "toplan":
        update(session, {"sessionUpdate": "current_mode_update", "currentModeId": PLAN})
    if MODE == "flipback":
        time.sleep(1.0)  # long enough for a second message to be queued, even on macOS CI
        update(session, {"sessionUpdate": "config_option_update", "configOptions": [
            {"type": "select", "id": "mode", "currentValue": AGENT},
            {"type": "select", "id": "allow_all", "currentValue": "on"}]})
        cancelled.wait(10)
        update(session, {"sessionUpdate": "config_option_update", "configOptions": [
            {"type": "select", "id": "mode", "currentValue": AGENT},
            {"type": "select", "id": "allow_all", "currentValue": "off"}]})
        time.sleep(2.0)  # long enough for a third message to be queued
    if MODE in ("hang", "strict", "modelflip", "modeflip") or (MODE == "byokhang" and turns[0] == 1):
        update(session, {"sessionUpdate": "agent_message_chunk",
                         "content": {"type": "text", "text": "Working"}})
        cancelled.wait(30)
        if MODE == "strict":
            # Before the answer: the next prompt is read only after it, so a
            # cancel for that one starts from nothing, as for the first.
            prompting.clear()
            cancelled.clear()
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
                         "kind": "execute", "rawInput": {
                             "command": ASK_COMMAND,
                             # As Copilot 1.0.103 lists the commands it found.
                             "commands": [{"identifier": ASK_COMMAND, "readOnly": False}]}},
            "options": [{"optionId": "allow_once", "name": "Allow once", "kind": "allow_once"},
                        {"optionId": "allow_always", "name": "Always", "kind": "allow_always"},
                        {"optionId": "reject_once", "name": "Reject", "kind": "reject_once"}]}})
        done.wait(30)
        chosen = (answers[ask_id][1] or {}).get("outcome", {}).get("optionId")
        text = "Allowed" if chosen == "allow_once" else "Denied"
        update(session, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}})
    else:
        reply = f"Hello there: {text}" if MODE == "slow" else "Hello there"
        for piece in ("Hello", reply[len("Hello"):]):
            update(session, {"sessionUpdate": "agent_message_chunk",
                             "content": {"type": "text", "text": piece}})
        # As Copilot logs a step: the whole reply, at the step's end.
        log_event(session, "assistant.turn_start", {"turnId": str(turns[0])})
        log_event(session, "assistant.message", {"content": reply})
        log_event(session, "assistant.turn_end", {"turnId": str(turns[0])})
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
        elif method is None:
            pass  # a reply to one of our requests (fs/terminal): logged above
        elif method == "initialize":
            info = params.get("clientInfo") or {}
            if isinstance(info, dict) and info.get("name"):
                client[0] = info["name"]  # as Copilot records it
            name = "Other" if MODE == "oldagent" else "Copilot"
            send({"jsonrpc": "2.0", "id": message_id, "result": {
                "protocolVersion": 1, "agentCapabilities": {"loadSession": True},
                "agentInfo": {"name": name, "version": "0.0.0-synthetic"}}})
        elif MODE == "signedout" and method in ("session/new", "session/load", "session/prompt"):
            send({"jsonrpc": "2.0", "id": message_id,
                  "error": {"code": -32000, "message": "Authentication required"}})
        elif method == "session/new":
            if MODE == "mute":
                continue
            session = str(uuid.uuid4())
            if SESSIONS:  # as Copilot records a session an ACP client started
                os.makedirs(os.path.join(SESSIONS, session), exist_ok=True)
                with open(os.path.join(SESSIONS, session, "workspace.yaml"), "w",
                          encoding="utf-8") as handle:
                    handle.write(f"id: {session}\nclient_name: {client[0]}\n")
            open_sessions.add(session)
            hold_lock(session)
            send({"jsonrpc": "2.0", "id": message_id,
                  "result": {"sessionId": session, "configOptions": options(session)}})
        elif method == "session/load":
            session = params.get("sessionId")
            if session in open_sessions:
                send({"jsonrpc": "2.0", "id": message_id,
                      "error": {"code": -32603, "message": "already loaded"}})
                continue
            open_sessions.add(session)
            hold_lock(session)
            if MODE == "inuse":  # logged at once, which 1.0.103 doesn't, but might
                log_event(session, "session.resume", {"alreadyInUse": True})
            else:
                resume_pending[session] = MODE == "inuselater"
            update(session, {"sessionUpdate": "user_message_chunk",
                             "content": {"type": "text", "text": "Replayed history"}})
            update(session, {"sessionUpdate": "agent_message_chunk",
                             "content": {"type": "text", "text": "Replayed reply"}})
            if MODE == "forgetmode":
                mode_of.pop(session, None)  # reopened in agent mode
            send({"jsonrpc": "2.0", "id": message_id,
                  "result": {"configOptions": options(session)}})
        elif method == "session/set_config_option" and params.get("configId") == "model":
            session, value = params.get("sessionId"), params.get("value")
            if MODE == "nomodel" or value not in {m["value"] for m in MODELS}:
                send({"jsonrpc": "2.0", "id": message_id,
                      "error": {"code": -32603, "message": "no such model"}})
                continue
            model_of[session] = value
            reply = options(session)
            if MODE == "modelflip":
                reply[-1] = {"type": "select", "id": "allow_all", "currentValue": "on"}
            send({"jsonrpc": "2.0", "id": message_id, "result": {"configOptions": reply}})
        elif method == "session/prompt":
            text = ((params.get("prompt") or [{}])[0] or {}).get("text", "")
            prompting.set()  # here, not in the thread: a cancel read next sees it
            threading.Thread(target=prompt, args=(message_id, params.get("sessionId"), text),
                             daemon=True).start()
        elif method == "session/set_config_option":
            session, value = params.get("sessionId"), params.get("value")
            if (MODE == "nomode" or params.get("configId") != "mode"
                    or value not in (AGENT, PLAN, AUTOPILOT)):
                send({"jsonrpc": "2.0", "id": message_id,
                      "error": {"code": -32603, "message": "no"}})
                continue
            if MODE != "keepagent":
                mode_of[session] = value
            reply = options(session)
            if MODE == "modeflip":
                reply[-1] = {"type": "select", "id": "allow_all", "currentValue": "on"}
            if MODE == "modeless":
                reply = [option for option in reply if option["id"] != "mode"]
            send({"jsonrpc": "2.0", "id": message_id, "result": {"configOptions": reply}})
        elif method == "session/close":
            open_sessions.discard(params.get("sessionId"))
            send({"jsonrpc": "2.0", "id": message_id, "result": {}})
        elif method == "session/cancel":
            if MODE != "deaf" and (MODE != "strict" or prompting.is_set()):
                cancelled.set()
        elif message_id is not None:
            send({"jsonrpc": "2.0", "id": message_id, "error": {"code": -32601, "message": "no"}})


if __name__ == "__main__":
    main()
