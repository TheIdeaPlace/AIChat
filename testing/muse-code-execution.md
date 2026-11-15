# Muse Code execution validation

Use NVDA 2026.3.1 (2026.3.1.58288) and JAWS 2027.2610.428
(Script Revision: 0), unless the user specifies updated versions. Use synthetic
conversation content and record manual results separately from automated tests.

## Known limitation

With Muse Code 1.4.20 on Windows, a command you allow doesn't run: Muse
reports "sandbox enforcement unavailable: windows_elevated setup_required",
even after `muse sandbox windows setup` reports ready. This is a Muse problem
([Muse Code SDK issue 149](https://github.com/meta-models/muse-code-sdk/issues/149)),
not the app's. Until it is fixed, step 3 can confirm the request and the
decision, but not the command's effect.

## Owned chats

1. With Muse Code 1.4.20 installed and signed in, open File, New Session.
   Provider offers Muse Code. Choosing it shows Model and effort as Muse
   Code's own (disabled) and a note saying each request is announced even
   while you read another chat, and that Ctrl+Shift+A allows it once, denies
   it or answers its question.
2. Start the chat with a first message. It opens, shows the message, and
   streams Muse Code's reply into the conversation. When the turn ends you
   hear "*title*: Muse Code finished." The reply box stays editable, and Send
   is available only while no turn runs. Chat, Turn Status (Ctrl+Shift+T)
   during the turn says "Muse Code is working.", and "Ready." after it.
3. Make Muse Code ask permission. A network command works, for example asking
   it to run `curl.exe -s https://example.com/`; a file write outside the
   chat's folder does not ask, because Muse refuses it outright. The request
   is announced once with its summary and how to answer it. Nothing runs on its
   own. Press Ctrl+Shift+A. The dialog shows the request and details
   read-only, then Muse Code's own one-time choices, "Allow once" and
   "Reject", and Not Yet. "Always allow" choices are never offered.
   - Reject: the command doesn't run, and Muse ends the turn.
   - Allow once: the decision is accepted (see the known limitation).
   - Not Yet: the request stays pending, and you're told Ctrl+Shift+A answers
     it later.
4. Make Muse Code ask a question, for example by asking it to ask which of
   two colours you prefer. Ctrl+Shift+A opens the question with its choices
   and a typed-answer box, then Send Answers and Cancel Question. Choose an
   answer and send it; Muse's reply uses your answer.
5. Press Stop mid-turn. Send stays unavailable until Muse confirms the
   cancellation, then you hear "*title*: Muse Code stopped." (not "finished").
   Sending straight after a finished or stopped turn starts a new turn;
   nothing is sent twice. Stop with nothing running says "Nothing is running
   in *title*." Steer (Ctrl+Alt+Enter) with text typed during a turn sends it into
   the turn: the reply box empties, you hear it was sent, and Muse's reply
   follows it. Once the turn has ended, Steer sends the text as a new message.
   If the turn ends just as you steer, Muse refuses it, and the text comes
   back to the reply box with why. While a request waits, Steer says to answer it
   first. Chat, Stop Background Task
   says Muse Code chats have no background tasks to stop.
6. Chat, Chat Settings, Change Model on the owned chat lists Muse Code's
   models with the chat's own marked "(now)". Choose another: you hear the
   chat now uses it, from Muse's next model call. Send a message. If the turn
   fails because the account can't use that model, you hear so and are told
   to choose another with Change Model. Change Model on a chat started in Muse
   Code says it's read-only here.
7. Chat, Chat Settings, Chat Permissions on the owned chat shows Muse Code's
   own mode, no kept rules, and the same answering instructions. Change
   Permission Mode says Muse Code chats always use Muse Code's own mode.
8. View, Show Sessions, Muse Code Chats lists the new chat with the Chat
   Place session identity. Externally created Muse conversations stay
   read-only: Send, Stop, Answer and permanent deletion must not become
   available for them, and no permission dialog may open for them. Chat menu
   commands that would change one say "*title* was started in Muse Code, so
   it's read-only here. Do that in Muse Code." Chat Permissions says Muse Code
   decides what it may do.
9. Restart The Chat Place and open the owned chat again. Its identity and
   history are stable; it remains a Chat Place session and can send again.
10. Make Muse Code unavailable (rename or remove its executable) and restart.
   Startup, other providers and shutdown must not break. Creating or sending
   in a Muse chat reports that execution is unavailable, without raw
   protocol data. Restore Muse and press F5 to confirm recovery.
11. Close the window while a Muse turn runs and while a permission request
    waits. The Muse host must exit; no late dialog or announcement may land
    after the window closes. Closing ends the running turn in Muse too.

## Visual and platform checks

Run the muse-new, muse-approval and chat-permissions-muse probe surfaces in
a Windows test VM at light 100% and one High Contrast variant. Review
clipping, overlap and measured contrast. Walk real Enter, Tab, Shift+Tab, F6
and Escape; verify focus, names and announcements separately with both screen
readers. Record Mac runtime and Linux Orca CI results separately. Do not
change the user's desktop theme to simulate a VM run. New pictures need
descriptions in testing/visual/descriptions.md, written from the pictures.

## Evidence

On 2026-11-14, scripts driving the app's own Muse classes against installed
Muse Code 1.4.20 (synthetic prompts, throwaway folders) confirmed: create,
send, reply and completion; a permission request with Reject (the command
didn't run) and Allow once (accepted, but blocked by the known limitation); a
question answered with the chosen option; Stop confirmed as cancelled; and
reopening a chat after Muse restarted. The results are in
[the execution dev note](../dev-notes/muse-code-execution.md). The full
automated suite passed (6908 passed, 61 skipped). The steps above, through the
window with NVDA and JAWS, and the VM visual checks have not been run yet;
record their results when performed.
