# Muse Code execution validation

Use NVDA 2026.3.1 (2026.3.1.58288) and JAWS 2027.2610.428
(Script Revision: 0), unless the user specifies updated versions. Use synthetic
conversation content and record manual results separately from automated tests.

## Owned chats

1. With Muse Code 1.4.18 or 1.4.20 installed and signed in, open File, New
   Session. Provider offers Muse Code. Choosing it shows Model and effort as
   Muse Code's own (disabled) and a note saying permission requests are
   announced and answered with Allow Once or Deny, or stopped.
2. Start the chat with a first message. It opens, shows the message, and
   streams Muse Code's reply into the conversation. The reply box stays
   editable and Send stays available only while no turn runs.
3. Make Muse Code ask permission (a shell command or file write). The request
   is announced once with its summary, with how to answer it. The turn waits:
   nothing runs on its own. Press Ctrl+Shift+A. The permission dialog shows
   the request and details read-only, with Allow Once, Deny and Not Yet.
   Allow Once carries the turn on; Deny carries it on without that action.
   Not Yet leaves it waiting and says so.
4. Press Stop mid-turn. The window says the stop was requested and Send works
   again once it lands. Sending straight after a finished turn starts a new
   turn; nothing is sent twice.
5. Chat, Chat Settings, Chat Permissions on the owned chat shows Muse Code's
   own mode, no kept rules, and the same answering instructions. Change
   Permission Mode says Muse Code chats always use Muse Code's own mode.
6. View, Show Sessions, Muse Code Chats lists the new chat with the Chat
   Place session identity. Externally created Muse conversations stay
   read-only: Send, Stop, Answer and permanent deletion must not become
   available for them, and no permission dialog may open for them.
7. Restart The Chat Place and open the owned chat again. Its identity and
   history are stable; it remains a Chat Place session and can send again.
8. Make Muse Code unavailable (rename or remove its executable) and restart.
   Startup, other providers and shutdown must not break. Creating or sending
   in a Muse chat reports that execution is unavailable, without raw
   protocol data. Restore Muse and press F5 to confirm recovery.
9. Close the window while a Muse turn runs and while a permission request
   waits. The Muse host must exit; no late dialog or announcement may land
   after the new chat is open.

## Visual and platform checks

Run the muse-new, muse-approval and chat-permissions-muse probe surfaces in
a Windows test VM at light 100% and one High Contrast variant. Review
clipping, overlap and measured contrast. Walk real Enter, Tab, Shift+Tab, F6
and Escape; verify focus, names and announcements separately with both screen
readers. Record Mac runtime and Linux Orca CI results separately. Do not
change the user's desktop theme to simulate a VM run. New pictures need
descriptions in testing/visual/descriptions.md, written from the pictures.

## Evidence

Automated provider, transport, store and dialog-plan checks run in this
sandbox (no wx, no Muse binary): all Muse modules plus the provider store
pass. Native execution verification (owned create/stream/stop plus one
allow and one deny round-trip against installed Muse, synthetic content
only, clean host shutdown) still needs the installed build and sign-in.
Manual screen-reader and VM visual results must be recorded when performed.
