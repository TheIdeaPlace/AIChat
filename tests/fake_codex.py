"""Stands in for ``codex app-server``: NDJSON JSON-RPC, synthetic text only.

Usage: fake_codex.py MODE, with FAKE_CODEX_DIR holding threads across restarts.
MODE: answer | ask | question | hang | unknown | apikey | fail | die | newrequest | netask
"""
import json
import os
import sys
import threading
import uuid
from pathlib import Path

MODE = sys.argv[1] if len(sys.argv) > 1 else "answer"
FOLDER = Path(os.environ.get("FAKE_CODEX_DIR", "."))
LOG = os.environ.get("FAKE_CODEX_LOG", "")
lock = threading.Lock()
waiters = {}
turn_ids = {}
interrupted = threading.Event()


def send(message):
    with lock:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()


def note(method, **params):
    send({"method": method, "params": params})


def threads():
    path = FOLDER / "threads.json"
    return json.loads(path.read_text()) if path.exists() else {}


def save(data):
    (FOLDER / "threads.json").write_text(json.dumps(data))


def record(thread, user, reply):
    data = threads()
    data.setdefault(thread, {"turns": []})["turns"].append({"id": str(uuid.uuid4()), "items": [
        {"id": "u", "type": "userMessage", "content": [{"type": "text", "text": user}]},
        {"id": "a", "type": "agentMessage", "text": reply}]})
    save(data)


def ask(rpc, method, params):
    waiter = threading.Event()
    waiters[rpc] = waiter
    send({"id": rpc, "method": method, "params": params})
    waiter.wait(30)
    return waiters.get(f"{rpc}-result")


def turn(thread, turn_id, text):
    note("turn/started", threadId=thread, turn={"id": turn_id, "status": "inProgress"})
    status, reply = "completed", ""
    if MODE == "ask":
        note("item/started", threadId=thread, turnId=turn_id, item={"id": "c1", "type": "commandExecution", "command": "echo synthetic"})
        answer = ask("appr-1", "item/commandExecution/requestApproval", {
            "threadId": thread, "turnId": turn_id, "itemId": "c1", "command": "echo synthetic", "cwd": "."})
        reply = "Allowed" if (answer or {}).get("decision") == "accept" else "Denied"
    elif MODE == "netask":
        answer = ask("appr-1", "item/commandExecution/requestApproval", {
            "threadId": thread, "turnId": turn_id, "itemId": "c1", "command": "curl",
            "cwd": ".", "networkApprovalContext": {"host": "example.com"}})
        reply = "Allowed" if (answer or {}).get("decision") == "accept" else "Denied"
    elif MODE == "question":
        answer = ask("q-1", "item/tool/requestUserInput", {
            "threadId": thread, "turnId": turn_id, "itemId": "q", "isBlocking": True, "questions": [
                {"id": "colour", "header": "Colour", "question": "Which colour?", "isOther": False, "isSecret": False,
                 "options": [{"label": "Red", "description": "warm"}, {"label": "Blue", "description": ""}]}]})
        reply = "Answered: " + json.dumps((answer or {}).get("answers"))
    elif MODE == "hang":
        note("item/agentMessage/delta", threadId=thread, turnId=turn_id, itemId="a1", delta="Working")
        interrupted.wait(30)
        status = "interrupted"
    elif MODE == "unknown":
        note("item/completed", threadId=thread, turnId=turn_id, item={"id": "n", "type": "somethingNew", "secret": "must never be stored"})
        note("turn/somethingNew", threadId=thread, turnId=turn_id, secret="must never be stored")
        reply = "Hello"
    elif MODE == "newrequest":
        send({"id": "x-1", "method": "item/permissions/requestApproval", "params": {"threadId": thread, "turnId": turn_id}})
        reply = "Hello"
    elif MODE == "fail":
        status = "failed"
    elif MODE == "die":
        note("item/agentMessage/delta", threadId=thread, turnId=turn_id, itemId="a1", delta="Working")
        sys.stdout.flush()
        os._exit(3)
    else:
        note("turn/plan/updated", threadId=thread, turnId=turn_id, plan=[{"step": "Say hello", "status": "completed"}])
        note("item/agentMessage/delta", threadId=thread, turnId=turn_id, itemId="a1", delta="Hello")
        reply = "Hello there: " + text
    if reply:
        note("item/completed", threadId=thread, turnId=turn_id, item={"id": "a1", "type": "agentMessage", "text": reply})
        record(thread, text, reply)
    note("turn/completed", threadId=thread, turn={"id": turn_id, "status": status})


def handle(message):
    method, rpc = message.get("method"), message.get("id")
    params = message.get("params") or {}
    if method is None:
        waiter = waiters.get(rpc)
        if waiter is not None:
            waiters[f"{rpc}-result"] = message.get("result")
            waiter.set()
        return
    if method == "initialize":
        send({"id": rpc, "result": {"userAgent": "fake"}})
    elif method == "initialized":
        pass
    elif method == "account/read":
        kind = "apikey" if MODE == "apikey" else "chatgpt"
        send({"id": rpc, "result": {"account": {"type": kind}, "requiresOpenaiAuth": True}})
    elif method == "thread/start":
        thread = str(uuid.uuid4())
        data = threads()
        data[thread] = {"turns": []}
        save(data)
        send({"id": rpc, "result": {"thread": {"id": thread}, "cwd": params.get("cwd")}})
    elif method == "thread/resume":
        send({"id": rpc, "result": {"thread": {"id": params["threadId"]}, "cwd": params.get("cwd")}})
    elif method == "turn/start":
        turn_id = str(uuid.uuid4())
        interrupted.clear()
        send({"id": rpc, "result": {"turn": {"id": turn_id, "status": "inProgress"}}})
        threading.Thread(target=turn, args=(params["threadId"], turn_id, params["input"][0]["text"]), daemon=True).start()
    elif method == "turn/interrupt":
        interrupted.set()
        send({"id": rpc, "result": {}})
    elif method == "thread/list":
        data = threads()
        send({"id": rpc, "result": {"data": [{"id": key, "preview": "A chat", "cwd": ".", "updatedAt": 1}
                                             for key in data], "nextCursor": None}})
    elif method == "thread/read":
        data = threads()
        send({"id": rpc, "result": {"thread": {"id": params["threadId"], "turns": data.get(params["threadId"], {}).get("turns", [])}}})
    elif rpc is not None:
        send({"id": rpc, "error": {"code": -32601, "message": "no such method"}})


for line in sys.stdin:
    if LOG:
        with open(LOG, "a", encoding="utf-8") as handle_log:
            handle_log.write(line)
    try:
        handle(json.loads(line))
    except ValueError:
        pass
