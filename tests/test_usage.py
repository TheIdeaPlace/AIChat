"""Context, compaction and usage limits (#19)."""
import json
import time

from thechatplace import usage
from thechatplace.claude_cli import StreamParser
from thechatplace.transcript import TranscriptParser

NOW = time.mktime((2026, 10, 7, 9, 0, 0, 0, 0, -1))  # a Wednesday, 9 AM local


def _info(five=0.08, seven=0.34, status="allowed", **extra):
    info = {"status": status, "resetsAt": NOW + 3600, "unifiedWindows": {
        "five_hour": {"utilization": five, "resetsAt": NOW + 3600},
        "seven_day": {"utilization": seven, "resetsAt": NOW + 2 * 86400}}}
    info.update(extra)
    return info


def test_limits_in_words():
    assert usage.limits_text(_info(), NOW) == (
        "5-hour limit 8% used, resets at 10:00 AM. "
        "Weekly limit 34% used, resets on Friday at 9:00 AM.")
    assert usage.limits_text(None, NOW).startswith("Usage limits: not known")
    reached = usage.limits_text(_info(five=1.0, status="rejected"), NOW)
    assert reached.startswith("You've reached a usage limit; it resets at 10:00 AM.")
    assert usage.limits_text(_info(isUsingOverage=True), NOW).endswith(
        "Turns are now charged to usage credits.")


def test_warnings_only_when_close_or_reached():
    assert usage.limit_warning(_info(), NOW) is None
    assert usage.limit_warning(_info(seven=0.93), NOW) == (
        "You've used 93% of your weekly limit; it resets on Friday at 9:00 AM.")
    assert usage.limit_warning(_info(status="rejected"), NOW).startswith("You've reached")


def test_when_reads_naturally():
    assert usage.when(NOW + 3600, NOW) == "at 10:00 AM"
    assert usage.when(NOW + 86400, NOW) == "tomorrow at 9:00 AM"
    assert usage.when(NOW + 10 * 86400, NOW) == "on 17 October at 9:00 AM"


def test_context_windows_and_words():
    assert usage.context_window("claude-haiku-4-5", 1000) == 0  # not known: no guess
    assert usage.context_window("claude-opus-5-5", 895_930) == 1_000_000  # past 200k: large
    assert usage.context_window("claude-sonnet-5[1m]", 10) == 1_000_000
    assert usage.context_window("x", 100, reported=400_000) == 400_000
    assert usage.context_text(124_000, 200_000) == "Context 62% full: 124,000 of 200,000 tokens."
    assert usage.context_text(0, 200_000).startswith("Context: not known")
    assert usage.context_text(160_000, 0).startswith(
        "Context: 160,000 tokens used; the window's size isn't known")
    assert usage.when(NOW - 60, NOW) == "now"


def test_usage_limit_errors_in_plain_words():
    epoch = int(NOW + 7200)
    assert usage.friendly_error(f"Claude AI usage limit reached|{epoch}", NOW) == (
        "You've reached your Claude usage limit. It resets at 11:00 AM.")
    assert usage.friendly_error("Something else", NOW) == "Something else"


def test_usage_lines_one_per_limit():
    """What Ctrl+Shift+U lists (#130): the context, then each limit alone."""
    lines = usage.usage_lines(("Quiet one", 124_000, 200_000), _info(), NOW)
    assert lines == ["Quiet one: Context 62% full: 124,000 of 200,000 tokens.",
                     "5-hour limit 8% used, resets at 10:00 AM.",
                     "Weekly limit 34% used, resets on Friday at 9:00 AM."]
    info = _info(five=1.0, status="rejected", isUsingOverage=True)
    info["unifiedWindows"]["seven_day_opus"] = {"utilization": 0.5}
    lines = usage.usage_lines(("Quiet one", 0, 0), info, NOW)
    assert lines[0] == "Quiet one: Context: not known until Claude's next reply."
    assert lines[1] == "You've reached a usage limit; it resets at 10:00 AM."
    assert "Weekly Opus limit 50% used." in lines
    assert lines[-1] == "Turns are now charged to usage credits."
    assert len(lines) == 6
    # The spoken text (a limit reached, said unasked) is the same lines run together.
    assert usage.limits_text(info, NOW) == " ".join(lines[1:])


