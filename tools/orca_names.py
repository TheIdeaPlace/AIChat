"""Every control Orca can reach in The Chat Place has a name (Linux).

Reads a visual probe run made with the Orca audit on
(``TCP_PROBE_ORCA=1 tools/ui_probe.py --out DIR``, in
``tools/linux_a11y_session.sh``) and fails if any surface has a control Orca
would read without a name, or if the audit didn't run at all, so a broken
setup can't pass as a clean one (#230).

    python tools/orca_names.py DIR          check a run made already
    python tools/orca_names.py --run DIR    make the run, then check it (what CI does)

``--run`` runs the probe itself, in a desktop of its own. A probe that
crashes (a segfault in GTK or AT-SPI, which isn't a finding about names) is
started again on the surface it crashed on and those it hadn't reached, and
the log says so (#579). That happens once in the whole run: a second crash,
on any surface, fails, as does a crash after the last surface (closing). A
control without a name is never run again: it fails. What the probe printed,
GTK's warnings included, is in DIR/probe.log.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TAG = "linux-light"
#: Exit codes from a probe that crashed rather than finished: a signal, as
#: the shell reports it (128 + the signal) or as Python does (negative).
#: Linux's numbers, written out so this also loads (and is tested) elsewhere.
_CRASH_SIGNALS = {4: "SIGILL", 6: "SIGABRT", 7: "SIGBUS", 8: "SIGFPE", 11: "SIGSEGV"}


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


def crashed(returncode: int) -> Optional[str]:
    """The signal's name if a probe that exited with ``returncode`` crashed,
    else None (it finished, failed, or ran out of time: 124)."""
    number = -returncode if returncode < 0 else returncode - 128 if returncode > 128 else 0
    return _CRASH_SIGNALS.get(number)


def _surfaces() -> List[str]:
    out = subprocess.run([sys.executable, str(HERE / "ui_probe.py"), "--list"],
                         capture_output=True, text=True, check=True, cwd=str(ROOT)).stdout
    return [line.split()[0] for line in out.splitlines() if line.strip()]


def _probe(folder: Path, names: List[str]) -> Tuple[int, Dict[str, dict]]:
    """One probe, in a desktop of its own, over ``names``: its exit code and
    the surfaces it finished (its manifest)."""
    manifest = folder / f"manifest-{TAG}.json"
    manifest.unlink(missing_ok=True)
    command = [str(HERE / "linux_a11y_session.sh"), sys.executable, "-X", "faulthandler",
               str(HERE / "ui_probe.py"), "--out", str(folder), "--tag", TAG]
    for name in names:
        command += ["--surface", name]
    env = dict(os.environ, TCP_PROBE_ORCA="1")
    process = subprocess.Popen(command, cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, errors="replace")
    warnings = 0
    # Everything the probe said goes in probe.log (kept with the pictures);
    # GTK's and ATK's CRITICAL warnings, thousands of them, only there.
    with (folder / "probe.log").open("a", encoding="utf-8") as log:
        for line in process.stdout:
            log.write(line)
            if "-CRITICAL **" in line:
                warnings += 1
            elif line.strip():
                print(line, end="", flush=True)
    process.wait()
    print(f"({warnings} GTK and ATK CRITICAL warnings, in {folder / 'probe.log'})", flush=True)
    try:
        done = json.loads(manifest.read_text(encoding="utf-8"))["surfaces"]
    except (OSError, ValueError, KeyError):
        done = {}
    return process.returncode, done


def run(folder: Path, names: List[str], probe=_probe) -> Tuple[int, List[str]]:
    """The probe over ``names``, started again after a crash on the surfaces
    it hadn't finished: once in the whole run, so a second crash anywhere
    fails. (exit code, problems)."""
    folder.mkdir(parents=True, exist_ok=True)
    remaining, finished, crashes, problems = list(names), {}, {}, []
    code = 0
    while remaining:
        code, done = probe(folder, remaining)
        finished.update(done)
        left = [n for n in remaining if n not in done]
        why = crashed(code)
        if why is None:
            if left:
                problems.append(f"the probe stopped (exit {code}) before {', '.join(left)}")
            elif code not in (0, 1):  # 1: a surface failed, reported below
                problems.append(f"the probe exited with {code} after its last surface")
            break
        if not left:
            # The app's own windows closing, or wx's: not something to run again.
            problems.append(f"the probe crashed ({why}) after its last surface, closing")
            break
        culprit = left[0]
        if crashes:
            earlier = ", ".join(crashes)
            problems.append(f"{culprit}: the probe crashed ({why}) again, after running "
                            f"again once for a crash on {earlier}")
            crashes[culprit] = why
            break
        crashes[culprit] = why
        print(f"::warning::The probe crashed ({why}) on {culprit}. That's a crash, not an "
              f"unnamed control, so it starts again on {culprit} and the {len(left) - 1} "
              "surfaces after it. Only once: another crash fails (#579).", flush=True)
        remaining = left
    # A surface that couldn't be shown has no audit to check, so it fails here.
    problems += [f"{name}: {str(entry['error']).strip().splitlines()[-1]}"
                 for name, entry in finished.items() if entry.get("error")]
    (folder / f"manifest-{TAG}.json").write_text(
        json.dumps({"tag": TAG, "surfaces": finished, "crashes": crashes}, indent=1),
        encoding="utf-8")
    return code, problems


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    make = "--run" in args
    if make:
        args.remove("--run")
    if len(args) != 1:
        print(__doc__)
        return 2
    folder = Path(args[0])
    problems = []
    if make:
        _code, problems = run(folder, _surfaces())
    unnamed, missing, seen = check(folder)
    for line in unnamed + missing + problems:
        print(line)
    if not seen:
        print("The audit found no controls at all: is the accessibility bus running?")
        return 1
    print(f"{seen} controls Orca can reach; {len(unnamed)} without a name; "
          f"{len(missing)} surfaces not audited.")
    return 1 if unnamed or missing or problems else 0


if __name__ == "__main__":
    sys.exit(main())
