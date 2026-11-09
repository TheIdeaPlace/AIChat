# Orca runs this at start (as orca-customizations.py in its data folder) in
# The Chat Place's Linux accessibility checks only: tools/linux_a11y_session.sh
# copies it into that run's own settings folder, never a person's own.
#
# 1. Orca buffers its debug log, so a check reading it while Orca runs saw
#    nothing: write it a line at a time.
#
# 2. Orca 46 (Ubuntu 24.04, what CI runs) remembers objects it has found dead
#    by hash(), which for an AT-SPI object is its address in Orca's memory.
#    Once a dead object is freed, a new one (a dialog just opened, the list
#    row just arrowed to) can get the same address, and Orca then takes it
#    for dead too and says nothing about it for up to a minute, until it
#    clears what it remembers. That made the Orca CI job fail on a different
#    step from run to run (#579): a dialog read without its title, or not at
#    all, a row arrowed to and not read. Newer Orca asks the object again
#    before believing it dead (AXObject.revalidate_if_known_dead); this does
#    the same for Orca 46, asking the app itself rather than AT-SPI's cache.
#    An object that really is gone still fails the question and stays dead,
#    so what Orca says about The Chat Place's own windows is unchanged: only
#    Orca's mistake is taken away. Each time it corrects one, the debug log
#    says so ("TCP #579"). This is for CI's Orca only: people using Orca 46
#    still meet the mistake.
from orca import debug

if debug.debugFile is not None:
    debug.debugFile.reconfigure(line_buffering=True)

try:
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
    from orca.ax_object import AXObject
except Exception:  # noqa: BLE001 - an Orca laid out differently: leave it alone
    AXObject = None

if AXObject is not None and hasattr(AXObject, "object_is_known_dead") \
        and not hasattr(AXObject, "revalidate_if_known_dead"):
    _known_dead = AXObject.object_is_known_dead
    # Without the cached role, get_role asks the app, which answers with an
    # error for an object it no longer has.
    _clear = getattr(Atspi.Accessible, "clear_cache_single", None) \
        or Atspi.Accessible.clear_cache

    def _still_dead(obj):
        if not _known_dead(obj):
            return False
        try:
            _clear(obj)
            role = Atspi.Accessible.get_role(obj)
            # The app can still hold an object whose widget it has destroyed.
            defunct = Atspi.Accessible.get_state_set(obj).contains(Atspi.StateType.DEFUNCT)
        except Exception:  # noqa: BLE001 - it really is gone
            return True
        if role == Atspi.Role.INVALID or defunct:
            return True
        AXObject._set_known_dead_status(obj, False)
        debug.printMessage(debug.LEVEL_INFO,
                           f"TCP #579: an object Orca had taken for dead answers ({role.value_nick}),"
                           " so it isn't", True)
        return False

    AXObject.object_is_known_dead = staticmethod(_still_dead)
    debug.printMessage(debug.LEVEL_INFO, "TCP #579: dead objects are asked again "
                       f"(cache cleared with {_clear.__name__})", True)
