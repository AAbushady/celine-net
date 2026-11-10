---
name: routines
description: The Routines: what runs on its own at a time, read, change and start them;
  their runs with their results, and the triggers.
---
A Routine is a prompt that runs on its own at a time of day (or a cron line), in a
conversation of its own; {{username}} sees them on the Routines page.

routines__list lists them (name, label, when, on or off, the newest run) and the
triggers: each trigger's name, label, on or off, when it last fired and the id of its
background task's conversation (one per trigger, every fire in it).
routines__list(day="2026-01-09") lists every routine's runs of that day (midnight to
midnight, what the Routines page shows under it), routines__list(runs=10) the newest 10 runs of all
routines: each run:<id> with its routine, time, state, error or reason and its result's
last line. routines__read(name) gives one whole: every setting, the prompt, the
reviewer prompt and its newest runs, each the same way. A run's whole talk:
past_conversations__read(conversation="run:<id>"); a trigger's:
past_conversations__read(conversation="<trigger name>"). routines__write makes or changes one
with the checks of the Routines page: a change changes only what you give (a prompt you
give replaces the old one whole, so read it first), a new one needs label, prompt and
time or cron. When it is not saved, nothing changed: the error names the field and what
it takes. routines__run(name) starts a run now. What you change and start shows as yours
in Events and in the routine's history.

A routine with a reviewer prompt starts its run as a talk. You answer the routine's
prompt with one line, and the reviewer answers it as a message in the run's conversation,
from system:<the reviewer's name>. The reviewer is a call to the run's own model with the
reviewer prompt and what the run asked of you; it reads the notebook, past conversations
and the chat archive, and it changes nothing. The two of you alternate for at most
`turns` of its lines (it may end sooner), then you are asked for your final answer. That
answer alone is the run's result; the reviewer's lines stay in the run's conversation.

Bastet asks {{username}} before every change but switching one off (unless they took
routines off her list): her banner with Approve and Deny shows on the page of the
conversation you call from (your chat, a run, a mission, a task), and a notice takes
them there. The call waits for their answer; a run waits as long as its time limit lets it.
The result says whether they said yes; after a no or no answer nothing changed. When
they ask where to
approve, it is on that page.

A run has an address on the intercom: run:<id> (routines__read lists them), or
routine:<name> for its newest run. Write there to reach a run while it works.

A prompt may hold {{date}}, {{day_before}}, {{weekday}}, {{mood}}, {{on_mind}} and
{{result:<routine>}} (that routine's answer of the same night).
