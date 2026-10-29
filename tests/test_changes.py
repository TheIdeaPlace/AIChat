"""What a turn changed in files (#18)."""
import pytest

from thechatplace.changes import by_file, edit_from_tool, file_text, summary_text
from thechatplace.transcript import TranscriptParser, parse_lines

from records import assistant_block, lines, text_block, tool_result, tool_use_block, user_text
from markers import windows_paths  # noqa: E402

PATCH = [{"oldStart": 10, "oldLines": 3, "newStart": 10, "newLines": 4,
          "lines": [" keep", "-old line", "+new line", "+another", " end"]}]


def test_edit_uses_the_patch_claude_code_recorded():
    edit = edit_from_tool("Edit", {"file_path": "C:\\r\\a.py"},
                          {"filePath": "C:\\r\\a.py", "structuredPatch": PATCH}, 1)
    assert (edit.added, edit.removed, edit.created) == (2, 1, False)
    text = file_text(by_file([edit])[0])
    assert text.split("\n") == [
        "C:\\r\\a.py: 2 lines added, 1 removed.", "", "Lines 10 to 13:",
        "Unchanged: keep", "Removed: old line", "Added: new line", "Added: another",
        "Unchanged: end"]


def test_without_a_patch_the_change_comes_from_the_input():
    edit = edit_from_tool("Edit", {"file_path": "a.py", "old_string": "x = 1\ny = 2",
                                   "new_string": "x = 1\ny = 3\nz = 4"}, None, 1)
    assert (edit.added, edit.removed) == (2, 1)
    multi = edit_from_tool("MultiEdit", {"file_path": "a.py", "edits": [
        {"old_string": "a", "new_string": "b"}, {"old_string": "c", "new_string": ""}]}, None, 1)
    assert (multi.added, multi.removed) == (1, 2)
    assert "A change:" in file_text(by_file([multi])[0])
    write = edit_from_tool("Write", {"file_path": "n.txt", "content": "one\ntwo\n"},
                           {"type": "create", "filePath": "n.txt", "content": "one\ntwo\n",
                            "structuredPatch": []}, 2)
    assert write.created and write.added == 2
    assert edit_from_tool("Bash", {"command": "ls"}, None, 1) is None


@windows_paths
def test_summary_groups_by_file_and_says_created():
    a1 = edit_from_tool("Edit", {}, {"filePath": "C:\\r\\a.py", "structuredPatch": PATCH}, 1)
    a2 = edit_from_tool("Edit", {}, {"filePath": "c:\\R\\A.py", "structuredPatch": PATCH}, 1)
    new = edit_from_tool("Write", {"file_path": "C:\\r\\new.md", "content": "x"},
                         {"type": "create"}, 1)
    gone = edit_from_tool("Edit", {"file_path": "b.py", "old_string": "x", "new_string": ""},
                          None, 1)
    assert summary_text([a1, a2, new, gone]) == (
        "Changed 3 files: a.py, 4 lines added, 2 removed; new.md, created, 1 line; "
        "b.py, 1 line removed.")
    assert summary_text([]) == ""
    many = [edit_from_tool("Write", {"file_path": f"f{i}.txt", "content": "x"}, None, 1)
            for i in range(7)]
    assert summary_text(many).endswith("; and 2 more.")


def test_transcript_keeps_successful_edits_by_turn():
    tr = parse_lines(lines(
        user_text("Fix it"),
        assistant_block(tool_use_block("Edit", {"file_path": "a.py", "old_string": "x",
                                                "new_string": "y"}, "t1"), "m1"),
        tool_result("t1", "The file a.py has been updated.",
                    toolUseResult={"filePath": "a.py", "structuredPatch": PATCH}),
        assistant_block(tool_use_block("Write", {"file_path": "b.py", "content": "z"}, "t2"),
                        "m2"),
        tool_result("t2", "File has not been read yet.", is_error=True),
        assistant_block(text_block("Done."), "m3"),
        user_text("Now the docs"),
        assistant_block(tool_use_block("Write", {"file_path": "README.md", "content": "Hi\n"},
                                       "t3"), "m4"),
        tool_result("t3", "File created.", toolUseResult={"type": "create",
                                                          "filePath": "README.md",
                                                          "content": "Hi\n",
                                                          "structuredPatch": []}),
    ))
    assert [(e.path, e.turn) for e in tr.edits] == [("a.py", 1), ("README.md", 2)]
    assert tr.turns == 2
    assert [e.path for e in tr.latest_turn_edits()] == ["README.md"]


