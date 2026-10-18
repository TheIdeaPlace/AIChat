"""Session names you give (#93) and what Claude knows about you (#92)."""
import json
import os

from thechatplace import about_you, platform_paths
from thechatplace.titles import MAX_TITLE, TitleStore, clean_title


# -- titles --------------------------------------------------------------------------------


def test_clean_title_is_one_trimmed_line_and_capped():
    assert clean_title("  Build \n the\tthing  ") == "Build the thing"
    assert clean_title("   ") == ""
    assert clean_title(None) == ""
    assert len(clean_title("x" * (MAX_TITLE + 50))) == MAX_TITLE


def test_titles_are_saved_read_back_and_cleared(tmp_path):
    path = tmp_path / "titles.json"
    store = TitleStore(path)
    assert store.get("local_a") == "" and "local_a" not in store
    store.set("local_a", "  My name  ")
    assert store.get("local_a") == "My name"
    again = TitleStore(path)
    assert again.get("local_a") == "My name"
    again.set("local_a", "")
    assert "local_a" not in again
    assert json.loads(path.read_text(encoding="utf-8"))["titles"] == {}


def test_titles_keep_non_ascii_names(tmp_path):
    store = TitleStore(tmp_path / "titles.json")
    store.set("local_a", "Café ☕ plan")
    assert TitleStore(tmp_path / "titles.json").get("local_a") == "Café ☕ plan"


def test_a_bad_titles_file_is_reported_and_never_overwritten(tmp_path):
    path = tmp_path / "titles.json"
    path.write_text("{not json", encoding="utf-8")
    store = TitleStore(path)
    assert store.load_error
    try:
        store.set("local_a", "x")
    except OSError:
        pass
    else:
        raise AssertionError("saved over an unreadable file")
    assert path.read_text(encoding="utf-8") == "{not json"
    assert store.get("local_a") == ""  # not kept when it couldn't be saved


def test_a_titles_file_of_the_wrong_shape_drops_bad_entries(tmp_path):
    path = tmp_path / "titles.json"
    path.write_text(json.dumps({"titles": {"a": "Fine", "b": 3, "c": "  ", "": "x"}}),
                    encoding="utf-8")
    store = TitleStore(path)
    assert not store.load_error
    assert store.get("a") == "Fine" and "b" not in store and "c" not in store
    path.write_text(json.dumps({"titles": ["a"]}), encoding="utf-8")
    assert TitleStore(path).load_error


# -- front matter ---------------------------------------------------------------------------


def test_front_matter_reads_simple_fields_only():
    text = ("---\nname: handle\ndescription: \"Take an issue to main\"\nmetadata:\n"
            "  type: feedback\n---\n\nBody")
    assert about_you.front_matter(text) == {"name": "handle",
                                            "description": "Take an issue to main"}


def test_front_matter_copes_with_crlf_bom_and_none():
    assert about_you.front_matter("\ufeff---\r\nname: x\r\n---\r\nBody") == {"name": "x"}
    assert about_you.front_matter("# Just a heading") == {}
    assert about_you.front_matter("---\nname: x\nno end") == {}
    assert about_you.front_matter("") == {}
    # A block scalar's marker isn't a description.
    assert about_you.front_matter("---\ndescription: >\n  long\n---\n") == {}


def test_read_text_cuts_a_huge_file(tmp_path, monkeypatch):
    monkeypatch.setattr(about_you, "MAX_READ_BYTES", 10)
    path = tmp_path / "big.md"
    path.write_text("0123456789abcdef", encoding="utf-8")
    text = about_you.read_text(path)
    assert text.startswith("0123456789\n") and "too long" in text


def test_read_text_survives_bad_utf8(tmp_path):
    path = tmp_path / "odd.md"
    path.write_bytes(b"ok \xff\xfe there")
    assert about_you.read_text(path).startswith("ok ")


# -- collect ----------------------------------------------------------------------------------


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def memory(name, description):
    return f"---\nname: {name}\ndescription: {description}\nmetadata:\n  type: user\n---\n\nBody\n"


