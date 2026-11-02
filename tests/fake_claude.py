"""Stands in for ``claude -p`` stream-json, synthetic text only.

FAKE_CLAUDE_MODE: answer | ask | question | hang | apikey | unknown | error | die | badcontrol
FAKE_CLAUDE_LOG, if set, gets the arguments and every line received.
"""
import json
import os
import sys

MODE = os.environ.get("FAKE_CLAUDE_MODE", "answer")
LOG = os.environ.get("FAKE_CLAUDE_LOG", "")


def send(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def log(text):
    if LOG:
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def say(text, block="m1"):
    send({"type": "assistant", "message": {"id": block, "role": "assistant", "content": [{"type": "text", "text": text}]}})


def result(subtype="success", is_error=False):
    send({"type": "result", "subtype": subtype, "is_error": is_error, "usage": {"input_tokens": 10},
          "modelUsage": {"m": {"contextWindow": 100}}})


log("ARGS " + json.dumps(sys.argv[1:]))
send({"type": "system", "subtype": "init", "apiKeySource": "ANTHROPIC_API_KEY" if MODE == "apikey" else "none",
      "model": "m", "session_id": "x"})
state = {"asked": False}


def on_user(text):
    if MODE == "ask":
        send({"type": "assistant", "message": {"id": "m0", "content": [
            {"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": "echo synthetic"}}]}})
        send({"type": "control_request", "request_id": "perm-1", "request": {
            "subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "echo synthetic"}}})
        state["asked"] = True
    elif MODE == "question":
        send({"type": "control_request", "request_id": "q-1", "request": {
            "subtype": "can_use_tool", "tool_name": "AskUserQuestion", "input": {"questions": [
                {"question": "Which colour?", "header": "Colour",
                 "options": [{"label": "Red", "description": "warm"}, {"label": "Blue"}]}]}}})
        state["asked"] = True
    elif MODE == "hang":
        say("Working")
    elif MODE == "die":
        say("Working")
        sys.stdout.flush()
        os._exit(3)
    elif MODE == "error":
        result("error_during_execution", True)
    elif MODE == "apikey":
        say("This must never be reached")
        result()
    elif MODE == "unknown":
        send({"type": "something_new", "secret": "must never be stored"})
        send({"type": "something_new", "secret": "must never be stored"})
        say("Hello")
        result()
    elif MODE == "badcontrol":
        send({"type": "control_request", "request_id": "x-1", "request": {"subtype": "new_kind_of_request"}})
        say("Hello")
        result()
    else:
        say("Hello there: " + text)
        result()


for line in sys.stdin:
    log(line.rstrip())
    try:
        message = json.loads(line)
    except ValueError:
        continue
    if message.get("type") == "user":
        on_user(message["message"]["content"])
    elif message.get("type") == "control_response" and state["asked"]:
        response = message["response"]["response"]
        if MODE == "question":
            say("Answered: " + json.dumps(response.get("updatedInput", {}).get("answers")))
        else:
            say("Allowed" if response.get("behavior") == "allow" else "Denied")
        result()
