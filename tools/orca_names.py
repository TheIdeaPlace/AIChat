"""Every control Orca can reach in The Chat Place has a name (Linux).

Reads a visual probe run made with the Orca audit on
(``TCP_PROBE_ORCA=1 tools/ui_probe.py --out DIR``, in
``tools/linux_a11y_session.sh``) and fails if any surface has a control Orca
would read without a name, or if the audit didn't run at all, so a broken
setup can't pass as a clean one (#230).

    python tools/orca_names.py DIR
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def check(folder: Path) -> tuple[list[str], list[str], int]:
    """(unnamed, not audited, controls seen) across the run's surfaces."""
    unnamed, missing, seen = [], [], 0
    for path in sorted(folder.glob("*.json")):
        if path.name.startswith("manifest-") or path.name.endswith("-orca.json"):
            continue
        info = json.loads(path.read_text(encoding="utf-8"))
        orca = info.get("orca")
        if not orca or orca.get("error"):
            missing.append(f"{path.stem}: {orca.get('error') if orca else 'no Orca audit'}")
            continue
        seen += len(orca.get("controls", []))
        unnamed += [f"{path.stem}: a {c.get('role', 'control')} with no name"
                    for c in orca.get("unnamed", [])]
    return unnamed, missing, seen


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__)
        return 2
    unnamed, missing, seen = check(Path(args[0]))
    for line in unnamed + missing:
        print(line)
    if not seen:
        print("The audit found no controls at all: is the accessibility bus running?")
        return 1
    print(f"{seen} controls Orca can reach; {len(unnamed)} without a name; "
          f"{len(missing)} surfaces not audited.")
    return 1 if unnamed or missing else 0


if __name__ == "__main__":
    sys.exit(main())
