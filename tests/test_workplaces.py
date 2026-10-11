"""Where a new session works (#154): recent folders, GitHub repositories,
branches and worktrees. Git runs for real in temporary folders; gh is
tests/fake_gh.py."""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from thechatplace import platform_paths, workplaces
from thechatplace.sessions import DESKTOP, OWN, SessionInfo

HERE = Path(__file__).resolve().parent
GIT = shutil.which("git")
needs_git = pytest.mark.skipif(not GIT, reason="git isn't installed")


def info(cwd, when, cowork=False, source=DESKTOP):
    return SessionInfo(source=source, key=f"k{when}{cwd}", title="t", cwd=cwd,
                       cli_session_id="c", last_activity_ms=when, cowork=cowork)


# -- recent folders -----------------------------------------------------------------------

def test_recent_folders_newest_first_each_once(tmp_path):
    a, b, c = (tmp_path / n for n in "abc")
    for folder in (a, b, c):
        folder.mkdir()
    sessions = [info(str(a), 10), info(str(b), 30), info(str(a), 50, source=OWN),
                info(str(c), 20), info(str(b) + os.sep, 5)]
    assert workplaces.recent_folders(sessions) == [str(a), str(b), str(c)]


def test_recent_folders_leave_out_cowork_missing_and_empty(tmp_path):
    here = tmp_path / "here"
    here.mkdir()
    sessions = [info(str(tmp_path / "gone"), 90), info(str(tmp_path / "outputs"), 80, cowork=True),
                info("", 70), info(str(here), 10)]
    (tmp_path / "outputs").mkdir()
    assert workplaces.recent_folders(sessions) == [str(here)]


def test_a_worktree_counts_as_its_repository(tmp_path):
    repo = tmp_path / "Repo"
    worktree = repo / ".claude" / "worktrees" / "fix-it"
    worktree.mkdir(parents=True)
    sessions = [info(str(worktree), 20), info(str(repo), 10)]
    assert workplaces.recent_folders(sessions) == [str(repo)]
    assert workplaces.repo_of_worktree("C:/G/Repo/.claude/worktrees/x/") == "C:/G/Repo"
    # A folder inside one too, as the session list reads it.
    assert workplaces.repo_of_worktree("C:/G/Repo/.claude/worktrees/x/src") == "C:/G/Repo"
    assert workplaces.repo_of_worktree("C:/G/.claude") == "C:/G/.claude"


def test_recent_folders_stop_at_the_limit(tmp_path):
    sessions = [info(str(tmp_path / str(n)), n) for n in range(30)]
    found = workplaces.recent_folders(sessions, limit=3, exists=lambda f: True)
    assert found == [str(tmp_path / n) for n in ("29", "28", "27")]


def test_recent_folders_limit_of_zero_offers_none(tmp_path):
    sessions = [info(str(tmp_path / "a"), 1)]
    assert workplaces.recent_folders(sessions, limit=0, exists=lambda f: True) == []


# -- taking folders off the recent list (#215) --------------------------------------------

def test_a_removed_folder_stays_off_until_a_session_uses_it_again(tmp_path):
    removed = workplaces.RemovedFolders(tmp_path / "recent_folders.json")
    a, b = str(tmp_path / "A"), str(tmp_path / "B")
    yes = lambda f: True  # noqa: E731
    sessions = [info(a, 100), info(b, 50)]
    removed.remove(a.lower() if os.name == "nt" else a, now_ms=200)
    assert workplaces.recent_folders(sessions, exists=yes, removed=removed) == [b]
    # Saved: a new store reads it back.
    again = workplaces.RemovedFolders(removed.path)
    assert workplaces.recent_folders(sessions, exists=yes, removed=again) == [b]
    # Worked in again after it was taken off: back on the list, at the top.
    sessions.append(info(a, 300))
    assert workplaces.recent_folders(sessions, exists=yes, removed=again) == [a, b]


def test_a_removed_worktree_repository_stays_off(tmp_path):
    repo = tmp_path / "Repo"
    worktree = str(repo / ".claude" / "worktrees" / "x")
    removed = workplaces.RemovedFolders(tmp_path / "r.json")
    removed.remove(str(repo), now_ms=500)
    assert workplaces.recent_folders([info(worktree, 400)], exists=lambda f: True,
                                     removed=removed) == []


