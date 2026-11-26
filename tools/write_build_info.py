"""Write thechatplace/_build.py: which build this is (thechatplace/build_info.py).

    python tools/write_build_info.py        # every build runs it before PyInstaller

The label is TCP_BUILD_LABEL ("bld05" for a test build, matching its folder
under testing\\tcp; unset for a release). The commit and branch come from
GitHub Actions' variables in CI, or from git; the date is today's (UTC).
_build.py is never committed (.gitignore): a source run reads .git instead.
"""
from __future__ import annotations

import datetime
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from thechatplace.build_info import LABEL  # noqa: E402

TARGET = ROOT / "thechatplace" / "_build.py"


def _git(*args: str) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                             timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def gather(env=os.environ) -> dict:
    label = (env.get("TCP_BUILD_LABEL") or "").strip()
    if label and not LABEL.fullmatch(label):
        raise SystemExit(f"TCP_BUILD_LABEL {label!r} isn't a build label: lower-case letters "
                         "and digits, starting with a letter, as in bld05.")
    # A pull request's checkout is a merge commit: name the branch's own.
    commit = (env.get("TCP_BUILD_COMMIT") or env.get("GITHUB_SHA") or _git("rev-parse", "HEAD"))
    branch = (env.get("GITHUB_HEAD_REF") or env.get("GITHUB_REF_NAME")
              or _git("rev-parse", "--abbrev-ref", "HEAD"))
    built = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    return {"LABEL": label, "COMMIT": commit, "BRANCH": branch, "BUILT": built}


def main() -> int:
    values = gather()
    lines = ['"""Written by tools/write_build_info.py for this build; never committed."""']
    lines += [f"{key} = {value!r}" for key, value in values.items()]
    TARGET.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Build: {values['LABEL'] or 'release'} {values['COMMIT'][:8]} on {values['BRANCH']}, "
          f"{values['BUILT']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
