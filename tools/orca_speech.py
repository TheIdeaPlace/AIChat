"""What Orca says as you use The Chat Place (Linux), checked a key at a time.

The Windows counterpart is the sr-test skill, which listens to NVDA in a test
VM. On Linux Orca can run with no desktop at all: a virtual screen (Xvfb), an
accessibility bus and Orca's debug log, which records every string it would
speak ("SPEECH OUTPUT: '...'"). So this needs no person, no sound and no VM,
and runs in CI (#230).

    tools/orca_speech.sh                    run the steps, report, exit 1 on a failure
    tools/orca_speech.sh --record           say what each step spoke, check nothing
    tools/orca_speech.sh --json out.json    and save every step's speech

``orca_speech.sh`` makes the session bus, virtual screen and accessibility bus
and then runs ``check`` here. ``check`` starts Orca with a debug log, starts
the app (``serve``) on the visual probe's made-up sessions (``tools/ui_probe.py``,
``tests/fake_env.py``: no real sessions, settings or claude), and presses each
step's keys with xdotool. A step passes when everything it expects was
spoken, in any order, before speech went quiet.

The app's own speech isn't faked, unlike the window tests': announcements go
to Orca as they would for a user, through D-Bus on Orca 49 and later.

Orca runs one copy per user, so on a PC where you use Orca, run this as
another user (the test VM has ``tcptest``). It refuses to start beside an
Orca it didn't start.

Needs: Orca, xdotool, Xvfb, at-spi2-core, dbus and a window manager
(``sudo apt install orca xdotool xvfb at-spi2-core dbus-x11 openbox``), and
``.venv`` (``linuxsetup.sh``).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

#: Orca's debug log line for each string it speaks: the text in quotes, then
#: the voice (50) or nothing (older), and the next line's time. The text can
#: run over several lines (a message with line breaks).
_SPOKEN = re.compile(r"SPEECH OUTPUT: '(.*?)'(?:[ \t][^\n]*)?(?:\n(?=\d\d:\d\d:\d\d)|\n?\Z)",
                     re.S)
#: Orca echoing a key you pressed: not what the app said.
_KEY_ECHO = {"tab", "escape", "return", "enter", "down", "up", "left", "right", "space",
             "left control", "right control", "left alt", "right alt", "left shift",
             "right shift", "f1", "f10", "home", "end"}
#: Quiet this long after a key, and its speech is taken as finished.
SETTLE = 1.2
#: The longest a step waits for what it expects to be said.
STEP_TIMEOUT = 15.0
#: The first step waits for the app to start, which a slow runner takes a while over.
START_TIMEOUT = 60.0


@dataclass
class Step:
    name: str
    keys: List[str]
    #: Each must be in what's spoken (case-insensitive), in any order.
    expect: List[str] = field(default_factory=list)
    #: None of these may be.
    refuse: List[str] = field(default_factory=list)
    #: Only with Orca 49 and later, which take announcements from the app
    #: through D-Bus whatever window is active.
    expect_dbus: List[str] = field(default_factory=list)
    #: Only with Orca 50 and later, which say the window's and the list's
    #: name as the app starts; 46 says only the row it lands on.
    expect_50: List[str] = field(default_factory=list)
    #: Something to happen in the app before the keys: "question" has Claude
    #: ask one in the open chat, as a turn would; "copilot" opens a Copilot
    #: chat of the app's own that asks to run a command (``serve`` watches
    #: for them, TRIGGERS).
    trigger: str = ""


#: The probe's sessions (tools/ui_probe.py build_world): "Fix the flaky upload
#: test" first, as it needs you, then "Visual probe", the app's own, whose
#: messages are the user's request and Claude's long reply.
#: The Copilot chat "copilot" opens (tools/ui_probe.py s_main_copilot's).
COPILOT_TITLE = "Copilot history preview"

STEPS = [
    Step("start", [], expect=["Fix the flaky upload test", "needs you"],
         expect_50=["The Chat Place", "Session list"]),
    Step("next session", ["Down"], expect=["Visual probe", "Chat Place session"]),
    Step("open it", ["Return"], expect=["Messages in Visual probe", "What changed"],
         expect_dbus=["Loaded Visual probe"]),
    Step("previous message", ["Up"], expect=["Make the uploader flush before it signals"]),
    Step("settings", ["ctrl+comma"], expect=["Settings", "Announcements", "Full"],
         refuse=["unlabeled", "unlabelled"]),
    Step("settings, tab", ["Tab"], expect=["Announce when any listed session", "check box"]),
    Step("settings, history retention", ["alt+y"],
         expect=["History retention and kept copies"]),
    Step("settings closed", ["Escape"], expect=["Messages in Visual probe"]),
    Step("F10, the menu bar", ["F10"], expect=["File", "menu"]),
    Step("menu closed, back in the messages", ["Escape"], expect=["Messages in Visual probe"]),
    Step("Alt alone, the menu bar", ["alt"], expect=["File", "menu"]),
    Step("menu closed again, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # Into a submenu and back out: Escape must still close the menu, not go
    # back to the session list (#230).
    Step("View menu", ["alt+v"], expect=["Go to Sessions"]),
    Step("Sort Sessions submenu", ["o"], expect=["radio menu item"]),
    Step("back to View", ["Left"], expect=["Sort Sessions"]),
    Step("View closed, back in the messages", ["Escape"], expect=["Messages in Visual probe"],
         refuse=["Session list"]),
    Step("keyboard shortcuts", ["F1"], expect=["Keyboard Shortcuts"]),
    Step("shortcuts closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # Answer (#415): it opens on the Question box, and Tab goes to the first
    # question's choices, not past them to its answer box (#428).
    Step("Claude asks a question", [], trigger="question", expect=["needs you"]),
    # Orca reads the Question box's first line as it lands there.
    Step("answer it", ["ctrl+shift+a"], expect=["Question", "Here's the draft"]),
    Step("Tab to the choices", ["Tab"], expect=["Keep it as is"],
         refuse=["your own answer"]),
    Step("answer later, back in the messages", ["Escape"], expect=["Messages in Visual probe"]),
    # Back Up Now (#433, #435): the folder question, and Escape back out.
    Step("Back Up Now", ["alt+f", "w"], expect=["Back up to which folder"]),
    Step("folder question closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # Backup Schedule (#437), then Restore from Backup (#436), the last items
    # before Exit: Up from the top goes to Exit first.
    # Lands on the schedule's choice box, which Orca reads with or without
    # its name depending on timing (#454): until that's fixed, only that the
    # menu reached Backup Schedule is checked.
    Step("Backup Schedule", ["alt+f", "Up", "Up", "Up", "Return"],
         expect=["Backup Schedule"]),
    Step("Backup Schedule closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # GTK's file picker is announced by its file list, not its title.
    Step("Restore from Backup", ["alt+f", "Up", "Up", "Return"],
         expect=["Restore from Backup", "Files"]),
    Step("restore's file question closed, back in the messages", ["Escape"],
         expect=["Messages in Visual probe"]),
    # Copilot's Auto (#426), last, as the Copilot chat replaces the session
    # list's discovery (copilot_chat below): a chat of the app's own in agent
    # mode, with Copilot asking to run npm test and npm run lint already
    # allowed for the chat.
    # Orca 46 reads the row the focus lands on, not the list's name.
    Step("a Copilot chat asks to run a command", [], trigger="copilot",
         expect=["What changed"],
         expect_dbus=["Copilot needs your answer", "npm test"]),
    # Agent, marked "(now)", is selected; Auto is the third choice.
    Step("Change Permission Mode", ["ctrl+shift+o"],
         expect=["Change Permission Mode", "Agent: asks before running commands", "(now)"]),
    Step("Auto", ["Down", "Down"], expect=["Auto: reads, and changes files"]),
    # The warning is a Yes/No message box, No the default.
    Step("the Auto warning", ["Return"],
         expect=["In Auto, Copilot reads and searches", "Turn on Auto for this chat"]),
    # Return takes the default, No: the chat still asks.
    Step("No, the default, so it still asks", ["Return"],
         expect=[f"Messages in {COPILOT_TITLE}"],
         expect_dbus=["still asks before Copilot acts"], refuse=["Auto. It reads"]),
    # Answer opens on the Request box; Tab goes to Allow for This Chat, whose
    # name says what it keeps.
    Step("answer Copilot", ["ctrl+shift+a"], expect=["Copilot needs permission", "Request"]),
    Step("Tab to Allow for This Chat", ["Tab"],
         expect=["Allow for this chat", "exactly this command: npm test"]),
    Step("Alt+C allows it for this chat", ["alt+c"], expect=[f"Messages in {COPILOT_TITLE}"],
         expect_dbus=["Approval kept for this chat and answer sent to Copilot"]),
    # Chat Permissions lists npm run lint, kept before, then npm test.
    Step("Copilot's Chat Permissions", ["ctrl+shift+h"],
         expect=["Chat Permissions", "Allowed for this chat", "npm run lint"]),
    Step("the command just kept", ["Down"], expect=["Exactly this command: npm test"]),
    Step("Chat Permissions closed, back in the messages", ["Escape"],
         expect=[f"Messages in {COPILOT_TITLE}"]),
]

#: The question "question" asks (STEPS above).
QUESTION = {"questions": [{
    "header": "Linux notes", "question": "How should it read?",
    "options": [{"label": "Keep it as is", "description": "As drafted",
                 "preview": "Linux (preview). The Chat Place now runs on Linux."},
                {"label": "Shorter", "description": "Just the essentials"}]}]}


# -- the app --------------------------------------------------------------------------------

def serve(root: Path) -> int:
    """The app on the probe's made-up sessions, shown and focused, until it's
    closed. Its speech is real, so Orca gets the announcements."""
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tests"))
    sys.path.insert(0, str(HERE))
    import wx
    from thechatplace import speech
    app = wx.App(False)
    import ui_probe

    def patch(target, name, value):
        # fake_env records speech for the window tests; here it must reach Orca.
        if target is speech.speaker and name == "speak":
            return
        setattr(target, name, value)
    env = ui_probe.build_world(root, patch, formatted_view=False)
    frame = ui_probe.build_frame(env)
    frame.SetSize((1000, 720))
    frame.Show()
    frame.Raise()
    frame.session_list.SetFocus()

    def watch(_event):
        # A step's trigger: check writes ROOT/<name>.trigger before its keys.
        for name, action in TRIGGERS.items():
            trigger = root / f"{name}.trigger"
            if trigger.exists():
                trigger.unlink()
                action(frame, env)
    timer = wx.Timer(frame)
    frame.Bind(wx.EVT_TIMER, watch, timer)
    timer.Start(200)
    app.MainLoop()
    return 0


def ask_question(frame) -> None:
    """Claude, in the probe's own session (open by then), writes a line and
    asks QUESTION, as a turn reports them (tools/ui_probe.py's _waiting)."""
    import fake_env
    from thechatplace.claude_cli import PermissionRequest, TurnEvent
    frame._pending.clear()
    frame._runners["own-1"] = fake_env.FakeRunner([], "", "", None)
    for kind, text in [("text", "Here's the draft release note for Linux."),
                       ("tool", "AskUserQuestion")]:
        frame._on_turn_event({"id": "own-1"}, "Visual probe", TurnEvent(kind, text=text))
    request = PermissionRequest("q1", "AskUserQuestion", QUESTION, suggestions=[])
    frame._on_turn_event({"id": "own-1"}, "Visual probe",
                         TurnEvent("permission", text=request.summary(), request=request))


def copilot_chat(frame, env) -> None:
    """A Copilot chat of the app's own (tools/ui_probe.py's made-up Copilot
    session), open in agent mode, with npm run lint allowed for it and Copilot
    asking to run npm test, as copilot_runtime reports a request. Copilot is a
    stand-in: nothing is run. The Chat Place's own record of the commands
    allowed for a chat (in the made-up app data folder) is real, so Allow for
    This Chat keeps npm test and Chat Permissions lists it."""
    import dataclasses
    from types import SimpleNamespace
    import wx
    import fake_env
    import ui_probe
    from thechatplace.own_store import OwnSession
    from thechatplace.providers import copilot_auto_store
    from thechatplace.providers.copilot_runtime import AGENT_MODE, CopilotChatState
    from thechatplace.sessions import IDLE, NEEDS_YOU, WORKING

    # The real message box from here on (these steps are last): the probe's
    # stand-in answers Yes at once, so Auto's warning would never be shown
    # or read, and Return couldn't take its default, No.
    import wx._core
    wx.MessageBox = wx._core.MessageBox
    # Opening it moves the focus to its messages, which Orca says.
    frame.session_list.SetFocus()
    ui_probe.s_main_copilot(frame, env, title=COPILOT_TITLE)
    info = frame._open
    native = info.cli_session_id
    record = OwnSession(native, info.title, info.cwd, provider="copilot", started=True)
    frame.store.add(record)
    copilot_auto_store.add(native, "npm run lint")
    request = ui_probe._copilot_request({
        "toolCallId": "call-1", "kind": "execute", "title": "Run the tests",
        "rawInput": {"command": "npm test",
                     "commands": [{"identifier": "npm test", "readOnly": False}]}})
    now = {"state": CopilotChatState(native, IDLE, "1", "completed", mode=AGENT_MODE)}
    allowed = set()

    def answer(_native, _request_id, _choice):
        # Copilot goes on with its turn, the request answered.
        now["state"] = dataclasses.replace(now["state"], state=WORKING, requests=())
        wx.CallAfter(frame._on_owned_update, "copilot", now["state"])

    def keep_allowed(_native, commands):
        allowed.clear()
        allowed.update(commands)

    frame.sessions._providers["copilot"].runtime = SimpleNamespace(
        known=lambda: [(native, info.cwd)], snapshot=lambda _native: now["state"],
        ready=lambda _native: True, adopt=lambda _native: None,
        prepare=lambda _native, _cwd: None, close=lambda: None,
        chat_candidate=lambda _native, _request_id: request.command,
        allowed_here=lambda _native: set(allowed), keep_allowed=keep_allowed, answer=answer,
        set_mode=lambda _native, mode: mode, set_auto=lambda _native, on: None)
    frame._codex_states[info.key] = now["state"]
    frame.open_session(dataclasses.replace(record.to_info(), state=IDLE))
    fake_env.pump(lambda: frame._chat_loaded and not frame._reader_busy
                  and not frame._codex_preparing, timeout=10)
    # The row is the app's own from the next refresh (as
    # ui_probe.s_main_copilot_owned), which the File menu's items go by.
    frame.refresh_sessions(force=True)
    fake_env.pump(lambda: not frame._snapshot_busy and frame._pending_refresh is None,
                  timeout=10)
    now["state"] = dataclasses.replace(now["state"], state=NEEDS_YOU, turn_id="2",
                                       outcome="inProgress", requests=(request,))
    frame._on_owned_update("copilot", now["state"])


#: What each step's trigger does in ``serve`` (Step.trigger).
TRIGGERS = {"question": lambda frame, env: ask_question(frame), "copilot": copilot_chat}


# -- Orca -----------------------------------------------------------------------------------

class OrcaLog:
    """What Orca spoke, read from its debug log as it grows."""

    def __init__(self, path: Path):
        self.path = path
        self.offset = 0

    def mark(self) -> None:
        self.offset = self.path.stat().st_size if self.path.exists() else 0

    def since_mark(self) -> List[str]:
        if not self.path.exists():
            return []
        with self.path.open("rb") as log:
            log.seek(self.offset)
            text = log.read().decode("utf-8", "replace")
        return [m.group(1) for m in _SPOKEN.finditer(text)]

    def wait_quiet(self, done, timeout: float = STEP_TIMEOUT,
                   settle: float = SETTLE) -> List[str]:
        """Speech since the mark, once ``done(speech)`` and Orca has been quiet
        for ``settle`` seconds, or at ``timeout``. Waiting for what a step
        expects, rather than for the first quiet, lets a slow runner take its
        time without the speech landing in the next step."""
        end = time.time() + timeout
        seen, quiet_since = [], time.time()
        while time.time() < end:
            now = self.since_mark()
            if len(now) != len(seen):
                seen, quiet_since = now, time.time()
            elif time.time() - quiet_since >= settle and done(seen):
                break
            time.sleep(0.1)
        return seen


def orca_version() -> tuple:
    out = subprocess.run(["orca", "--version"], capture_output=True, text=True).stdout
    match = re.search(r"(\d+)\.(\d+)", out)
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def _our_orcas() -> List[int]:
    out = subprocess.run(["pgrep", "-u", str(os.getuid()), "-x", "orca"],
                         capture_output=True, text=True).stdout
    return [int(p) for p in out.split()]


def start_orca(log_path: Path) -> subprocess.Popen:
    if _our_orcas():
        raise SystemExit("Orca is already running as this user. Run this as another user "
                         "(the test VM has tcptest), so it can't take over your Orca.")
    out = log_path.with_name(log_path.name + ".out")
    orca = subprocess.Popen(["orca", "--debug-file", str(log_path)],
                            stdout=open(out, "w"), stderr=subprocess.STDOUT)
    end = time.time() + 30
    while time.time() < end:
        if log_path.exists() and "SPEECH OUTPUT" in log_path.read_text("utf-8", "replace"):
            return orca
        time.sleep(0.2)
    orca.kill()
    said = out.read_text("utf-8", "replace").strip()[-2000:]
    raise SystemExit("Orca didn't start speaking within 30 seconds. Is the accessibility "
                     "bus running? (orca_speech.sh starts one.)"
                     + (f"\nOrca said:\n{said}" if said else ""))


#: Keys xdotool can't send as themselves: it picked F10's Ctrl+Alt level
#: (switch to virtual terminal 10) on Ubuntu's keyboard map, so Orca heard
#: "left alt" and the app nothing. AT-SPI sends these by key code instead.
_KEYCODE_KEYS = {"F1", "F10"}


def _keycode(name: str) -> int:
    out = subprocess.run(["xmodmap", "-pke"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = line.split()
        if len(parts) > 3 and parts[3] == name:
            return int(parts[1])
    raise SystemExit(f"{name} isn't on this display's keyboard map")


def press(keys: List[str]) -> None:
    for key in keys:
        if key in _KEYCODE_KEYS:
            # The system Python, which has PyGObject; .venv may not.
            subprocess.run(["/usr/bin/python3", "-c",
                            "import sys, gi; gi.require_version('Atspi', '2.0'); "
                            "from gi.repository import Atspi; "
                            "Atspi.generate_keyboard_event(int(sys.argv[1]), None, "
                            "Atspi.KeySynthType.PRESSRELEASE)", str(_keycode(key))],
                           check=True)
        else:
            subprocess.run(["xdotool", "key", key], check=True)
        time.sleep(0.3)


# -- the check ------------------------------------------------------------------------------

def _found(needle: str, spoken: List[str]) -> bool:
    text = " ".join(spoken).casefold()
    return needle.casefold() in text


def run_steps(log: OrcaLog, steps: List[Step], version: tuple, record: bool,
              world: Path) -> List[dict]:
    results = []
    for index, step in enumerate(steps):
        expect = (step.expect + (step.expect_dbus if version >= (49, 0) else [])
                  + (step.expect_50 if version >= (50, 0) else []))

        def done(spoken, expect=expect):
            said = [s for s in spoken if s.casefold().rstrip(".") not in _KEY_ECHO]
            if expect and not record:
                return all(_found(e, said) for e in expect)
            return bool(spoken)
        if step.keys or step.trigger:
            log.mark()
        # The first step's speech is the app starting, from the mark made
        # before it was started.
        if step.trigger:
            (world / f"{step.trigger}.trigger").write_text("", encoding="utf-8")
        press(step.keys)
        spoken = log.wait_quiet(done, timeout=START_TIMEOUT if index == 0 else STEP_TIMEOUT)
        said = [s for s in spoken if s.casefold().rstrip(".") not in _KEY_ECHO]
        missing = [] if record else [e for e in expect if not _found(e, said)]
        refused = [] if record else [r for r in step.refuse if _found(r, said)]
        results.append({"step": step.name, "keys": step.keys, "spoken": said, "heard": spoken,
                         "missing": missing, "refused": refused,
                         "passed": not missing and not refused})
    return results


def report(results: List[dict], version: tuple, record: bool) -> str:
    lines = [f"Orca {version[0]}.{version[1]}: what The Chat Place said, step by step", ""]
    for r in results:
        mark = "    " if record else ("ok  " if r["passed"] else "FAIL")
        keys = " ".join(r["keys"]) or "(start)"
        lines.append(f"{mark} {r['step']} [{keys}]: {' | '.join(r['spoken']) or '(nothing)'}")
        for m in r["missing"]:
            lines.append(f"       not said: {m!r}")
        for m in r["refused"]:
            lines.append(f"       said, and shouldn't be: {m!r}")
    if not record:
        failed = sum(not r["passed"] for r in results)
        lines += ["", f"{len(results) - failed} of {len(results)} steps passed."]
    return "\n".join(lines)


def check(args) -> int:
    for tool in ("orca", "xdotool"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} isn't installed (see this file's docstring).")
    if not os.environ.get("DISPLAY"):
        raise SystemExit("No display: run this through tools/orca_speech.sh.")
    work = Path(tempfile.mkdtemp(prefix="tcp-orca-"))
    log_path = Path(args.orca_log) if args.orca_log else work / "orca-debug.log"
    version = orca_version()
    orca = start_orca(log_path)
    log = OrcaLog(log_path)
    log.mark()
    app = subprocess.Popen([sys.executable, str(Path(__file__)), "serve", "--root",
                            str(work / "world")], cwd=str(ROOT),
                           stdout=open(work / "app.log", "w"), stderr=subprocess.STDOUT)
    try:
        results = run_steps(log, STEPS, version, record=args.record, world=work / "world")
    finally:
        app.terminate()
        try:
            app.wait(10)
        except subprocess.TimeoutExpired:
            app.kill()
        orca.terminate()
        try:
            orca.wait(10)
        except subprocess.TimeoutExpired:
            orca.kill()
    text = report(results, version, args.record)
    print(text)
    if args.json:
        Path(args.json).write_text(json.dumps({"orca": list(version), "steps": results},
                                              indent=2), encoding="utf-8")
    if app.returncode not in (0, None, -15) and not args.record:
        print(f"\nThe app exited with {app.returncode}; its output is in {work / 'app.log'}")
    return 0 if args.record or all(r["passed"] for r in results) else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("serve", help="run the app on made-up sessions (check starts it)")
    s.add_argument("--root", required=True)
    c = sub.add_parser("check", help="run the steps against Orca")
    c.add_argument("--record", action="store_true", help="report what was said, check nothing")
    c.add_argument("--json", help="save every step's speech here")
    c.add_argument("--orca-log", help="Orca's debug log (default: a temporary file)")
    args = parser.parse_args(argv)
    if args.command == "serve":
        return serve(Path(args.root))
    return check(args)


if __name__ == "__main__":
    sys.exit(main())
