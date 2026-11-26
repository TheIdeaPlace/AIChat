"""Check a release tag against the app's version, and print the version.

    python tools/check_version.py v0.1.0   # exits 1 unless they agree
    python tools/check_version.py                       # prints the version
    python tools/check_version.py --pack                # the version to pack as: with
                                                        # TCP_BUILD_LABEL, "0.6.4-bld05"

The version lives in one place, ``thechatplace/__init__.py``. The release
workflow runs this before building, so a tag that disagrees with it never
produces a release (the updater would compare against the wrong number).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from thechatplace import __version__  # noqa: E402
from thechatplace.build_info import LABEL  # noqa: E402

TAG_PREFIX = "v"


def main(argv: list, env=os.environ) -> int:
    if not re.fullmatch(r"\d+\.\d+\.\d+", __version__):
        print(f"thechatplace/__init__.py has version {__version__!r}; vpk needs "
              "major.minor.patch.", file=sys.stderr)
        return 1
    label = (env.get("TCP_BUILD_LABEL") or "").strip()
    if argv[:1] == ["--pack"]:
        # The version to pack as (vpk --packVersion): a test build's label
        # makes it a pre-release of this version, "0.6.4-bld05", which
        # Velopack sorts before 0.6.4, so the real release still installs.
        if label and not LABEL.fullmatch(label):
            print(f"TCP_BUILD_LABEL {label!r} isn't a build label (as in bld05).",
                  file=sys.stderr)
            return 1
        print(f"{__version__}-{label}" if label else __version__)
        return 0
    if not argv:
        print(__version__)
        return 0
    tag = argv[0]
    if label:
        print(f"A release tag ({tag}) is never built with a test build's label "
              f"(TCP_BUILD_LABEL={label}).", file=sys.stderr)
        return 1
    if not tag.startswith(TAG_PREFIX):
        print(f"Tag {tag} doesn't start with {TAG_PREFIX}.", file=sys.stderr)
        return 1
    tagged = tag[len(TAG_PREFIX):]
    if tagged != __version__:
        print(f"Tag {tag} says {tagged}, but thechatplace/__init__.py says {__version__}. "
              "Change __version__ in the same commit as the tag.", file=sys.stderr)
        return 1
    print(__version__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
