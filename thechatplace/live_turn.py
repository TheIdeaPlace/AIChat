"""What The Chat Place says about a Claude turn while it runs (#468), and
the words for changing it live. Plain text, without wx, so it can be tested.

Claude Code reports more than a reply as it works: a request being retried
(``api_retry``), the turn moving to the fallback model (``model_fallback``),
a memory saved (``memory_saved``), the background work with each task's id
(``background_tasks_changed``), and, asked with ``get_context_usage``, what
fills the context. The fields are as Claude Code 2.1.296's own schema has
them; claude_cli.py's parser hands them on as plain dicts.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from .claude_cli import describe_elapsed, model_spoken

#: After the first retry of a turn is said, later ones are said at most this
#: often (seconds); the status bar has every one.
RETRY_SPEAK_GAP = 30.0

#: api_retry's ``error`` values (Claude Code's error kinds), in words.
_RETRY_REASONS = {
    "overloaded": "the service is overloaded",
    "rate_limit": "the service is busy (rate limited)",
    "server_error": "the service had an error",
    "authentication_failed": "signing in failed",
    "oauth_org_not_allowed": "your organization isn't allowed",
    "account_on_hold": "your account is on hold",
    "verification_required": "your account needs verifying",
    "billing_error": "of a billing problem",
    "invalid_request": "the request was refused",
    "model_not_found": "the model wasn't found",
    "max_output_tokens": "the reply was too long",
    "cloud_credential_error": "of a credentials problem",
}


def retry_reason(data: dict) -> str:
    """Why Claude Code is retrying, in words."""
    error = str(data.get("error") or "")
    if error in _RETRY_REASONS:
        return _RETRY_REASONS[error]
    status = data.get("error_status")
    if type(status) is int and status:
        return "the service is overloaded" if status == 529 else f"the service answered {status}"
    # No answer at all: "unknown" with no status, as captured with Claude
    # Code 2.1.296 when the connection failed.
    return "it couldn't reach the service"


def _seconds(milliseconds) -> str:
    seconds = max(0, round((milliseconds if type(milliseconds) is int else 0) / 1000))
    return "1 second" if seconds == 1 else f"{seconds} seconds"


def retry_text(data: dict) -> str:
    """"Claude is retrying: the service is overloaded. Attempt 2 of 10, next
    in 8 seconds." Counted as Claude Code's own terminal counts them
    ("attempt 2/10")."""
    attempt = data.get("attempt") if type(data.get("attempt")) is int else 0
    most = data.get("max_retries") if type(data.get("max_retries")) is int else 0
    text = f"Claude is retrying: {retry_reason(data)}."
    if attempt > 0:
        text += f" Attempt {attempt}" + (f" of {most}" if most > 0 else "")
        text += f", next in {_seconds(data.get('retry_delay_ms'))}."
    return text


def retry_status_text(data: Optional[dict]) -> str:
    """For Turn Status while Claude Code is retrying: "Claude is retrying:
    the service is overloaded, attempt 2 of 10." "" when it isn't."""
    if not data:
        return ""
    attempt = data.get("attempt") if type(data.get("attempt")) is int else 0
    most = data.get("max_retries") if type(data.get("max_retries")) is int else 0
    text = f"Claude is retrying: {retry_reason(data)}"
    if attempt > 0:
        text += f", attempt {attempt}" + (f" of {most}" if most > 0 else "")
    return text + "."


class RetryThrottle:
    """When a retry is spoken (#468): the first of each turn, then at most
    every ``gap`` seconds, so a long run of retries doesn't talk over
    everything. ``key`` is the turn's chat."""

    def __init__(self, gap: float = RETRY_SPEAK_GAP) -> None:
        self.gap = gap
        self._last: Dict[str, float] = {}

    def should_speak(self, key: str, now: float) -> bool:
        last = self._last.get(key)
        if last is not None and now - last < self.gap:
            return False
        self._last[key] = now
        return True

    def turn_ended(self, key: str) -> None:
        """The next turn's first retry is said again."""
        self._last.pop(key, None)


