Follow [CLAUDE.md](CLAUDE.md) for this repository's architecture, conventions, and validation.

## Keep the checkout clean

- Put scratch files (patches, zips, source copies, PR drafts) outside the repository folder,
  for example in your temp folder, never in the repo root.
- Make worktrees next to the repository (`../AIChat-<topic>`), not inside it.
- After your PR merges, remove its worktree and delete its local branch (`git branch -D`;
  squash merges hide it from `git branch --merged`).
