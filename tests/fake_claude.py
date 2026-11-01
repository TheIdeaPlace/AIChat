"""A stand-in for `claude -p --input-format stream-json --output-format stream-json`,
for tests.

Reads stream-json lines from stdin as raw bytes until the user's message,
records what it got, and writes stream-json to stdout as raw UTF-8 (with a raw
U+2028 inside one string, as JSON writers leave it). FAKE_CLAUDE_MODE=hang
starts a grandchild process and waits, so a test can check that cancelling
kills the whole tree. FAKE_CLAUDE_MODE=ask sends a permission request and
waits for the answer, which goes in the result. FAKE_CLAUDE_MODE=elicit
(a form) and elicit-url (a page to open) send an MCP server's elicitation
request the same way (#468). After the result it waits for stdin to close,
as the real CLI does. With ``--rewind-files`` it answers as Claude Code
does (#468) and writes rewound.txt in its folder;
FAKE_CLAUDE_MODE=rewind-unknown refuses the message.
"""
import json
import os
import subprocess
import sys
import time

if "--rewind-files" in sys.argv:
    # Rewind to Here's files (#468), as Claude Code 2.1.296 answers: no turn
    # and no stdin; a settings warning first on stderr, then the outcome.
    target = sys.argv[sys.argv.index("--rewind-files") + 1]
    path = os.environ.get("FAKE_CLAUDE_LOG")
    if path:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"args": sys.argv[1:], "checkpoints": os.environ.get(
                "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING", "")}, handle)
    sys.stderr.write("Permission allow rule (settings.json): Bash(x *) has a wildcard.\n")
    if os.environ.get("CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING") != "1":
        sys.stderr.write("Error: File rewinding is not enabled.\n")
        sys.exit(1)
    if os.environ.get("FAKE_CLAUDE_MODE") == "rewind-unknown":
        sys.stderr.write(f"Error: --rewind-files requires a user message UUID, but {target} "
                         "is not a user message in this session\n")
        sys.exit(1)
    with open(os.path.join(os.getcwd(), "rewound.txt"), "w", encoding="utf-8") as handle:
        handle.write(target)
    sys.stdout.write(f"Files rewound to state at message {target}\n")
    sys.exit(0)

received = []
prompt = None
for raw in iter(sys.stdin.buffer.readline, b""):
    message = json.loads(raw.decode("utf-8"))
    received.append(message)
    if message.get("type") == "control_request" \
            and message.get("request", {}).get("subtype") == "initialize":
        # As the real CLI does, at once, before any message.
        sys.stdout.buffer.write(json.dumps({"type": "control_response", "response": {
            "subtype": "success", "request_id": message.get("request_id"),
            "response": {"commands": [], "models": [
                {"value": "default", "resolvedModel":
                    os.environ.get("FAKE_CLAUDE_DEFAULT", "claude-opus-5-5")},
                {"value": "opus", "resolvedModel": "claude-opus-5-5"}]}}}).encode("utf-8")
            + b"\n")
        sys.stdout.buffer.flush()
    if message.get("type") == "user":
        prompt = message["message"]["content"]
        break


def log(**extra):
    path = os.environ.get("FAKE_CLAUDE_LOG")
    if path:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"prompt": prompt, "args": sys.argv[1:],
                       "received": [m.get("type") for m in received],
                       "claude_env": sorted(k for k in os.environ
                                            if k.upper() in ("CLAUDECODE", "ANTHROPIC_API_KEY")),
                       "checkpoints": os.environ.get("CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING", ""),
                       **extra}, handle)


