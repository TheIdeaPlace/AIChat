"""Development preview: list or export local Codex history without starting turns."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from thechatplace import export, platform_paths  # noqa: E402
from thechatplace.providers.codex import CodexProvider  # noqa: E402


def read(provider, thread_id=None, fmt="txt"):
    """Use the production adapter; reject absent/child threads and partial history."""
    sessions = provider.collect().sessions
    if thread_id is None:
        return json.dumps([{"id": s.cli_session_id, "title": s.title,
                            "archived": s.archived, "state": s.state}
                           for s in sessions], ensure_ascii=False, indent=2)
    session = next((s for s in sessions if s.cli_session_id == thread_id), None)
    if session is None:
        raise ValueError("Chat is not in the local Codex catalog")
    reader = provider.open_reader(session)
    if reader is None:
        raise ValueError("Chat history is unavailable")
    reader.refresh()
    return export.render(fmt, session.title, reader.transcript.messages, folder=session.cwd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", help="Path to the native Codex executable")
    parser.add_argument("--thread", help="Chat ID from the listing; omit to list local chats")
    parser.add_argument("--format", choices=("txt", "md", "html"), default="txt")
    args = parser.parse_args(argv)
    executable = args.codex or platform_paths.find_codex()
    if not executable:
        parser.error("Native Codex executable not found; pass --codex")
    provider = CodexProvider(executable=executable)
    result = 0
    try:
        text = read(provider, args.thread, args.format)
        # Conversation/tool output is untrusted terminal text. Remove control
        # characters that could issue terminal commands while retaining layout.
        text = text.translate({c: None for c in (*range(32), *range(127, 160))
                               if c not in (9, 10, 13)})
        print(text)
    except Exception:  # noqa: BLE001 - never print RPC payloads or credentials
        print("Could not read Codex history. Check the native CLI and local chat availability. "
              "Partial or paginated histories are not supported yet.", file=sys.stderr)
        result = 1
    finally:
        try:
            provider.close()
        except Exception:  # noqa: BLE001 - keep process diagnostics private
            print("Could not finish Codex backend cleanup.", file=sys.stderr)
            result = 1
    return result


if __name__ == "__main__":
    raise SystemExit(main())
