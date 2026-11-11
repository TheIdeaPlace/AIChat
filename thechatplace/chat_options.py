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

An agent's own model beats the chat's. Checked with Claude Code 2.1.316: an
agent with ``model: sonnet``, run with ``--model haiku --agent wide``, had
system/init report haiku while its replies came from sonnet. Its
``permissionMode: acceptEdits`` did not beat ``--permission-mode plan``. So the
Agent list says when an agent has its own model, and one that could bill usage
credits is refused (``agent_problem``).
"""
from __future__ import annotations

import json
import os
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


def _tidied(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\0", "").strip()


def clean_instructions(text: str) -> str:
    """Instructions as kept: line endings made \\n, blank edges trimmed, no
    NUL (which can't go on a command line), at most MAX_INSTRUCTIONS. Trimmed
    again after the cut, which can leave a space at the end: such text then
    failed ``is_safe_instructions``, so the chat's turns couldn't be built."""
    if not isinstance(text, str):
        return ""
    return _tidied(text)[:MAX_INSTRUCTIONS].rstrip()


def is_safe_instructions(text: str) -> bool:
    return isinstance(text, str) and clean_instructions(text) == text


def instructions_problem(text: str) -> str:
    """Why OK won't keep these instructions, or "": they're never cut unsaid."""
    length = len(_tidied(text))
    if length <= MAX_INSTRUCTIONS:
        return ""
    return (f"Those instructions are {length:,} characters, and the limit is "
            f"{MAX_INSTRUCTIONS:,}. Shorten them, then press OK again.")


def folder_problem(folder: str) -> str:
    """Why ``folder`` can't be one of More Folders, or ""."""
    from .claude_cli import is_safe_dir
    # A root is its own parent: C:\\, a share's root, or a Mac's or Linux's /.
    if folder and os.path.isdir(folder) and os.path.dirname(folder) == folder:
        return "A whole drive can't be added. Choose a folder inside it."
    if not folder or not is_safe_dir(folder) or not os.path.isdir(folder):
        return "That folder can't be added: choose an existing folder."
    return ""


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

    @property
    def own_model(self) -> str:
        """The model it always runs on, whatever the chat's is: "" for none,
        as "inherit" means."""
        model = " ".join(self.model.split())
        return "" if model.lower() == "inherit" else model

    def label(self, limit: int = 90) -> str:
        """For the Agent choice: its name, its own model if it has one, and the
        start of its description."""
        text = self.name
        if self.own_model:
            text += f" (uses its own model, {self.own_model})"
        description = " ".join(self.description.split())
        if description:
            text += ": " + description
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


#: In a model's name, what could bill usage credits (as ``claude_cli``'s
#: ``fable_problem`` and ``is_safe_model`` have it): some plans bill Fable, in
#: ``-p`` turns without asking, and every subscription plan bills 1M context.
_CREDIT_MODELS = ("fable", "[1m]")


def agent_problem(agent: Optional[Agent], chat_model: str = "") -> str:
    """Why a chat on ``chat_model`` can't run as ``agent``, or "". The chat
    having chosen that model itself makes it fine."""
    if agent is None:
        return ""
    model, chosen = agent.own_model.lower(), (chat_model or "").lower()
    if not any(mark in model and mark not in chosen for mark in _CREDIT_MODELS):
        return ""
    return (f"The agent {agent.name} uses its own model, {agent.own_model}, whatever model the "
            "chat is on. That model can bill usage credits, so The Chat Place won't run this "
            "chat as that agent. Choose another agent, or change the model in that agent's file.")


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
        # "Turn", as every other change of a chat's settings says (#596).
        return f"{title}: " + ", ".join(parts) + ", from its next turn."
