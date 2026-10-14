import pytest

from thechatplace.desktop_groups import DesktopGroups
from thechatplace.providers.base import Snapshot
from thechatplace.providers.discovery import DiscoveryRegistry
from thechatplace.sessions import IDLE, NEEDS_YOU, WORKING, SessionInfo, SORT_NEWEST, SORT_VALUES
from thechatplace.hub import finished_turns


class Provider:
    def __init__(self, provider_id, snapshot):
        self.provider_id = provider_id
        self.snapshot = snapshot
        self.error = None

    def collect(self):
        if self.error:
            raise self.error
        return self.snapshot


def session(provider, time=1):
    return SessionInfo("external", f"{provider}:same", provider, "", "same",
                       provider=provider, last_activity_ms=time)


def test_mixed_sorting_and_colliding_ids_preserve_claude_ownership():
    groups = DesktopGroups(names=["Desktop group"], by_session={"claude:same": "Desktop group"})
    claude = Provider("claude", Snapshot(sessions=[session("claude")],
                      desktop_cli_ids={"same"}, cowork_cli_ids={"cowork"}, desktop_groups=groups,
                      unreadable_files=2))
    codex = Provider("codex", Snapshot(sessions=[session("codex", 20)]))
    result = DiscoveryRegistry([claude, codex]).collect(SORT_NEWEST)
    assert [s.identity for s in result.sessions] == [("codex", "same"), ("claude", "same")]
    assert result.desktop_cli_ids == {"same"}
    assert result.cowork_cli_ids == {"cowork"}
    assert result.desktop_groups == groups
    assert result.unreadable_files == 2
    assert not result.stale_providers


def test_failed_backend_keeps_private_cache_and_announces_each_outage_once():
    claude = Provider("claude", Snapshot(sessions=[session("claude")]))
    codex = Provider("codex", Snapshot(sessions=[session("codex")]))
    registry = DiscoveryRegistry([claude, codex])
    first = registry.collect()
    first.sessions[0].title = "Changed by UI"
    codex.snapshot.sessions[0].title = "Changed by backend"
    codex.error = RuntimeError("private credential: do not publish")
    claude.snapshot.sessions[0].last_activity_ms = 30
    failed = registry.collect()
    assert {s.provider: s.title for s in failed.sessions} == {"claude": "claude", "codex": "codex"}
    assert failed.stale_providers == {"codex"}
    assert failed.newly_failed_providers == {"codex"}
    assert failed.provider_errors == {"codex": "Session discovery unavailable"}
    assert not registry.collect().newly_failed_providers
    codex.error = None
    recovered = registry.collect()
    assert not recovered.stale_providers and not recovered.provider_errors
    assert next(s for s in recovered.sessions if s.provider == "codex").title == "Changed by backend"
    codex.error = OSError("disconnected again")
    assert registry.collect().newly_failed_providers == {"codex"}


def test_first_failure_does_not_remove_other_provider():
    claude = Provider("claude", Snapshot(sessions=[session("claude")]))
    codex = Provider("codex", Snapshot())
    codex.error = OSError("not installed")
    result = DiscoveryRegistry([codex, claude]).collect()
    assert [s.provider for s in result.sessions] == ["claude"]
    assert result.stale_providers == {"codex"}


@pytest.mark.parametrize("bad", [None, Snapshot(sessions=[session("claude")]),
    Snapshot(desktop_cli_ids={"same"}), Snapshot(cowork_cli_ids={"same"}),
    Snapshot(desktop_groups=DesktopGroups(names=["Foreign group"])),
    Snapshot(desktop_groups=DesktopGroups(by_session={"same": "Foreign group"})),
    Snapshot(desktop_groups=DesktopGroups(read_ok=False)), Snapshot(live={"same": object()})])