def test_notes_deletions_and_writes_over_a_file():
    patch = [{"oldStart": 5, "oldLines": 2, "newStart": 5, "newLines": 0,
              "lines": ["-gone", "-also gone", "\\ No newline at end of file"]}]
    edit = edit_from_tool("Edit", {}, {"filePath": "a.py", "structuredPatch": patch}, 1)
    assert file_text(by_file([edit])[0]).split("\n")[2:] == [
        "At line 5:", "Removed: gone", "Removed: also gone", "Note: No newline at end of file"]
    update = edit_from_tool("Write", {"file_path": "b.txt", "content": "one\nTWO\n"},
                            {"type": "update", "filePath": "b.txt", "content": "one\nTWO\n",
                             "originalFile": "one\ntwo\n", "structuredPatch": []}, 1)
    assert (update.created, update.added, update.removed) == (False, 1, 1)


def test_a_result_read_later_than_its_call_still_counts():
    parser = TranscriptParser()
    parser.feed(lines(user_text("Go"), assistant_block(tool_use_block(
        "Edit", {"file_path": "a.py", "old_string": "x", "new_string": "y"}, "t1"), "m1")))
    assert parser.transcript.edits == []
    parser.feed(lines(tool_result("t1", "Updated.", toolUseResult={
        "filePath": "a.py", "structuredPatch": PATCH})))
    assert [e.path for e in parser.transcript.edits] == ["a.py"]


def test_a_git_style_diff_is_read_file_by_file():
    """Copilot's ``detailedContent`` (#352): one section per file, absolute
    paths after a/ and b/, /dev/null for a file created or deleted."""
    from thechatplace.changes import parse_git_diff
    text = "\n".join([
        "diff --git a/C:/work/notes.txt b/C:/work/notes.txt",
        "index 0000000..0000000 100644",
        "--- a/C:/work/notes.txt",
        "+++ b/C:/work/notes.txt",
        "@@ -1,4 +1,5 @@",
        " alpha",
        "-beta",
        "+BETA",
        "+--- not a header",
        "",
        "diff --git a/C:/work/extra.txt b/C:/work/extra.txt",
        "--- a/dev/null",
        "+++ b/C:/work/extra.txt",
        "@@ -1,0 +1,2 @@",
        "+hello",
        "diff --git a/C:/work/spare.txt b/dev/null",
        "--- a/C:/work/spare.txt",
        "+++ b/dev/null",
        "@@ -1,3 +0,0 @@",
        "-one",
    ])
    files = parse_git_diff(text)
    assert [path for path, _ in files] == ["C:/work/notes.txt", "C:/work/extra.txt",
                                          "C:/work/spare.txt"]
    notes = files[0][1]
    assert (notes[0].old_start, notes[0].new_start) == (1, 1)
    assert notes[0].lines == [" alpha", "-beta", "+BETA", "+--- not a header"]
    assert files[1][1][0].lines == ["+hello"] and files[2][1][0].lines == ["-one"]
    assert parse_git_diff("") == [] and parse_git_diff("not a diff\n+x") == []


def test_a_deleted_file_says_so():
    from thechatplace.changes import FileEdit, Hunk
    removed = FileEdit("C:/work/spare.txt", "apply_patch", 1, [Hunk(1, 0, ["-one", "-two"])],
                       deleted=True)
    assert by_file([removed])[0].counts() == "deleted"
    # Where its lines were, rather than "A change:" (#406).
    assert file_text(by_file([removed])[0]).split("\n")[2:] == [
        "Lines 1 to 2:", "Removed: one", "Removed: two"]
    created = FileEdit("C:/work/spare.txt", "apply_patch", 1, [Hunk(0, 1, ["+one"])], created=True)
    assert by_file([created, removed])[0].counts() == "created, then deleted"
    assert summary_text([created, removed]) == "Changed 1 file: spare.txt, created, then deleted."


def test_file_text_marks_lines_in_the_style_chosen():
    """#174: the default is unchanged; the styles come from app-kit's difftext."""
    edit = edit_from_tool("Edit", {}, {"filePath": "a.py", "structuredPatch": PATCH}, 1)
    changes = by_file([edit])[0]
    assert file_text(changes, "words") == file_text(changes)
    assert file_text(changes, "symbols").split("\n")[2:] == [
        "Lines 10 to 13:", "= keep", "- old line", "+ new line", "+ another", "= end"]
    assert file_text(changes, "changed").split("\n")[3:6] == [
        "= keep", "!- old line", "!+ new line"]
    assert file_text(changes, "no such style") == file_text(changes)


def test_a_removed_line_starting_with_dashes_is_still_removed():
    """Edit's fallback diff once dropped any line starting "---" or "+++"
    as a file header, so removing a "-- comment" line counted nothing."""
    edit = edit_from_tool("Edit", {"file_path": "q.sql", "old_string": "-- old note\nSELECT 1",
                                   "new_string": "++ new\nSELECT 1"}, None, 1)
    assert (edit.added, edit.removed) == (1, 1)
    assert "Removed: -- old note" in file_text(by_file([edit])[0])


