"""tools/ui_probe_requirements.ps1: how the visual probe keeps the VM's packages
in step with this checkout's requirements (#585). Run through PowerShell,
with made-up requirements files and no VM; skipped where there's no PowerShell."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
HELPER = TOOLS / "ui_probe_requirements.ps1"
# Windows PowerShell first: the probe runs in it, so the helper must work there.
SHELL = shutil.which("powershell") or shutil.which("pwsh")
pytestmark = pytest.mark.skipif(SHELL is None, reason="no PowerShell here")

APPKIT = ("https://github.com/TheIdeaPlace/app-kit/archive/"
          "{}.zip#subdirectory=python")
REQUIREMENTS = """wxPython>=4.2
# A commit of app-kit's main.
theideaplace-appkit @ {}
markdown==3.11.2
comtypes==1.4.17; sys_platform == "win32"
"""


def run(expression, *args):
    """The JSON of a PowerShell expression, with the helper loaded and the
    arguments as $a0, $a1..."""
    params = ", ".join(f"$a{i}" for i in range(len(args)))
    quoted = " ".join("'" + str(arg).replace("'", "''") + "'" for arg in args)
    command = (f"& {{ param({params}) . '{HELPER}'; "
               f"ConvertTo-Json -Compress @({expression}) }} {quoted}")
    done = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-Command", command],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def repo(path, requirements, dev="-r requirements.txt\npytest>=9.2.0\n", newline="\n"):
    path.mkdir(exist_ok=True)
    (path / "requirements.txt").write_bytes(requirements.replace("\n", newline).encode())
    (path / "requirements-dev.txt").write_bytes(dev.replace("\n", newline).encode())
    return path


def stamp(path):
    return run("Get-ProbeRequirementsStamp $a0", path)[0]


def test_the_stamp_changes_when_the_app_kit_pin_moves(tmp_path):
    # The same version number in both: only the archive it points at moved.
    before = stamp(repo(tmp_path / "a", REQUIREMENTS.format(APPKIT.format("fcbf6cd"))))
    after = stamp(repo(tmp_path / "b", REQUIREMENTS.format(APPKIT.format("0123abc"))))
    assert len(before) == 64 and before != after


def test_the_stamp_changes_with_the_dev_requirements_too(tmp_path):
    text = REQUIREMENTS.format(APPKIT.format("fcbf6cd"))
    assert stamp(repo(tmp_path / "a", text)) != stamp(
        repo(tmp_path / "b", text, dev="-r requirements.txt\npytest>=9.3.0\n"))


def test_the_stamp_is_the_same_whatever_the_line_endings(tmp_path):
    text = REQUIREMENTS.format(APPKIT.format("fcbf6cd"))
    assert stamp(repo(tmp_path / "a", text)) == stamp(repo(tmp_path / "b", text, newline="\r\n"))


def test_a_missing_dev_file_still_gives_a_stamp_that_changes_when_it_appears(tmp_path):
    folder = repo(tmp_path / "a", REQUIREMENTS.format(APPKIT.format("fcbf6cd")))
    before = stamp(folder)
    (folder / "requirements-dev.txt").unlink()
    assert stamp(folder) != before


def test_only_archive_pins_are_reinstalled_by_force(tmp_path):
    url = APPKIT.format("fcbf6cd")
    folder = repo(tmp_path / "a", REQUIREMENTS.format(url) + (
        "# other @ https://example.invalid/commented-out.zip\n"
        "extra[a,b] @ https://example.invalid/extra.zip ; python_version >= '3.10'\n"
        "-r requirements-build.txt\n"),
        dev=f"-r requirements.txt\ntheideaplace-appkit @ {url}\n")
    # The URL keeps its #subdirectory, the marker goes, and each comes once.
    assert run("Get-ProbeDirectUrlRequirements $a0", folder) == [
        url, "https://example.invalid/extra.zip"]


def test_no_archive_pins_means_nothing_to_reinstall(tmp_path):
    folder = repo(tmp_path / "a", "wxPython>=4.2\nmarkdown==3.11.2\n")
    assert run("Get-ProbeDirectUrlRequirements $a0", folder) == []


def test_the_real_requirements_reinstall_app_kit(tmp_path):
    urls = run("Get-ProbeDirectUrlRequirements $a0", TOOLS.parent)
    assert len(urls) == 1 and "TheIdeaPlace/app-kit/archive/" in urls[0]
    assert urls[0].endswith("#subdirectory=python")


@pytest.mark.parametrize("stored, current", [
    ("", False),                 # a venv from before #585 has no stamp
    ("abc\r\n", True),           # as Get-Content -Raw gives it back
    ("abd", False),
])
def test_a_venv_is_current_only_with_the_same_stamp(stored, current):
    assert run("Test-ProbeVenvCurrent $a0 'abc'", stored) == [current]


def test_files_taken_in_with_r_count_too(tmp_path):
    folder = repo(tmp_path / "a", "-r requirements-build.txt\nwxPython>=4.2\n")
    build = folder / "requirements-build.txt"
    build.write_text("pyinstaller==6.22.3\n", encoding="utf-8")
    before = stamp(folder)
    url = APPKIT.format("0123abc")
    build.write_text(f"--requirement=requirements.txt\nkit @ {url}\n", encoding="utf-8")
    # Changed, and its archive pin found, though the probe installs from
    # requirements-dev.txt; the loop back to requirements.txt is followed once.
    assert stamp(folder) != before
    assert run("Get-ProbeDirectUrlRequirements $a0", folder) == [url]
    names = [Path(p).name for p in run("Get-ProbeRequirementFiles $a0", folder)]
    assert names == ["requirements-dev.txt", "requirements.txt", "requirements-build.txt"]


@pytest.mark.parametrize("url", ["https://example.invalid/a.zip?x=1&y=2",
                                 "https://example.invalid/a%20b.zip"])
def test_a_url_cmd_would_mangle_is_refused(tmp_path, url):
    folder = repo(tmp_path / "a", f"kit @ {url}\n")
    command = (f"& {{ param($a0) . '{HELPER}'; "
               "try { $null = Get-ProbeDirectUrlRequirements $a0; 'none' } catch { 'refused' } } "
               f"'{folder}'")
    done = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-Command", command],
                          capture_output=True, text=True, timeout=60)
    assert done.stdout.strip() == "refused"


def test_the_probe_checks_the_stamp_and_keeps_it_after_installing():
    script = (TOOLS / "ui_probe_vm.ps1").read_text(encoding="utf-8")
    assert ". (Join-Path $PSScriptRoot 'ui_probe_requirements.ps1')" in script
    install = script.index("function Install-ProbePackages")
    force = script.index("--force-reinstall --no-deps")
    first = script.index("Install-ProbePackages", force)   # a new or half-made venv
    check = script.index("Test-ProbeVenvCurrent")
    again = script.index("Install-ProbePackages", check)   # requirements changed
    keep = script.index("requirements.stamp -Value")
    assert install < force < first < check < again < keep
