"""The words and the tone the app shows for a state: one table per idea, the server's.

A row or an event the page gets carries its state as {words, tone} from here, beside the
plain code the page acts on. The page shows the words in that tone and keeps no table of
its own, so a list, a page's head, a report's heading and what the agent's tools say
cannot drift apart. A tone is one of TONES (the page's StateWord tones)."""
from __future__ import annotations

TONES = ("running", "warn", "error", "success", "muted")

# a mission's status (missions.status)
MISSION = {"active": ("Running", "running"), "needs_you": ("Waiting for you", "warn"),
           "done": ("Done", "success"), "stopped": ("Stopped", "muted")}


def state(table: dict[str, tuple[str, str]], code: str | None) -> dict | None:
    """`code` of `table` as a row carries it: {words, tone}; None for no code or one the
    table does not hold."""
    got = table.get(code or "")
    return {"words": got[0], "tone": got[1]} if got else None
