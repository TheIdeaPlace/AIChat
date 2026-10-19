# Checking Codex task lists (#318)

These are the keyboard, focus and speech checks for Kelly after the automated tests
and VM visual probes. Use a Codex chat run through The Chat Place with enough work
for Codex to report a task list; a short reply may have no task list.

1. Press Ctrl+Shift+J (View, Tasks). Arrow through the steps and press Enter to read
   a step's details. Check that its name, state and position are spoken and that
   closing Lists returns focus to the control you used to open it.
2. While work is running, press Ctrl+Shift+T. It should name the current step and
   completed count. Open session Properties from the session list and check the
   same progress line. Waiting for permission should not say the task stopped.
3. At the Full announcement level, listen for a newly completed step: "Done: ...".
   It should queue behind current speech. Repeated updates and reopening the chat
   should not announce all previously completed steps again. At a lower announcement
   level, task completion should be silent.
4. If Codex revises or reorders its plan, reopen Tasks and check that the list follows
   the new order. A completed step that only moved should not be announced again.
5. Stop a turn with an unfinished step. Tasks should retain the reported states and
   Turn Status should say "Stopped at task ..." rather than marking everything done.
6. Close and reopen The Chat Place, then reopen the owned chat. The latest captured
   steps should still be listed. This does not promise recovery of older checklists.
7. Send a short follow-up that produces no task update. Tasks and Properties should
   retain the previous list. Turn Status should leave out an old or finished list
   once the turn is idle. If a later turn completes a step, hear Done before the reply.
8. Switch Show items from to This message. Only messages in the turn where the list
   was reported should show its steps, both before and after reopening the chat.
9. Open an older Codex chat or one run elsewhere that has no captured list. View,
   Tasks should explain that task lists are kept for chats run here. An owned chat
   where Codex has never reported tasks should simply say "No tasks in this chat." Its proposed plan text,
   if any, should remain ordinary conversation content rather than a checklist.

Claude's "Let Claude keep a task list" setting should retain its existing label and
behavior and should not control whether Codex updates appear.
