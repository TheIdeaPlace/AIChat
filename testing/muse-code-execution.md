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
   to choose another with Change Model. Change Effort lists the efforts the
   chat's model offers, with the one it uses marked "(now)"; choose another
   and you hear the chat's effort is now that one. Set the highest effort,
   then switch to a model without it: you hear the effort became that model's
   default, and the next turn still works. Change Model and Change Effort on
   a chat started in Muse Code say it's read-only here. Chat, Insert Command
   or Skill (Ctrl+/) lists the chat's skills; type in Search to narrow them,
   choose plan, and "/plan " goes before your draft with focus at its end.
   Send it: Muse replies in the plan skill's way. A message starting with a
   name Muse doesn't list is answered as ordinary text. Chat, Ask Aside
   (Ctrl+Shift+Y) asks a question about the chat, idle and with a turn
   running: the answer comes from the chat's history, the chat itself gets
   nothing, and no permission or question dialog ever opens. The side chat
   doesn't appear in the list; View, Show Sessions, Hidden shows it. Chat,
   Attach Files (Ctrl+Shift+F) a picture and a text file from outside the
   chat's folder, then send a message about both: Muse's reply describes the
   picture and quotes the file. A file deleted before sending says it's no
   longer there, and the message isn't sent.
7. Chat, Chat Settings, Chat Permissions on the owned chat shows Muse Code's
   profile ("Muse Code: Ask me"), no kept rules, and answering instructions.
   Chat, Chat Settings, Change Permission Mode (Ctrl+Shift+O) lists Read
   only, Ask me (now), Auto-review and Unrestricted. Choose Read only: no
   question, and you hear the chat now uses it; send a message, then restart
   The Chat Place and check Chat Permissions still says Read only. Choose
   Auto-review or Unrestricted: a warning asks first with No focused; No
   keeps the old profile. Set it back to Ask me. In the messages list,
   choose your second message and press Ctrl+Shift+Z (or Retry from Here on
   the list's menu): the dialog says files
   aren't put back; OK opens "*title* (retry)" with that message in the
   reply box and focus there. Its history ends before the message, and the
   original chat is unchanged. Choose Muse Code's first reply and Branch from
   Here: a question with No focused; No changes nothing. Yes opens
   "*title* (branch)" with an empty reply box; ask what it remembers and it
   knows only the first turn. The original is unchanged. Chat, Stop
   Background Task with nothing in the background says so. With background
   work running (needs commands to run: see the known limitation), it lists
   it by tool and command, asks with No focused, and Yes says it's stopping
   while the turn carries on.
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
