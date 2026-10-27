# Steering a running turn (#238, #239)

What was checked before building Steer, and how the turn knows what happened.

## Claude: `claude -p` stream-json, Claude Code 2.1.296

Run headless as The Chat Place runs it (`--input-format stream-json --output-format stream-json`,
`--permission-prompts host`), with a prompt that makes three `sleep 6` Bash calls one after
another. A user message written to stdin during the turn, **with no `interrupt` before it**:

- **Written during a tool call:** Claude took it in after that tool call finished, followed it
  ("skip the third command"), and the turn ended with **one** result covering both messages.
  This is real steering, not queueing.
- **Written while Claude was writing its final reply** (a no-tools prompt): no step came to take
  it in at, so Claude finished the reply (result 1), then answered the message as its own turn in
  the same process (result 2).

From the result count alone the two can't be told apart, and the turn's bookkeeping (a result
per message, from Send Now, #83) would wait for ever for the second result in the first case.

`--replay-user-messages` settles it: Claude Code echoes each message from stdin
(`{"type": "user", "isReplay": true, …}`) at the moment it takes it in, in order. Observed:

| Case | Order of events |
|---|---|
| Steer during a tool call | echo(prompt), tool, echo(steer), tool, result |
| Steer during the final reply | echo(prompt), result, echo(steer), result |
| Send Now (interrupt, then message) | echo(prompt), tool, result (error_during_execution), echo(message), result |

So `TurnRunner` counts **a result as answering every message taken in since the last result**.
With no echoes at all (an older Claude Code, a test fake) it falls back to one result per message.
A steer still untaken when a result comes is marked late. Its `steer_taken` event says so, and
the UI says "had finished, and is answering your message now".

The echo isn't shown: the transcript has the message, in the place Claude took it in.

## Codex: app-server `turn/steer`

Documented at the app-server's "Steer an active turn": `turn/steer` with `threadId`,
`expectedTurnId` (must match the active turn) and `input` appends to the running turn without
starting a new one or emitting `turn/started`, and returns `{"turnId": …}`. It fails with no
active turn, and can't change turn settings. `turn/interrupt` is separate (Stop uses it). Nothing
documented says when steered input reaches the model, so the UI says "Steered" on acceptance.

## The behaviour chosen

One command and key for both providers: Steer, Ctrl+Alt+Enter, from the reply box and on a
queued message, checked before Ctrl+Enter so it can't become a plain Send. With no turn running
it's Send. A steer that can't be delivered comes back to the reply box (or stays queued) with the
reason; it's never sent twice, and a Codex steer is never turned into a new turn on its own.
Copilot chats don't steer yet.
