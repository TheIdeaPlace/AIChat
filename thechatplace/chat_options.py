"""A Claude chat's options beyond model, effort and mode (#468): the agent it
runs as, standing instructions, and an output style.

Each is given to every turn, as the model is, because every turn is a new
``claude -p`` process. Checked with Claude Code 2.1.296 and real Haiku turns:

* ``--agent <name>`` with ``-p`` and ``--resume`` runs the chat as that agent
  (a project agent whose prompt said to begin every reply with ARRR did, on
  the first turn and on resumed ones).
* ``--append-system-prompt=<text>`` adds the instructions. The ``=`` form
  keeps text that starts with ``-`` (or holds ``--model opus``) from reading
  as a flag; newlines and quotes come through as typed.
* ``--settings {"outputStyle": "<name>"}`` chooses an output style for the
  process: system/init reported it, and a style saying "write in capitals"
  did.

All three become part of the system prompt, which Claude Code records on a
conversation's first request and sends as recorded on every later one
(``--system-prompt-snapshot``, on by default) until the chat is compacted.
So a change made after the first turn was ignored: instructions changed from
French to German still answered in French. With ``--system-prompt-snapshot
off`` the change applied at once. A chat whose options change after it has
started therefore passes that from then on (``OwnSession.prompt_fresh``).

The agents and styles offered are the ones Claude Code lists for the folder
in its answer to ``initialize`` (``agents``: ``[{name, description, model}]``;
``available_output_styles`` and ``output_style``), which The Chat Place
already asks for, at no cost, to list slash commands (#23).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

#: The longest instructions kept: well inside Windows' 32,767-character
#: command line, with the rest of a turn's flags.
MAX_INSTRUCTIONS = 8000

# An agent's name as Claude Code lists them ("code-reviewer", a plugin's
# "plugin:agent"): never readable as a flag, one word.
_SAFE_AGENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}")
# An output style's name ("Explanatory", "My style", "plugin:Style"): one
# line, printable. It goes inside JSON, so it can't read as a flag either.
_SAFE_STYLE = re.compile(r"[^\x00-\x1f\x7f]{1,100}")

AGENT_NONE_LABEL = "None (Claude Code's default)"
STYLE_DEFAULT_LABEL = "Your setting"


def is_safe_agent(name: str) -> bool:
    """Whether ``name`` may be passed as ``--agent`` ("" means none)."""
    return name == "" or (isinstance(name, str) and bool(_SAFE_AGENT.fullmatch(name)))


def is_safe_style(name: str) -> bool:
    """Whether ``name`` may be given as the output style ("" means none)."""
    return name == "" or (isinstance(name, str) and bool(_SAFE_STYLE.fullmatch(name))
                          and name == name.strip())


def clean_instructions(text: str) -> str:
    """Instructions as kept: line endings made \\n, blank edges trimmed, no
    NUL (which can't go on a command line), at most MAX_INSTRUCTIONS."""
    if not isinstance(text, str):
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\0", "")
    return text.strip()[:MAX_INSTRUCTIONS]


def is_safe_instructions(text: str) -> bool:
    return isinstance(text, str) and clean_instructions(text) == text


@dataclass(frozen=True)
class ChatOptions:
    """What a Claude chat passes every turn besides its model, effort and
    mode. Empty values pass nothing, leaving Claude Code's own settings."""

    agent: str = ""
    instructions: str = ""
    output_style: str = ""
    #: The options changed after the chat's first turn: the recorded system
    #: prompt would hide the change, so each turn renders it afresh.
    fresh: bool = False

    def __bool__(self) -> bool:
        return bool(self.agent or self.instructions or self.output_style)

    def flags(self) -> List[str]:
        """The command-line flags for these options. Raises ValueError for a
        value that couldn't have come from the dialog."""
        flags: List[str] = []
        if self.agent:
            if not is_safe_agent(self.agent):
                raise ValueError(f"Not a valid agent name: {self.agent!r}")
            flags += ["--agent", self.agent]
        if self.instructions:
            if not is_safe_instructions(self.instructions):
                raise ValueError("Those instructions can't be passed to Claude Code.")
            flags.append("--append-system-prompt=" + self.instructions)
        if self.output_style:
            if not is_safe_style(self.output_style):
                raise ValueError(f"Not a valid output style: {self.output_style!r}")
            flags += ["--settings", json.dumps({"outputStyle": self.output_style},
                                               ensure_ascii=False)]
        if self.fresh:
            flags += ["--system-prompt-snapshot", "off"]
        return flags

    def summary(self) -> str:
        """In words, for Properties and what's said: "agent code-reviewer,
        instructions, output style Concise", or "none"."""
        parts = []
        if self.agent:
            parts.append(f"agent {self.agent}")
        if self.instructions:
            parts.append("instructions")
        if self.output_style:
            parts.append(f"output style {self.output_style}")
        return ", ".join(parts) or "none"

    def heading_part(self) -> str:
        """What the chat's heading adds: only the agent, and only a short
        name, so the heading still fits a narrow window ("as code-reviewer")."""
        if self.agent and len(self.agent) <= 30:
            return f"as {self.agent}"
        return ""


@dataclass(frozen=True)
class Agent:
    name: str
    description: str = ""
    model: str = ""

    def label(self, limit: int = 90) -> str:
        """For the Agent choice: its name and the start of its description."""
        text = self.name
        description = " ".join(self.description.split())
        if description:
            text += ": " + description
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


@dataclass(frozen=True)
class ChatChoices:
    """What Claude Code offers in a folder: its agents and output styles,
    and the style your settings choose."""

    agents: Tuple[Agent, ...] = ()
    styles: Tuple[str, ...] = ()
    current_style: str = ""

    def agent_names(self) -> List[str]:
        return [agent.name for agent in self.agents]


def choices_from_init(body) -> Optional[ChatChoices]:
    """The agents and styles in an ``initialize`` answer, or None if it has
    neither list (an older Claude Code). Anything unsafe to pass back is left
    out."""
    if not isinstance(body, dict):
        return None
    raw_agents, raw_styles = body.get("agents"), body.get("available_output_styles")
    if not isinstance(raw_agents, list) and not isinstance(raw_styles, list):
        return None
    agents, seen = [], set()
    for item in raw_agents if isinstance(raw_agents, list) else ():
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name or not is_safe_agent(name) or name in seen:
            continue
        seen.add(name)
        description = item.get("description")
        model = item.get("model")
        agents.append(Agent(name, description if isinstance(description, str) else "",
                            model if isinstance(model, str) else ""))
    styles = []
    for name in raw_styles if isinstance(raw_styles, list) else ():
        if isinstance(name, str) and name and is_safe_style(name) and name not in styles:
            styles.append(name)
    current = body.get("output_style")
    return ChatChoices(tuple(agents), tuple(styles),
                       current if isinstance(current, str) and is_safe_style(current) else "")


def style_label(name: str) -> str:
    """An output style as the choice lists it: "default" is Claude Code's
    own style, named as it names it."""
    return "Default" if name == "default" else name


@dataclass
class OptionsChange:
    """What Chat Options changed, for saying so."""

    before: ChatOptions
    after: ChatOptions
    added_dirs: List[str] = field(default_factory=list)
    removed_dirs: List[str] = field(default_factory=list)

    @property
    def options_changed(self) -> bool:
        return (self.before.agent, self.before.instructions, self.before.output_style) != (
            self.after.agent, self.after.instructions, self.after.output_style)

    def said(self, title: str) -> str:
        parts = []
        if self.before.agent != self.after.agent:
            parts.append(f"agent {self.after.agent}" if self.after.agent else "no agent")
        if self.before.instructions != self.after.instructions:
            parts.append("new instructions" if self.after.instructions else "no instructions")
        if self.before.output_style != self.after.output_style:
            parts.append(f"output style {self.after.output_style}" if self.after.output_style
                         else "your output style setting")
        if self.added_dirs:
            parts.append(f"{len(self.added_dirs)} more folder"
                         + ("s" if len(self.added_dirs) != 1 else ""))
        if self.removed_dirs:
            parts.append(f"{len(self.removed_dirs)} folder"
                         + ("s" if len(self.removed_dirs) != 1 else "") + " removed")
        if not parts:
            return "Chat options unchanged."
        return f"{title}: " + ", ".join(parts) + ", from its next message."
