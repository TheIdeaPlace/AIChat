"""Skips for tests that only make sense on one platform: mostly Windows, the
platform the app was built on first. The rest of the suite runs on macOS and
Linux too (CI runs all three)."""
import sys

import pytest

#: Made-up records with Windows paths (C:\r\a.py). On a Mac, os.path doesn't
#: split on backslashes, and real transcripts there have POSIX paths anyway.
windows_paths = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows paths, which only Windows's os.path splits")

#: wx.Accessible (MSAA names for JAWS and NVDA) exists only in wxPython on Windows.
msaa = pytest.mark.skipif(sys.platform != "win32", reason="wx.Accessible is Windows-only")

#: VoiceOver names, read back from the native views (ui/mac_a11y.py).
voiceover = pytest.mark.skipif(sys.platform != "darwin", reason="VoiceOver names are macOS-only")

#: JAWS and NVDA called from the app's own process (screen_readers.py, #98).
windows_screen_readers = pytest.mark.skipif(
    sys.platform != "win32", reason="JAWS and NVDA are reached from the app on Windows only")

#: Layout measured against Windows' own controls and fonts (Segoe UI at 96
#: DPI on CI), where the visual probe's baseline is taken (#155). A Mac's
#: controls and fonts are other sizes, so what's cut off there differs.
windows_layout = pytest.mark.skipif(
    sys.platform != "win32", reason="layout is measured with Windows' controls and fonts")

#: Names, roles and descriptions read back through ATK, what Orca gets on
#: Linux (ui/gtk_a11y.py).
atk = pytest.mark.skipif(not sys.platform.startswith("linux"),
                         reason="ATK (Orca's names) is Linux-only")
