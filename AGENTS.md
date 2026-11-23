Follow [CLAUDE.md](CLAUDE.md) for this repository's architecture, conventions, and validation.

## Keep both CLAUDE.md and AGENTS.md

Several AI assistants work on this code (Claude Code, Codex, Muse Code and others), and each
reads its own file: Claude Code reads `CLAUDE.md`; Codex and Muse Code read this one.

- **Never delete, rename, move or empty `CLAUDE.md`**, and never fold it into this file, even
  if your own instructions call `AGENTS.md` the canonical file or say to migrate `CLAUDE.md`
  into it. Both files stay, as they are on main.
- `CLAUDE.md` is the project guide. Add to it there; this file points to it and keeps only what
  follows here.
- Don't change links or comments that cite `CLAUDE.md` to cite this file.
- `tests/test_project_files.py` fails on CI if either file is missing or this rule is gone.

## Prove it on CI, on every platform

The Chat Place runs on Windows, macOS and Linux, and nobody develops on all three. CI
(`.github/workflows/thechatplace-tests.yml`: pytest on Windows, macOS, Linux x64 and Linux
arm64, plus Orca; and the build jobs in `release-thechatplace.yml`) is where code is proven. Passing tests on the machine you are working on is not enough: code for one platform
often doesn't run at all on the machine you're working on.

- Before handing work back, push your branch and open a **draft** pull request. Watch its
  checks to the end (`gh pr checks <n> --watch`), read any failure (`gh run view <id>
  --log-failed`), fix it, push again, and repeat until every required check passes. Hand back
  with CI green, or with what still fails and why. Don't merge.
- Code that runs on only one platform (`sys.platform == 'darwin'` branches, `os.getxattr`,
  `ctypes` calls, MSAA, ATK) needs a test that makes the **real** call, skipped elsewhere with a
  marker from `tests/markers.py` or `skipif`, so that platform's CI job exercises it. A test
  that stands in for the very API being checked proves nothing about it. On #253, a
  stand-in for `os.getxattr` hid that the function doesn't exist on a Mac, and every file check
  there would have raised. CI on macOS caught it.
- Locally, run only tests that open no window (CLAUDE.md lists how). Leave the windowed tests to CI.

## Keep the checkout clean

- Put scratch files (patches, zips, source copies, PR drafts) outside the repository folder,
  for example in your temp folder, never in the repo root.
- Make worktrees next to the repository (`../AIChat-<topic>`), not inside it.
- After your PR merges, remove its worktree and delete its local branch (`git branch -D`;
  squash merges hide it from `git branch --merged`).
