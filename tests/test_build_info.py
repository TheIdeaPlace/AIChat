"""Which build this is (thechatplace/build_info.py): the About box's line,
the version a test build is packed as, and the update order that lets an
installed test build take the real release (Kelly, 2026-11-26)."""
import subprocess
import sys
from pathlib import Path

import pytest

from thechatplace import __version__, build_info, updater
from thechatplace.build_info import BuildInfo, describe

from test_updater import FakeManager, Update

ROOT = Path(__file__).resolve().parent.parent


def test_the_about_line_for_a_test_build_a_release_and_a_source_run():
    commit = "487a6d67c0ffee00487a6d67c0ffee00487a6d67"
    test = BuildInfo(label="bld05", commit=commit, branch="build/bld05", built="2026-11-26")
    assert describe(test) == (f"{__version__}, build bld05 (commit 487a6d67 on build/bld05, "
                              "built 2026-11-26)")
    assert test.version == f"{__version__}-bld05"
    release = BuildInfo(commit=commit, branch="v0.6.4", built="2026-11-27")
    assert describe(release) == f"{__version__} (commit 487a6d67 on v0.6.4, built 2026-11-27)"
    assert release.version == __version__
    source = BuildInfo(commit=commit, branch="main", source=True)
    assert describe(source) == f"{__version__}, from source (commit 487a6d67 on main)"
    assert describe(BuildInfo()) == __version__   # nothing known: just the version


def _git_folder(tmp_path, head, refs=None, packed=""):
    git = tmp_path / "repo" / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text(head + "\n", encoding="utf-8")
    for name, sha in (refs or {}).items():
        path = git / "refs" / "heads" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sha + "\n", encoding="utf-8")
    if packed:
        (git / "packed-refs").write_text(packed, encoding="utf-8")
    return git.parent


def test_a_source_run_reads_its_commit_from_the_git_files(tmp_path):
    root = _git_folder(tmp_path, "ref: refs/heads/main", {"main": "a" * 40})
    info = build_info._from_git(root)
    assert (info.commit, info.branch, info.source) == ("a" * 40, "main", True)
    # A branch only in packed-refs, and a detached HEAD.
    root = _git_folder(tmp_path / "two", "ref: refs/heads/old",
                       packed="# pack-refs\n" + "b" * 40 + " refs/heads/old\n")
    assert build_info._from_git(root).commit == "b" * 40
    root = _git_folder(tmp_path / "three", "c" * 40)
    assert build_info._from_git(root).commit == "c" * 40
    # No git at all: still a source run, with nothing more said.
    assert build_info._from_git(tmp_path / "nowhere") == BuildInfo(source=True)


def test_a_worktree_finds_its_branch_in_the_main_git_folder(tmp_path):
    main = _git_folder(tmp_path, "ref: refs/heads/main", {"main": "a" * 40, "fix/x": "d" * 40})
    own = main / ".git" / "worktrees" / "x"
    own.mkdir(parents=True)
    (own / "HEAD").write_text("ref: refs/heads/fix/x\n", encoding="utf-8")
    (own / "commondir").write_text("../..\n", encoding="utf-8")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / ".git").write_text(f"gitdir: {own}\n", encoding="utf-8")
    info = build_info._from_git(tree)
    assert (info.commit, info.branch) == ("d" * 40, "fix/x")


def test_the_build_file_is_read_and_a_bad_label_is_ignored(monkeypatch):
    import types
    fake = types.ModuleType("thechatplace._build")
    fake.LABEL, fake.COMMIT, fake.BRANCH, fake.BUILT = "bld05", "e" * 40, "main", "2026-11-26"
    monkeypatch.setitem(sys.modules, "thechatplace._build", fake)
    import thechatplace
    monkeypatch.setattr(thechatplace, "_build", fake, raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert build_info.current() == BuildInfo("bld05", "e" * 40, "main", "2026-11-26")
    assert build_info.packed_version() == f"{__version__}-bld05"
    fake.LABEL = "BLD 05; rm"
    assert build_info.current().label == ""
    # A source run ignores a _build.py a local build left behind: it says
    # "from source", with what git says it's on now.
    monkeypatch.delattr(sys, "frozen")
    fake.LABEL = "bld05"
    now = build_info.current()
    assert now.source and now.label == "" and now.commit != "e" * 40
    assert build_info.packed_version() == __version__


def test_no_test_sees_a_build_label():
    """A labelled build runs the whole suite with TCP_BUILD_LABEL set;
    conftest clears it, or the release-tag checks would refuse it."""
    import os
    assert "TCP_BUILD_LABEL" not in os.environ


def test_a_test_build_sorts_before_its_release():
    key = updater._version_key
    assert key("0.6.4-bld05") < key("0.6.4") < key("0.6.5-bld01") < key("0.6.5")
    assert key("0.6.4-bld05") > key("0.6.3")
    assert key("0.6.4-bld04") < key("0.6.4-bld05")
    assert key("0.6.4") == key("0.6.4")


def test_an_installed_test_build_is_offered_the_real_release():
    manager = FakeManager(Update("0.6.4"))
    service = updater.UpdateService("0.6.4-bld05", manager_factory=lambda url: manager,
                                    latest=lambda: "0.6.4")
    result = service.check()
    assert result.status == updater.AVAILABLE and result.version == "0.6.4"
    # The release itself, running, is up to date.
    service = updater.UpdateService("0.6.4", manager_factory=lambda url: manager,
                                    latest=lambda: "0.6.4")
    assert service.check().status == updater.CURRENT


def _run(args, label=None):
    import os
    env = dict(os.environ)
    env.pop("TCP_BUILD_LABEL", None)
    if label is not None:
        env["TCP_BUILD_LABEL"] = label
    return subprocess.run([sys.executable, str(ROOT / "tools" / "check_version.py"), *args],
                          capture_output=True, text=True, env=env)


def test_check_version_packs_a_test_build_and_refuses_a_labelled_tag():
    assert _run(["--pack"]).stdout.strip() == __version__
    assert _run(["--pack"], "bld05").stdout.strip() == f"{__version__}-bld05"
    assert _run(["--pack"], "Bad Label").returncode == 1
    assert _run([f"v{__version__}"]).returncode == 0
    refused = _run([f"v{__version__}"], "bld05")
    assert refused.returncode == 1 and "label" in refused.stderr


def test_write_build_info_takes_ci_values_and_refuses_a_bad_label(monkeypatch):
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import write_build_info
    finally:
        sys.path.remove(str(ROOT / "tools"))
    values = write_build_info.gather({"TCP_BUILD_LABEL": "bld05", "TCP_BUILD_COMMIT": "f" * 40,
                                      "GITHUB_HEAD_REF": "build/bld05",
                                      "GITHUB_REF_NAME": "724/merge"})
    assert values["LABEL"] == "bld05" and values["COMMIT"] == "f" * 40
    assert values["BRANCH"] == "build/bld05"
    assert len(values["BUILT"]) == 10
    with pytest.raises(SystemExit):
        write_build_info.gather({"TCP_BUILD_LABEL": "bld 05"})
