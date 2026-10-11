# The first visual review (#155)

The first look at The Chat Place's screens, taken on 2026-10-10 with the visual probe
(`tools/ui_probe.py`, run through `tools/ui_probe_vm.ps1` in the test VM). It covered 34
surfaces in six Windows variants, 204 pictures in all:
- light at 100, 150 and 175% scaling;
- dark mode for apps;
- High Contrast Aquatic (dark) and Desert (light).

Agents reviewed each variant against `tools/ui_review_prompt.md`, opening every picture and
measuring colours from its pixels. `descriptions.md` beside this file says what each screen
looks like.

## What it found

| Issue | What | Where |
|---|---|---|
| #175 | Status bar buttons are 16 pixels tall and need 23, so the bottoms of letters are cut off ("needs vou", "readv"). **Fixed:** the bar is 30 tall | Every main-window picture, every variant |
| #176 | Long labels run off the dialog's edge: the Remote Control check box, Report a Bug's note (the support address is hidden), Session List Columns' intro, Usage's first row. Question and Settings have large empty bands. **Fixed:** long labels wrap and dialogs fit their contents | settings, bug-report, session-columns, usage, question |
| #177 | Windows dark mode isn't followed: the app stays light, and only the formatted pages turn dark, inside light dialogs | Every dark-100 picture |
| #178 | Continue Here's Title box opens scrolled to its end and its About box cuts a line in half; New Session's Folder box is cramped. **Fixed:** the Folder box is full width, the title shows its start and the note shows whole (field edges 2 pixels apart remain) | continue-here, new-session |
| #179 | **Fixed:** The split between the session list and the session moved with the status text (378, 326, then 268 pixels), and status bar fields were cut mid-word | main-own, main-desktop, main-own-working |
| #180 | The Last message column (#146) is out of sight at the default window size | main-last-message |
| #181 | Both lists show the same blue selection; only a faint dotted outline (1.89:1) says which has focus | Every main-window picture, light and dark |
| #182 | A first run shows an empty white list and no word on how to start. **Fixed:** the messages say how to start | main-empty |
| #185 | The app isn't DPI aware, so Windows stretches it at 150% and 175% and text is soft | light-150, light-175 |
| #186 | At 175% on a 1080-pixel screen, tall dialogs run under the taskbar and their buttons can't be seen | new-session, continue-here, bug-report, about-you, plan and the formatted pages at 175% |

Noted, not filed:
- **The messages list:** it shows a message's first line as written, so a heading reads "Claude: ## What changed".
- **Attachments:** the attachment list has no visible label (it has an accessible name).
- **Window titles:** in High Contrast Desert they are faint, but that is Windows drawing an inactive title bar, not the app.

A formatted message's code block loses its edge under High Contrast, because a background
colour is all that marks it. Phase 3 of #155 adds a border there; until it merges, the
High Contrast pictures show the block marked only by its indent and font.

## What passed

- **Text contrast (WCAG 1.4.3):** every text and background pair measured passes AA in light,
  dark (the formatted pages) and both High Contrast themes. The lowest app-drawn pair is white
  on the selection blue, at 4.53:1.
- **Non-text contrast (WCAG 1.4.11):** this doesn't all pass. The list focus outline is 1.89:1
  (#181), and the formatted view's code box is 1.1:1 against its page in light mode and 1.2:1
  in dark. The code box is only a tint, though, since the monospaced text marks the block.
- **High Contrast:** both themes reach every part of the app, including the custom-drawn status
  bar, list selection and the formatted pages.
- **Rendering:** no screen is blank, and none shows stray `&` marks, mojibake or placeholder
  text.

## The probe's own mistakes, found and fixed along the way

- **High Contrast themes:** the first runs used the wrong ones. Their files name themselves only
  by resource strings, so the probe now finds each by its window colour.
- **Window size at 150%:** the main window was forced to 1000 by 720 and ran under the taskbar.
  It now fits the screen, as the app sizes itself.
- **A formatted page photographed blank:** the probe now waits until the page shows more than one
  colour.
- **A turn in progress said "idle":** it now refreshes first, as the app's timer would.