def out(**data):
    sys.stdout.buffer.write(json.dumps(data, ensure_ascii=False).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


log()
out(type="system", subtype="init", session_id="abc-1", apiKeySource="none")
mode = os.environ.get("FAKE_CLAUDE_MODE")
if mode == "hang":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    with open(os.path.join(os.getcwd(), "grandchild.pid"), "w") as handle:
        handle.write(str(child.pid))
    time.sleep(120)
result = "done"


def answer_controls(until):
    """Answer live control requests (#468) as Claude Code 2.1.296 does, until
    ``until(subtype)`` says enough or stdin closes. FAKE_CLAUDE_REFUSE names a
    subtype to refuse, as an older Claude Code would."""
    refuse = os.environ.get("FAKE_CLAUDE_REFUSE", "")
    for raw in iter(sys.stdin.buffer.readline, b""):
        message = json.loads(raw.decode("utf-8"))
        received.append(message)
        if message.get("type") != "control_request":
            continue
        request, rid = message.get("request", {}), message.get("request_id")
        subtype = request.get("subtype")
        if subtype == refuse:
            out(type="control_response", response={
                "subtype": "error", "request_id": rid,
                "error": f"Unsupported control request subtype: {subtype}"})
        elif subtype == "set_model":
            out(type="control_response", response={"subtype": "success", "request_id": rid})
        elif subtype == "set_permission_mode":
            out(type="control_response", response={"subtype": "success", "request_id": rid,
                                                   "response": {"mode": request.get("mode")}})
            out(type="system", subtype="status", status=None,
                permissionMode=request.get("mode"), session_id="abc-1")
        elif subtype == "stop_task":
            # The list empties first, then the (empty) success: as captured.
            out(type="system", subtype="task_notification", task_id=request.get("task_id"),
                status="stopped", output_file="", summary="Sleep")
            out(type="system", subtype="background_tasks_changed", tasks=[])
            out(type="control_response", response={"subtype": "success", "request_id": rid,
                                                   "response": {}})
        elif subtype == "get_context_usage":
            out(type="control_response", response={"subtype": "success", "request_id": rid,
                                                   "response": {"categories": [
                                                       {"name": "System prompt", "tokens": 2362,
                                                        "kind": "used"},
                                                       {"name": "Free space", "tokens": 990000,
                                                        "kind": "free"}],
                                                       "totalTokens": 2362,
                                                       "maxTokens": 1000000}})
        else:
            continue
        log(controls=[m.get("request", {}).get("subtype") for m in received
                      if m.get("type") == "control_request"
                      and m.get("request", {}).get("subtype") != "initialize"])
        if until(subtype):
            return


if mode == "retry":
    # Two retries, as a refused connection gives them (captured), then the reply.
    for attempt, delay in ((1, 525), (2, 1018)):
        out(type="system", subtype="api_retry", attempt=attempt, max_retries=10,
            retry_delay_ms=delay, error_status=None, error="unknown", session_id="abc-1")
if mode == "live":
    # Claude is working: wait for a set_model or set_permission_mode, answer
    # it, then reply.
    out(type="assistant", parent_tool_use_id=None,
        message={"content": [{"type": "text", "text": "Working on it."}]})
    answer_controls(lambda subtype: subtype in ("set_model", "set_permission_mode"))
if mode == "background":
    # A background sleep with its task id, the answer, idle; then stop_task
    # ends it, idle again, and The Chat Place ends the turn.
    out(type="system", subtype="session_state_changed", state="running")
    out(type="system", subtype="background_tasks_changed", tasks=[
        {"task_id": "b375k20qt", "task_type": "local_bash", "description": "Sleep"}])
    out(type="assistant", parent_tool_use_id=None,
        message={"content": [{"type": "text", "text": "It's running."}]})
    out(type="result", subtype="success", is_error=False, result="It's running.",
        session_id="abc-1")
    out(type="system", subtype="session_state_changed", state="idle")
    answer_controls(lambda subtype: subtype == "stop_task")
    out(type="system", subtype="session_state_changed", state="idle")
    answer_controls(lambda subtype: False)  # the context request, until stdin closes
    sys.exit(0)
if mode == "ask":
    out(type="control_request", request_id="req-1",
        request={"subtype": "can_use_tool", "tool_name": "Bash",
                 "input": {"command": "git push"}, "tool_use_id": "t1"})
    answer = json.loads(sys.stdin.buffer.readline().decode("utf-8"))
    log(answer=answer)
    result = answer["response"]["response"]["behavior"]
if mode in ("elicit", "elicit-url"):
    # An MCP server asking for input (#468), shaped as Claude Code 2.1.296
    # sent it from a real stdio server's tool call.
    if mode == "elicit":
        request = {"subtype": "elicitation", "mcp_server_name": "elicit-test",
                   "message": "Where would you like to go, and for how long?", "mode": "form",
                   "requested_schema": {"type": "object", "properties": {
                       "city": {"type": "string", "title": "City",
                                "description": "Which city to plan for"},
                       "nights": {"type": "integer", "title": "Nights", "minimum": 1,
                                  "maximum": 14},
                       "pets": {"type": "boolean", "title": "Bringing pets", "default": False},
                       "budget": {"type": "string", "title": "Budget",
                                  "enum": ["low", "medium", "high"],
                                  "enumNames": ["Low", "Medium", "High"]}},
                       "required": ["city", "nights"]}}
    else:
        request = {"subtype": "elicitation", "mcp_server_name": "elicit-test",
                   "message": "Open this page to finish signing in.", "mode": "url",
                   "url": "https://example.com/sign-in", "elicitation_id": "elic-123"}
    out(type="control_request", request_id="elicit-1", request=request)
    answer = json.loads(sys.stdin.buffer.readline().decode("utf-8"))
    log(answer=answer)
    result = json.dumps(answer["response"].get("response"), sort_keys=True)
if mode == 'command-result':
    result = '\x1b[1mCost report is ready.\x1b[0m ' + 'x' * 100000
    record = {'type': 'user', 'uuid': 'synthetic-command-result',
              'message': {'role': 'user', 'content': '<local-command-stdout>' + result + '</local-command-stdout>'}}
    with open('synthetic-command.jsonl', 'w', encoding='utf-8') as transcript:
        transcript.write(json.dumps(record) + '\n')
elif mode in ('compact', 'compact-no-boundary'):
    boundary = {'type': 'system', 'subtype': 'compact_boundary', 'uuid': 'synthetic-compact',
                'compactMetadata': {'trigger': 'manual', 'preTokens': 968000, 'postTokens': 9000}}
    stdout = {'type': 'user', 'uuid': 'synthetic-command-output',
              'message': {'role': 'user', 'content': '<local-command-stdout>Compacted</local-command-stdout>'}}
    with open('synthetic-compact.jsonl', 'w', encoding='utf-8') as transcript:
        transcript.write(json.dumps(boundary) + '\n' + json.dumps(stdout) + '\n')
    if mode == 'compact':
        out(**boundary)
    result = ''  # Slash commands can finish without any assistant message.
else:
    out(type="assistant", parent_tool_use_id=None,
        message={"content": [{"type": "text", "text": "reply with a line separator"}]})
out(type="result", subtype="success", is_error=False, result=result, session_id="abc-1")
sys.stdin.buffer.read()  # until The Chat Place closes stdin
