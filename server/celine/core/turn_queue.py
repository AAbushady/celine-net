"""The order the queue of turns is taken in (turns/store.py claim), as SQL over the turns
table `t`: the user's turns first, then the oldest; turns queued in one write go in the
order they were made. A list that shows the queue orders it the same way."""
QUEUE_ORDER = "t.origin != 'user', t.created_at, t.rowid"
