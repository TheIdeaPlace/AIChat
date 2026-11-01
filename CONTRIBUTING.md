# Contributing to The Chat Place

Thank you for wanting to help. The Chat Place is a keyboard- and screen-reader-friendly app for AI chats (Claude, Codex and Copilot), so every change is held to that bar: it has to work by keyboard, and everything it shows has to be named and announced properly for JAWS, NVDA, VoiceOver and Orca.

## Before you start

For anything bigger than a small fix, **open an issue first** and say what you'd like to do. It saves duplicated work, and lets us agree on the approach before code is written.

## Getting started

1. Fork the repository and create a branch from `main`.
2. Read [`CLAUDE.md`](CLAUDE.md). It's the working reference for the architecture, the tests and the conventions, for people and for AI assistants alike.
3. The [README](README.md) is the user-facing spec: shortcuts, file locations and behaviour. A change in behaviour updates it, and the in-app guide (`thechatplace/assets/user-guide.md`, then `python tools/make_docs.py`), in the same pull request.

## Running from source and testing

| | Set up | Run | Test |
|---|---|---|---|
| Windows | `winsetup.bat` | `.venv\Scripts\pythonw TheChatPlace.pyw` | `.venv\Scripts\python -m pytest tests` |
| Mac | `./macsetup.sh` | `.venv/bin/python -m thechatplace` | `.venv/bin/python -m pytest tests` |
| Linux | `./linuxsetup.sh` | `.venv/bin/python -m thechatplace` | `.venv/bin/python -m pytest tests` |

On Linux, `.venv` uses your distribution's wxPython (PyPI has no Linux wheels), and without a display, run the window tests under `GDK_BACKEND=x11 xvfb-run -a`. [`DEVELOPING.md`](DEVELOPING.md) covers building and more.

The test suite runs on Windows, macOS and Linux for every pull request; Windows and macOS must pass before a pull request can merge. A non-blocking Linux job also runs Orca to check what it says. Tests of one platform's behaviour use the markers in `tests/markers.py` so they skip elsewhere. New features and fixes should come with tests:

- Tests use **made-up** records built by `tests/records.py`. Never copy real conversation content into a test.
- `tests/fake_claude.py` stands in for `claude -p`, so no test runs the real Claude Code.
- UI tests build the window hidden. Use the `env` fixture from `tests/fake_env.py` so nothing touches your real settings, runs the real `claude`, or speaks aloud.

## Accessibility

- Everything works from the keyboard, with a shortcut listed in the README.
- Controls have short, clear names. Instructions are spoken when they're useful, not built into a control's name.
- Announcements are short and aren't repeated.
- Focus lands somewhere sensible after every action.

If your change needs a screen reader check, list the exact steps in the pull request so it can be checked with JAWS, NVDA or VoiceOver. A change to a keyboard path (focus, menus, dialogs, Escape) also adds or updates a step in `tools/orca_speech.py`.

## Rules that protect people's data

- Never write to the Claude desktop app's files or to any transcript. The Chat Place only reads them; its own data lives in its own folder.
- The app runs each AI on the person's own subscription or sign-in (`claude -p` for Claude, ChatGPT sign-in for Codex). Never add an API key path.
- Chats The Chat Place didn't start are read-only. Don't add a way to send to them.

## Commits and pull requests

- A commit message is one plain-English sentence about the change as a user would see it, ending with the issue number, for example `Code blocks: list, read and copy each one (#17)`.
- Keep a pull request to one feature or fix, and link the issue it closes (`Closes #N`).
- A user-visible change adds an entry to the open release notes file in `release-notes/` (the highest version without a tag). `CLAUDE.md` explains the format. Tests, CI and refactors need no entry.
- Research and design notes go in `dev-notes/`, not `docs/`: GitHub Pages publishes everything in `docs/`.

## Questions

Open an issue. We're happy to help you find your way around.
