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

#: How the turn would be charged, as initialize's ``account`` says it
#: (#658), shaped as Claude Code 2.1.325 answers: FAKE_CLAUDE_ACCOUNT=
#: subscription (the default), api (the app's apiKeyHelper), apikey_env (a
#: key in the environment), bedrock, or none (no account: an older Claude Code).
ACCOUNTS = {
    "subscription": {"email": "someone@example.com", "organization": "Example",
                     "subscriptionType": "max", "apiProvider": "firstParty"},
    # The app's apiKeyHelper: initialize names it only as the token's source
    # (system/init says apiKeySource "apiKeyHelper"), checked live (#662).
    "api": {"tokenSource": "apiKeyHelper", "apiProvider": "firstParty"},
    "apikey_env": {"apiKeySource": "ANTHROPIC_API_KEY", "apiProvider": "firstParty",
                   "tokenSource": "claude.ai"},
    "bedrock": {"apiProvider": "bedrock"},
}
account_kind = os.environ.get("FAKE_CLAUDE_ACCOUNT", "subscription")
#: system/init's apiKeySource, following the account unless FAKE_CLAUDE_SOURCE says.
init_source = os.environ.get("FAKE_CLAUDE_SOURCE") or {
    "api": "apiKeyHelper", "apikey_env": "ANTHROPIC_API_KEY"}.get(account_kind, "none")

received = []
prompt = None
for raw in iter(sys.stdin.buffer.readline, b""):
    message = json.loads(raw.decode("utf-8"))
    received.append(message)
    if message.get("type") == "control_request" \
            and message.get("request", {}).get("subtype") == "initialize" \
            and os.environ.get("FAKE_CLAUDE_MODE") != "no-init":
        # As the real CLI does, at once, before any message.
        body = {"commands": [], "models": [
            {"value": "default", "resolvedModel":
                os.environ.get("FAKE_CLAUDE_DEFAULT", "claude-opus-5-5")},
            {"value": "opus", "resolvedModel": "claude-opus-5-5"},
            {"value": "fable", "resolvedModel": "claude-fable-5-2"}]}
        if account_kind in ACCOUNTS:
            body["account"] = ACCOUNTS[account_kind]
        if account_kind in ("api", "apikey_env"):
            body["auth_conflict"] = {"message": "Both a subscription and an API key are set."}
        sys.stdout.buffer.write(json.dumps({"type": "control_response", "response": {
            "subtype": "success", "request_id": message.get("request_id"),
            "response": body}}).encode("utf-8") + b"\n")
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
                       # Whether any variable holds something shaped like a key (#658).
                       "key_in_env": any("sk-ant-" in value for value in os.environ.values()),
                       "checkpoints": os.environ.get("CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING", ""),
                       **extra}, handle)


import threading  # noqa: E402 - only the wake modes (#749) write from a thread

_out_lock = threading.Lock()


