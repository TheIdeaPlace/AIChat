# The visual probe

The probe (#155) opens every screen of The Chat Place on made-up data, and saves a
picture of each with a JSON description of its controls. Claude then reviews the
pictures against a checklist and describes them in words. That's how the app's look is
checked without anyone needing to see it.

## What it photographs

`python tools/ui_probe.py --list` lists the surfaces:
- the main window in several states (as it opens, an own session, mid-turn, a desktop
  session, tool activity, the Last message column, an update waiting, no sessions);
- every dialog, opened through its own menu command wherever the made-up data allows;
- the formatted view (shortcuts, user guide, a message with a heading, list, code, table
  and link).

`tests/test_ui_probe.py` fails if a dialog class has no surface, so a new dialog has to
be added to the probe.

The data is the hidden-window tests' (`tests/fake_env.py`): no real sessions, no real
`%APPDATA%`, no real `claude`, no speech.

## Windows: in the test VM

The probe shows real windows, and the variants change Windows' theme and scaling, so it
runs in a test VM from vmtest's pool (ClaudeTesting and any others), never on a PC someone is
using. From the repo:

```
vmtest begin
powershell -File tools\ui_probe_vm.ps1                       # every variant
powershell -File tools\ui_probe_vm.ps1 -Variant light-100,dark-100 -Surface settings,permission
vmtest save                                                  # when done for now
```

The variants are in `tools/ui_probe_plan.json`:
- light at 100, 150 and 175% scaling;
- dark apps;
- High Contrast Aquatic and Desert.

`tools/ui_probe_guest.ps1` sets each one inside the VM, runs the probe, and puts the display
back. It turns on Windows' "Underline access keys" setting, so the pictures don't differ by
whether the keyboard was used last, and ClearType, so text is smoothed the same way in every
run. It refuses to run outside a virtual machine.

A pop-up from Windows itself (OneDrive asking to back up, for example) can land on a
screen and show as a change. Check the diff picture before accepting a changed screen.

For a run, the VM is switched to its largest screen mode, 1920 by 1080. Windows allows up to
175% on that screen, so 200% would need ClaudeTesting's Hyper-V display set larger. The first
run installs Python 3.12, the Visual C++ runtime and the app's packages in the VM, which
takes a few minutes. The pictures come back to a new folder under `%TEMP%\tcp-ui-probe`.

At 150% and 175% the pictures are screen copies in real pixels, because the app isn't DPI
aware (#185): Windows draws it at 100% and stretches it, and the pictures show that, as a
person would see it. The main window is shrunk to fit the screen, as the app does.

Each `manifest-<tag>.json` records what the variant really was: the scaling, the work area
the windows were fitted to, High Contrast, and Windows' own dark mode setting
(`windows_apps_dark`; wx's `dark` says whether the app's colours are dark, which is a
different question).

## macOS: by hand

On a Mac, from the repo with its `.venv`, and with Screen Recording allowed for the
terminal (System Settings, Privacy & Security, Screen Recording):

```
.venv/bin/python tools/ui_probe.py --out ~/Desktop/tcp-probe --tag mac-light
```

Switch to Dark appearance and run it again with `--tag mac-dark`. A Mac has no WebView2,
so the formatted-page surfaces show the plain-text dialogs there, as the app does.

## Comparing with the baseline

The pictures and descriptions of the last accepted run are in `testing/visual/baseline/windows/`,
with each variant's manifest. To see what a change did:

```
.venv\Scripts\python tools\ui_compare.py %TEMP%\tcp-ui-probe\<run>
```

It needs the app's venv, for wx. It writes `compare.md` in the run folder, listing each screen
that changed and what changed in words:
- controls gone, new, moved or resized by more than 2 pixels;
- enabled or disabled, text, values, list items and selections;
- new layout problems.

It also gives the share of pixels that changed, which catches colour and theme changes the
layout can't see. For each changed screen, `diff\<screen>.png` marks the changed pixels in red.

Screens the probe reported as failed are listed separately and not compared. It exits with 1 when
anything changed, is missing or failed. New screens alone give 0: they have no baseline yet.

A run of some variants is compared only with those variants. Two runs of the same code compare
as unchanged:
- anti-aliasing differences are within the tolerance;
- the window's outer border, drawn by Windows, isn't compared;
- the 150% and 175% pictures stop at the taskbar.

When the review agrees the changes are right, accept them into the baseline in the same PR:

```
.venv\Scripts\python tools\ui_compare.py %TEMP%\tcp-ui-probe\<run> --accept                    # the whole run
.venv\Scripts\python tools\ui_compare.py %TEMP%\tcp-ui-probe\<run> --accept settings-light-100   # one screen
```

A name that isn't in the run leaves the baseline untouched. When a surface is taken out of the
probe, `git rm` its pictures and descriptions from the baseline too, or every comparison reports
them missing.

A Mac run needs its own baseline folder (`--baseline testing/visual/baseline/macos`). Against the
Windows one, every screen would show as new.

The baseline is about 15 MB. The dark-100 pictures are almost all the same as light-100 for now,
because the app doesn't follow Windows dark mode yet (#177). They stay so that the day it does,
the change shows. Accept only the screens that changed, rather than a whole run, to keep the
repository's history from growing by a full set each time.

A formatted page (WebView2) now and then fails to draw in a freshly reset VM. The probe tries
once more on a new window. If it's still blank, it reports the screen as an error, and `--accept`
refuses any screen the run reported as failed, so a blank picture never becomes the baseline.
Run that screen again.

## Reviewing a run

Ask Claude to review the run folder with `tools/ui_review_prompt.md`. It gives each
picture a PASS, FAIL or UNSURE verdict, writes the description of each surface in
`testing/visual/descriptions.md`, and lists each defect once, to file as an issue.

The probe's own flags are in each `manifest-<tag>.json` (`problems`): text smaller than
it needs, controls overlapping, controls outside the window. They're measured, so treat
them as findings to confirm in the picture, not opinions.