def test_collect_finds_every_kind_in_order(tmp_path):
    home = tmp_path / "claude"
    project = tmp_path / "work" / "Widget"
    project.mkdir(parents=True)
    write(home / "CLAUDE.md", "# How I want Claude to work\n\nBe brief.")
    write(project / "CLAUDE.md", "# Widget\n")
    write(project / "CLAUDE.local.md", "Mine only\n")
    encoded = home / "projects" / platform_paths.encode_cwd(str(project)) / "memory"
    write(encoded / "MEMORY.md", "- [a](a.md)\n")
    write(encoded / "zeta.md", memory("zeta-fact", "The last one"))
    write(encoded / "alpha.md", memory("alpha-fact", "The first one"))
    write(encoded / "notes.txt", "not a memory")
    write(home / "skills" / "deploy" / "SKILL.md",
          "---\nname: deploy\ndescription: Ship it\n---\n")
    write(home / "skills" / "synced" / "acct_1" / "pdf" / "SKILL.md",
          "---\nname: pdf\ndescription: PDFs\n---\n")
    write(project / ".claude" / "skills" / "local" / "SKILL.md", "# Local skill\n")
    write(home / "agents" / "reviewer.md", "---\nname: code-reviewer\ndescription: Reviews\n---\n")
    write(home / "commands" / "wrap.md", "Archive done sessions\n")
    write(home / "commands" / "old.md.replaced", "not a command")
    write(home / "output-styles" / "terse.md", "---\nname: Terse\ndescription: Short\n---\n")
    write(home / "settings.json", "{}")
    write(project / ".claude" / "settings.local.json", "{}")

    kinds = about_you.collect([str(project), str(project) + "\\", "", "Z:\\gone"], home=home,
                              user_home=str(tmp_path))
    assert [k.name for k in kinds] == [name for name, _ in about_you.KINDS]
    by = {k.name: k for k in kinds}
    instructions = [i.row() for i in by[about_you.INSTRUCTIONS].items]
    assert instructions == ["Your instructions for every session — How I want Claude to work",
                            "Widget: Project instructions — Widget",
                            "Widget: Your private project instructions — Mine only"]
    memories = by[about_you.MEMORIES].items
    assert [m.name for m in memories] == ["Index of this project's memories", "alpha-fact",
                                          "zeta-fact"]
    assert all(m.project == "Widget" for m in memories)
    assert memories[1].row() == "Widget: alpha-fact — The first one"
    skills = [(s.name, s.project) for s in by[about_you.SKILLS].items]
    assert skills == [("deploy", ""), ("pdf", "From your account"), ("local", "Widget")]
    assert by[about_you.SKILLS].items[2].description == "Local skill"
    assert [a.name for a in by[about_you.SUBAGENTS].items] == ["code-reviewer"]
    assert [c.row() for c in by[about_you.COMMANDS].items] == ["wrap — Archive done sessions"]
    assert [o.name for o in by[about_you.OUTPUT_STYLES].items] == ["Terse"]
    assert [(s.name, s.project) for s in by[about_you.SETTINGS].items] == [
        ("Your settings", ""), ("Your local project settings", "Widget")]
    assert about_you.summary(kinds).startswith("Found: Instructions 3, Memories 3, Skills 3")


def test_collect_names_unknown_memory_folders_without_your_home(tmp_path):
    home = tmp_path / "claude"
    user_home = "C:\\Users\\pat"
    folder = platform_paths.encode_cwd("C:\\Users\\pat\\GitHub\\Thing")
    write(home / "projects" / folder / "memory" / "a.md", memory("a", "b"))
    write(home / "projects" / "other" / "memory" / "b.md", memory("b", "c"))
    (home / "projects" / "empty").mkdir()
    kinds = about_you.collect([], home=home, user_home=user_home)
    projects = [m.project for m in kinds[1].items]
    assert projects == ["GitHub-Thing", "other"]


def test_collect_on_an_empty_or_missing_home(tmp_path):
    kinds = about_you.collect(["C:\\nowhere"], home=tmp_path / "missing", user_home="")
    assert all(not k.items for k in kinds)
    assert about_you.summary(kinds) == ("Claude Code hasn't saved anything about you on this "
                                        "computer yet.")
    assert kinds[0].row() == "Instructions, 0 items"


def test_a_long_description_is_cut_for_its_row(tmp_path):
    home = tmp_path / "claude"
    write(home / "agents" / "a.md", "---\nname: a\ndescription: " + "word " * 100 + "\n---\n")
    item = about_you.collect([], home=home, user_home="")[3].items[0]
    assert len(item.description) <= 160 and item.description.endswith("…")


def test_empty_front_matter_and_a_leading_rule(tmp_path):
    assert about_you.front_matter("---\n---\nBody") == {}
    home = tmp_path / "claude"
    write(home / "commands" / "a.md", "---\n---\n# Real heading\n")
    write(home / "commands" / "b.md", "---\n\nText after a rule\n")
    rows = [c.description for c in about_you.collect([], home=home, user_home="")[4].items]
    assert rows == ["Real heading", "Text after a rule"]