#: model_fallback's ``trigger``, as what happened to the first model.
_FALLBACK_REASONS = {
    "overloaded": "was overloaded",
    "server_error": "had a server error",
    "model_not_found": "isn't available",
    "permission_denied": "isn't available on your account",
    "model_blocked": "is turned off for now",
    "last_resort": "failed",
}


def _model_words(model: str) -> str:
    return model_spoken(model) if model else "another model"


def fallback_text(data: dict) -> str:
    """"Claude switched to Sonnet 5.5 because Opus 5.5 was overloaded." For
    this turn only: Claude Code tries the first model again next turn."""
    to = _model_words(str(data.get("fallback_model") or ""))
    original = str(data.get("original_model") or "")
    reason = _FALLBACK_REASONS.get(str(data.get("trigger") or ""), "couldn't be used")
    if original:
        return f"Claude switched to {to} because {_model_words(original)} {reason}."
    return f"Claude switched to {to} for this turn."


def memory_name(path: str) -> str:
    """A memory file's name as said: its file name without the folder or
    ``.md``, with dashes and underscores as spaces ("build-steps.md" is
    "build steps")."""
    name = os.path.basename((path or "").replace("\\", "/").rstrip("/"))
    stem, extension = os.path.splitext(name)
    name = stem if extension.lower() == ".md" and stem else name
    return " ".join(name.replace("-", " ").replace("_", " ").split())


def memory_text(data: dict) -> str:
    """"Claude saved a memory: build steps." or "Claude saved 2 memories:
    build steps, release." ``verb`` is Claude Code's ("Saved", "Updated")."""
    names = [memory_name(p) for p in data.get("paths") or [] if memory_name(p)]
    verb = str(data.get("verb") or "Saved").strip().lower() or "saved"
    if not names:
        return f"Claude {verb} a memory."
    if len(names) == 1:
        return f"Claude {verb} a memory: {names[0]}."
    return f"Claude {verb} {len(names)} memories: {', '.join(names)}."


def background_choice(entry: dict, now: float) -> str:
    """"Windows build, running 3 minutes": one row of Stop Background Task.
    ``since`` and ``now`` are on the turn's clock."""
    description = " ".join(str(entry.get("description") or "A background task").split())
    since = entry.get("since")
    if isinstance(since, (int, float)):
        return f"{description}, running {describe_elapsed(now - since)}"
    return description


def stop_result_text(description: str, ok: bool, still_running: bool, error: str = "") -> str:
    """What's said once Stop Background Task is answered."""
    if not ok:
        return f"Couldn't stop {description}: {error or 'Claude Code refused'}."
    if still_running:
        return f"Asked Claude Code to stop {description}, but it's still running."
    return f"Stopped {description}."


def _tokens(count) -> str:
    return f"{count:,} token" + ("" if count == 1 else "s")


def context_lines(usage: Optional[dict]) -> List[str]:
    """What fills the context (#468), one line per category from Claude
    Code's get_context_usage answer, in its order: "System prompt: 2,362
    tokens", "Skills: 9,056 tokens", "Free space: 927,833 tokens". Deferred
    tools are only loaded when Claude needs one, so they say so. [] when
    there's nothing to show."""
    if not isinstance(usage, dict):
        return []
    categories = usage.get("categories")
    if not isinstance(categories, list):
        return []
    lines = []
    for category in categories:
        if not isinstance(category, dict):
            continue
        name = " ".join(str(category.get("name") or "").split())
        tokens = category.get("tokens")
        if not name or type(tokens) is not int or tokens <= 0:
            continue
        line = f"{name}: {_tokens(tokens)}"
        if category.get("isDeferred") or category.get("kind") == "deferred":
            if name.lower().endswith(" (deferred)"):
                line = f"{name[:-len(' (deferred)')]}: {_tokens(tokens)}"
            line += " (loaded when needed)"
        elif category.get("kind") == "buffer":
            line += " (kept free for compacting)"
        lines.append(line)
    if not lines:
        return []
    return ["What fills the context, when the latest turn ended:"] + lines
