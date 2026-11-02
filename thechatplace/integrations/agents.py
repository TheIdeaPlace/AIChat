"""The ACP agents the app knows. A new agent is a new row here.

Only agents actually run are listed. Being listed doesn't make an agent trusted:
``verified_versions`` holds the versions whose approvals, cancel, reload and
options have been exercised, and any other version's Allow carries a caution.
"""
from __future__ import annotations

from typing import Tuple

from .acp import AgentDefinition

_MODE = "https://agentclientprotocol.com/protocol/session-modes#"

COPILOT = AgentDefinition(
    id="copilot",
    name="GitHub Copilot",
    program="copilot",
    args=("--acp", "--disable-client-telemetry"),
    sign_in_hint="Run copilot login in a terminal, then try again.",
    billing="Your GitHub Copilot plan. Environment settings that would move turns to another provider are removed.",
    agent_names=("Copilot",),
    verified_versions=("1.0.105",),
    # Autopilot isn't here: it turns allow-all on and stops asking.
    safe_modes={_MODE + "agent": ("Agent", "Asks before it acts."),
                _MODE + "plan": ("Plan", "Plans multi-step work, and asks before it acts.")},
    forbidden_options={"allow_all": ("on",)},
    strip_env_names=("COPILOT_ALLOW_ALL", "COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN",
                     "COPILOT_OFFLINE", "COPILOT_MODEL", "COPILOT_AUTO_TIER"),
    strip_env_prefixes=("COPILOT_PROVIDER_",),
)

HERMES = AgentDefinition(
    id="hermes",
    name="Hermes",
    program="hermes",
    args=("acp",),
    sign_in_hint="Run hermes setup in a terminal, then try again.",
    # Its models come from several providers, so a turn may spend an API key.
    billing="Whichever provider and key Hermes is set up with, which may be a paid API key. "
            "The Chat Place can't check this.",
)

AGENTS: Tuple[AgentDefinition, ...] = (COPILOT, HERMES)
