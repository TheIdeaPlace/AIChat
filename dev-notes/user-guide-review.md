# User guide review: agreed changes

A review of the user guide by Claude and Codex in an agent conversation ("review user guide"),
22 October 2026, against version 0.2.0. Each read the whole guide on its own, checked claims
against the code, then the two lists were merged here. This is for Kelly to decide what to do.
Done: all of section 1 and all of section 2. Section 1 item 1 (Codex "preview") and section 2
Gaps item 5 (more in Troubleshooting, which "When something isn't right" became) were done in
#389; the rest of sections 1 and 2, with the shortcut check, in the user guide review PR. The
README got only the same corrections (version, issue numbers, Codex loading on Enter,
Claude-only features, where the app writes, the shortcut table). Section 3 is still open.

- **The guide** is `thechatplace/assets/user-guide.md` (Help, User Guide). `docs/user-guide.html`
  and the other `docs/` copies are made from it by `python tools/make_docs.py`.
- **The README** is the full reference, and also the user guide named in `CLAUDE.md`.

Line numbers are the guide's unless marked "README".

## 1. Agreed corrections: wrong, contradictory or misleading

These say something that isn't true, or that contradicts the app. Fix them first.

1. **Codex setup says it needs a preview turned on.** "Before you start" (line 26) says Codex
   needs "the preview enabled in Settings"; README line 785 says "the preview needs". Codex chats
   appear on their own when Codex is installed and signed in (line 413), and the setting is
   "Include Codex chats (restart required)". Remove "preview" from both.
2. **Codex tasks are said to be Claude-only.** Lines 192–195 and 265–266 say tasks are captured
   in Codex chats run here, but lines 311–312 say tasks are listed only for Claude chats. Fix
   311–312, and check the README and the shortcut descriptions for the same old restriction.
3. **Queued-message actions are described as if every chat has them.** Lines 559–563 say that
   on a queued message Ctrl+Enter sends it now, and its menu edits or removes it. In a Codex chat,
   these actions are unavailable: Send Now says messages go after the reply; Edit and Remove
   say to Stop the turn to return queued messages for editing (`main_frame.py`). Explain the
   shared behavior of queuing messages, then what each provider offers.
4. **Permanent deletion is promised for every chat started here.** Lines 938–940 say
   Shift+Delete deletes "one of The Chat Place's own sessions". Codex chats can't be deleted yet.
   Say it's Claude chats for now, and separate deleting an agent conversation (which keeps its
   chats) from deleting a chat.
5. **Claude-only features don't say they're Claude-only.**
   - Commands and skills (Ctrl+/): "These are for The Chat Place's own sessions" (line 578), but
     the picker says it isn't available for Codex chats yet.
   - Copy Session Link isn't available for Codex chats.
   - Remote Control and Your sessions on other computers should start by saying they're for
     Claude chats.
6. **Codex chats are said to load when selected.** Lines 418–419: "selecting a Codex chat opens
   its history automatically". Like any session, arrowing doesn't load it; Enter does. Say "Press
   Enter on a Codex chat to load its history."
7. **"The only files it writes are its own" is too broad.** Lines 1185–1187. Export writes
   where you choose, and From GitHub and A new worktree write into your project folders. Say
   where the app keeps its settings, that providers keep the conversations, and that exports,
   clones and worktrees are created in their selected or documented locations. Keep the promise
   that reading chats does not rewrite their transcripts or the desktop app's files. Distinguish
   this from providers saving new messages and explicit permanent deletion of a Claude chat's
   transcript.
8. **Whole-message reading isn't qualified for the Mac where it's described.** Lines 126–130 say
   the screen reader reads the whole message while arrowing; the VoiceOver exception is left to
   Good to know (line 1188). Say so where it's described.
9. **"Unavailable" status isn't explained.** The guide gives only needs you, working and idle.
   A Codex chat can say unavailable when its live state can't be checked, which doesn't mean its
   work has finished. Explain it with the other states.
10. **The README's status block is out of date.** README line 21 says version 0.1.5; the app is
    0.2.0.
