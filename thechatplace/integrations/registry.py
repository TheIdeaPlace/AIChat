"""Which integrations exist. The rest of the app asks here, by no name."""
from __future__ import annotations

from typing import List

from ..contracts import Integration
from .acp import AcpIntegration
from .agents import AGENTS
from .claude import ClaudeIntegration
from .codex import CodexIntegration
from .openai_chat import OpenAIChatIntegration


def default_integrations() -> List[Integration]:
    return [ClaudeIntegration(), CodexIntegration(), *(AcpIntegration(agent) for agent in AGENTS),
            OpenAIChatIntegration()]
