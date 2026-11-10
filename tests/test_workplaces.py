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
    with pytest.raises(workplaces.ToolError, match="in use in another worktree"):
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


# -- where a new worktree's branch starts (#561) --------------------------------------------

def _commit(folder, name):
    (folder / f"{name}.txt").write_text(name)
    git("add", ".", cwd=folder)
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", name,
        cwd=folder)


def _head(folder):
    return subprocess.run([GIT, "rev-parse", "HEAD"], cwd=folder, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def cloned(repo, tmp_path):
    """A clone of ``repo``, on a feature branch with a commit of its own,
    while the remote's main has moved on since it was cloned."""
    clone = tmp_path / "Clone"
    subprocess.run([GIT, "clone", "-q", str(repo), str(clone)], check=True, capture_output=True)
    git("checkout", "-q", "-b", "work", cwd=clone)
    _commit(clone, "on-feature")
    _commit(repo, "newer-on-main")
    return clone


@needs_git
def test_a_new_branch_starts_from_the_remotes_main_fetched_first(cloned, repo):
    info = workplaces.repo_info(GIT, str(cloned))
    made = workplaces.create_worktree(GIT, info, "fix/fresh")
    assert (made.how, made.base, made.note) == ("new", "origin/main", "")
    assert _head(made.folder) == _head(repo)  # the newer main, not the clone's old copy
    assert not (made.folder / "on-feature.txt").exists()
    assert made.said() == "Made worktree fix-fresh on new branch fix/fresh, from origin/main."
    # It doesn't track main, so pushing it can never go to main.
    upstream = subprocess.run([GIT, "rev-parse", "--abbrev-ref", "@{upstream}"],
                              cwd=made.folder, capture_output=True, text=True)
    assert upstream.returncode != 0
    assert workplaces.branch_start(GIT, workplaces.repo_info(GIT, str(made.folder))) \
        == "origin/main"


@needs_git
def test_a_new_branch_can_start_from_the_current_branch(cloned):
    info = workplaces.repo_info(GIT, str(cloned))
    made = workplaces.create_worktree(GIT, info, "fix/here", workplaces.START_HERE)
    assert (made.how, made.base) == ("new", "work")
    assert (made.folder / "on-feature.txt").exists()
    assert made.said().endswith("on new branch fix/here, from work.")
    # Its reflog names where it started, so Properties can say.
    assert workplaces.branch_start(GIT, workplaces.repo_info(GIT, str(made.folder))) == "work"


@needs_git
def test_no_remote_starts_from_the_current_branch_and_says_so(repo):
    made = workplaces.create_worktree(GIT, workplaces.repo_info(GIT, str(repo)), "solo")
    assert made.base == "main" and "has no origin remote" in made.note
    assert made.note in made.said()


@needs_git
def test_a_failed_fetch_starts_from_the_current_branch_and_says_so(cloned, repo, tmp_path):
    git("remote", "set-url", "origin", str(tmp_path / "gone"), cwd=cloned)
    made = workplaces.create_worktree(GIT, workplaces.repo_info(GIT, str(cloned)), "offline")
    assert made.base == "work"
    assert "origin/main couldn't be fetched" in made.note
    assert (made.folder / "on-feature.txt").exists()


@needs_git
def test_existing_and_remote_branches_ignore_start_from(cloned):
    git("branch", "kept", cwd=cloned)
    info = workplaces.repo_info(GIT, str(cloned))
    assert not workplaces.is_new_branch(info, "kept")
    assert not workplaces.is_new_branch(info, "feature/old")      # only the remote has it
    assert not workplaces.is_new_branch(info, "origin/feature/old")
    assert not workplaces.is_new_branch(info, "")
    assert workplaces.is_new_branch(info, "brand-new")
    made = workplaces.create_worktree(GIT, info, "kept")
    assert made.how == "existing" and made.said().endswith("on existing branch kept.")
    made = workplaces.create_worktree(GIT, workplaces.repo_info(GIT, str(cloned)), "feature/old")
    assert made.how == "tracking" and made.base == "origin/feature/old"
    assert made.said().endswith("tracking origin/feature/old.")


@needs_git
@pytest.mark.parametrize("name", ["-x", "@{-1}", "refs/heads/x", "two words"])
def test_names_are_checked_before_any_fetch(cloned, monkeypatch, name):
    called = []
    monkeypatch.setattr(workplaces, "_fetched_default", lambda *a: called.append(a))
    with pytest.raises(workplaces.ToolError):
        workplaces.create_worktree(GIT, workplaces.repo_info(GIT, str(cloned)), name)
    assert not called


@needs_git
def test_the_fetch_updates_origin_main_even_when_the_clone_doesnt_fetch_it(cloned, repo):
    # A clone that fetches only some branches: a plain "fetch origin main"
    # would leave origin/main old while saying it started from it.
    git("config", "remote.origin.fetch", "+refs/heads/feature/old:refs/remotes/origin/feature/old",
        cwd=cloned)
    made = workplaces.create_worktree(GIT, workplaces.repo_info(GIT, str(cloned)), "fix/narrow")
    assert made.base == "origin/main" and _head(made.folder) == _head(repo)


@needs_git
def test_a_commit_claude_code_started_from_is_named(cloned):
    # Claude Code's worktrees record "Created from <commit>^{commit}".
    main_commit = subprocess.run([GIT, "rev-parse", "origin/main"], cwd=cloned,
                                 capture_output=True, text=True).stdout.strip()
    git("branch", "worktree-x", f"{main_commit}^{{commit}}", cwd=cloned)
    git("checkout", "-q", "worktree-x", cwd=cloned)
    info = workplaces.repo_info(GIT, str(cloned))
    assert workplaces.branch_start(GIT, info) == "origin/main"
    facts = dict(workplaces.branch_facts(GIT, str(cloned)))
    assert facts["Branch"] == "worktree-x" and facts["Branch started from"] == "origin/main"


def test_branch_facts_outside_a_repository_is_nothing(tmp_path):
    if GIT:
        assert workplaces.branch_facts(GIT, str(tmp_path)) == []


# -- branches checked out somewhere (#587) -----------------------------------------------------

def test_busy_branches_say_where_they_are(repo):
    elsewhere = workplaces.add_worktree(GIT, workplaces.repo_info(GIT, str(repo)), "feature/old")
    elsewhere = elsewhere.parent if elsewhere.name == "sub" else elsewhere
    git("branch", "spare", cwd=repo)
    info = workplaces.repo_info(GIT, str(repo / "sub"))
    assert sorted(info.busy) == ["feature/old", "main"]
    assert os.path.samefile(info.busy_at["main"], repo)
    assert os.path.samefile(info.busy_at["feature/old"], elsewhere)
    assert workplaces.in_use_reason(info, "main") == "checked out in this folder"
    assert workplaces.in_use_reason(info, "feature/old") == "in use in another worktree"
    assert workplaces.in_use_reason(info, "spare") == ""
    # The usable ones first, then the busy ones with the reason, never a path.
    choices = workplaces.branch_choices(info)
    assert choices[0] == "spare"
    assert set(choices[1:]) == {"main (checked out in this folder)",
                                "feature/old (in use in another worktree)"}
    assert not any(str(repo) in choice for choice in choices)
    assert workplaces.branch_choices(None) == []


def test_a_choice_names_its_branch():
    assert workplaces.branch_from_choice("main (in use in another worktree)") == "main"
    assert workplaces.branch_from_choice(" work (checked out in this folder) ") == "work"
    assert workplaces.branch_from_choice(" fix/new ") == "fix/new"
    assert workplaces.branch_from_choice("") == ""
    # Remote-only items (#622) name the local branch that will track them.
    assert workplaces.branch_from_choice("feature/x (on origin)") == "feature/x"
    assert workplaces.branch_from_choice(" fix (on upstream) ") == "fix"
    assert workplaces.branch_from_choice("feature/x (on two words)") == "feature/x (on two words)"


# -- branches only a remote has (#622) ------------------------------------------------------

def test_branches_only_a_remote_has_are_listed_after_the_local_ones():
    info = _described(branch="work", branches=["older", "feature/x"], busy=["work", "main"],
                      remotes=["origin/main", "origin/feature/x", "origin/topic",
                               "origin/a/b", "upstream/topic", "upstream/extra", "origin/HEAD"],
                      busy_at={})
    assert workplaces.remote_only(info) == [("topic", "origin"), ("a/b", "origin"),
                                            ("extra", "upstream")]
    assert workplaces.branch_choices(info) == [
        "older", "feature/x",
        "topic (on origin)", "a/b (on origin)", "extra (on upstream)",
        "work (checked out in this folder)", "main (in use in another worktree)"]
    # Each names a branch that's tracked, never new.
    for item in ("topic (on origin)", "a/b (on origin)", "extra (on upstream)"):
        assert not workplaces.is_new_branch(info, item)
        assert workplaces.in_use_reason(info, workplaces.branch_from_choice(item)) == ""


@needs_git
def test_a_remote_only_item_makes_a_tracking_worktree(cloned):
    info = workplaces.repo_info(GIT, str(cloned))
    # main has a local branch, so only feature/old is the remote's alone.
    assert workplaces.branch_choices(info) == [
        "main", "feature/old (on origin)", "work (checked out in this folder)"]
    made = workplaces.create_worktree(GIT, info, "feature/old (on origin)")
    assert (made.how, made.branch, made.base) == ("tracking", "feature/old", "origin/feature/old")
    upstream = subprocess.run([GIT, "rev-parse", "--abbrev-ref", "@{upstream}"],
                              cwd=made.worktree, capture_output=True, text=True).stdout.strip()
    assert upstream == "origin/feature/old"
    # Now it's local and checked out: listed as in use, not as on origin.
    after = workplaces.branch_choices(workplaces.repo_info(GIT, str(cloned)))
    assert "feature/old (in use in another worktree)" in after
    assert "feature/old (on origin)" not in after


@needs_git
def test_a_branch_only_another_remote_has_tracks_that_remote(cloned, tmp_path):
    other = tmp_path / "Other.git"
    subprocess.run([GIT, "clone", "-q", "--bare", str(cloned), str(other)], check=True,
                   capture_output=True)
    git("branch", "up-only", "work", cwd=other)
    git("remote", "add", "upstream", str(other), cwd=cloned)
    git("fetch", "-q", "upstream", cwd=cloned)
    info = workplaces.repo_info(GIT, str(cloned))
    choices = workplaces.branch_choices(info)
    assert "up-only (on upstream)" in choices
    assert "feature/old (on origin)" in choices and "feature/old (on upstream)" not in choices
    made = workplaces.create_worktree(GIT, info, "up-only (on upstream)")
    assert (made.how, made.base) == ("tracking", "upstream/up-only")


@needs_git
def test_fetching_lists_branches_pushed_since_and_drops_deleted_ones(cloned, repo):
    git("branch", "pushed-since", cwd=repo)
    git("branch", "-D", "feature/old", cwd=repo)
    info = workplaces.repo_info(GIT, str(cloned))
    assert "pushed-since (on origin)" not in workplaces.branch_choices(info)
    assert workplaces.fetch_remotes(GIT, info) == ""
    choices = workplaces.branch_choices(workplaces.repo_info(GIT, str(cloned)))
    assert "pushed-since (on origin)" in choices
    assert "feature/old (on origin)" not in choices      # pruned


@needs_git
def test_fetching_with_no_remote_is_nothing_and_a_failed_fetch_says_so(repo, cloned, tmp_path):
    assert workplaces.fetch_remotes(GIT, workplaces.repo_info(GIT, str(repo))) == ""
    git("remote", "set-url", "origin", str(tmp_path / "gone"), cwd=cloned)
    said = workplaces.fetch_remotes(GIT, workplaces.repo_info(GIT, str(cloned)))
    assert said == ("Couldn't fetch from origin, so Branch lists its branches as they were at "
                    "the last fetch.")
    # What was fetched before is still listed.
    assert "feature/old (on origin)" in workplaces.branch_choices(
        workplaces.repo_info(GIT, str(cloned)))


@needs_git
def test_one_remote_that_fails_doesnt_stop_the_others(cloned, repo, tmp_path):
    # Each remote is fetched on its own (#622 review): a gone one is named,
    # and the rest are still brought up to date.
    for name in ("old-fork", "older-fork"):
        git("remote", "add", name, str(tmp_path / name), cwd=cloned)
    git("branch", "pushed-since", cwd=repo)
    said = workplaces.fetch_remotes(GIT, workplaces.repo_info(GIT, str(cloned)))
    assert said == ("Couldn't fetch from old-fork and older-fork, so Branch lists their "
                    "branches as they were at the last fetch.")
    assert "pushed-since (on origin)" in workplaces.branch_choices(
        workplaces.repo_info(GIT, str(cloned)))


def _described(**changes):
    values = dict(top=r"C:\R", main=r"C:\R", common=r"C:\R\.git", rel="", branch="work",
                  branches=["older"], busy=["work", "main"], remotes=["origin/main"],
                  busy_at={"work": r"C:\R", "main": r"C:\R\.claude\worktrees\main"})
    values.update(changes)
    return workplaces.RepoInfo(**values)


def test_the_in_use_message_says_where_and_what_to_do():
    info = _described()
    assert workplaces.in_use_message(info, "main", "origin/main") == (
        r"main is in use in another worktree, at C:\R\.claude\worktrees\main, so a new "
        "worktree can't use it. Type a new branch name, and Start from origin/main, to work "
        "from main.")
    # The folder's own branch names the folder.
    assert workplaces.in_use_message(info, "work", "origin/main") == (
        r"work is checked out in this folder, C:\R, so a new worktree can't use it. To work "
        "on work here, choose The folder as it is. Or type a new branch name, and Start "
        "from work, to work from it.")
    # A branch that's neither the remote's main nor here: that worktree's folder.
    info = _described(busy=["work", "topic"], busy_at={"topic": r"C:\T"})
    said = workplaces.in_use_message(info, "topic", "origin/main")
    assert said.startswith(r"topic is in use in another worktree, at C:\T, so a new worktree")
    assert "choose that worktree's folder" in said
    # No path known (a described repository): still a sentence.
    info = _described(busy_at={})
    assert workplaces.in_use_message(info, "main").startswith(
        "main is in use in another worktree, so a new worktree can't use it.")


def test_a_busy_branch_is_never_new_and_never_made(repo):
    info = workplaces.repo_info(GIT, str(repo))
    for text in ("main", "main (checked out in this folder)"):
        assert not workplaces.is_new_branch(info, text)
        with pytest.raises(workplaces.ToolError, match="checked out in this folder"):
            workplaces.create_worktree(GIT, info, text, workplaces.START_HERE)
    assert not (repo / ".claude").exists()


def test_a_branch_in_two_worktrees_is_listed_once(repo, tmp_path):
    # git worktree add -f can check one branch out twice.
    git("worktree", "add", "-q", str(tmp_path / "One"), "feature/old", cwd=repo)
    git("worktree", "add", "-q", "-f", str(tmp_path / "Two"), "feature/old", cwd=repo)
    info = workplaces.repo_info(GIT, str(repo))
    assert info.busy.count("feature/old") == 1
    assert workplaces.branch_choices(info).count("feature/old (in use in another worktree)") == 1
    assert os.path.samefile(info.busy_at["feature/old"], tmp_path / "One")


def test_a_worktree_whose_folder_is_gone_still_holds_its_branch(repo, tmp_path):
    gone = tmp_path / "Gone"
    git("worktree", "add", "-q", str(gone), "feature/old", cwd=repo)
    shutil.rmtree(gone)
    info = workplaces.repo_info(GIT, str(repo))
    assert info.busy_gone == ["feature/old"]
    assert workplaces.in_use_reason(info, "feature/old") == "held by a removed worktree"
    assert "feature/old (held by a removed worktree)" in workplaces.branch_choices(info)
    assert workplaces.branch_from_choice("feature/old (held by a removed worktree)") \
        == "feature/old"
    said = workplaces.in_use_message(info, "feature/old", "origin/main")
    assert said.startswith("feature/old was checked out in a worktree whose folder, ")
    assert said.endswith("is gone, so git still holds it. Run git worktree prune in the "
                         "repository to free it, then choose feature/old again.")
    assert "choose that worktree's folder" not in said
    with pytest.raises(workplaces.BranchInUse, match="git worktree prune"):
        workplaces.create_worktree(GIT, info, "feature/old")
    # Once pruned, it's free again.
    git("worktree", "prune", cwd=repo)
    info = workplaces.repo_info(GIT, str(repo))
    assert "feature/old" in info.branches and not info.busy_gone


def test_a_remote_name_means_its_local_branch(cloned):
    # Typing origin/main while main is in a worktree of its own (#587).
    git("worktree", "add", "-q", str(cloned.parent / "MainTree"), "main", cwd=cloned)
    info = workplaces.repo_info(GIT, str(cloned))
    assert workplaces.local_branch(info, "origin/main") == "main"
    assert workplaces.local_branch(info, "brand-new") == "brand-new"
    assert workplaces.in_use_reason(info, "origin/main") == "in use in another worktree"
    assert not workplaces.is_new_branch(info, "origin/main")
    with pytest.raises(workplaces.BranchInUse) as refused:
        workplaces.create_worktree(GIT, info, "origin/main", start_remote="origin/main")
    assert str(refused.value).startswith("main is in use in another worktree, at ")
    assert str(refused.value).endswith("Type a new branch name, and Start from origin/main, "
                                       "to work from main.")
