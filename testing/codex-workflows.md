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

## Before checks 4 and 5

These are integration checks, not features supplied by signing into Codex alone. Use a
ChatGPT-subscription Codex chat. Check 4 requires a configured HTTP MCP server that supports
browser OAuth and an account for that service. "Off" means off for this chat, not signed out.
An app's installation page does not test the standalone server sign-in path. Check 5 requires
a harmless tool action that Codex actually asks a human to approve; enabling a server does
not guarantee a prompt. Use test data, and a permission mode with human review. A command
approval does not test connected-tool arguments.

If only **Apps unavailable** is listed, neither integration check is ready. This is a failed
inventory lookup, not a tool. Do not try Sign In on it. There may also be no configured servers.
After signing into an off server, use **Copy with Tool On** to use it. Reconnect alone cannot
enable it. An externally started chat means one created in another Codex client; signing in
must leave that original chat's tools and read-only status in The Chat Place unchanged.

## Practice the dialogs without a connected account

From a source checkout with the development dependencies installed, run these separately:

```
python tools/preview_codex_consent.py sign-in
python tools/preview_codex_consent.py approval
python tools/preview_codex_consent.py long-approval
```

These open the real dialog with synthetic requests. They do not open a browser, call a
service, save an approval, or create a chat. This developer preview is not included as a menu
in packaged builds. It checks the following with JAWS/NVDA, VoiceOver, or Orca:

- Sign-in: initial focus is **Address**, with the real example.com address. Tab reaches
  **Message**, **Open in Browser**, **Decline**, and **Answer Later**. Choosing Open in Browser
  only records the practice answer; this preview deliberately opens nothing.
- Approval: initial focus is **Message**. Read the note arguments by line, then Tab to
  **Send**, **Decline**, and **Answer Later**. Escape closes this practice window. In a real
  chat it leaves the request waiting so Ctrl+Shift+A can reopen it.
- Long approval: hear the shortened arguments, the number of characters not shown, and
  "Your answer covers this call only."

These previews prove no service behavior. The existing native probe
`tools/probe_codex_workflows.py --out <path-outside-checkout>` exercises real Codex consent,
reload and local OAuth against disposable synthetic services without an account or paid
model request. It is an automated protocol check and does not open these dialogs. A real
service is still needed to verify its browser flow and actual task result.