def test_invalid_foreign_snapshot_cannot_replace_good_cache(bad):
    codex = Provider("codex", Snapshot(sessions=[session("codex")]))
    registry = DiscoveryRegistry([codex])
    registry.collect()
    codex.snapshot = bad
    result = registry.collect()
    assert [s.identity for s in result.sessions] == [("codex", "same")]
    assert result.stale_providers == {"codex"}
    assert not result.desktop_cli_ids and not result.cowork_cli_ids and not result.live
    assert result.desktop_groups == DesktopGroups()


def test_duplicate_provider_is_rejected():
    provider = Provider("claude", Snapshot())
    with pytest.raises(ValueError, match="Duplicate provider"):
        DiscoveryRegistry([provider, provider])


@pytest.mark.parametrize("field,value", [("last_activity_ms", "bad"),
    ("last_activity_ms", None), ("last_activity_ms", True), ("last_activity_ms", -1),
    ("key", 7), ("title", []), ("cwd", 7), ("state", {})])
@pytest.mark.parametrize("order", SORT_VALUES)
def test_bad_sort_fields_preserve_good_cache_and_other_provider(field, value, order):
    codex = Provider("codex", Snapshot(sessions=[session("codex")]))
    claude = Provider("claude", Snapshot(sessions=[session("claude")]))
    registry = DiscoveryRegistry([codex, claude])
    registry.collect(order)
    setattr(codex.snapshot.sessions[0], field, value)
    result = registry.collect(order)
    assert {s.identity for s in result.sessions} == {("codex", "same"), ("claude", "same")}
    assert result.stale_providers == {"codex"}
    assert next(s for s in result.sessions if s.provider == "codex").last_activity_ms == 1


@pytest.mark.parametrize("bad", [Snapshot(unreadable_files="bad"),
    Snapshot(unreadable_files=True), Snapshot(unreadable_files=-1),
    Snapshot(sessions=None), Snapshot(sessions=[object()])])
def test_bad_merge_fields_do_not_poison_cache(bad):
    codex = Provider("codex", Snapshot(sessions=[session("codex")]))
    registry = DiscoveryRegistry([codex])
    registry.collect()
    codex.snapshot = bad
    result = registry.collect()
    assert [s.identity for s in result.sessions] == [("codex", "same")]
    assert result.unreadable_files == 0
    assert result.stale_providers == {"codex"}


def test_stale_snapshot_cannot_complete_turns_or_carry_live_state_into_recovery():
    claude, codex = session("claude"), session("codex")
    claude.state = codex.state = WORKING
    running = Snapshot(sessions=[claude, codex])
    previous = running.fresh_states()
    codex.state = IDLE
    outage = Snapshot(sessions=[claude, codex], stale_providers={"codex"})
    assert not finished_turns(previous, outage.sessions, stale_providers=outage.stale_providers)
    assert outage.fresh_states() == {claude.key: WORKING}
    recovered = Snapshot(sessions=[claude, codex])
    assert not finished_turns(outage.fresh_states(), recovered.sessions)
    # An outage on Claude is treated the same way, without affecting Codex.
    claude.state, codex.state = IDLE, WORKING
    stale_claude = Snapshot(sessions=[claude, codex], stale_providers={"claude"})
    assert stale_claude.fresh_states() == {codex.key: WORKING}
    assert not finished_turns(previous, stale_claude.sessions,
                              stale_providers=stale_claude.stale_providers)


@pytest.mark.parametrize("state", ["status unavailable", NEEDS_YOU, WORKING])
def test_codex_unknown_or_waiting_state_is_not_a_finished_turn(state):
    codex = session("codex")
    codex.state = state
    assert not finished_turns({codex.key: WORKING}, [codex])
    snapshot = Snapshot(sessions=[codex])
    assert (codex.key in snapshot.fresh_states()) == (state != "status unavailable")


def test_fresh_codex_idle_can_complete_but_claude_waiting_keeps_existing_behavior():
    codex, claude = session("codex"), session("claude")
    claude.state = NEEDS_YOU
    result = finished_turns({codex.key: WORKING, claude.key: WORKING}, [codex, claude])
    assert result == [codex, claude]