def test_clearing_takes_every_folder_off_and_replaces_single_removals(tmp_path):
    removed = workplaces.RemovedFolders(tmp_path / "r.json")
    removed.remove(str(tmp_path / "old"), now_ms=10)
    removed.clear(now_ms=1000)
    sessions = [info(str(tmp_path / "a"), 999), info(str(tmp_path / "b"), 1000),
                info(str(tmp_path / "c"), 1001)]
    assert workplaces.recent_folders(sessions, exists=lambda f: True, removed=removed) == \
        [str(tmp_path / "c")]
    raw = json.loads(removed.path.read_text(encoding="utf-8"))
    assert raw == {"version": 1, "cleared_ms": 1000, "removed": {}}


@pytest.mark.parametrize("text", ["not json", "[]", '{"removed": []}',
                                  '{"removed": {}, "cleared_ms": "x"}',
                                  '{"removed": {}, "cleared_ms": true}'])
def test_an_unreadable_removed_folders_file_is_reported_and_never_saved_over(tmp_path, text):
    path = tmp_path / "recent_folders.json"
    path.write_text(text, encoding="utf-8")
    removed = workplaces.RemovedFolders(path)
    assert removed.load_error and str(path) in removed.load_error
    assert not removed.hides("C:/x", 0)
    with pytest.raises(OSError):
        removed.remove("C:/x")
    with pytest.raises(OSError):
        removed.clear()
    assert path.read_text(encoding="utf-8") == text


def test_bad_entries_in_the_removed_folders_file_are_skipped(tmp_path):
    path = tmp_path / "recent_folders.json"
    path.write_text(json.dumps({"version": 1, "cleared_ms": 0, "removed": {
        workplaces.folder_key("C:/good"): 50, "bad": "x", "worse": True, "": 5}}),
        encoding="utf-8")
    removed = workplaces.RemovedFolders(path)
    assert not removed.load_error
    assert removed.hides("C:/good", 50) and not removed.hides("C:/good", 51)
    assert not removed.hides("bad", 0) and not removed.hides("worse", 0)


def test_a_failed_save_changes_nothing(tmp_path, monkeypatch):
    removed = workplaces.RemovedFolders(tmp_path / "r.json")

    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(workplaces.os, "replace", fail)
    with pytest.raises(OSError):
        removed.remove("C:/x", now_ms=5)
    assert not removed.hides("C:/x", 1)
    assert not list(tmp_path.glob("*.tmp"))


# -- repository names ---------------------------------------------------------------------

@pytest.mark.parametrize("typed, expected", [
    ("kellylford/AIChat", "kellylford/AIChat"),
    ("  kellylford/AIChat  ", "kellylford/AIChat"),
    ("https://github.com/kellylford/AIChat", "kellylford/AIChat"),
    ("https://github.com/kellylford/AIChat.git", "kellylford/AIChat"),
    ("https://github.com/kellylford/AIChat/", "kellylford/AIChat"),
    ("git@github.com:kellylford/my.repo.git", "kellylford/my.repo"),
    ("kellylford", None),
    ("-x/y", None),
    ("a/-y", None),
    ("../etc", None),
    ("a/..", None),
    ("a/b/c", None),
    ("a/b c", None),
    ("a/b;rm", None),
    ("https://example.com/a/b", None),
    ("", None),
])
def test_parse_repo(typed, expected):
    assert workplaces.parse_repo(typed) == expected


def test_clone_target_is_under_the_root(tmp_path):
    assert workplaces.clone_target(tmp_path, "someone/Thing") == tmp_path / "Thing"


def test_repo_rows():
    repo = workplaces.GitHubRepo("me/Tool", "Does   a\nthing", True)
    assert repo.row() == "me/Tool, private: Does a thing"
    assert workplaces.GitHubRepo("me/Bare").row() == "me/Bare"
    assert len(workplaces.GitHubRepo("me/L", "x" * 400).row()) < 200


# -- gh -----------------------------------------------------------------------------------

@pytest.fixture
def fake_gh(monkeypatch):
    monkeypatch.setattr(workplaces, "_gh", lambda gh: [sys.executable, str(HERE / "fake_gh.py")])
    return "gh"


