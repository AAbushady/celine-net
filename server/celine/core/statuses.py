"""The statuses the app shows, each set an enum, and how every member is displayed.

A status is stored and sent as its value (a StrEnum member is that string, so the
database's CHECK and the API's JSON hold what they held). `display` is how the page shows
it: its words and their tone. A row or an event carries both, the value to act on and
the display to show, so the page keeps no table of its own and a list, a page's head, a
report's heading and what the agent's tools say cannot drift apart.

A status set that is not here yet is still bare strings where it is used; it moves here
as an enum when its words do."""
from __future__ import annotations

from enum import StrEnum


class Tone(StrEnum):
    """The tone a status is shown in (the page's StateWord tones)."""
    RUNNING = "running"
    WARN = "warn"
    ERROR = "error"
    SUCCESS = "success"
    MUTED = "muted"


class MissionStatus(StrEnum):
    """Where a mission stands (missions.status)."""
    ACTIVE = "active"
    NEEDS_YOU = "needs_you"
    DONE = "done"
    STOPPED = "stopped"

    @property
    def display(self) -> dict:
        """{words, tone}: this status as a row carries it for the page."""
        words, tone = _MISSION[self]
        return {"words": words, "tone": tone.value}


_MISSION = {
    MissionStatus.ACTIVE: ("Running", Tone.RUNNING),
    MissionStatus.NEEDS_YOU: ("Waiting for you", Tone.WARN),
    MissionStatus.DONE: ("Done", Tone.SUCCESS),
    MissionStatus.STOPPED: ("Stopped", Tone.MUTED),
}
