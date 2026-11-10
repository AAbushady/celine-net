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


class TurnState(StrEnum):
    """Where a turn stands (turns.state)."""
    QUEUED = "queued"
    PREPARING = "preparing"
    STREAMING = "streaming"
    TOOLS = "tools"
    DONE = "done"
    ABORTED = "aborted"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CAPPED = "capped"


def sql_list(states) -> str:
    """Statuses as an SQL list for `state IN {...}`: ('queued','preparing')."""
    return "(" + ",".join(f"'{s.value}'" for s in states) + ")"


# a live turn holds its conversation; an ended one is over
LIVE_TURNS = (TurnState.QUEUED, TurnState.PREPARING, TurnState.STREAMING, TurnState.TOOLS)
ENDED_TURNS = (TurnState.DONE, TurnState.ABORTED, TurnState.FAILED, TurnState.INTERRUPTED,
               TurnState.CAPPED)
# a claimed turn has a worker: live, and no longer waiting in the queue
CLAIMED_TURNS = (TurnState.PREPARING, TurnState.STREAMING, TurnState.TOOLS)
LIVE_TURNS_SQL = sql_list(LIVE_TURNS)
CLAIMED_TURNS_SQL = sql_list(CLAIMED_TURNS)


class RunState(StrEnum):
    """Where a routine's run stands (runs.state)."""
    WAITING = "waiting"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    STOPPED = "stopped"

    @property
    def display(self) -> dict:
        """{words, tone}: this state as a row carries it for the page."""
        words, tone = _RUN[self]
        return {"words": words, "tone": tone.value}


_RUN = {
    RunState.WAITING: ("Queued", Tone.MUTED),
    RunState.RUNNING: ("Running", Tone.RUNNING),
    RunState.DONE: ("Done", Tone.SUCCESS),
    RunState.FAILED: ("Failed", Tone.ERROR),
    RunState.SKIPPED: ("Skipped", Tone.MUTED),
    RunState.STOPPED: ("Stopped", Tone.MUTED),
}
# a row whose work waits on the user (a request in it), whatever its own status
WAITING_FOR_YOU = {"words": "Waiting for you", "tone": Tone.WARN.value}
# a slot of a schedule that has not come yet: no run, so no RunState
SCHEDULED = {"words": "Scheduled", "tone": Tone.MUTED.value}


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