def test_list_github_repos(fake_gh, monkeypatch):
    monkeypatch.setenv("FAKE_GH_REPOS", json.dumps([
        {"nameWithOwner": "me/One", "description": "First", "isPrivate": False},
        {"nameWithOwner": "me/Two", "description": None, "isPrivate": True},
        {"nameWithOwner": "bad name/x"}, "junk"]))
    repos = workplaces.list_github_repos(fake_gh)
    assert [(r.name, r.description, r.private) for r in repos] == [
        ("me/One", "First", False), ("me/Two", "", True)]


def test_list_github_repos_says_why_it_failed(fake_gh, monkeypatch):
    monkeypatch.setenv("FAKE_GH_FAIL", "To get started with GitHub CLI, please run: gh auth login")
    with pytest.raises(workplaces.ToolError, match="gh auth login"):
        workplaces.list_github_repos(fake_gh)


def _clone(gh, repo, target):
    result = {}
    done = threading.Event()

    def finished(folder, error):
        result.update(folder=folder, error=error)
        done.set()
    clone = workplaces.Clone(gh, repo, target, finished).start()
    return clone, result, done


@needs_git
def test_clone_and_recognise_the_clone(fake_gh, tmp_path):
    target = tmp_path / "Thing"
    _clone_obj, result, done = _clone(fake_gh, "me/Thing", target)
    assert done.wait(30)
    assert result == {"folder": target, "error": ""}
    assert workplaces.is_clone_of(GIT, target, "me/Thing")
    assert workplaces.is_clone_of(GIT, target, "ME/thing")  # GitHub ignores case
    assert not workplaces.is_clone_of(GIT, target, "other/Thing")
    assert not workplaces.is_clone_of(GIT, tmp_path, "me/Thing")


def test_a_clone_that_has_finished_kills_nothing(fake_gh, tmp_path, monkeypatch):
    """Once gh has exited its pid may be another program's, which taskkill
    (without a job) or killpg would end: nothing is killed after that."""
    kills = []
    monkeypatch.setattr(workplaces.platform_paths.ProcessTree, "kill",
                        lambda self: kills.append(self))
    monkeypatch.setenv("FAKE_GH_MODE", "fail")
    clone, _result, done = _clone(fake_gh, "me/Nope", tmp_path / "Nope")
    assert done.wait(30)
    clone.cancel()  # too late: it has finished
    assert kills == []


