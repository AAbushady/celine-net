"""The statuses the app shows, each set an enum, and how every member is displayed.

A status is stored and sent as its value (a StrEnum member is that string, so the
database's CHECK and the API's JSON hold what they held). `display` is how the page shows
it: its words and their tone. A row or an event carries both, the value to act on and
the display to show, so the page keeps no table of its own and a list, a page's head, a
report's heading and what the agent's tools say cannot drift apart.

A status set that is not here yet is still bare strings where it is used; it moves here
as an enum when its words do."""
from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any


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


class Shown(StrEnum):
    """A status set the page shows: every member has a display."""

    @property
    def display(self) -> dict:
        """{words, tone}: this status as a row carries it for the page."""
        words, tone = _DISPLAYS[type(self)][self]
        return {"words": words, "tone": tone.value}


class RunState(Shown):
    """Where a routine's run stands (runs.state)."""
    WAITING = "waiting"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    STOPPED = "stopped"


_RUN_SHOWN = {
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


class MissionStatus(Shown):
    """Where a mission stands (missions.status)."""
    ACTIVE = "active"
    NEEDS_YOU = "needs_you"
    DONE = "done"
    STOPPED = "stopped"


_MISSION_SHOWN = {
    MissionStatus.ACTIVE: ("Running", Tone.RUNNING),
    MissionStatus.NEEDS_YOU: ("Waiting for you", Tone.WARN),
    MissionStatus.DONE: ("Done", Tone.SUCCESS),
    MissionStatus.STOPPED: ("Stopped", Tone.MUTED),
}


class PlanStepState(Shown):
    """Where a step of a plan stands (plans/store.py)."""
    TODO = "todo"
    DOING = "doing"
    DONE = "done"


_PLAN_STEP_SHOWN = {
    PlanStepState.TODO: ("To do", Tone.MUTED),
    PlanStepState.DOING: ("Running", Tone.RUNNING),
    PlanStepState.DONE: ("Done", Tone.SUCCESS),
}


class AgentState(Shown):
    """Where a sub-agent stands (sub_agents/store.py `look` works it out; nothing stores it)."""
    QUEUED = "queued"
    WORKING = "working"
    DONE = "done"
    STOPPED = "stopped"
    FAILED = "failed"
    PAUSED = "paused"


_AGENT_SHOWN = {
    AgentState.QUEUED: ("Queued", Tone.MUTED),
    AgentState.WORKING: ("Running", Tone.RUNNING),
    AgentState.DONE: ("Done", Tone.SUCCESS),
    AgentState.STOPPED: ("Stopped", Tone.MUTED),
    AgentState.FAILED: ("Failed", Tone.ERROR),
    AgentState.PAUSED: ("Paused", Tone.WARN),
}
# how a sub-agent with nothing left to come stands, by the state its last turn ended in
AGENT_ENDED = {
    TurnState.ABORTED: AgentState.STOPPED,
    TurnState.FAILED: AgentState.FAILED,
    TurnState.INTERRUPTED: AgentState.FAILED,
    TurnState.CAPPED: AgentState.PAUSED,
}

class MadeLife(Shown):
    """How long an image or track a batch made stays (attachments.life, comfyui/life.py):
    temporary until its days are up, kept until it is let go, or cleared."""
    TEMPORARY = "temporary"
    KEPT = "kept"
    CLEARED = "cleared"


_MADE_LIFE_SHOWN = {
    MadeLife.TEMPORARY: ("Temporary", Tone.MUTED),
    MadeLife.KEPT: ("Kept", Tone.SUCCESS),
    MadeLife.CLEARED: ("Cleared", Tone.MUTED),
}


class ModelFileState(Shown):
    """Where a model file the agent asked for stands (model_requests.state,
    comfyui/model_files.py): its request waits for the user, was denied or not answered;
    the comfy helper downloads it, has it and waits for ComfyUI to list it, has it listed,
    or failed."""
    ASKED = "asked"
    DENIED = "denied"
    UNANSWERED = "unanswered"
    FETCHING = "fetching"
    LISTING = "listing"
    READY = "ready"
    FAILED = "failed"


_MODEL_FILE_SHOWN = {
    ModelFileState.ASKED: ("Waiting for you", Tone.WARN),
    ModelFileState.DENIED: ("Denied", Tone.MUTED),
    ModelFileState.UNANSWERED: ("No answer", Tone.MUTED),
    ModelFileState.FETCHING: ("Downloading", Tone.RUNNING),
    ModelFileState.LISTING: ("Waiting for ComfyUI", Tone.RUNNING),
    ModelFileState.READY: ("Ready", Tone.SUCCESS),
    ModelFileState.FAILED: ("Failed", Tone.ERROR),
}
# a request that is over: nothing follows it any more
MODEL_FILE_SETTLED = (ModelFileState.DENIED, ModelFileState.UNANSWERED, ModelFileState.READY,
                      ModelFileState.FAILED)


class KeyState(Shown):
    """Whether a key is kept: a provider's (settings/registry.key_state), the Civitai key the
    comfy helper keeps (comfyui/model_files.civitai_key)."""
    SET = "set"
    NOT_SET = "not set"


_KEY_SHOWN = {
    KeyState.SET: ("Set", Tone.SUCCESS),
    KeyState.NOT_SET: ("Not set", Tone.MUTED),
}

# each shown set's members as (words, tone); one table per set, since a member equals its
# string and "done" of one set would be "done" of another in a shared table
_DISPLAYS: dict[type[Shown], Mapping[Any, tuple[str, Tone]]] = {
    RunState: _RUN_SHOWN, MissionStatus: _MISSION_SHOWN,
    PlanStepState: _PLAN_STEP_SHOWN, AgentState: _AGENT_SHOWN, MadeLife: _MADE_LIFE_SHOWN,
    ModelFileState: _MODEL_FILE_SHOWN, KeyState: _KEY_SHOWN}
