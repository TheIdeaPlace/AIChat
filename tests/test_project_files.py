"""The project's guides for AI assistants: CLAUDE.md, which Claude Code reads,
and AGENTS.md, which Codex and Muse Code read. Muse Code's own instructions
call AGENTS.md canonical and fold CLAUDE.md into it, which once deleted
CLAUDE.md on a branch (#701). Both must stay."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_claude_md_is_the_project_guide():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.startswith("# CLAUDE.md")
    for section in ("## What this is", "## Commands", "## Releasing", "## Architecture",
                    "## Tests", "## Conventions"):
        assert section in text, f"CLAUDE.md lost its {section} section"
    assert "Keep both `CLAUDE.md` and `AGENTS.md`" in text


def test_agents_md_points_to_claude_md_and_says_to_keep_it():
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert text.startswith("Follow [CLAUDE.md](CLAUDE.md)")
    assert "Never delete, rename, move or empty `CLAUDE.md`" in text
