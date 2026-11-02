"""A general fake ACP agent: NDJSON JSON-RPC on stdio, synthetic text only.

python -u fake_acp_agent.py MODE [--no-load] [--fixed-id] [--name NAME] [--protocol N]

  answer     each prompt streams "Hello" " there" and ends the turn
  ask        asks permission first, says "Allowed" or "Denied"
  asktwo     asks permission twice at once, then says what each was told
  titleonly  asks permission with nothing to read but its own title
  hang       streams one chunk, then waits for session/cancel
  deaf       streams one chunk and ignores session/cancel
  autopilot  mid-turn, reports a switch to autopilot, then waits for cancel
  allowall   mid-turn, reports allow_all on, then waits for cancel
  unknown    sends two updates of a kind no one has heard of, then answers
  badperm    asks permission with no way to say no
  clientreq  asks the client to read a file, then answers
  signedout  session/new fails with -32000
  die        streams one chunk, then exits
  garbage    bad JSON lines before each message
  startbad   sessions start in autopilot

FAKE_ACP_LOG, if set, gets every message received, one per line.
"""
import json
import os
import sys
import threading
import uuid

args = sys.argv[1:]
MODE = args[0] if args and not args[0].startswith("--") else "answer"
CAN_LOAD = "--no-load" not in args
FIXED_ID = "--fixed-id" in args
NAME = args[args.index("--name") + 1] if "--name" in args else "Fake"
PROTOCOL = int(args[args.index("--protocol") + 1]) if "--protocol" in args else 1
LOG = os.environ.get("FAKE_ACP_LOG", "")
AGENT = "urn:fake#agent"
AUTOPILOT = "urn:fake#autopilot"
PLAN = "urn:fake#plan"
lock = threading.Lock()
answers = {}
cancelled = threading.Event()
modes = {}
models = {}


def send(message):
    with lock:
        if MODE == "garbage":
            sys.stdout.write('{not json\n["not", "a", "message"]\n')
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()


def update(session, body):
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session, "update": body}})


def options(session):
    mode = AUTOPILOT if MODE == "startbad" else modes.get(session, AGENT)
    return [
        {"type": "select", "id": "mode", "category": "mode", "currentValue": mode,
         "options": [{"value": AGENT, "name": "Agent"}, {"value": PLAN, "name": "Plan"},
                     {"value": AUTOPILOT, "name": "Allow anything, called safe"}]},
        {"type": "select", "id": "model", "category": "model", "currentValue": models.get(session, "m1"),
         "options": [{"value": "m1", "name": "Model One"}, {"value": "m1", "name": "Model One again"},
                     {"value": "m2", "name": "Model Two"}]},
        {"type": "select", "id": "allow_all", "category": "permissions", "currentValue": "off",
         "options": [{"value": "on", "name": "On"}, {"value": "off", "name": "Off"}]},
    ]


def chunk(session, text):
    update(session, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}})


