# The Chat Place: ACP-first experiment

This branch is an experiment, not a release. It answers one question from issue #489: **if The Chat Place had started with what we know now, what would it look like, and is that better?**

It's a from-scratch rebuild, with no history shared with `main`. It keeps its data in its own folder (`TheChatPlaceExperiment`), so it can't touch a real install's chats or settings, and it never writes to the files of the Claude desktop app or any transcript.

## The idea

In `main`, each AI is integrated separately, and the window checks which AI it's dealing with in hundreds of places, so every feature gets fitted in again for every provider. Here:

- **The window knows no provider.** It reads one set of plain descriptions (`contracts.py`): what a chat can do, what its options are, what's being asked. A test fails if shared code names a provider.
- **One backend covers every ACP agent** (`integrations/acp.py`). An agent is one row in `integrations/agents.py`.
- **Claude and Codex stay native**, because ACP adapters haven't shown they keep the billing and read-only guarantees. They reach the window through the same contracts.
- **One adapter covers local chat models** (`integrations/openai_chat.py`), such as Ollama.
- **One test suite covers every backend** (`tests/test_contract.py`), and checks a feature's absence as well as its presence.
- **An agent's words are information, never authorization.** This app's policy (`policy.py`) decides what's offered. An agent can't label a button, and only one-time choices are ever offered.

`dev-notes/acp-first-findings.md` has what the experiment found, including what it costs and what isn't here.

## Running it

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m thechatplace
```

On a Mac or Linux, use `.venv/bin/python`. On Linux, create the venv with `--system-site-packages` and install the distribution's wxPython first.

Claude is on from the start. Choose **Help, AI Providers** to turn on the others. Each one says whether it's installed, who pays for its turns, and whether this version has been checked.

## The window

Three parts, in Tab order: the chats, the messages, and the reply box with Send and Stop. A chat from elsewhere has a note saying where to reply instead of a reply box.

| Key | What it does |
|---|---|
| Ctrl+1, Ctrl+2, Ctrl+3 | Go to the chats, the messages or the reply box |
| F6, Shift+F6 | Next or previous part of the window |
| Enter in the chats | Open that chat and move to its messages |
| Enter in the messages | Read the whole message; Escape comes back |
| Ctrl+C in the messages | Copy the whole message |
| Ctrl+Enter in the reply box | Send; during a turn it waits its turn |
| Ctrl+Period | Stop the turn; waiting messages come back to the reply box |
| Escape | From the messages or the reply box, back to the chats |
| Ctrl+N | New chat |
| Ctrl+Shift+O | Options for this chat |
| Ctrl+Shift+D | Turn details: what it's doing, its plan and how full the context is |
| F5 | Refresh the chats |

## Tests

```
.venv\Scripts\python -m pytest -q tests
```

Window tests are skipped unless `TCP_GUI_TESTS=1` is set, because on a PC someone's working on, a test window can take focus and be read aloud. CI sets it. `tools/real_smoke.py` runs the real AIs and spends a little of each account, so it's only run by hand.
