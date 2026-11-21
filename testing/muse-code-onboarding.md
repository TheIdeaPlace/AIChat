# Muse Code setup validation

## Screen-reader versions

Use these versions for Muse Code project testing unless the user specifies an
update:

- NVDA 2026.3.1 (2026.3.1.58288).
- JAWS 2027.2610.428 (Script Revision: 0).

Record pass/fail results for each flow when it is tested; these standard
versions do not imply that untested flows have passed.

Use a disposable test account/workspace and a supported native Muse build.
Record the app version, Muse version, operating system and screen reader.
Never include account tokens, verification codes or real conversation content
in reports or screenshots shared outside the test environment.

## Missing installation

1. On a machine without Muse, open Tools, Sign In, Muse Code.
2. Confirm that it says Muse Code was not found and explains how to install it.
3. Confirm that the message uses the same native warning with an OK button as
   missing Copilot, rather than a read-only edit box.
4. Dismiss it with OK or Enter. Confirm that
   focus returns to the control used before opening Tools, Sign In, Muse Code.
   On Windows and Linux the menu access-key sequence is Alt+T, N, M.
5. Confirm that displaying the instructions starts no installer or browser.
6. Install Muse separately, then check again. If the app's process cannot see
   the new PATH, restart the app and repeat.

## Installed setup

1. With a supported native Muse installed, open Tools, Sign In, Muse Code.
2. Confirm that detection and status checking leave the main window responsive.
3. Compare the reported credential method with Muse's own setup. A stored API
   key must not be described as browser sign-in. A status that cannot be checked
   must say so rather than claiming the user is signed out.
   Status and error notices must use native OK dialogs, rather than read-only
   edit boxes. Confirm Enter dismisses them and restores focus.
4. Where the build supports browser sign-in, decline the offer once and confirm
   that no browser opens. Repeat, accept, and review the address and code with
   the screen reader before opening the browser.
5. Complete sign-in and confirm one final outcome, a refreshed account state
   and a usable focus position. Repeat with cancellation and expiry.
6. Activate Tools, Sign In, Muse Code twice while checking or signing in. Confirm one
   active operation. Close the app during checking and confirm no late dialog.
7. Confirm that setup has not created a chat or sent a model prompt and that
   Muse is not offered as an executable chat provider yet.

## Visual and accessibility evidence

Run the Muse probe surfaces in the test VM in light at 100% and a High Contrast
variant. Compare and review pictures for clipping, overlap and contrast. Walk
real Tab, Shift+Tab and Escape in that VM. Record NVDA/JAWS results separately
from hidden-window tests. Mac validation and Linux Orca CI cover their own
native controls; a Windows result does not establish either one.

The user supplied light-theme screenshots of the Help menu and missing-Muse
dialog, and confirmed that live JAWS 2027.2610.428 (Script Revision: 0) and
NVDA 2026.3.1 (2026.3.1.58288) testing passes for that flow.
Those screenshots predate the upstream menu merge. The user subsequently
completed manual testing of the updated Tools, Sign In, Muse Code flow and
confirmed it works as expected with both JAWS 2027.2610.428 (Script Revision: 0)
and NVDA 2026.3.1 (2026.3.1.58288). This records a user-confirmed pass for the
updated menu flow; it does not infer results for individual authentication cases
that were not specified.
The subsequent conversion of all remaining status/error notices to native OK
dialogs has automated regression coverage; live reader results for those
changed notices have not yet been reported.
The screenshot layout and contrast review is recorded separately in
[the screenshot review](visual/muse-code-signin-screenshot-review.md).

Native Windows detection and read-only account status were verified on
2026-11-13 with Muse Code 1.4.18 (1.4.18-R8587.1). The host reported signed out;
no sign-in or model turn was started. The official Windows launcher's active
native executable is recognized directly.

Real Muse browser authentication, VM keyboard walkthrough and High Contrast
pictures remain unverified.