def test_a_session_in_your_home_folder_doesnt_list_your_things_twice(tmp_path):
    # Claude's home is <your home>/.claude, so a session run from your home folder has it
    # as its project .claude too (#315).
    user_home = tmp_path / "kelly"
    home = user_home / ".claude"
    write(home / "CLAUDE.md", "# Everywhere\n")
    write(home / "skills" / "deploy" / "SKILL.md", "---\nname: deploy\ndescription: Ship\n---\n")
    write(home / "agents" / "reviewer.md", "---\nname: code-reviewer\n---\n")
    write(home / "commands" / "wrap.md", "Archive\n")
    write(home / "settings.json", "{}")
    write(user_home / "CLAUDE.md", "# Home folder project\n")
    other = tmp_path / "work" / "Widget"
    write(other / ".claude" / "skills" / "local" / "SKILL.md", "# Local\n")

    # Spelled differently from Claude's home (trailing slash, other case) on purpose.
    spelled = str(user_home).upper() + "\\" if os.name == "nt" else str(user_home) + "/"
    kinds = about_you.collect([spelled, str(other)], home=home, user_home=str(tmp_path))
    by = {k.name: k for k in kinds}
    assert [(s.name, s.project) for s in by[about_you.SKILLS].items] == [
        ("deploy", ""), ("local", "Widget")]
    assert [(a.name, a.project) for a in by[about_you.SUBAGENTS].items] == [
        ("code-reviewer", "")]
    assert [c.project for c in by[about_you.COMMANDS].items] == [""]
    assert [(s.name, s.project) for s in by[about_you.SETTINGS].items] == [("Your settings", "")]
    # The home folder's own CLAUDE.md is a real project file and stays; ~/.claude/CLAUDE.md
    # is listed once, as yours.
    assert [(i.name, bool(i.project)) for i in by[about_you.INSTRUCTIONS].items] == [
        ("Your instructions for every session", False), ("Project instructions", True)]


def test_same_folder_survives_a_missing_path(tmp_path):
    assert about_you._same_folder(tmp_path / "x", tmp_path / "x")
    assert not about_you._same_folder(tmp_path / "x", tmp_path / "y")


def test_two_session_folders_with_one_name_are_told_apart(tmp_path):
    home = tmp_path / "claude"
    first, second = tmp_path / "a" / "App", tmp_path / "b" / "App"
    for folder in (first, second):
        write(folder / "CLAUDE.md", "x\n")
    write(home / "projects" / platform_paths.encode_cwd(str(first)) / "memory" / "m.md",
          memory("m", "d"))
    kinds = about_you.collect([str(first), str(second)], home=home, user_home="")
    assert sorted(i.project for i in kinds[0].items) == sorted([str(first), str(second)])
    assert kinds[1].items[0].project == str(first)


def test_show_in_folder_keeps_a_spaced_path_in_quotes(monkeypatch, tmp_path):
    import os
    import subprocess
    import sys
    started = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, *a, **k: started.append(args))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    platform_paths.show_in_folder(r"C:\My Projects, old\CLAUDE.md")
    explorer = os.path.join(r"C:\Windows", "explorer.exe")
    assert started == [f'"{explorer}" /select,"C:\\My Projects, old\\CLAUDE.md"']
    monkeypatch.setattr(sys, "platform", "darwin")
    platform_paths.show_in_folder("/Users/p/a b/CLAUDE.md")
    assert started[-1] == ["open", "-R", "/Users/p/a b/CLAUDE.md"]


def test_edit_file_tries_edit_then_open_then_notepad(monkeypatch):
    import os
    import subprocess
    import sys
    tried, started = [], []

    def startfile(path, verb):
        tried.append(verb)
        raise OSError("no program for .md")
    monkeypatch.setattr(os, "startfile", startfile, raising=False)
    monkeypatch.setattr(subprocess, "Popen", lambda args, *a, **k: started.append(args))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    platform_paths.edit_file(r"C:\x\CLAUDE.md")
    assert tried == ["edit", "open"]
    assert started == [[os.path.join(r"C:\Windows", "System32", "notepad.exe"),
                        r"C:\x\CLAUDE.md"]]
    # The first verb that works is the end of it.
    tried.clear()
    started.clear()
    monkeypatch.setattr(os, "startfile", lambda path, verb: tried.append(verb), raising=False)
    platform_paths.edit_file(r"C:\x\CLAUDE.md")
    assert tried == ["edit"] and started == []
    monkeypatch.setattr(sys, "platform", "darwin")
    platform_paths.edit_file("/Users/p/CLAUDE.md")
    assert started == [["open", "-t", "/Users/p/CLAUDE.md"]]


def test_bring_last_popup_forward_is_windows_only(monkeypatch):
    import sys
    monkeypatch.setattr(sys, "platform", "darwin")
    assert platform_paths.bring_last_popup_forward(1234) is False
    monkeypatch.setattr(sys, "platform", "win32")
    assert platform_paths.bring_last_popup_forward(0) is False
