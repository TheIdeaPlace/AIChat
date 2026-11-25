"""Usage and Context's lines for Muse Code (#701), from installed 1.4.24's
shapes. No Muse Code runs."""
import time

from thechatplace.providers.muse_usage import (CONTEXT_NOT_YET, PLAN_UNAVAILABLE,
                                               context_lines, plan_lines)


def test_context_says_how_full_and_its_pressure():
    assert context_lines({"windowTokens": 1000, "usedTokens": 910, "pressure": "warning"}, None) == [
        "Context 91% full: 910 of 1,000 tokens, nearly full."]
    assert context_lines({"windowTokens": 1000, "usedTokens": 1200, "pressure": "blocked"}, None) == [
        "Context 100% full: 1,200 of 1,000 tokens, full."]
    # A level this app doesn't know is left out, not guessed.
    assert context_lines({"windowTokens": 1000, "usedTokens": 10, "pressure": "new"}, None) == [
        "Context 1% full: 10 of 1,000 tokens."]


def test_context_not_reported_yet_or_unreadable():
    assert context_lines(None, None) == [CONTEXT_NOT_YET]
    assert context_lines({"windowTokens": 0, "usedTokens": 5}, None) == [CONTEXT_NOT_YET]
    assert context_lines({"windowTokens": "1000", "usedTokens": -1}, {"usage": "x"}) == [CONTEXT_NOT_YET]


def test_last_turn_leaves_out_what_isnt_reported():
    assert context_lines(None, {"usage": {"inputTokens": 1200, "outputTokens": 30}})[1] == (
        "Last turn: 1,200 input, 30 output tokens.")


def test_plan_tier_limits_and_resets():
    now = time.time()
    lines = plan_lines({"tierName": "Synthetic  Tier", "subscriptionActive": False, "usage": {
        "window": {"usedPercent": 40, "windowDurationMins": 300, "resetsAtMs": int((now + 3600) * 1000)},
        "weekly": {"usedPercent": 101}}}, now=now)
    assert lines[0] == "Muse Code plan: Synthetic Tier, not active."
    assert lines[1].startswith("5-hour limit: 40% used, 60% remaining, resets ")
    assert len(lines) == 2  # a weekly figure over 100% isn't believed


def test_plan_without_anything_reported():
    assert plan_lines({}) == ["Muse Code usage limits: not reported."]
    assert plan_lines(None) == [PLAN_UNAVAILABLE]