def out(**data):
    with _out_lock:  # whole lines, even from a wake's thread (#749)
        sys.stdout.buffer.write(json.dumps(data, ensure_ascii=False).encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()


log()
out(type="system", subtype="init", session_id="abc-1", apiKeySource=init_source)
mode = os.environ.get("FAKE_CLAUDE_MODE")
#: What the result says the process cost (#658), as Claude Code reports it:
#: total_cost_usd, and modelUsage's costUSD by model. ``usage`` is this
#: turn's own tokens and modelUsage the cumulative ones, the restored cost
#: state's included (checked with 2.1.325, #663):
#: FAKE_CLAUDE_RESTORED_TOKENS (default 0) is how many tokens a --resume
#: restored, which modelUsage counts and usage doesn't.
cost = float(os.environ.get("FAKE_CLAUDE_COST", "0.0123"))
restored_tokens = int(os.environ.get("FAKE_CLAUDE_RESTORED_TOKENS", "0"))
COST = {"total_cost_usd": cost,
        "usage": {"input_tokens": 12, "output_tokens": 3, "cache_read_input_tokens": 0,
                  "cache_creation_input_tokens": 34000},
        "modelUsage": {"claude-haiku-5-5": {
            "inputTokens": 12 + restored_tokens, "outputTokens": 3, "cacheReadInputTokens": 0,
            "cacheCreationInputTokens": 34000, "costUSD": cost}}}
if mode == "max-budget":
    # Over --max-budget-usd, checked after a request: it can run over.
    out(type="result", subtype="error_max_budget_usd", is_error=True, session_id="abc-1",
        errors=["Reached maximum budget"], **COST)
    sys.stdin.buffer.read()
    sys.exit(0)
if mode in ("credit-low", "bad-key"):
    text = ("Credit balance is too low to access the Anthropic API. Please go to Plans & "
            "Billing to upgrade or purchase credits." if mode == "credit-low"
            else "Invalid API key · Fix external API key")
    out(type="assistant", parent_tool_use_id=None,
        message={"model": "<synthetic>", "content": [{"type": "text", "text": text}]})
    out(type="result", subtype="success", is_error=True, result=text, session_id="abc-1",
        total_cost_usd=0, modelUsage={})
    sys.stdin.buffer.read()
    sys.exit(0)
if mode == "stderr-key":
    # A key quoted in an error: it must never reach the window (#658).
    sys.stderr.write("Error: request failed for key sk-ant-api03-SECRETSECRETSECRET-abc_123\n")
    sys.stderr.flush()
    sys.exit(1)
if mode == "hang":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    with open(os.path.join(os.getcwd(), "grandchild.pid"), "w") as handle:
        handle.write(str(child.pid))
    time.sleep(120)
result = "done"


def answer_controls(until, other=None):
    """Answer live control requests (#468) as Claude Code 2.1.296 does, until
    ``until(subtype)`` says enough or stdin closes. FAKE_CLAUDE_REFUSE names a
    subtype to refuse, as an older Claude Code would. ``other`` is given
    every other message (a follow-up, a permission's answer)."""
    refuse = os.environ.get("FAKE_CLAUDE_REFUSE", "")
    for raw in iter(sys.stdin.buffer.readline, b""):
        message = json.loads(raw.decode("utf-8"))
        received.append(message)
        if message.get("type") != "control_request":
            if other is not None:
                other(message)
            continue
        request, rid = message.get("request", {}), message.get("request_id")
        subtype = request.get("subtype")
        # Log before answering: an answer can end the turn, and the runner may
        # end this process before a log written afterwards gets to disk (a
        # Stop test failed that way on a slow Mac runner).
        log(controls=[m.get("request", {}).get("subtype") for m in received
                      if m.get("type") == "control_request"
                      and m.get("request", {}).get("subtype") != "initialize"])
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
        elif subtype == "interrupt":
            # As Claude Code 2.1.325 does mid-reply (#658): the answer, then
            # the interrupted turn's result, with what it cost so far.
            out(type="control_response", response={"subtype": "success", "request_id": rid})
            out(type="result", subtype="error_during_execution", is_error=True,
                session_id="abc-1", **COST)
        else:
            continue
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
if mode == "interruptible":
    # Replying, until Stop's interrupt; then the result, and it stays open
    # (only the runner ending the process ends it).
    out(type="assistant", parent_tool_use_id=None,
        message={"content": [{"type": "text", "text": "Once upon a time"}]})
    answer_controls(lambda subtype: subtype == "interrupt")
    time.sleep(60)
    sys.exit(0)
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
if mode == "cron":
    # Claude sets a timer (#749), shaped as Claude Code 2.1.331 sent it in
    # the probe of 2026-11-27: the CronCreate call, its result naming the
    # job, the reply, the result and idle; then it waits for stdin to close,
    # and the timer with it. FAKE_CLAUDE_CRON is the expression (one-shot
    # unless FAKE_CLAUDE_RECURRING=1); FAKE_CLAUDE_CRON_THEN=delete cancels
    # it with CronDelete, =refused has CronCreate fail, =monitor adds a
    # Monitor watch, listed as an ambient background task.
    expression = os.environ.get("FAKE_CLAUDE_CRON", "0 14 27 11 *")
    recurring = os.environ.get("FAKE_CLAUDE_RECURRING") == "1"
    then = os.environ.get("FAKE_CLAUDE_CRON_THEN", "")
    #: The model that writes every reply (FAKE_CLAUDE_MODEL, #749's usage-credit test).
    cron_model = os.environ.get("FAKE_CLAUDE_MODEL", "claude-opus-5-5")
    out(type="system", subtype="session_state_changed", state="running")
    if os.environ.get("FAKE_CLAUDE_WAKE_OVERAGE") == "1":
        # Past the plan's allowance already, in the turn that sets the
        # timer: its wake must be checked against usage credits too (#749).
        out(type="rate_limit_event", session_id="abc-1", rate_limit_info={
            "status": "allowed_warning", "isUsingOverage": True,
            "overageStatus": "allowed", "rateLimitType": "five_hour"})
    out(type="assistant", parent_tool_use_id=None, uuid="rec-tool", message={
        "id": "msg_tool", "model": cron_model, "content": [{
            "type": "tool_use", "id": "toolu_cron1", "name": "CronCreate",
            "input": {"cron": expression, "prompt": "Check the pull request's checks.",
                      "recurring": recurring}}]})
    if then == "refused":
        out(type="user", parent_tool_use_id=None, message={"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "toolu_cron1", "is_error": True,
            "content": "<tool_use_error>Too many scheduled jobs (max 50). "
                       "Cancel one first.</tool_use_error>"}]})
    else:
        kind = "recurring job" if recurring else "one-shot task"
        out(type="user", parent_tool_use_id=None, message={"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "toolu_cron1",
            "content": f"Scheduled {kind} f0d1911f ({expression}). Next run: Nov 27 2:00pm. "
                       "Session-only (not written to disk, dies when Claude exits). "
                       "It will fire once then auto-delete."}]})
    if then == "delete":
        out(type="assistant", parent_tool_use_id=None, message={
            "model": cron_model, "content": [{
                "type": "tool_use", "id": "toolu_cron2", "name": "CronDelete",
                "input": {"id": "f0d1911f"}}]})
        out(type="user", parent_tool_use_id=None, message={"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "toolu_cron2",
            "content": "Cancelled job f0d1911f."}]})
    if then == "monitor":
        out(type="assistant", parent_tool_use_id=None, message={
            "model": cron_model, "content": [{
                "type": "tool_use", "id": "toolu_mon1", "name": "Monitor",
                "input": {"command": "gh pr checks 749 --watch", "description": "Watch PR 749"}}]})
        out(type="system", subtype="background_tasks_changed", tasks=[
            {"task_id": "m1", "task_type": "monitor", "description": "Watch PR 749",
             "ambient": True}])
        out(type="user", parent_tool_use_id=None, message={"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "toolu_mon1",
            "content": "Monitor started (task m1)."}]})
    reply = "I'll check the pull request again at 2:00 PM."
    out(type="assistant", parent_tool_use_id=None, uuid="rec-reply", message={
        "id": "msg_reply", "model": cron_model,
        "content": [{"type": "text", "text": reply}]})
    out(type="result", subtype="success", is_error=False, result=reply, session_id="abc-1",
        **COST)
    out(type="system", subtype="session_state_changed", state="idle")
    log(waited=True)
    # Part 2 (#749): with FAKE_CLAUDE_WAKE=<seconds>, the timer fires that
    # long after idle, as the probe of 2026-11-27 saw it with Claude Code
    # 2.1.331: running, command_lifecycle started (wakeup_source), a fresh
    # system/init, the reply, the result, command_lifecycle completed, idle.
    # FAKE_CLAUDE_WAKES is how many times (a repeating timer: the default 1
    # is a one-shot's); FAKE_CLAUDE_WAKE_SOURCE is the wake's apiKeySource;
    # FAKE_CLAUDE_WAKE_ASK=1 has it ask permission and wait for the answer;
    # FAKE_CLAUDE_WAKE_OVERAGE=1 has the first turn's rate limit say usage
    # credits; FAKE_CLAUDE_WAKE_DELETE=1 has it delete the timer (CronDelete). A
    # message sent meanwhile is answered as a turn of its own.
    wake_after = os.environ.get("FAKE_CLAUDE_WAKE")
    answers = []
    answered = threading.Event()

    def other(message):
        if message.get("type") == "control_response":
            answers.append(message)
            answered.set()
        elif message.get("type") == "user":
            text = message["message"]["content"]
            out(type="system", subtype="session_state_changed", state="running")
            out(type="assistant", parent_tool_use_id=None, uuid="rec-follow", message={
                "id": "msg_follow", "model": cron_model,
                "content": [{"type": "text", "text": f"Got it: {text}"}]})
            out(type="result", subtype="success", is_error=False, result=f"Got it: {text}",
                session_id="abc-1", **COST)
            out(type="system", subtype="session_state_changed", state="idle")

    def wake():
        for number in range(1, int(os.environ.get("FAKE_CLAUDE_WAKES", "1")) + 1):
            time.sleep(float(wake_after))
            out(type="system", subtype="session_state_changed", state="running")
            out(type="command_lifecycle", command_uuid=f"wake-{number}", state="started",
                wakeup_source="schedule_wakeup", session_id="abc-1")
            out(type="system", subtype="init", session_id="abc-1",
                apiKeySource=os.environ.get("FAKE_CLAUDE_WAKE_SOURCE") or init_source,
                model=cron_model)
            if os.environ.get("FAKE_CLAUDE_WAKE_ASK") == "1":
                answered.clear()
                out(type="system", subtype="session_state_changed", state="requires_action")
                out(type="control_request", request_id=f"wake-ask-{number}", request={
                    "subtype": "can_use_tool", "tool_name": "Bash",
                    "input": {"command": "gh pr checks 749"}, "tool_use_id": "toolu_ask"})
                answered.wait(120)
                out(type="system", subtype="session_state_changed", state="running")
            if os.environ.get("FAKE_CLAUDE_WAKE_DELETE") == "1":
                out(type="assistant", parent_tool_use_id=None, message={
                    "model": cron_model, "content": [{
                        "type": "tool_use", "id": f"toolu_del{number}", "name": "CronDelete",
                        "input": {"id": "f0d1911f"}}]})
                out(type="user", parent_tool_use_id=None, message={"role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": f"toolu_del{number}",
                    "content": "Cancelled job f0d1911f."}]})
            text = f"Scheduled check {number}: the checks passed."
            out(type="assistant", parent_tool_use_id=None, uuid=f"rec-wake{number}", message={
                "id": f"msg_wake{number}", "model": cron_model,
                "content": [{"type": "text", "text": text}]})
            out(type="result", subtype="success", is_error=False, result=text,
                session_id="abc-1", **COST)
            out(type="command_lifecycle", command_uuid=f"wake-{number}", state="completed",
                session_id="abc-1")
            out(type="system", subtype="session_state_changed", state="idle")
            log(waited=True, wakes=number, answers=[a.get("response", {}).get("response", {})
                                                    .get("behavior") for a in answers])

    if wake_after:
        threading.Thread(target=wake, daemon=True).start()
    answer_controls(lambda subtype: False, other)  # until The Chat Place closes stdin
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
elif mode == 'aside':
    # Ask Aside (#468): partial messages (--include-partial-messages), as
    # Claude Code 2.1.296 sends them, then the whole reply and the result.
    out(type='stream_event', parent_tool_use_id=None, event={'type': 'message_start'})
    for piece in ('The code word ', 'is ZEPHYR.'):
        out(type='stream_event', parent_tool_use_id=None,
            event={'type': 'content_block_delta', 'index': 0,
                   'delta': {'type': 'text_delta', 'text': piece}})
    out(type="assistant", parent_tool_use_id=None,
        message={"content": [{"type": "text", "text": "The code word is ZEPHYR."}]})
    result = "The code word is ZEPHYR."
elif mode == 'no-message':
    # A cut Claude Code can't find (#559): said on stderr, then it exits.
    sys.stderr.write("No message found with message.uuid of: 0000-cut\n")
    sys.stderr.flush()
    sys.exit(1)
elif mode == 'no-message-result':
    # The same, as an error result, as 'missing' reports its own (#559).
    out(type="result", subtype="error_during_execution", is_error=True,
        errors=["No message found with message.uuid of: 0000-cut"], session_id="abc-1")
    sys.stdin.buffer.read()
    sys.exit(1)
elif mode == 'missing':
    out(type="result", subtype="error_during_execution", is_error=True,
        errors=["No conversation found with session ID: abc-1"], session_id="abc-1")
    sys.stdin.buffer.read()
    sys.exit(1)
else:
    out(type="assistant", parent_tool_use_id=None,
        message={"content": [{"type": "text", "text": "reply with a line separator"}]})
out(type="result", subtype="success", is_error=False, result=result, session_id="abc-1", **COST)
sys.stdin.buffer.read()  # until The Chat Place closes stdin