def test_pathless_unified_diff_keeps_hunks_line_numbers_and_newline_notes():
    from thechatplace.changes import parse_unified_diff
    hunks = parse_unified_diff("@@ -10,2 +10,3 @@\r\n keep\r\n-old\r\n+new\r\n+more\r\n"
                               "@@ -20 +21 @@\n---content\n+++content\n\\ No newline at end of file\n")
    assert [(h.old_start, h.new_start, h.added, h.removed) for h in hunks] == [(10, 10, 2, 1), (20, 21, 1, 1)]
    assert hunks[1].lines == ["---content", "+++content", "\\ No newline at end of file"]
    assert parse_unified_diff("--- a/file\n+++ b/file\nnot a hunk\n+not a change") == []


def test_renamed_file_can_have_added_or_removed_lines():
    from thechatplace.changes import FileEdit, Hunk
    edits = [FileEdit("new.py", "Codex", 1, [Hunk(1, 1, ["-gone"])], moved_from="old.py")]
    assert by_file(edits)[0].counts() == "renamed, 1 line removed"
    edits[0].hunks[0].lines.append("+new")
    assert by_file(edits)[0].counts() == "renamed, 1 line added, 1 removed"


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("terminated", [False, True])
def test_unified_diff_keeps_edits_after_a_context_blank_with_its_space_stripped(newline, terminated):
    from thechatplace.changes import FileEdit, parse_unified_diff
    diff = newline.join(["@@ -1,3 +1,3 @@", " a", "", "-b", "+c"])
    hunks = parse_unified_diff(diff + (newline if terminated else ""))
    assert len(hunks) == 1
    assert (hunks[0].added, hunks[0].removed) == (1, 1)
    assert hunks[0].lines == [" a", " ", "-b", "+c"]
    detail = file_text(by_file([FileEdit("a.txt", "Codex", 1, hunks)])[0])
    assert "Lines 1 to 3:" in detail
    assert "Unchanged: blank line\nRemoved: b\nAdded: c" in detail


@pytest.mark.parametrize("start, removed, heading", [
    (1, ["-one", "-two", "\\ No newline at end of file"], "Lines 1 to 2:"),
    (7, ["-one"], "At line 7:"),
    (0, ["-one"], "A change:"),
])
def test_deleted_file_uses_its_recorded_old_range(start, removed, heading):
    from thechatplace.changes import FileEdit, Hunk
    change = by_file([FileEdit("gone.txt", "Codex", 1, [Hunk(start, 0, removed)], deleted=True)])[0]
    assert file_text(change).split("\n")[2] == heading


def test_delete_then_recreate_keeps_both_changes_under_the_same_path():
    from thechatplace.changes import FileEdit, Hunk
    edits = [FileEdit("a.txt", "Codex", 1, [Hunk(1, 0, ["-old"])], deleted=True),
             FileEdit("a.txt", "Codex", 2, [Hunk(0, 1, ["+new", "+more"])], created=True)]
    changes = by_file(edits)
    assert len(changes) == 1
    assert changes[0].counts() == "2 lines added, 1 removed"
    detail = file_text(changes[0])
    assert detail.index("Removed: old") < detail.index("Added: new")


def test_edits_before_a_rename_stay_under_the_old_path():
    from thechatplace.changes import FileEdit, Hunk
    changes = by_file([FileEdit("old.py", "Codex", 1, [Hunk(1, 1, ["-old", "+new"])]),
                       FileEdit("new.py", "Codex", 2, moved_from="old.py")])
    assert [change.path for change in changes] == ["old.py", "new.py"]
    assert [change.counts() for change in changes] == ["1 line added, 1 removed", "renamed"]
    assert "Removed: old\nAdded: new" in file_text(changes[0])
    assert "Renamed from: old.py" in file_text(changes[1])



@pytest.mark.parametrize("separator", ["\n", "\n\n", "\n\n\n"])
def test_unified_diff_ignores_blank_separators_after_complete_hunks(separator):
    from thechatplace.changes import FileEdit, parse_unified_diff
    diff = "@@ -1 +1 @@\n-a\n+b" + separator + "@@ -9 +9 @@\n-c\n+d" + separator
    hunks = parse_unified_diff(diff)
    assert [h.lines for h in hunks] == [["-a", "+b"], ["-c", "+d"]]
    detail = file_text(by_file([FileEdit("a.txt", "Codex", 1, hunks)])[0])
    assert "At line 1:" in detail and "At line 9:" in detail
    assert "Unchanged: blank line" not in detail
