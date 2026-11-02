"""Run the real AIs through the service, with no window: a one-word prompt, a
permission request that's denied, then a reload. This spends a little of each
account, so it's run by hand, never in CI.

python tools/real_smoke.py [--deny] [--model NAME] INTEGRATION...
"""
from __future__ import annotations

import argparse
import json
import queue
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from thechatplace.contracts import AskPermission, AskQuestion, Decision, Notice, TurnEnded  # noqa: E402
from thechatplace.integrations.registry import default_integrations  # noqa: E402
from thechatplace.service import ChatService  # noqa: E402
from thechatplace.store import Store  # noqa: E402

ONE_WORD = "Reply with the single word OK and nothing else. Don't use any tools."
NEEDS_A_TOOL = ("Create a file named probe.txt in the current folder containing the word hello, using your "
                "file tool. Don't ask me first; just try it.")
TURN_SECONDS = 180


def wait_turn(events, service, ref, decision=None):
    asked = []
    deadline = time.monotonic() + TURN_SECONDS
    while time.monotonic() < deadline:
        try:
            _ref, event = events.get(timeout=1)
        except queue.Empty:
            continue
        if isinstance(event, AskPermission):
            asked.append(event.request)
            service.answer_permission(ref, event.request, decision or Decision.DENY)
        elif isinstance(event, AskQuestion):
            service.answer_question(ref, event.request, None)
        elif isinstance(event, Notice):
            print("   notice:", event.text)
        elif isinstance(event, TurnEnded):
            while service.state(ref).busy and time.monotonic() < deadline:
                time.sleep(0.05)
            return event, asked
    service.stop(ref)
    return None, asked


def run_one(integration, deny, model):
    folder = Path(tempfile.mkdtemp(prefix="tcp-smoke-"))
    data = Path(tempfile.mkdtemp(prefix="tcp-smoke-data-"))
    events: queue.Queue = queue.Queue()
    service = ChatService([integration], Store(data), lambda ref, event: events.put((ref, event)))
    service.store.set_enabled(integration.id, True)
    print(f"== {integration.name}")
    try:
        status = service.status(integration.id)
        print("   status:", status.summary, "| verified:", status.verified)
        if not status.installed:
            return "not installed"
        options = {}
        for group in service.new_chat_options(integration.id):
            print(f"   new-chat option {group.id}: {[v.id for v in group.values][:6]} current={group.current!r}")
        if model:
            options["model"] = model
        started = time.monotonic()
        info = service.start_chat(integration.id, str(folder), "Smoke", options)
        print(f"   started in {time.monotonic() - started:.1f}s, caps: {sorted(c.value for c in info.capabilities)}")
        for group in service.chat_options(info.ref):
            print(f"   chat option {group.id}: {[v.label for v in group.values][:5]} current={group.current[:60]!r}")
        service.send(info.ref, ONE_WORD)
        ended, _asked = wait_turn(events, service, info.ref)
        print("   turn:", ended.status.value if ended else "TIMED OUT", ended.message if ended else "")
        print("   messages:", [f"{m.role}:{m.text[:60]}" for m in service.messages(info.ref)])
        if deny:
            service.send(info.ref, NEEDS_A_TOOL)
            ended, asked = wait_turn(events, service, info.ref, Decision.DENY)
            print("   tool turn:", ended.status.value if ended else "TIMED OUT",
                  "| asked:", [(r.summary, r.detail[:80], [d.value for d in r.decisions], bool(r.caution)) for r in asked])
            print("   file written despite denial:", (folder / "probe.txt").exists())
        integration.close()
        # A new connection has to find its way back to the same chat.
        service.send(info.ref, ONE_WORD)
        ended, _asked = wait_turn(events, service, info.ref)
        print("   after reconnect:", ended.status.value if ended else "TIMED OUT", ended.message if ended else "")
        fresh_history = integration.read_history(info.ref) if not integration.app_keeps_history else service.store.read_history(info.ref)
        print("   history reads back:", [f"{m.role}:{m.text[:40]}" for m in fresh_history])
        print("   drift:", json.dumps(service.drift_report().get(integration.id)))
        foreign = integration.list_foreign()
        print(f"   other chats it lists: {len(foreign)}")
        return "ok"
    except Exception as error:  # noqa: BLE001 - report it and go on to the next
        print("   FAILED:", type(error).__name__, error)
        return "failed"
    finally:
        service.close()
        shutil.rmtree(folder, ignore_errors=True)
        shutil.rmtree(data, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deny", action="store_true")
    parser.add_argument("--model", default="")
    parser.add_argument("names", nargs="+")
    args = parser.parse_args()
    known = {i.id: i for i in default_integrations()}
    results = {name: run_one(known[name], args.deny, args.model) for name in args.names}
    print(results)


if __name__ == "__main__":
    main()
