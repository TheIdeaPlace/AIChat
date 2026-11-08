# The Chat Place's sounds: the rules and the set

The first set of sounds (#250, 0.5 previews) took ten of its fifteen sounds from QuickMail and gave
them different meanings. Filed (QuickMail: a message was archived) meant a conversation was
compacted. Matched (a mail rule matched) meant a message from another session. Reminder (a calendar
reminder) meant a usage warning. Disconnected (the connection was lost) meant service trouble.
Sent, Queued, Error, Arrived, Attention and Failed came across too. Steering had no sound of its own,
and Ask Aside borrowed Reply finished and Message not sent. A sound that means one thing in one app
and another thing in the next is worse than no sound. These rules replace that set.

## The rules

1. **One sound, one meaning.** Every event has a sound of its own, made for it. No two events share a
   sound, and no sound is borrowed from another app. QuickMail keeps its sounds; The Chat Place has
   its own. If two apps ever share a sound, it's because the event means exactly the same thing in
   both, and Kelly has agreed to it.
2. **Families share a pattern, so a new sound is half-learned.** What a sound is about decides its
   shape:

   | Family | Pattern | Level |
   | --- | --- | --- |
   | Work going on | One or two very short ticks, high, or one low hum | Soft, under speech |
   | Your message | Two or three quick notes in the middle | Normal |
   | Answers | Notes rising to an open fifth | Normal; another chat's softer and lower |
   | Needs you | The same note fast, three times, then a leap up | Loudest; goes first |
   | Something went wrong | Falling, or bending down a half step, or wavering | Normal |
   | Housekeeping | A chord settling into one note | Normal |
   | Another session | A knock: one note twice, then up | Normal |

3. **Rising means something started or arrived for you. Falling means something didn't work.** A
   repeated note means waiting, or wanting you.
4. **Related events sound related.** Steering is the send sound with a bend in it. A queued message
   going out is the queued sound rising into a send. Ask Aside's failure starts on the same note as
   its answer and then falls.
5. **Short.** Work-going-on sounds are under a tenth of a second plus a short ring. Nothing is longer
   than a second.
6. **A tone doesn't change the meaning.** Glass, Soft bell and Wood play the same notes in a
   different timbre, so a sound means the same thing in any of them.
7. **A new event needs a new sound, made by these rules, and a line in this file.** Never point a new
   event at an existing sound.

## The events and their sounds

Notes are named with their octave (C5 is the C above middle C). Times are in seconds after the
sound starts.

### Work going on

| Event | When | Sound (`thechatplace/<tone>/…`) |
| --- | --- | --- |
| Tool ran | A tool starts in a chat: a command, a search, an MCP tool | `tool`: one tick, E6. Very soft and short |
| File changed | A file change has been made | `edit`: two ticks rising, E6 then A6 |
| Still working | 20 quiet seconds of a turn in the loaded chat | `working`: one low hum, C3. The softest |

### Your message

| Event | When | Sound |
| --- | --- | --- |
| Message sent | Your message starts a turn, or goes to a chat waiting only on background work | `send`: D5 then A5, a quick rising fifth |
| Message queued | Your message waits for the turn to finish | `queue`: D5 three times, each softer: held |
| Message steered | Your message goes into the running turn without stopping it | `steer`: D5, E5, A5: the send with a bend in it |
| Steer taken | Claude has your steered message, at its next step. (Codex accepts one as it's sent, so there only Message steered plays.) | `steertaken`: A5 then D6, soft: carrying on from where the steer ended |
| Queued message sent | A message that was waiting goes, on its own, when the turn ends | `queuesent`: D5 twice then A5: the held note rising into a send |
| You stopped a turn | Stop, from you | `stop`: one low damped C4 |

### Answers

| Event | When | Sound |
| --- | --- | --- |
| Reply finished | A turn in the loaded chat finishes with a reply | `reply`: G4, D5, G5, rising open fifths |
| Another chat finished | The same, in a chat that isn't loaded | `otherreply`: G3, D4, G4: the same shape, an octave lower and softer |
| Ask Aside answered | Your aside question has its answer | `aside`: B4 then E5, quiet |

### Needs you

| Event | When | Sound |
| --- | --- | --- |
| Needs you | A permission, a question, a form or a plan waits for you | `needsyou`: A5 three times, fast, then E6 |

### Something went wrong

| Event | When | Sound |
| --- | --- | --- |
| Turn failed | A turn ends in an error | `turnfailed`: D5, A4, F4, falling minor |
| Message not sent | A message comes back to you unsent | `notsent`: E4 bending down to E-flat 4 |
| Ask Aside not answered | Your aside question couldn't be answered | `asidefailed`: B4 falling to F4: the aside's first note, then down |
| Service trouble | The AI is retrying, moved to another model, or a turn ended without you stopping it | `trouble`: D4 and C-sharp 4 wavering, four quick notes |
| Usage or context warning | Close to a usage limit, or the context is filling up | `warning`: E6, C6, E6: high, alternating |

### Housekeeping and other sessions

| Event | When | Sound |
| --- | --- | --- |
| Conversation compacted | The conversation was compacted | `compacted`: C5, E5 and G5 together, settling into C5 |
| Message from another session | Another session sent this chat a message | `peer`: E5 twice like a knock, then B5 |

## How they're made

`sounds/generate.py` in TheIdeaPlace/app-kit builds every sound from these notes, in each tone, with
no recordings and no licences. The Chat Place's set is in `sounds/thechatplace/<tone>/`, apart from
QuickMail's. app-kit's `sounds/apps.json` lists each event with its sound, and The Chat Place copies
the files into `thechatplace/assets/sounds/`. `sounds/measure.py` checks every file's format, length,
level and that it starts and ends without a click, since nobody can check by listening in CI.
Whether they sound right is for Kelly to hear.
