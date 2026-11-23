"""Console output pasted into a message goes in as a code block (#701).
Synthetic console text only, shaped like what a Windows console copies."""
import pytest

from thechatplace.codeblocks import find_code_blocks
from thechatplace.console_paste import as_code_block, is_console, line_count

PAD = " " * 60

POWERSHELL_ERROR = "\n".join([
    r"PS C:\work\demo> .\tools\build-demo.ps1" + PAD,
    r".\tools\build-demo.ps1 : The term '.\tools\build-demo.ps1' is not recognized." + PAD,
    "At line:1 char:1" + PAD,
    r"+ .\tools\build-demo.ps1" + PAD,
    "+ ~~~~~~~~~~~~~~~~~~~~~~" + PAD,
    "    + CategoryInfo          : ObjectNotFound: (.\\tools\\build-demo.ps1:String) []" + PAD,
    "",
    r"PS C:\work\demo>" + PAD,
])


@pytest.mark.parametrize("text", [
    POWERSHELL_ERROR,
    "C:\\work>dir /b\nnotes.txt\nreport.md",
    "me@box:~/work$ ls\nnotes.txt\nreport.md",
    "me@mac work % ls\nnotes.txt\nreport.md",
    'Traceback (most recent call last):\n  File "demo.py", line 1, in <module>\nNameError: x',
    "\n".join(f"setting_{n} = on" + PAD for n in range(4)),  # padded, with no prompt
])
def test_console_output_is_recognized(text):
    assert is_console(text)


@pytest.mark.parametrize("text", [
    "",
    "PS C:\\work> one line only",
    "Please look at this.\nIt has two lines of ordinary prose.",
    "A list:\n- one\n- two\n- three",
    "```\nPS C:\\work> already fenced\n```",
    "~~~\nC:\\work>dir\n~~~",
    "Two padded lines" + PAD + "\nare not enough" + PAD + "\nfor a console",
    "> quoted\n> text",
])
def test_ordinary_text_is_left_alone(text):
    assert not is_console(text)


def test_the_block_is_trimmed_fenced_and_found_as_code():
    block = as_code_block("\n\n" + POWERSHELL_ERROR + "\n\n")
    lines = block.split("\n")
    assert lines[0] == "```console" and lines[-1] == "```"
    assert all(line == line.rstrip() for line in lines)  # the padding's gone
    assert lines[1].startswith("PS C:\\work\\demo>")
    assert line_count(block) == 8
    found = find_code_blocks("Here's the error:\n" + block + "\nWhat went wrong?")
    assert len(found) == 1 and found[0].language == "console" and found[0].lines == 8
    assert found[0].describe() == "Code block, Console, 8 lines"


def test_backticks_inside_never_close_the_block_early():
    block = as_code_block("PS C:\\work> echo ````\n````\nPS C:\\work>")
    assert block.startswith("`````console\n") and block.endswith("\n`````")
    assert len(find_code_blocks(block)) == 1