def test_a_failed_clone_says_why_and_leaves_nothing(fake_gh, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_GH_MODE", "fail")
    target = tmp_path / "Nope"
    _c, result, done = _clone(fake_gh, "me/Nope", target)
    assert done.wait(30)
    assert result["folder"] is None and "Could not resolve" in result["error"]
    assert not target.exists()


def test_never_clones_into_an_existing_folder(fake_gh, tmp_path):
    target = tmp_path / "Mine"
    target.mkdir()
    (target / "keep.txt").write_text("mine")
    _c, result, done = _clone(fake_gh, "me/Mine", target)
    assert done.wait(30)
    assert result["folder"] is None and "already exists" in result["error"]
    assert (target / "keep.txt").read_text() == "mine"


# -- branches and worktrees ---------------------------------------------------------------

def git(*args, cwd):
    subprocess.run([GIT, *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    if not GIT:
        pytest.skip("git isn't installed")
    folder = tmp_path / "Repo"
    folder.mkdir()
    git("init", "-q", "-b", "main", cwd=folder)
    (folder / "sub").mkdir()
    (folder / "sub" / "file.txt").write_text("x")
    git("add", ".", cwd=folder)
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q",
        "-m", "first", cwd=folder)
    git("branch", "feature/old", cwd=folder)
    return folder


def test_repo_info(repo, tmp_path):
    found = workplaces.repo_info(GIT, str(repo / "sub"))
    assert Path(found.top) == repo and Path(found.main) == repo
    assert found.rel == "sub"
    assert found.branch == "main"
    # main is checked out (here), so it can't have a worktree too.
    assert found.branches == ["feature/old"] and found.busy == ["main"]
    assert workplaces.repo_info(GIT, str(repo)).rel == ""
    plain = tmp_path / "plain"
    plain.mkdir()
    assert workplaces.repo_info(GIT, str(plain)) is None
    assert workplaces.repo_info(GIT, str(tmp_path / "missing")) is None
    assert workplaces.repo_info(GIT, "") is None


@needs_git
@pytest.mark.parametrize("name", ["", "  ", "-f", "@{-1}", "a b", "a..b", "a~1", "x.lock",
                                  "@", "a@{b", "HEAD", "refs/heads/x"])
def test_bad_branch_names_are_refused(name):
    with pytest.raises(workplaces.ToolError):
        workplaces.check_branch_name(GIT, name)


@needs_git
def test_good_branch_names():
    assert workplaces.check_branch_name(GIT, " fix/thing-2 ") == "fix/thing-2"


def test_a_new_branch_gets_a_worktree(repo):
    info = workplaces.repo_info(GIT, str(repo))
    folder = workplaces.add_worktree(GIT, info, "fix/new-thing")
    assert folder == repo / ".claude" / "worktrees" / "fix-new-thing"
    assert (folder / ".git").is_file()
    assert workplaces.repo_info(GIT, str(folder)).branch == "fix/new-thing"
    # The repository itself is still on its own branch, and its status
    # doesn't show the worktree.
    assert workplaces.repo_info(GIT, str(repo)).branch == "main"
    status = subprocess.run([GIT, "status", "--porcelain"], cwd=repo, capture_output=True,
                            text=True).stdout
    assert ".claude" not in status
    exclude = (repo / ".git" / "info" / "exclude").read_text()
    assert exclude.count("/.claude/worktrees/") == 1
    # Already ignored: not added again.
    workplaces.add_worktree(GIT, workplaces.repo_info(GIT, str(repo)), "another")
    assert (repo / ".git" / "info" / "exclude").read_text().count("/.claude/worktrees/") == 1


def test_an_existing_branch_gets_a_worktree(repo):
    folder = workplaces.add_worktree(GIT, workplaces.repo_info(GIT, str(repo)), "feature/old")
    assert workplaces.repo_info(GIT, str(folder)).branch == "feature/old"
    # Now it's checked out, so it's no longer offered, and asking says why.
    info = workplaces.repo_info(GIT, str(repo))
    assert "feature/old" not in info.branches
    with pytest.raises(workplaces.ToolError, match="already checked out"):
        workplaces.add_worktree(GIT, info, "feature/old")
    (repo / ".claude" / "worktrees" / "again").mkdir()
    assert workplaces.worktree_folder(str(repo), "again").name == "again-2"


def test_a_subfolder_works_in_the_same_subfolder_of_the_worktree(repo):
    info = workplaces.repo_info(GIT, str(repo / "sub"))
    folder = workplaces.add_worktree(GIT, info, "in-sub")
    assert folder == repo / ".claude" / "worktrees" / "in-sub" / "sub"
    assert (folder / "file.txt").is_file()


def test_a_worktree_from_a_worktree_goes_beside_it(repo):
    first = workplaces.add_worktree(GIT, workplaces.repo_info(GIT, str(repo)), "first")
    info = workplaces.repo_info(GIT, str(first))
    assert Path(info.main) == repo and Path(info.top) == first
    second = workplaces.add_worktree(GIT, info, "second")
    assert second == repo / ".claude" / "worktrees" / "second"
    # Made from what the chosen worktree had checked out.
    assert workplaces.repo_info(GIT, str(second)).branch == "second"


def test_a_branch_only_a_remote_has_is_tracked(repo, tmp_path):
    clone = tmp_path / "Clone"
    subprocess.run([GIT, "clone", "-q", str(repo), str(clone)], check=True, capture_output=True)
    info = workplaces.repo_info(GIT, str(clone))
    assert "origin/feature/old" in info.remotes and "feature/old" not in info.branches
    folder = workplaces.add_worktree(GIT, info, "feature/old")
    upstream = subprocess.run([GIT, "rev-parse", "--abbrev-ref", "@{upstream}"], cwd=folder,
                              capture_output=True, text=True).stdout.strip()
    assert upstream == "origin/feature/old"


def test_remove_tree_removes_read_only_files(tmp_path):
    folder = tmp_path / "x" / ".git" / "objects" / "pack"
    folder.mkdir(parents=True)
    pack = folder / "pack-1.pack"
    pack.write_text("p")
    os.chmod(pack, 0o444)
    assert workplaces.remove_tree(tmp_path / "x")
    assert not (tmp_path / "x").exists()
    assert workplaces.remove_tree(tmp_path / "never-there")


@needs_git
def test_cancel_ends_the_whole_clone_and_removes_only_its_own_folder(fake_gh, tmp_path,
                                                                     monkeypatch):
    monkeypatch.setenv("FAKE_GH_MODE", "hang")
    target = tmp_path / "Slow"
    clone, result, done = _clone(fake_gh, "me/Slow", target)
    pack = clone.staging / ".git" / "objects" / "pack" / "pack-1.pack"
    for _ in range(300):
        if pack.exists():
            break
        threading.Event().wait(0.05)
    assert pack.exists() and not target.exists()  # cloning beside, not into, the target
    threading.Event().wait(0.5)  # the child holding the pipes has started
    clone.cancel()
    # fake gh's child sleeps for a minute holding gh's output pipes; only
    # killing the whole tree lets the clone end now.
    assert done.wait(15)
    assert result == {"folder": None, "error": ""}
    clone.wait(10)
    assert not clone.staging.exists() and not target.exists()


@needs_git
def test_a_target_that_appears_during_the_clone_is_left_alone(fake_gh, tmp_path, monkeypatch):
    target = tmp_path / "Race"
    real_popen = subprocess.Popen

    def popen_then_someone_makes_the_folder(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        target.mkdir()
        (target / "theirs.txt").write_text("theirs")
        return process
    monkeypatch.setattr(workplaces.subprocess, "Popen", popen_then_someone_makes_the_folder)
    clone, result, done = _clone(fake_gh, "me/Race", target)
    assert done.wait(30)
    assert result["folder"] is None and "left as it is" in result["error"]
    assert (target / "theirs.txt").read_text() == "theirs"
    assert not clone.staging.exists()


@pytest.mark.skipif(sys.platform == "darwin", reason="a Mac also looks in Homebrew's folder")
def test_find_tool_never_returns_a_script():
    assert platform_paths.find_tool("git", which=lambda n: r"C:\x\git.cmd") is None
    assert platform_paths.find_tool("git", which=lambda n: None) is None
    assert platform_paths.find_tool("gh", which=lambda n: r"C:\x\gh.exe") == r"C:\x\gh.exe"


@needs_git
def test_a_folder_inside_a_clone_is_not_the_clone(fake_gh, tmp_path):
    target = tmp_path / "Thing"
    _c, result, done = _clone(fake_gh, "me/Thing", target)
    assert done.wait(30)
    (target / "Inner").mkdir()
    assert not workplaces.is_clone_of(GIT, target / "Inner", "me/Thing")


def test_a_remote_branch_by_its_full_name_tracks_it(repo, tmp_path):
    clone = tmp_path / "Clone2"
    subprocess.run([GIT, "clone", "-q", str(repo), str(clone)], check=True, capture_output=True)
    info = workplaces.repo_info(GIT, str(clone))
    folder = workplaces.add_worktree(GIT, info, "origin/feature/old")
    assert workplaces.repo_info(GIT, str(folder)).branch == "feature/old"
    refs = subprocess.run([GIT, "for-each-ref", "--format=%(refname)", "refs/heads/"],
                          cwd=clone, capture_output=True, text=True).stdout.split()
    assert "refs/heads/origin/feature/old" not in refs


@needs_git
def test_a_clone_that_cant_be_renamed_is_kept(fake_gh, tmp_path, monkeypatch):
    monkeypatch.setattr(workplaces.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(Path, "rename", lambda self, target: (_ for _ in ()).throw(
        PermissionError("in use")))
    clone, result, done = _clone(fake_gh, "me/Held", tmp_path / "Held")
    assert done.wait(30)
    assert result["folder"] is None and str(clone.staging) in result["error"]
    assert (clone.staging / ".git").is_dir()  # the finished clone isn't thrown away


def test_old_temporary_clones_are_swept(tmp_path):
    old, new = tmp_path / ".thechatplace-clone-old", tmp_path / ".thechatplace-clone-new"
    for folder in (old, new):
        folder.mkdir()
    long_ago = time.time() - workplaces._STALE_SECONDS - 60
    os.utime(old, (long_ago, long_ago))
    workplaces._sweep_old_clones(tmp_path)
    assert not old.exists() and new.exists()  # a clone in progress is left alone


def test_tool_folders_go_first_only_when_missing(monkeypatch):
    monkeypatch.setattr(workplaces, "_TOOL_FOLDERS", [os.path.join("X", "git")])
    path = os.pathsep.join(["A", "B"])
    assert workplaces._tool_path(path) == os.pathsep.join([os.path.join("X", "git"), "A", "B"])
    already = os.pathsep.join(["A", os.path.join("X", "git")])
    assert workplaces._tool_path(already) == already
