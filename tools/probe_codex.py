"""Check the local read-only app-server contract without sending a prompt.

Print only aggregate counts and account type, never titles, thread IDs,
messages or tokens. Use --codex for an executable outside the shell's PATH.
Uses the managed read-only transport; never starts a turn (#184) (#171).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from thechatplace import platform_paths  # noqa: E402
from thechatplace.providers.codex_rpc import ReadOnlyCodexConnection  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default=platform_paths.find_codex())
    args = parser.parse_args()
    if not args.codex:
        parser.error("Codex executable not found; pass --codex")
    probe = ReadOnlyCodexConnection(args.codex)
    try:
        account = probe.request("account/read", {"refreshToken": False}).get("account")
        summary = {"initialized": True,
                   "account_type": account.get("type") if isinstance(account, dict) else None}
        for archived in (False, True):
            count, cursor, cursors = 0, None, set()
            while True:
                params = {"limit": 100, "archived": archived,
                          "sourceKinds": ["cli", "vscode", "exec", "appServer", "unknown"]}
                if cursor:
                    params["cursor"] = cursor
                result = probe.request("thread/list", params)
                data = result.get("data")
                if not isinstance(data, list):
                    raise RuntimeError("thread/list: invalid data")
                count += len(data)
                cursor = result.get("nextCursor")
                if cursor is None:
                    break
                if not isinstance(cursor, str) or not cursor or cursor in cursors:
                    raise RuntimeError("thread/list: invalid pagination")
                cursors.add(cursor)
            summary["archived_threads" if archived else "active_threads"] = count
        print(json.dumps(summary, indent=2))
    finally:
        probe.close()


if __name__ == "__main__":
    main()
