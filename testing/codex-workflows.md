# Codex workflow listening checks

Use an expendable Codex chat started here. Repeat with JAWS and NVDA on Windows,
VoiceOver on macOS, or Orca on Linux. Automated CI checks do not prove human speech.

1. While Codex is working, queue two different messages. In Messages, select the
   first, press the Applications key (Shift+F10), and choose Edit Queued Message.
   Expect the turn to continue, that message before your current draft, and focus
   in Your message. Hear that sending it again puts it after any remaining queue.
   Add an image and a skill before queueing; both should survive editing. The text
   box should contain your words, without generated attachment or skill lines.
   Tab to Attachments and delete the image: hear its removal, then send and check
   that no image path or image input was sent. A skill token you typed stays in
   the draft; delete that token and its attachment to remove the selected skill.
2. On a queued message press Delete. Hear which chat's message was removed. The
   draft and running turn stay available. Ctrl+Enter on a queued message should
   say it was steered after Codex accepts it; an already running command continues.
3. Ask Codex to use a subagent for a small task. With sounds on, the parent's
   sound settings apply to the child's tools and successful file edits. In Lists,
   Approvals, a child automatic-review decision has Codex subagent in its details.
   Allowed decisions stay quiet with tool activity off; a denial is announced.
4. Tools, Connected Tools: select a configured HTTP server that's off and press
   Sign In. Hear the actual address before opening it. Cancel leaves the tool off
   and creates no chat. If you finish sign-in, hear the result; Reconnect an enabled
   tool to use it. Repeat from an externally started Codex chat: it stays read-only.
   A real third-party account is required to check that service's browser flow.
5. With a disposable connected tool that asks permission, open Answer. Focus starts
   on Message. Read its tool arguments by line. Tab reaches Send, Decline and Answer
   Later, with their names; Escape leaves it waiting. Decline prevents the call.
   Send allows this call only, without saving a session or permanent grant. With
   arguments longer than 4096 characters, hear the preview and the number of
   characters not shown, followed by the one-call scope.
