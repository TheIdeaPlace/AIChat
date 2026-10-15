"""Synthetic stdio server; never reads account data or runs an agent."""
import json
import sys
import threading
import time

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

lock = threading.Lock()
pair = []
initialized = False
account_reads = 0
config_reads = 0


def emit(message, partial=False):
    raw = json.dumps(message, ensure_ascii=False) + "\n"
    with lock:
        if partial:
            middle = len(raw) // 2
            sys.stdout.write(raw[:middle])
            sys.stdout.flush()
            time.sleep(.01)
            sys.stdout.write(raw[middle:])
        else:
            sys.stdout.write(raw)
        sys.stdout.flush()


for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialize":
        if len(sys.argv) > 1 and sys.argv[1] == "hang-init":
            continue
        if len(sys.argv) > 1 and sys.argv[1] == "bad-init":
            emit({"id": request["id"], "error": {"message": "private init error"}})
            continue
        emit({"id": request["id"], "result": {"userAgent": "fake"}})
        continue
    if method == "initialized":
        initialized = True
        continue
    if not initialized:
        emit({"id": request["id"], "error": {"message": "not initialized"}})
        continue
    params = request.get("params", {})
    if method == "account/read" and len(sys.argv) > 1 and sys.argv[1].startswith("account-"):
        account_reads += 1
        kind = sys.argv[1][8:]
        if kind.startswith("route-"):
            kind = "chatgpt"
        if kind == "changed":
            kind = "chatgpt" if account_reads == 1 else "apiKey"
        result = {"account": {"type": kind}, "requiresOpenaiAuth": True}
        if kind == "missing":
            result["account"] = None
        if kind == "other-provider":
            result = {"account": {"type": "chatgpt"}, "requiresOpenaiAuth": False}
        emit({"id": request["id"], "result": result})
        continue
    scenario = sys.argv[1] if len(sys.argv) > 1 else ""
    if scenario.startswith("account-") and method == "thread/read":
        thread = {"id": params["threadId"], "modelProvider": "openai", "cwd": "/synthetic"}
        if scenario == "account-route-provider":
            thread["modelProvider"] = "custom"
        if scenario == "account-route-wrong-id":
            thread["id"] = "other"
        if scenario == "account-route-missing-cwd":
            thread.pop("cwd")
        emit({"id": request["id"], "result": {"thread": thread}})
        continue
    if scenario.startswith("account-") and method == "config/read":
        config_reads += 1
        config = {}
        kind = scenario.removeprefix("account-route-")
        if kind in ("openai_base_url", "chatgpt_base_url", "model_provider", "forced_login_method"):
            config[kind] = "alternate"
        if kind == "builtin-override":
            config["model_providers"] = {"openai": {"env_key": "ALTERNATE_KEY"}}
        if kind == "malformed-providers":
            config["model_providers"] = []
        if kind == "malformed-config":
            config = None
        if kind == "changed" and config_reads > 1:
            config["openai_base_url"] = "alternate"
        if kind == "cwd-sensitive" and params.get("cwd") != "/synthetic":
            config["model_provider"] = "alternate"
        emit({"id": request["id"], "result": {"config": config, "echo": params}})
        continue
    mode = params.get("mode")
    if mode in ("events", "event-overflow", "bad-event"):
        if mode == "bad-event":
            emit({"method": "item/agentMessage/delta", "params": {"threadId": "one"}})
        elif mode == "event-overflow":
            for number in range(64):
                emit({"method": "item/agentMessage/delta", "params": {
                    "threadId": "one", "turnId": "turn", "itemId": "reply", "delta": str(number)}})
        else:
            for notification, data in [
                ("turn/started", {"threadId": "one", "turn": {"id": "a", "status": "inProgress"}}),
                ("item/agentMessage/delta", {"threadId": "one", "turnId": "a", "itemId": "reply", "delta": "Draft"}),
                ("item/agentMessage/delta", {"threadId": "two", "turnId": "b", "itemId": "reply", "delta": "Other"}),
                ("item/completed", {"threadId": "one", "turnId": "a", "item": {"type": "agentMessage", "id": "reply", "text": "Final result"}}),
                ("turn/completed", {"threadId": "one", "turn": {"id": "a", "status": "completed"}}),
                ("turn/completed", {"threadId": "two", "turn": {"id": "b", "status": "interrupted", "error": {"message": "private secret"}}})]:
                emit({"method": notification, "params": data}, partial=True)
    if mode == "hang":
        continue
    if mode == "exit":
        break
    if mode == "malformed":
        print("private invalid JSON", flush=True)
        continue
    if mode == "nested":
        print("[" * 10000 + "0" + "]" * 10000, flush=True)
        continue
    if mode == "server-request":
        emit({"id": "approval", "method": "item/commandExecution/requestApproval",
              "params": {"private": "do not approve"}})
        continue
    if mode == "unknown-id":
        emit({"id": 999, "result": {}})
        continue
    if mode == "bad-result":
        emit({"id": request["id"], "result": []})
        continue
    if mode == "error":
        emit({"id": request["id"], "error": {"message": "private secret"}})
        continue
    response = {"id": request["id"], "result": {"echo": params}}
    if mode == "pair":
        pair.append(response)
        if len(pair) == 2:
            emit({"method": "future/notification", "params": {"text": "ignored"}})
            for message in reversed(pair):
                emit(message, partial=True)
            pair.clear()
    else:
        emit(response, partial=True)