def test_usage_lines_when_nothing_is_known():
    lines = usage.usage_lines(None, None, NOW)
    assert lines == ["Context: no session is loaded. Load one to see how full its context is.",
                     "Usage limits: not known until a Chat Place session runs a turn."]
    assert usage.usage_lines(("Hub probe", 5, 0), {"status": "allowed"}, NOW)[1] == (
        "Usage limits: Claude Code didn't say.")
    assert usage.limit_lines({}, NOW) == [
        "Usage limits: not known until a Chat Place session runs a turn."]


def _line(**data):
    return json.dumps(data)


def test_parser_reports_limits_compaction_and_the_window():
    parser = StreamParser()
    events = parser.feed(_line(type="rate_limit_event", rate_limit_info=_info()))
    assert events[0].kind == "limits" and events[0].data["status"] == "allowed"
    assert parser.feed(_line(type="system", subtype="compact_boundary"))[0].kind == "compacted"
    parser.feed(_line(type="result", subtype="success", result="ok", modelUsage={
        "claude-opus-5-5": {"contextWindow": 1_000_000}, "claude-haiku": {"contextWindow": 200_000}}))
    assert parser.context_window == 1_000_000


def test_transcript_tracks_context_and_compaction():
    parser = TranscriptParser()
    parser.feed([_line(type="assistant", uuid="a1", message={
        "model": "claude-opus-5-5", "content": [{"type": "text", "text": "hi"}],
        "usage": {"input_tokens": 10, "cache_read_input_tokens": 120_000,
                  "cache_creation_input_tokens": 3_000, "output_tokens": 990}})])
    assert parser.transcript.context_tokens == 124_000
    assert parser.transcript.model == "claude-opus-5-5"
    added = parser.feed([_line(type="system", subtype="compact_boundary", uuid="c1",
                               compactMetadata={"trigger": "auto"})])
    assert added[0].text == "Conversation compacted (the context was full)."
    assert parser.transcript.context_tokens == 0 and parser.transcript.compactions == 1


def test_the_weekly_fable_limit_is_named_and_kept_after_other_turns():
    """Fable turns report seven_day_overage_included, which Claude Code calls
    the Fable limit (#722); other models' turns don't, so Usage keeps the
    last Fable report's."""
    fable = _info()
    fable["unifiedWindows"]["seven_day_overage_included"] = {"utilization": 0.25,
                                                             "resetsAt": NOW + 2 * 86400}
    said = "Weekly Fable limit 25% used, resets on Friday at 9:00 AM."
    assert said in usage.limit_lines(fable, NOW)
    opus = _info(seven=0.4)
    merged = usage.with_fable_window(opus, fable, NOW)
    lines = usage.limit_lines(merged, NOW)
    assert said in lines
    assert "Weekly limit 40% used, resets on Friday at 9:00 AM." in lines
    assert "seven_day_overage_included" not in opus["unifiedWindows"]  # not changed
    # A newer report of its own wins; nothing to add, nothing added.
    fresh = _info()
    fresh["unifiedWindows"]["seven_day_overage_included"] = {"utilization": 0.5,
                                                             "resetsAt": NOW + 86400}
    assert usage.with_fable_window(fresh, fable, NOW) is fresh
    assert usage.with_fable_window(opus, None, NOW) is opus
    assert usage.with_fable_window(None, fable, NOW) is None
    # Once its reset time has passed, the old figure isn't carried.
    assert usage.with_fable_window(opus, fable, NOW + 3 * 86400) is opus


def test_fable_on_usage_credits_is_still_said_after_another_models_turn():
    """Fable has its own allowance: an Opus turn saying it isn't on usage
    credits says nothing about Fable (#722)."""
    fable = _info(isUsingOverage=True)
    opus = _info(isUsingOverage=False)
    lines = usage.limit_lines(usage.with_fable_window(opus, fable, NOW), NOW)
    assert lines[-1] == "Turns are now charged to usage credits."
    assert opus["isUsingOverage"] is False  # not changed
    # After the limit it named has reset, it isn't said any more.
    later = usage.with_fable_window(opus, fable, NOW + 7200)
    assert "Turns are now charged to usage credits." not in usage.limit_lines(later, NOW)
