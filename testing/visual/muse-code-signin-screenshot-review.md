# Muse Code sign-in screenshot review

Historical evidence from before the upstream six-menu merge: sign-in now lives
under Tools, Sign In, Muse Code. The Help-menu screenshot and reader passes
below describe the earlier layout. Separately, the user has confirmed manual
testing passes for the updated Tools submenu flow with JAWS 2027.2610.428
(Script Revision: 0) and NVDA 2026.3.1 (2026.3.1.58288). This does not update the
visual evidence for the new menu layout.

Reviewed the two supplied 1920 by 1080 PNGs with the checklist in
`tools/ui_review_prompt.md`. The verdicts below apply to the Muse menu item and
warning dialog, in the light appearance captured. They are not whole-window
accessibility or High Contrast conformance results.

## Results

```json
[
  {
    "file": "Focus on help menu.png",
    "scope": "Help menu and Muse Code Sign-in item",
    "verdict": "PASS",
    "failed": [],
    "note": "The complete menu label fits. The focused item has visible outlines. No menu clipping, overlapping labels, stray ampersands or missing content is visible. Text and focus contrast pass."
  },
  {
    "file": "Muse Code Sign-In missing binary.png",
    "scope": "Native Muse Code Sign-in warning dialog",
    "verdict": "PASS",
    "failed": [],
    "note": "The warning heading wraps onto two lines. The installation command, verification command, restart guidance and official address fit inside the dialog. The OK button is fully visible and focused. No clipped dialog text or overlapping controls is visible. Text and focus contrast pass."
  }
]
```

The first image shows the Help menu above the session list, with Muse Code
Sign-in between Copilot Sign-in and Report a Bug. Both a black inner outline
and a thick blue outer highlight surround the selected item. The screenshot
does not establish whether the outer highlight comes from the app or an
assistive tool.

The second image shows a native warning centered over the app. A yellow warning
icon is to the left of the blue heading. Black instructions sit below on white.
A light gray footer contains the single OK button on the right, with a visible
border and dotted focus rectangle. The dialog is an OK warning, not a read-only
edit box.

## Pixel measurements

Images were opened directly and sampled with `System.Drawing.Bitmap.GetPixel`.
The ratios use core text/stroke colors and their adjacent backgrounds, not the
lighter ClearType edge pixels. Ratios were computed from linearized sRGB
luminance using `(lighter + 0.05) / (darker + 0.05)`. The repository requires
4.5:1 for ordinary text and 3:1 for focus indicators.

| Element | Foreground | Background | Ratio | Required | Result |
| --- | --- | --- | --- | --- | --- |
| Menu text and black inner outline | #000000 | #F9F9F9 | 19.946:1 | 4.5:1 text / 3:1 outline | Pass |
| Visible blue menu highlight | #0336FF | #F9F9F9 | 6.703:1 | 3:1 | Pass |
| Warning heading | #003399 | #FFFFFF | 10.861:1 | 4.5:1 | Pass |
| Warning body | #000000 | #FFFFFF | 21.000:1 | 4.5:1 | Pass |
| OK text and dotted focus | #0D0D0D | #FDFDFD | 19.107:1 | 4.5:1 text / 3:1 focus | Pass |
| OK blue border | #0078D4 | #FDFDFD | 4.452:1 | 3:1 | Pass |

Sampling rectangles are image pixels, with the origin at the top left:

- Menu label: x=479, y=311, width=150, height=18.
- Blue menu highlight: x=431, y=299, width=365, height=7.
- Warning heading: x=684, y=350, width=550, height=53.
- Warning body: x=683, y=427, width=577, height=138.
- OK button: x=1185, y=596, width=85, height=27.

## Separate existing selection-color observation

The selected conversation row behind the Help menu uses white (#FFFFFF) on
Windows blue (#0078D7). Sampling x=848, y=565, width=695, height=17 gives
**4.49886148:1**, fractionally below 4.5:1. Rounding this value up would hide the
shortfall. This is outside the Muse menu/dialog verdicts and prevents claiming
that the complete app window passes every text-contrast check.

Long session and message rows visibly end at their list boundaries. The Muse
menu labels and warning instructions are fully visible; the list rows are not
used as evidence that the warning has clipped text.

## Evidence limits

The user also reports that live JAWS and NVDA testing passes for the Help-menu
and missing-Muse dialog flow reviewed here. This is user-confirmed runtime
evidence, separate from the screenshot measurements, using:

- NVDA 2026.3.1 (2026.3.1.58288).
- JAWS 2027.2610.428 (Script Revision: 0).

It does not establish the installed-agent authentication flow.

These are user-supplied screenshots, without probe JSON or a matching baseline.
The pictures support visible layout and pixel contrast checks; they do not
establish native control bounds, keyboard operation, screen-reader output,
focus restoration after dismissal, other display scales, or High Contrast.
No baseline was accepted and no application code was changed for this review.

Source files:

- `C:\dev\Screenshots\Muse-Code\Sign In\Focus on help menu.png`
- `C:\dev\Screenshots\Muse-Code\Sign In\Muse Code Sign-In missing binary.png`
