# The Linux port: Orca, GTK and the AppImage

Written while adding Linux (x64 and arm64), with Orca as the screen reader. What was measured, on
Debian 13 (Orca 48.1, AT-SPI 2.56, GTK 3.24.49, wxPython 4.2.3 / wxWidgets 3.2.7, WebKitGTK 2.50)
and Ubuntu 24.04 (wxPython 4.2.1 / wxWidgets 3.2.4), both arm64, in virtual machines with Xvfb,
Openbox and Orca writing what it spoke to its debug log. Not yet tried on real hardware.

## How Orca was watched

Orca runs with `--debug-file`, and an `orca-customizations.py` that makes that file line
buffered (`debug.debugFile.reconfigure(line_buffering=True)`); without it nothing appears until
4 KB have built up. Its `SPEECH OUTPUT:` and `BRAILLE LINE:` lines are what it said and brailled.
xdotool presses the keys. speech-dispatcher needs somewhere to send audio, or Orca hangs at
"SPEECH: Initializing": with no sound card, ALSA's null device (`pcm.!default { type null }`).
`tools/atspi_audit.py` lists what Orca can reach in a window, and the probe runs it on every
surface (`TCP_PROBE_ORCA=1`, see `testing/visual-probe.md`).

## Names (ui/gtk_a11y.py)

wx can't name a control for Orca: `SetName` reaches nothing native, and `wx.Accessible` is
Windows-only. wxGTK does make the static text before a control its label (a GTK mnemonic label,
which ATK turns into `labelled-by`), but on the control's outer widget. A `wx.ListBox` is a
`GtkScrolledWindow` around a `GtkTreeView`, a multi-line `wx.TextCtrl` one around a
`GtkTextView`, and Orca lands on the inner one: the session list was "table with 0 rows 1
column". So, as `mac_a11y` does for VoiceOver, the name is set on the native widget, through
ATK with ctypes: `GetGtkWidget()` (not `GetHandle()`, which on GTK is an X window id, 0 until
shown), into the scrolled window's child, `atk_object_set_name`.

Controls wx draws itself (the status bar's text parts) are "panel" to GTK, with no name: they
get the label role and their words as their name. The status bar gets the status bar role, so
Orca's read-status-bar command finds the parts.

wxGTK links labels at idle time, in `RealizeTabOrder`, to the next focusable sibling, and that
includes a radio box's label: Settings' first check box became "Announcements, Announce when
any listed session finishes a turn". `gtk_a11y.forget_labels` removes it, on the dialog's first
idle event (any earlier and wx links it again).

## The messages list's rows (#11)

On Windows the screen reader reads each row as the whole message (MSAA, `ListItemsAccessible`).
On Linux the row's accessible name can be set (it's a `GtkTextCellAccessible`), and Orca's log
showed the new name, but Orca 48 speaks a table cell's displayed text, not its name. It does
speak the cell's description after it, so the selected row's description is the rest of the
message: the spoken message less the words the row shows (matched word by word, since the row
has the first line as written and the message is read as words). It's set when the selection or
focus changes, which wx handles inside GTK's own signal, before Orca's query reaches the app.
GTK makes a cell's accessible only when it's asked for, so rows aren't done ahead.

Orca 48 and 49 don't say a list's name when focus lands on one of its rows:
`SpeechGenerator._generate_table_cell` computes the ancestors and then overwrites them (`result =`
where it meant `result +=`). Orca 50 has it fixed. Where Am I reads the name either way.

## Speech (orca.py)

Orca has no API like NVDA's controller client before 49. What there is:

| Way in | Orca | Works from the background? |
|---|---|---|
| `PresentMessage` on D-Bus: `org.gnome.Orca.Service` (49, 50), `org.gnome.Orca1.Service` (51) | 49 | Yes |
| ATK `notification` signal (ATK 2.50; `announcement` in 2.46) from the app's window | 45.2 | No: `EventManager._should_process_event` drops events from an app that isn't active |
| speech-dispatcher directly (`spd-say`) | any | Yes, in speech-dispatcher's voice |