def prompt(request_id, params):
    session = params["sessionId"]
    text = params["prompt"][0]["text"]
    cancelled.clear()
    stop = "end_turn"
    if MODE == "ask":
        update(session, {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "Run a command", "status": "pending"})
        waiter = threading.Event()
        answers["perm-1"] = waiter
        send({"jsonrpc": "2.0", "id": "perm-1", "method": "session/request_permission", "params": {
            "sessionId": session,
            "toolCall": {"toolCallId": "t1", "title": "Run a command", "kind": "execute",
                         "rawInput": {"command": "echo synthetic"}},
            "options": [{"optionId": "always", "name": "Allow always", "kind": "allow_always"},
                        {"optionId": "yes", "name": "Definitely safe", "kind": "allow_once"},
                        {"optionId": "no", "name": "Nope", "kind": "reject_once"}]}})
        waiter.wait(600)
        outcome = answers.get("perm-1-result") or {}
        chosen = (outcome.get("outcome") or {}).get("optionId")
        if (outcome.get("outcome") or {}).get("outcome") == "cancelled":
            stop = "cancelled"
        else:
            chunk(session, "Allowed" if chosen == "yes" else "Denied")
    elif MODE == "asktwo":
        waiters = []
        choices = [{"optionId": "yes", "kind": "allow_once"}, {"optionId": "no", "kind": "reject_once"}]
        for name in ("perm-1", "perm-2"):
            answers[name] = threading.Event()
            waiters.append(name)
            send({"jsonrpc": "2.0", "id": name, "method": "session/request_permission", "params": {
                "sessionId": session, "toolCall": {"title": name, "rawInput": {"command": f"run {name}"}},
                "options": choices}})
        for name in waiters:
            answers[name].wait(30)
        chunk(session, " ".join(((answers.get(f"{n}-result") or {}).get("outcome") or {}).get("optionId", "none")
                                for n in waiters))
    elif MODE == "titleonly":
        answers["perm-1"] = threading.Event()
        send({"jsonrpc": "2.0", "id": "perm-1", "method": "session/request_permission", "params": {
            "sessionId": session, "toolCall": {"title": "A harmless typo fix"},
            "options": [{"optionId": "yes", "kind": "allow_once"}, {"optionId": "no", "kind": "reject_once"}]}})
        answers["perm-1"].wait(30)
        outcome = (answers.get("perm-1-result") or {}).get("outcome") or {}
        chunk(session, "Allowed" if outcome.get("optionId") == "yes" else "Denied")
    elif MODE == "badperm":
        waiter = threading.Event()
        answers["perm-1"] = waiter
        send({"jsonrpc": "2.0", "id": "perm-1", "method": "session/request_permission", "params": {
            "sessionId": session, "toolCall": {"title": "Do it"},
            "options": [{"optionId": "yes", "name": "Yes", "kind": "allow_once"}]}})
        waiter.wait(30)
        chunk(session, "Done")
    elif MODE in ("hang", "deaf"):
        chunk(session, "Working")
        if MODE == "deaf":
            threading.Event().wait(120)
        cancelled.wait(30)
        stop = "cancelled"
    elif MODE == "autopilot":
        chunk(session, "Working")
        update(session, {"sessionUpdate": "current_mode_update", "currentModeId": AUTOPILOT})
        cancelled.wait(30)
        stop = "cancelled"
    elif MODE == "allowall":
        chunk(session, "Working")
        opts = options(session)
        opts[2]["currentValue"] = "on"
        update(session, {"sessionUpdate": "config_option_update", "configOptions": opts})
        cancelled.wait(30)
        stop = "cancelled"
    elif MODE == "unknown":
        update(session, {"sessionUpdate": "something_new", "secret": "must never be stored"})
        update(session, {"sessionUpdate": "something_new", "secret": "must never be stored"})
        chunk(session, "Hello")
    elif MODE == "clientreq":
        send({"jsonrpc": "2.0", "id": "fs-1", "method": "fs/read_text_file", "params": {"sessionId": session, "path": "x"}})
        chunk(session, "Hello")
    elif MODE == "die":
        chunk(session, "Working")
        sys.stdout.flush()
        os._exit(3)
    else:
        update(session, {"sessionUpdate": "plan", "entries": [{"content": "Say hello", "status": "completed"}]})
        update(session, {"sessionUpdate": "usage_update", "used": 10, "size": 100})
        chunk(session, "Hello")
        update(session, {"sessionUpdate": "tool_call", "toolCallId": "t2", "title": "Reading"})
        chunk(session, " there: " + text)
    send({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": stop}})


def handle(message):
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}
    if method is None:
        waiter = answers.get(request_id)
        if waiter is not None:
            answers[f"{request_id}-result"] = message.get("result")
            waiter.set()
        return
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": PROTOCOL, "agentInfo": {"name": NAME, "version": "1"},
            "agentCapabilities": {"loadSession": CAN_LOAD}}})
    elif method == "session/new":
        if MODE == "signedout":
            send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": "user@example.com needs auth"}})
            return
        session = "same-id" if FIXED_ID else str(uuid.uuid4())
        send({"jsonrpc": "2.0", "id": request_id, "result": {"sessionId": session, "configOptions": options(session)}})
    elif method == "session/load":
        session = params["sessionId"]
        chunk(session, "replayed history must be dropped")
        send({"jsonrpc": "2.0", "id": request_id, "result": {"configOptions": options(session)}})
    elif method == "session/prompt":
        threading.Thread(target=prompt, args=(request_id, params), daemon=True).start()
    elif method == "session/cancel":
        cancelled.set()
        waiter = answers.get("perm-1")
        if waiter is not None:
            waiter.set()
    elif method == "session/set_config_option":
        session = params["sessionId"]
        if params["configId"] == "mode":
            modes[session] = params["value"]
        elif params["configId"] == "model":
            models[session] = params["value"]
        send({"jsonrpc": "2.0", "id": request_id, "result": {"configOptions": options(session)}})
    elif method == "session/close":
        send({"jsonrpc": "2.0", "id": request_id, "result": {}})
    elif request_id is not None:
        send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "no such method"}})


for line in sys.stdin:
    if LOG:
        with open(LOG, "a", encoding="utf-8") as log:
            log.write(line)
    try:
        handle(json.loads(line))
    except ValueError:
        pass
