"""Live check of #749 Part 2 through the app's own runner: a real `claude -p`
(on the subscription, as the app runs it) sets a one-time timer, the turn is
kept open for it, Claude Code fires it by itself, and the wake turn's answer
comes as the turn's last event, with its own system/init checked. Then a
ScheduleWakeup, the same way.

Skipped unless TCP_LIVE_WAITS=1; never in CI. Each run is a few short Haiku
turns on the subscription and takes about four minutes. Run it before a build
with a change to waits goes to a person: the fakes are only as right as what
we believe Claude Code sends."""
import os
import shutil
import threading
import uuid

import pytest

from thechatplace import claude_cli
from thechatplace.scheduled_waits import WaitSlots

LIVE = os.environ.get("TCP_LIVE_WAITS") == "1"
pytestmark = pytest.mark.skipif(not LIVE, reason="live waits check: set TCP_LIVE_WAITS=1")

PROMPTS = {
    "cron": ("Use the CronCreate tool to schedule a one-shot job (recurring: false) that fires "
             "at the next minute that is at least 2 minutes from now, with the prompt "
             "'Reply with exactly the words TIMER FIRED.' Then reply with one short sentence "
             "saying when you'll check, and nothing else."),
    "wakeup": ("Use the ScheduleWakeup tool once, with delaySeconds 60, the prompt 'Reply with "
               "exactly the words WAKEUP FIRED.' and the reason 'live check'. Then reply with "
               "one short sentence saying when you'll be back, and nothing else."),
}
TOOLS = {"cron": "CronCreate", "wakeup": "ScheduleWakeup"}


def run_live(tmp_path, kind):
    exe = shutil.which("claude")
    assert exe, "claude isn't on PATH"
    command = claude_cli.build_new_command(exe, str(uuid.uuid4()), "Live waits check",
                                           "default", model="haiku",
                                           allowed_tools=[TOOLS[kind]])
    events, done = [], threading.Event()

    def on_event(event):
        events.append(event)
        if event.kind in ("finished", "failed"):
            done.set()
    slots = WaitSlots(lambda: 5)
    runner = claude_cli.TurnRunner(command, str(tmp_path), PROMPTS[kind], on_event,
                                   wait_slots=slots)
    runner.start()
    try:
        assert done.wait(420), [e.kind for e in events]
    finally:
        if not done.is_set():
            runner.cancel()
    runner.join(30)
    return runner, events, slots


@pytest.mark.parametrize("kind", ["cron", "wakeup"])
def test_a_real_check_keeps_the_turn_then_fires_and_closes(tmp_path, kind):
    runner, events, slots = run_live(tmp_path, kind)
    kinds = [e.kind for e in events]
    print(kind, kinds)
    assert "waiting" in kinds and "woke" in kinds, kinds
    waiting = next(e for e in events if e.kind == "waiting")
    assert waiting.data["scheduled"] is True and waiting.data["waits"]
    woke = next(e for e in events if e.kind == "woke")
    assert woke.data["source"] == ("schedule_wakeup" if kind == "cron" else "loop_wakeup")
    final = events[-1]
    assert final.kind == "finished", (final.kind, final.text)
    assert final.data.get("woke") == woke.data["source"]
    assert "FIRED" in final.text.upper()
    assert "waits" not in final.data          # it ran: nothing left to lose
    assert runner.held_for_waits and len(slots) == 0
