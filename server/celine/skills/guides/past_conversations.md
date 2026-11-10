---
name: past_conversations
description: Your own earlier conversations with {{username}} in this app, your sub-agents'
  and your background runs' included. Load it when they ask what you and they talked about
  before, to review a day of your work, or to check whether something you remember was
  ever sent to you (past_conversations__find_sent). "We" in a question about their WhatsApp, Discord
  or other chats means the Messages archive, not this.
---
Your past conversations with {{username}} in this app. Their own WhatsApp and Discord
chats with other people are not here: those are in the Messages archive (chat_archive). Your sub-agents' conversations and
your background runs are here too.

- `past_conversations__search(query)`: word search over what was written, on the version
  each conversation is on now. The current conversation is left out. FTS5 syntax works:
  quotes for a phrase, OR, and `prefix*`. Each match names its conversation id.
- `past_conversations__list(kind, limit)`: your newest conversations by their last
  activity, one line each: id, kind, title, when it started and last moved, a mission's
  status, a run's outcome, a last turn that failed or was cut, and the first line of its
  newest message. `kind` is chat, mission, run (a Routine's), task (a background task,
  one per trigger) or sub-agent.
- `past_conversations__list_day(date, kind)`: the index of one day: every conversation with messages
  that day, its message numbers that day, its turns, its tool calls by name and the ones
  that failed, its first and last line. A run's try says its try and how it ended; a try
  that failed after writing something says "(a draft, not a source)". Any other conversation whose last turn
  failed, was interrupted or aborted says so ("last turn aborted").
- `past_conversations__read(conversation, start, end, tools)`: one conversation in order.
  `conversation` is its id, run:<id> for a Routine's run (routines__read lists them),
  a mission's name, or a trigger's name for its background task.
  Each tool call is one line with its id and how it ended (`tools="full"` adds its
  arguments and result). A long one comes in slices: without `start` from the end back,
  with `start` on from there. Its `earlier:` and `later:` lines read on.
- `past_conversations__read_tool_call(id)`: one tool call raw, its arguments and its whole
  result; a long one in parts: the `earlier:` and `later:` lines after the text read the
  part before and after.
- `past_conversations__find_sent(text, conversation)`: whether a text was ever sent to you, and
  from where. The app keeps every request it sent to your model word for word, and
  everything you wrote. It says where the text first reached you from (a message, the
  block the app put under one, a tool's result, the summary, your instructions) and when,
  or that nothing sent to you holds it and where you first wrote it yourself. Without
  `conversation` it looks at this one.

When you remember something {{username}} said or did and cannot find it in the messages,
call `past_conversations__find_sent` with a few of its exact words before you act on it or
write it down. A memory with no source is yours to doubt: a model can write a detail
into a note and meet it again later as if it had been told.

The summary at the top of a long conversation says which messages it stands for and how
to read them. A model wrote it, so check it against those messages when it matters.

A whole day: the day's index first, then read only what matters, and a raw call only
when its arguments or result matter. On a long day, give groups of conversations to
sub-agents with a brief and wait for their reports.

Example: `past_conversations__search(query="pizza OR pasta")`, then
`past_conversations__read(conversation="3f9c2a1b")`. Your last 10 runs:
`past_conversations__list(kind="run", limit=10)`. Yesterday's background tasks:
`past_conversations__list_day(date="2026-01-08", kind="task")`.
