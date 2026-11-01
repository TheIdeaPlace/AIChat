# Security policy

## Reporting a vulnerability

**Please don't open a public issue for a security problem.**

Use GitHub's private reporting instead: on the [Security tab](https://github.com/TheIdeaPlace/AIChat/security) of this repository, choose **Report a vulnerability**. If you can't use that, send a private message to [@kellylford](https://github.com/kellylford) on GitHub.

Please include:

- What the problem is, and what someone could do with it
- Steps to reproduce it, or a small proof of concept
- The version of The Chat Place, and your operating system and version

You'll get an acknowledgement within a few days. If the report is confirmed, a fix is released as quickly as possible, and you're credited in the release notes unless you'd rather not be.

## What's in scope

The Chat Place is a desktop app for Windows, macOS and Linux that lists and runs AI chats on the user's own sign-in: Claude (it reads Claude Code's local session files and runs `claude -p`), Codex (through `codex app-server`) and Copilot (through the Copilot CLI). The areas most likely to matter:

- **Formatted messages.** Long messages open as a web page, under a Content Security Policy that allows only the page's own styles. Anything that lets a message run script, load remote content or reach local files is in scope.
- **Permission and question answers.** Anything that makes The Chat Place approve a permission or approval request from any of these providers, or answer a question, without the user choosing to is in scope.
- **The `thechatplace://` links.** A crafted link that does more than open the app at a session or message is in scope.
- **Updates.** The app updates itself through Velopack from this repository's releases. Anything that would let it install something that isn't a release published here (signed on Windows and the Mac; Linux AppImages aren't signed) is in scope.
- **Session files.** The Chat Place must never write to the Claude desktop app's files, to a transcript or to another provider's history. A path that does is in scope.
- **Chats it didn't start.** Those are read-only. Anything that sends to one, or resumes it, is in scope.

Problems in Claude Code, Codex or the Copilot CLI themselves, in the Claude desktop app, or in the operating system are out of scope, unless The Chat Place makes them worse.