11. **Typo:** "An Codex chat" at lines 400 and 547.
12. **Issue numbers in user text.** "(#318)" at line 266; the README has many more (#209, #279,
    #231 …). Take them out of text written for users.
13. **One point is said twice.** In Choosing what each line says, typing a letter no longer
    jumping to a title is said at line 891 and again at line 909. Keep one.

## 2. Agreed improvements: structure, language and gaps

### Structure

1. **Break up the Codex part.** "Local Codex chats" (lines 411–473) is one paragraph of about 25
   lines, then four dense ones. Suggested headings: Seeing Codex chats; Starting one and
   replying; If the connection is lost; What a Codex chat may do (tools and rules); Setting up
   the Windows sandbox.
2. **Make permissions one chapter.** "Answering permissions and questions" and "Permissions:
   what a chat may do without asking" overlap, and Codex detail is repeated in three places. The
   first section also opens with Codex, and lines 742–745 (changing the policy) don't belong
   there. Suggested: one chapter, with **Answer a request** first (the everyday steps, short),
   then **Review or change permissions**, then each provider's detail and how the rules combine.
3. **A new session: one field list.** It opens with a Codex paragraph and then says "The model
   names and permission modes below describe Claude chats" (lines 479–487). Add Provider as the
   first field, and give each provider's choices under Model, Effort and Permission mode.
4. **Usage in one place.** Usage and Context is a bullet under More things you can do, and Codex
   usage is a separate section (line 1002). Make it one "Usage and context" section.
5. **More things you can do repeats things.** Its Changed Files item repeats Lists. Move or
   cut what's covered elsewhere.
6. **The desktop app's "needs you"** is explained under Claude permissions (line 769). It belongs
   with the session list's states (Finding it in the list).
7. **Kinds of session.** "The four kinds side by side" no longer covers every Kind: Codex adds
   editor, command-line and external, and an agent conversation is a Kind too. Say "the Claude
   kinds" or bring the table up to date, and rewrite the sentence before it.
8. **What works for each provider.** A short table of what works for Claude and for Codex today
   (reply, queue actions, commands, attachments, delete, links, Remote Control, Lists kinds,
   permissions), with the same limit also said beside each procedure.

### Gaps

1. **A quick start.** "Your first chat": install, sign in, Ctrl+N, send, hear the reply,
   Ctrl+Shift+A when asked, Escape back to the list.
2. **A Settings section.** The guide sends you to Settings about 15 times but never lists it.
   Cover announcement level and which sessions are announced; reading back your own messages;
   formatted and whole-message reading; speech engine and rate; notifications; recent folders
   and Clear Recent Folders; Remote Control defaults; Claude task lists; Include Codex chats;
   automatic agent hand-offs; and the update-installed notice. Give Windows Alt keys where
   available, the platform differences, whether a setting is for every chat or one, and when a
   change counts. Clear Recent Folders takes effect after its own question, and Cancel in
   Settings doesn't undo it.
3. **The status bar's parts.** Left and Right move between them, and the context, needs you and
   update buttons. Only the README has this.
4. **Export as a short procedure.** It starts in Documents (when there is one), named after the
   chat and the date; you choose the folder and the format (Markdown, web page, plain text);
   you hear where it saved. Tool activity is included when Show Tool Activity is on.
5. **More in "When something isn't right":** I don't hear announcements (the Speech problem box
   in Settings, `speech.log`); Codex chats don't appear; a Codex chat is read-only (the Windows
   sandbox); a Codex chat lost its connection; a turn was stopped because it would run on Fable.
6. **Terms.** Define each where it's first needed: turn, context, compaction, tool activity,
   worktree, provider, kind. A short glossary at the end for later reference.

### Language

1. **Rewrite the Codex text in the guide's plain voice.** It reads like a spec: "rule inventory
   cannot be checked", "approval amendments", "this connection receives a usage report",
   "Revisiting a chat preserves its running turn", "Requests The Chat Place cannot present
   safely", "Verification and saving must succeed before the new policy can send a turn". Say
   what you'll notice and what to do.
2. **One set of words.** Use "chat" in the text; keep the exact labels the app shows (New
   Session, Session List); explain "session" once. Replace "owned" (lines 193, 1011 and others)
   with "chat started here".
3. **Vague lines.** "Features that belong to one provider name it; their availability depends on
   the selected chat" (line 10) says little; the provider table above would replace it.
4. **Command results and compaction** (lines 157–169) is dense: "remains tool activity",
   "without interrupting sent-message read-back". Rewrite plainly.
5. **The Mac Delete key.** The guide says Fn+Delete, "Delete or Fn+Delete" and "Backspace on
   Mac". These are partly right: the session list needs forward Delete (Fn+Delete on a laptop),
   while Session List Columns and Chat Permissions also take the ordinary Delete key. Say the
   real key for each place, worded the same way each time, rather than one key everywhere.

### Shortcuts

- Check each shortcut's "Where" against `ui_text.py` `SHORTCUTS` (for example, whether Ctrl+N
  works only from the session list). Ctrl+O is listed twice, for the selected and the loaded
  session: keep both if each says which.

## 3. Decisions for Kelly

1. **The README's scope.** Its "What it does" is one heading with about 40 long bullets (README
   lines 40–781), so heading keys can't move through it. Either give it subheadings, or make the
   README shorter and point to the guide for how to use the app.
2. **Developer content.** Run from source, Build it yourself, Releasing, Files, and mentions of
   "Kelly's PC" and the vmtest VM sit in the user-facing README. Move them to a CONTRIBUTING or
   developer document, or leave them.
3. **Uninstalling and what's left behind.** Neither document explains uninstalling. Add steps
   for each platform and verify which app data, provider histories, exports and project folders
   remain?
4. **Mac paths in How it works** (README). It gives only Windows paths. Add the Mac ones, or
   move both platforms' storage paths into a separate reference?

## 4. Doing the work

- Edit `thechatplace/assets/user-guide.md`, then run `python tools/make_docs.py` to make the
  `docs/` copies. Keep the README and `ui_text.py` `SHORTCUTS` in step with whatever changes.
- Run the documentation consistency checks: `.venv\Scripts\python -m pytest -q tests/test_docs.py`
  on Windows, or `.venv/bin/python -m pytest -q tests/test_docs.py` on a Mac.
- After restructuring, check that every key and menu path the guide names still matches the app
  and `SHORTCUTS`, that headings nest properly (H2, then H3) so heading navigation works, and
  that links between sections still go somewhere.
- Read the rebuilt Help, User Guide page with JAWS and NVDA, by heading and by table, and with
  VoiceOver on a Mac.
- Changes to user-visible text count as user-visible changes: add an entry in the open release
  notes file.
