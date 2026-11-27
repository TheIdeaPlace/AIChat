# Codex workflow listening checks

Use an expendable Codex chat started here. Repeat with JAWS and NVDA on Windows,
VoiceOver on macOS, or Orca on Linux. Automated CI checks do not prove human speech.

## First: set up a connected tool in Codex

Checks 4 and 5 need a connected tool (an MCP server) configured in Codex itself. Signing in to
Codex doesn't add one, and The Chat Place has no Add Server form: if `codex mcp list` shows
nothing, Tools, Connected Tools (MCP) has nothing to turn on. Add one in a terminal.

- **A harmless local test server**, MCP's own "everything" server (it needs Node.js; its tools
  echo text and add numbers). On Windows these work as written from PowerShell or Command
  Prompt; Codex finds `npx` itself, so no `npx.cmd` or `cmd /c` is needed:

  ```
  codex mcp add everything -- npx -y @modelcontextprotocol/server-everything@2.4.0
  codex mcp list
  ```

  The list shows everything, npx, enabled, Auth Unsupported. Codex downloads the server the
  first time a chat starts it. The version is pinned so the tool count below holds: without
  `@2.4.0`, `npx -y` fetches the newest version, which can have a different number of tools.

- **A web service that signs in through your browser**, for the Sign In check. It must support
  OAuth dynamic client registration. GitHub's (`https://api.githubcopilot.com/mcp/`) doesn't:
  `codex mcp add github --url …` fails with "Dynamic client registration not supported", and
  Sign In in The Chat Place says "Couldn't start signing in to github. This service doesn't
  support signing in through Codex. Give Codex a token for it instead: …", naming
  `--bearer-token-env-var`. Notion's does support it. Add it by
  editing `config.toml` (in `CODEX_HOME`, normally `~/.codex`), not with `codex mcp add`, which
  starts its own sign-in at once for a web server and would leave nothing for Sign In to do:

  ```
  [mcp_servers.notion]
  url = "https://mcp.notion.com/mcp"
  ```

Then, in a Codex chat started here: Tools, Connected Tools (MCP) (Alt+T, C). Codex lists
configured servers first ("everything: off for this chat, configured Codex MCP server"), then
the ChatGPT apps your account can use (three to five in November 2026), then one row, "More
ChatGPT apps: 5,022 more aren't connected to your account. To use one, connect it in ChatGPT's
settings, under Apps, then press Refresh here." The list's name counts only the servers and
apps, not that row. The first open after The Chat Place starts takes most of a minute while
Codex fetches ChatGPT's app catalog; opening it again takes a few seconds, and so do Reconnect
and the refresh after a sign-in. Only the Refresh button fetches the catalog again (most of a
minute). Sign In on everything says "everything: This tool doesn't need signing in: Codex
starts it on this computer without an account." and asks Codex nothing. Select the tool and
choose **Copy with Tool On** (Alt+O). It's the only way to
turn a tool on in a Codex chat. In the copy it opens, the row says "everything: connected,
configured Codex MCP server, 19 tools" with server-everything 2.4.0 (measured 2026-11-27 through
The Chat Place's own Codex code, Codex 0.163; another version of the server can list another
number), and Details lists them. Ask for a task by name. The
server's adding tool is get-sum, not "add": "Use the everything tool's get-sum tool to add 2 and
3." Codex answers 5. Remove a test server afterward with `codex mcp remove everything`.

Codex doesn't ask before calling the everything server's tools, which declare themselves
read-only or harmless, even in a chat that asks before changing files. For check 5, make it ask
by adding this to `config.toml`, then making a new tools copy:

```
[mcp_servers.everything.tools.get-sum]
approval_mode = "prompt"
```

Codex then sends a connected-tool question: Allow the everything MCP server to run tool
"get-sum"?, the arguments, and "Your answer covers this call only." Send lets that call run,
and Decline refuses it. `codex mcp remove everything` removes this setting too.

Verified on Windows on 2026-11-26 with Codex 0.163 and everything 2.3.1, in a throwaway
`CODEX_HOME`. The commands ran in PowerShell 7, Windows PowerShell and Command Prompt.
`codex exec` called get-sum. The Chat Place's provider code was driven without a window:
listing, Copy with Tool On, a turn calling the tool, the prompted question answered with both
Send and Decline, and Sign In's start, which fails for GitHub and returns an address for
Notion. No sign-in was finished. On 2026-11-26 the fixed list (three to five usable apps and
the More ChatGPT apps row, about 44 seconds cold, 3 seconds from Codex's copy) and Sign In's
reasons for GitHub and everything were checked the same way with Codex 0.163.

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
   Sign In. The question is titled "Sign In to" and the tool's name, and opens on
   the Address box, whose first line is "Opens" with the host, then the whole address.
   Decline or Escape leaves the tool off, creates no chat, says "Sign-in page was not
   opened." and puts you back on Sign In. If you finish sign-in, hear the result;
   Reconnect an enabled tool to use it. Repeat from an externally started Codex chat:
   it stays read-only, with no Turn Off for This Chat button, and Reconnect says
   there's nothing to reconnect there and that Sign In still works.
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
**More ChatGPT apps** isn't a tool either: Sign In, Reconnect and Change Tool on it only say its
message.
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

The preview writes navigation feedback (for example, Enter in Message telling you to Tab)
to its terminal; it does not use the running app's speech settings or announcement routing.
Check those spoken navigation hints in the real app.

These previews prove no service behavior. The existing native probe
`tools/probe_codex_workflows.py --out <path-outside-checkout>` exercises real Codex consent,
reload and local OAuth against disposable synthetic services without an account or paid
model request. It is an automated protocol check and does not open these dialogs. A real
service is still needed to verify its browser flow and actual task result.
