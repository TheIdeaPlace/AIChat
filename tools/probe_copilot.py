"""Development preview: run one GitHub Copilot CLI chat through The Chat Place's
Copilot back end (#352), before the window uses it.

Plain text in and out, so it reads well with a screen reader in a terminal.
Each message you type is sent through copilot_runtime with all its safety
rules; when Copilot asks permission, you're asked "allow once or deny".

    python tools/probe_copilot.py --folder PATH [--resume CHAT_ID]

PATH is the folder Copilot works in, and may change. Give it a scratch
folder, never this repository.

An empty line or "quit" ends. Ctrl+C while Copilot is answering stops that
turn. Every message uses a premium request from your Copilot plan.
"""
from __future__ import annotations

import argparse
import queue
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from thechatplace.providers.copilot_acp import AcpError  # noqa: E402
from thechatplace.providers.copilot_runtime import (ALLOW_ONCE, REJECT,  # noqa: E402
                                                    CopilotChatError, CopilotChatRuntime)

DONE = ("completed", "interrupted", "failed")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--folder", required=True,
                        help="the folder Copilot works in: a scratch folder, since it may change")
    parser.add_argument("--resume", help="reopen a chat this tool or The Chat Place started")
    args = parser.parse_args(argv)
    states: "queue.Queue" = queue.Queue()
    runtime = CopilotChatRuntime(states.put)
    seen = {"printed": {}, "notes": set()}  # across turns: nothing is said twice
    try:
        if args.resume:
            # A development tool, run on purpose with an id: it takes that as
            # the person saying the chat is theirs. The window checks its own
            # record of owned chats instead.
            runtime.adopt(args.resume)
            runtime.prepare(args.resume, args.folder)
            chat = args.resume
            print(f"Reopened chat {chat}.")
        else:
            chat = runtime.create(args.folder)
            print(f"New Copilot chat {chat} in {args.folder}.")
        print("Safe to send (agent mode, allow-all off):", "yes" if runtime.ready(chat) else "no")
        while True:
            try:
                text = input("\nYou (empty line to quit): ").strip()
            except EOFError:
                break
            if text.lower() in ("", "quit"):
                break
            try:
                runtime.send(chat, text)
            except CopilotChatError as exc:
                print("Not sent:", exc)
                continue
            try:
                follow(runtime, chat, states, seen)
            except KeyboardInterrupt:
                print("\nStopping this turn.")
                waiting = runtime.stop(chat)
                if waiting:
                    print("Not sent:", " | ".join(waiting))
                follow(runtime, chat, states, seen)
    except (CopilotChatError, AcpError, OSError) as exc:
        print("Couldn't use Copilot:", exc)
        return 1
    finally:
        runtime.close()
    print(f"\nTo continue this chat later: python tools/probe_copilot.py --resume {chat}")
    return 0


def follow(runtime, chat, states, seen) -> None:
    """Print the reply as it arrives and ask about each permission request."""
    printed, asked, notes = seen["printed"], set(), seen["notes"]
    while True:
        state = states.get()
        if state.native_id != chat:
            continue
        for reply_id, text in state.replies:
            done = printed.get(reply_id, 0)
            if len(text) > done:
                if done == 0:
                    print("\nCopilot: ", end="")
                print(text[done:], end="", flush=True)
                printed[reply_id] = len(text)
        for request in state.requests:
            if request.request_id in asked:
                continue
            asked.add(request.request_id)
            print("\n\nCopilot asks permission.\n" + request.detail())
            answer = input("Allow once? Type y to allow, anything else to deny: ").strip().lower()
            try:
                runtime.answer(chat, request.request_id, ALLOW_ONCE if answer == "y" else REJECT)
                print("Allowed once." if answer == "y" else "Denied.")
            except (CopilotChatError, ValueError) as exc:
                print("Couldn't answer:", exc)
        for label, text in (("Note", state.notice), ("Stopped", state.problem)):
            if text and (label, text) not in notes:
                notes.add((label, text))
                print(f"\n{label}:", text)
        if state.outcome in DONE and not state.requests:
            print(f"\n[turn {state.outcome}]")
            if state.unsent:
                print("Not sent:", " | ".join(state.unsent))
            return


if __name__ == "__main__":
    sys.exit(main())