So: D-Bus first; then the window, while The Chat Place is active (asked of the UI thread, which
the speech thread waits on for a second at most); then speech-dispatcher. That last one isn't
Windows' #98 defect, a second voice over the screen reader. Orca speaks through
speech-dispatcher at its MESSAGE priority, and The Chat Place's voice at TEXT: speech-dispatcher
speaks one message at a time, a TEXT message waits while a MESSAGE is spoken, and Orca's next
MESSAGE cancels it. Orca 48 users hear announcements from the background in speech-dispatcher's
default voice, not Orca's.

D-Bus is called through libgio (ctypes, `orca._gio_present`), so the text goes in memory and not on
a command line other users can read in `/proc`. Without libgio, `busctl`, `gdbus` or `dbus-send`,
whichever there is. All the attempts for one announcement share a 1.5 s deadline (`orca.DEADLINE`),
because the speaker drops the announcement after 3 s and the window announcer may take 1 s of
that; the way that worked last is tried first. gdbus reads its arguments as GVariant text ("true" would be a boolean), so the
text is quoted as a GVariant string. An Orca whose service doesn't exist (D-Bus says
ServiceUnknown) isn't asked again until it restarts (another process id).

## The formatted page

WebKitGTK shows it, and Orca can move by heading and landmark in it once the page has the focus.
wx's `SetFocus` focuses its own scrolled window around WebKit's view, so the app focuses the
`WebKitWebView` itself, and a page script focuses the message's first element and puts the
selection there. Even so, Orca 48 read neither line by line (Down) nor Say All, in the app or in
a bare GTK window with WebKitGTK 2.50, so the plain text box is the default on Linux. It reads by
line, word and character under Orca.

## The AppImage

PyInstaller copies every library the app loads, GTK and WebKitGTK included (332 MB). Those must
be the system's: Orca talks to the desktop's accessibility bridge, the theme and input methods
are the desktop's, and a copied WebKitGTK can't find its helper programs on another system. So
`build_linux.sh` takes back out all but Python, wxWidgets and the image libraries distributions
name differently (libjpeg.so.62 on Debian, .8 on Ubuntu): 67 MB, 32 MB as an AppImage. The PC
it's built on is then the oldest it runs on (Ubuntu 24.04 in CI). A library every distribution
names alike must not be kept: Ubuntu 24.04's OpenSSL, loaded before Debian 13's, stopped
Debian's libcurl, and with it wxWidgets, from loading.

PyInstaller points `LD_LIBRARY_PATH` at the app's libraries, and every program the app started
inherited it; `restore_system_library_path` puts the system's back at start.

Velopack packs the AppImage with the classic AppImage runtime, which needs FUSE 2: not installed
by default on Debian 13 or Ubuntu 24.04 (`libfuse2t64`). `vpk pack` needs `mksquashfs`; the
smoke tests run the AppImage with `APPIMAGE_EXTRACT_AND_RUN=1`.

An AppImage doesn't install anything, so the app writes its own desktop entry
(`~/.local/share/applications/thechatplace.desktop`), which puts it in the menu and makes it the
`thechatplace://` handler. Its `Exec` is quoted only when the path needs it: `xdg-open` with no
desktop to ask (a bare window manager) can't start a quoted program, though GIO can.

## Smaller things

- `wx.GetActiveWindow()` on GTK returns the focused control, not its window, so "a dialog of
  ours is open" was always true and an update was never installed. `_active_top_level` takes
  the control's top-level window.
- GTK's names: `g_set_prgname("thechatplace")` before the first window, or Orca calls the app
  "__main__.py" and the window doesn't match its desktop entry.
- A status bar part was given a height of -4 before the bar was laid out; GTK warns at every one.
- On Linux the native status bar fields stay empty: wx draws GTK's status bar itself, Orca can't
  read their text, and it showed through the buttons, doubled.

`Speaker.stop()` on Linux also runs `spd-say -C`, because killing `spd-say` doesn't stop text
speech-dispatcher already has. `-C` cancels every client's messages, so Orca's own speech in
progress is cut off too; it runs only when an engine process was running.
